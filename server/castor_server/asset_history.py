"""One picture from many scans: what each run saw of an asset, what a run covered and didn't see,
and the changes across a whole project.

Every report on an asset is an observation (assets.observe). This module adds the other half:

- **Not seen.** A finished run whose results Castor reads for coverage (`ABSENCE`: an nmap host
  discovery, an nmap port scan, an httpx probe) also says what it *didn't* find among what it
  covered. Coverage is the card's targets (its target/targets/hosts… arguments, target lists and
  asset references expanded, its `exclude` taken away) and, for services, the ports the scan
  tried (nmap's `<scaninfo>`, or the card's `ports` argument). A scan of 10.0.1.0/24 says nothing
  about 10.0.2.5, and a scan of port 445 nothing about port 22. Each such asset gets an `absent`
  observation and `missing_since`; seeing it again clears that (assets.observe).
- **Timelines.** `timeline` turns an asset's observations (oldest first) into what changed at each
  one: new, changed (field, before, after), seen again unchanged, gone, back.
- **Project changes.** `project_changes` runs the same over every asset a project's runs touched in
  a date range: new hosts, ports that opened or went away, pages whose title or status changed.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m
from .models import utcnow

# Built-in parsers whose runs say what they didn't find, and the asset types they speak for.
# A port scan with --open lists only hosts with open ports, so it says nothing about hosts.
ABSENCE: dict[str, tuple[str, ...]] = {"nmap.hosts": ("host",), "nmap.services": ("service",), "httpx.results": ("url",)}
TARGET_ARGS = ("target", "targets", "host", "hosts", "url", "urls", "domain", "domains", "ip", "ips", "range", "ranges")
EXCLUDE_ARGS = ("exclude", "excludes")
PortRanges = dict[str, list[tuple[int, int]]]


@dataclass
class Coverage:
    parser: str
    types: tuple[str, ...]
    targets: list[str]
    exclude: list[str]
    ports: PortRanges | None = None  # services: the ports it tried, per protocol (None: unknown)
    hosts: set[str] | None = None  # httpx: the hosts that answered at all


# ---------- what a run covered ----------


def split_targets(value: Any) -> list[str]:
    items = value if isinstance(value, list) else re.split(r"[\s,]+", str(value or ""))
    return [str(x).strip() for x in items if str(x).strip()]


async def job_targets(db: AsyncSession, task: m.Task) -> tuple[list[str], list[str]]:
    """The card's targets and exclusions, with target lists and asset references expanded."""
    args = dict(task.command_args or {})
    if task.command_ref:
        from .command_library import load_spec
        from .variables import VariableError, expand_list_refs

        spec = await load_spec(db, task.command_ref)
        if spec is not None:
            try:
                args = await expand_list_refs(db, spec, args, task.project_id)
            except VariableError:
                return [], []
    targets = [t for name in TARGET_ARGS for t in split_targets(args.get(name)) if not isinstance(args.get(name), dict)]
    exclude = [t for name in EXCLUDE_ARGS for t in split_targets(args.get(name)) if not isinstance(args.get(name), dict)]
    return targets, exclude


def parse_ports(spec: str, default_proto: str = "tcp") -> PortRanges:
    """nmap's port syntax: "22,80,8000-8100", "T:80,U:53", "-" (all). Unknown bits are skipped."""
    out: PortRanges = {}
    proto = default_proto
    for part in re.split(r"[\s,]+", spec.strip()):
        if not part:
            continue
        mt = re.match(r"^([TUS]):(.*)$", part, re.IGNORECASE)
        if mt:
            proto = {"T": "tcp", "U": "udp", "S": "sctp"}[mt[1].upper()]
            part = mt[2]
            if not part:
                continue
        if part == "-":
            out.setdefault(proto, []).append((1, 65535))
            continue
        rng = re.match(r"^(\d*)-(\d*)$", part)
        if rng:
            out.setdefault(proto, []).append((int(rng[1] or 1), int(rng[2] or 65535)))
        elif part.isdigit():
            out.setdefault(proto, []).append((int(part), int(part)))
    return out


def port_covered(ports: PortRanges, proto: str, port: int) -> bool:
    return any(lo <= port <= hi for lo, hi in ports.get(proto or "tcp", []))


def nmap_coverage(data: bytes) -> tuple[str, PortRanges | None]:
    """From nmap XML: its command line (`args`) and the ports it tried (`<scaninfo>`)."""
    from .parsers import ParseError, _nmap_root

    try:
        root = _nmap_root(data)
    except ParseError:
        return "", None
    ports: PortRanges = {}
    for info in root.findall("scaninfo"):
        proto = info.get("protocol") or "tcp"
        for p, ranges in parse_ports(info.get("services") or "", proto).items():
            ports.setdefault(p, []).extend(ranges)
    return root.get("args") or "", ports or None


def _ipv4_range(target: str, entry: str) -> bool | None:
    """nmap's octet ranges ("10.0.0.1-20", "10.0.1-3.*"); None when the entry isn't one."""
    parts = entry.split(".")
    if len(parts) != 4 or not any(c in entry for c in "-*,") or "/" in entry:
        return None
    try:
        octets = [int(x) for x in target.split(".")]
    except ValueError:
        return False
    if len(octets) != 4:
        return False
    for value, part in zip(octets, parts, strict=True):
        ok = False
        for piece in part.split(","):
            if piece == "*":
                ok = True
            elif "-" in piece:
                lo, _, hi = piece.partition("-")
                if not (lo or "0").isdigit() or not (hi or "255").isdigit():
                    return None
                ok = int(lo or 0) <= value <= int(hi or 255)
            elif piece.isdigit():
                ok = value == int(piece)
            else:
                return None
            if ok:
                break
        if not ok:
            return False
    return True


def _bare(entry: str) -> str:
    """"https://app.example.com:8443/x" → "app.example.com", "10.0.0.5:8080" → "10.0.0.5"."""
    entry = entry.strip()
    if "://" in entry:
        return urlsplit(entry).hostname or entry
    mt = re.match(r"^\[([^\]]+)\](?::\d+)?$", entry) or re.match(r"^([^:/\s]+):\d+$", entry)
    return mt[1] if mt else entry


def target_matches(target: str, entry: str) -> bool:
    from .assets import _matches

    entry = _bare(entry)
    ranged = _ipv4_range(target, entry)
    if ranged is not None:
        return ranged
    return _matches(target, entry)


def covers(cov: Coverage, asset: m.Asset) -> bool:
    """Whether a run's coverage took in this asset (its address or name, and for a service its port)."""
    from .assets import _targets

    names = _targets(asset)
    if not names or not any(target_matches(t, e) for t in names for e in cov.targets):
        return False
    if any(target_matches(t, e) for t in names for e in cov.exclude):
        return False
    f = asset.fields or {}
    if asset.type == "service":
        try:
            port = int(f.get("port"))
        except (TypeError, ValueError):
            return False
        return cov.ports is not None and port_covered(cov.ports, str(f.get("proto") or "tcp"), port)
    if asset.type == "url":
        parts = urlsplit(str(f.get("url") or ""))
        # httpx probes a host's root; a path ffuf found isn't something it looked at. And it tries
        # https before http, so one scheme not answering isn't the host going away: only a host
        # that didn't answer at all counts.
        return parts.path in ("", "/") and cov.hosts is not None and (parts.hostname or "") not in cov.hosts
    return True


async def coverage_of(db: AsyncSession, job: m.Job, task: m.Task, out: m.JobOutput) -> Coverage | None:
    types = ABSENCE.get(out.parser)
    if not types or out.error:
        return None
    targets, exclude = await job_targets(db, task)
    if not targets:
        return None
    cov = Coverage(out.parser, types, targets, exclude)
    if out.parser.startswith("nmap."):
        args, ports = "", None
        from . import parsers
        from .outputs import _specs, read_inputs

        spec = next((s for s in await _specs(db, job) if s.name == out.name), None)
        if spec is not None:
            try:
                blobs, _ = await read_inputs(db, job, spec)
            except (parsers.ParseError, OSError):
                blobs = []
            for blob in blobs:
                a, p = nmap_coverage(blob)
                args = args or a
                for proto, ranges in (p or {}).items():
                    ports = ports or {}
                    ports.setdefault(proto, []).extend(ranges)
        flags = set(args.split())
        if out.parser == "nmap.hosts" and "--open" in flags:
            return None  # only hosts with an open port are listed: no word on the rest
        if out.parser == "nmap.services":
            if ports is None and isinstance((task.command_args or {}).get("ports"), str):
                ports = parse_ports(task.command_args["ports"]) or None
            if ports is None:
                return None
            cov.ports = ports
    if out.parser == "httpx.results":
        rows = out.value if isinstance(out.value, list) else []
        cov.hosts = {(urlsplit(str(r.get("url") or "")).hostname or "") for r in rows if isinstance(r, dict)}
    return cov


async def record_absent(db: AsyncSession, job: m.Job, task: m.Task, outputs: list[m.JobOutput], batch: Any) -> None:
    """For a finished run: every confirmed asset it covered, seen before it started, that it didn't
    see gets a "not seen" observation (once per run, however often it's parsed again)."""
    if job.status != m.JobStatus.succeeded:
        return  # a scan that failed or was cancelled says nothing about what it didn't reach
    if job.runner == "manual":
        return  # output someone brought in (manual_runs.py) is a part of something, not a whole scan
    from .assets import segment_of

    started = job.started_at or job.queued_at
    segment = await segment_of(db, job)
    already = set((await db.execute(select(m.AssetObservation.asset_id).where(
        m.AssetObservation.job_id == job.id, m.AssetObservation.absent.is_(True)))).scalars())
    now = utcnow()
    for out in outputs:
        cov = await coverage_of(db, job, task, out)
        if cov is None:
            continue
        q = select(m.Asset).where(m.Asset.project_id == task.project_id, m.Asset.type.in_(cov.types), m.Asset.status == "confirmed")
        if started is not None:
            q = q.where(m.Asset.first_seen < started)
        for asset in (await db.execute(q)).scalars():
            if asset.id in batch.seen or asset.id in already or not covers(cov, asset):
                continue
            already.add(asset.id)
            db.add(m.AssetObservation(asset_id=asset.id, source="parser", parser=out.parser, job_id=job.id, task_id=task.id,
                                      agent_id=job.agent_id, segment=segment, fields={}, absent=True, confidence=85, observed_at=now))
            if segment and not await _seen_from(db, asset.id, segment):
                continue  # never reached from here: a difference in reach (segmentation.py), not something gone
            if asset.missing_since is None:
                asset.missing_since = now
                batch.missing[asset.id] = asset
    await db.flush()


async def _seen_from(db: AsyncSession, asset_id: uuid.UUID, segment: str) -> bool:
    """Whether a run from this segment has seen the asset (or no run said where it ran from)."""
    rows = (await db.execute(select(m.AssetObservation.segment).where(
        m.AssetObservation.asset_id == asset_id, m.AssetObservation.absent.is_(False), m.AssetObservation.source == "parser").distinct())).scalars().all()
    return segment in rows or not any(rows)


# ---------- timelines ----------


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, list) and isinstance(b, list):
        return sorted(map(str, a)) == sorted(map(str, b))
    return a == b


def timeline(observations: list[Any]) -> list[dict[str, Any]]:
    """What each observation (oldest first) changed: `kind` new | changed | seen | gone | still_gone |
    back, and with network segments (an observation's `segment`, its run's vantage point) also
    reached (first seen from another segment) | unreached (not seen from a segment that never saw it,
    while others do: a difference in reach, not something gone). `changes` [{field, before, after}]
    (before None: first reported then). Newest last."""
    state: dict[str, Any] = {}
    present: bool | None = None  # anywhere
    seen_in: dict[str, bool] = {}  # by segment
    out: list[dict[str, Any]] = []
    for o in observations:
        seg = getattr(o, "segment", None) or ""
        if o.absent:
            if seg and seen_in.get(seg) is None and present is not None:
                kind = "unreached"
            elif seg:
                kind = "gone" if seen_in.get(seg) else "still_gone"
            else:
                kind = "gone" if present else "still_gone"
            if seg:
                seen_in[seg] = False
            else:
                seen_in = dict.fromkeys(seen_in, False)
            present = any(seen_in.values()) if seen_in and seg else False
            out.append({"obs": o, "kind": kind, "changes": []})
            continue
        changes = [{"field": k, "before": state.get(k), "after": v} for k, v in (o.fields or {}).items()
                   if v is not None and not _same(state.get(k), v)]
        before = seen_in.get(seg) if seg else present
        if present is None:
            kind = "new"
        elif seg and before is None and seen_in:
            kind = "reached"
        elif before is False or (not seg and present is False):
            kind = "back"
        else:
            kind = "changed" if any(c["before"] is not None for c in changes) else "seen"
        state.update({k: v for k, v in (o.fields or {}).items() if v is not None})
        if seg:
            seen_in[seg] = True
        present = True
        out.append({"obs": o, "kind": kind, "changes": changes})
    return out


async def sources(db: AsyncSession, observations: list[Any]) -> dict[str, dict]:
    """The cards, commands and agents behind observations, by kind: {"tasks": {id: task}, "agents": {id: name}, "jobs": {id: job}}."""
    task_ids = {o.task_id for o in observations if o.task_id}
    job_ids = {o.job_id for o in observations if o.job_id}
    tasks = {t.id: t for t in (await db.execute(select(m.Task).where(m.Task.id.in_(task_ids)))).scalars()} if task_ids else {}
    jobs = {j.id: j for j in (await db.execute(select(m.Job).where(m.Job.id.in_(job_ids)))).scalars()} if job_ids else {}
    agent_ids = {o.agent_id for o in observations if o.agent_id} | {j.agent_id for j in jobs.values() if j.agent_id}
    agents = {a.id: a.name for a in (await db.execute(select(m.Agent).where(m.Agent.id.in_(agent_ids)))).scalars()} if agent_ids else {}
    return {"tasks": tasks, "jobs": jobs, "agents": agents}


def source_out(o: Any, src: dict[str, dict]) -> dict[str, Any]:
    t = src["tasks"].get(o.task_id) if o.task_id else None
    j = src["jobs"].get(o.job_id) if o.job_id else None
    agent_id = o.agent_id or (j.agent_id if j else None)
    return {
        "kind": getattr(o, "source", "parser"), "parser": o.parser, "job_id": str(o.job_id) if o.job_id else None,
        "task": {"id": str(t.id), "title": t.title, "link": f"/projects/{t.project_id}?task={t.id}"} if t else None,
        "command": t.command_ref if t else None,
        "agent": src["agents"].get(agent_id) if agent_id else None,
        "agent_id": str(agent_id) if agent_id else None,
        "segment": getattr(o, "segment", None),
    }


# ---------- across a project ----------

MAX_ASSETS = 3000
MAX_EVENTS = 2000
KINDS = ("new", "changed", "gone", "back")


async def _pictures(db: AsyncSession, items: list[dict[str, Any]]) -> None:
    """A change of look gets the screenshots before and after (`pictures`: [before, after] attachment ids)."""
    wanted = [x for x in items if any(c["field"] == "look" for c in x["changes"])]
    if not wanted:
        return
    ids = list({uuid.UUID(x["asset"]["id"]) for x in wanted})
    rows = (await db.execute(select(m.AssetEvidence).where(m.AssetEvidence.asset_id.in_(ids), m.AssetEvidence.kind == "screenshot",
                                                           m.AssetEvidence.attachment_id.is_not(None))
                             .order_by(m.AssetEvidence.observed_at))).scalars().all()
    from .evidence import similar

    for x in wanted:
        mine = [e for e in rows if str(e.asset_id) == x["asset"]["id"]]
        after = next((e for e in mine if x["source"]["job_id"] and str(e.job_id) == x["source"]["job_id"]), None)
        if after is None:
            continue
        earlier = [e for e in mine if _aware(e.observed_at) < _aware(after.observed_at)]
        before = next((e for e in reversed(earlier) if not similar(e.look, after.look)), None)
        x["pictures"] = [str(before.attachment_id) if before else None, str(after.attachment_id)]


def _aware(when: datetime | None) -> datetime | None:
    from datetime import UTC

    return when.replace(tzinfo=UTC) if when is not None and when.tzinfo is None else when


async def project_changes(
    db: AsyncSession, project: m.Project, *, since: datetime, until: datetime | None = None, type_name: str | None = None,
    scope: str | None = None, command: str | None = None, agent: str | None = None, kinds: set[str] | None = None,
) -> dict[str, Any]:
    """What changed in a project's assets between two times, from what its runs saw: new assets,
    changed values, assets that went unseen and ones back again. Newest first."""
    from .assets import SUGGESTING, scope_groups, scope_of

    until = until or utcnow()
    q = (select(m.AssetObservation.asset_id).join(m.Asset, m.Asset.id == m.AssetObservation.asset_id)
         .where(m.Asset.project_id == project.id, m.Asset.status == "confirmed", m.AssetObservation.observed_at >= since,
                m.AssetObservation.observed_at <= until, m.AssetObservation.source.not_in(SUGGESTING)).distinct())
    if type_name:
        q = q.where(m.Asset.type == type_name)
    ids = list((await db.execute(q.limit(MAX_ASSETS + 1))).scalars())
    truncated = len(ids) > MAX_ASSETS
    ids = ids[:MAX_ASSETS]
    if not ids:
        return {"items": [], "counts": dict.fromkeys(KINDS, 0), "truncated": False}
    assets = {a.id: a for a in (await db.execute(select(m.Asset).where(m.Asset.id.in_(ids)))).scalars()}
    sc = scope_of(project)
    if scope:
        assets = {k: a for k, a in assets.items() if (scope == "-" and not scope_groups(project, a, sc))
                  or (scope == "*" and scope_groups(project, a, sc)) or scope in scope_groups(project, a, sc)}
    obs = list((await db.execute(
        select(m.AssetObservation).where(m.AssetObservation.asset_id.in_(list(assets)), m.AssetObservation.observed_at <= until,
                                         m.AssetObservation.source.not_in(SUGGESTING))
        .order_by(m.AssetObservation.observed_at, m.AssetObservation.id))).scalars())
    by_asset: dict[uuid.UUID, list[m.AssetObservation]] = {}
    for o in obs:
        by_asset.setdefault(o.asset_id, []).append(o)
    events: list[dict[str, Any]] = []
    for asset_id, rows in by_asset.items():
        for e in timeline(rows):
            o = e["obs"]
            if e["kind"] not in KINDS or _aware(o.observed_at) < _aware(since):
                continue
            if kinds and e["kind"] not in kinds:
                continue
            if e["kind"] == "changed":
                e["changes"] = [c for c in e["changes"] if c["before"] is not None]
            events.append({**e, "asset": assets[asset_id]})
    src = await sources(db, [e["obs"] for e in events])
    items = []
    for e in events:
        s = source_out(e["obs"], src)
        if command and s["command"] != command:
            continue
        if agent and s["agent_id"] != agent:
            continue
        a = e["asset"]
        items.append({"asset": {"id": str(a.id), "type": a.type, "label": a.label, "scope_groups": scope_groups(project, a, sc)},
                      "kind": e["kind"], "at": e["obs"].observed_at, "changes": e["changes"], "source": s})
    await _pictures(db, items)
    items.sort(key=lambda x: _aware(x["at"]), reverse=True)
    counts = dict.fromkeys(KINDS, 0)
    for x in items:
        counts[x["kind"]] += 1
    return {"items": items[:MAX_EVENTS], "counts": counts, "truncated": truncated or len(items) > MAX_EVENTS}

