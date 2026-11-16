"""Vantage points: what each network segment reaches, and segmentation findings.

An agent's `segment` ("VLAN 20 / user LAN", set by admins) is where its runs look from; every
observation records it (`asset_observations.segment`). So the same scan from agents in five segments
(a card fanned out with `{tags, all: true}`), or ordinary runs over time, answer "which segments can
reach this, and what does each see?":

- `matrix` is the reachability matrix: for each asset, per segment, the latest word from there,
  seen (with what it saw: a service's state and version, a page's status, title, look and
  certificate) or not seen, and whether the segments disagree.
- Segmentation rules (`projects.settings.segmentation`: [{id, name, from, type, where, scope,
  severity}]) say what a segment must not reach: "Guest must not reach services in the Management
  scope group". A run from that segment that reaches a matching asset records a **finding**: a
  vulnerability asset (tool `segmentation`) that affects it. Findings feed the inventory, the feed,
  triggers and reports like any vulnerability. `check` applies the rules to what's known already.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m

# What a cell shows and compares, per asset type (other types: every field the run reported).
COMPARE: dict[str, tuple[str, ...]] = {
    "host": ("status", "os", "hostname"),
    "service": ("state", "name", "product", "version", "tls"),
    "url": ("status", "title", "look", "cert_subject", "webserver"),
    "share": ("access",),
}
MAX_ROWS = 2000
SEVERITIES = ("info", "low", "medium", "high", "critical")


class RuleError(ValueError):
    pass


def rules_of(project: m.Project) -> list[dict[str, Any]]:
    return [r for r in (project.settings or {}).get("segmentation") or [] if isinstance(r, dict) and r.get("from")]


def clean_rules(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Checked rules, each with an id (kept when it has one, so its findings stay its own)."""
    from .assets import AssetError, parse_where, type_of

    out = []
    for r in rules:
        name, seg = str(r.get("name") or "").strip()[:120], str(r.get("from") or "").strip()[:80]
        if not name or not seg:
            raise RuleError("each rule needs a name and the segment it's about")
        type_name = str(r.get("type") or "").strip() or None
        if type_name:
            try:
                type_name = type_of(type_name).name
            except AssetError as exc:
                raise RuleError(f"“{name}”: {exc}") from exc
        where = str(r.get("where") or "").strip()[:500]
        parse_where(where)
        severity = str(r.get("severity") or "medium").lower()
        if severity not in SEVERITIES:
            raise RuleError(f"“{name}”: severity is one of {', '.join(SEVERITIES)}")
        out.append({"id": str(r.get("id") or uuid.uuid4().hex[:8])[:16], "name": name, "from": seg, "type": type_name,
                    "where": where, "scope": str(r.get("scope") or "").strip() or None, "severity": severity})
    return out


async def _matching(db: AsyncSession, project: m.Project, rule: dict[str, Any]) -> set[uuid.UUID]:
    """The confirmed assets a rule is about (its type, words and conditions, scope group)."""
    from .assets import query, scope_groups, scope_of

    rows = await query(db, project.id, rule.get("type"), rule.get("where") or "")
    sc = scope_of(project)
    want = rule.get("scope")
    return {a.id for a in rows if a.type != "vulnerability"
            and (not want or (want == "*" and scope_groups(project, a, sc)) or want in scope_groups(project, a, sc))}


async def _finding(db: AsyncSession, project: m.Project, rule: dict[str, Any], asset: m.Asset, batch: Any, *,
                   job_id: uuid.UUID | None = None, task_id: uuid.UUID | None = None, agent_id: uuid.UUID | None = None) -> m.Asset:
    from .assets import link, observe

    ident = f"SEGMENTATION-{rule['id']}-{hashlib.sha256(f'{asset.type}:{asset.key}'.encode()).hexdigest()[:10]}"
    vuln = await observe(db, project.id, "vulnerability", {
        "id": ident, "target": asset.label, "title": f"{rule['name']}: {rule['from']} reaches {asset.label}",
        "severity": rule["severity"], "tool": "segmentation",
        "detail": f"A run from the {rule['from']} segment reached {asset.label}, which the rule “{rule['name']}” says it must not.",
    }, batch=batch, source="parser", parser="segmentation", job_id=job_id, task_id=task_id, agent_id=agent_id, segment=rule["from"])
    await link(db, project.id, vuln, asset, "affects", batch=batch, source="parser", parser="segmentation", job_id=job_id, task_id=task_id)
    return vuln


async def after_ingest(db: AsyncSession, job: m.Job, task: m.Task, batch: Any) -> None:
    """A finished run from a segment: anything it reached that a rule says that segment must not."""
    from .assets import segment_of

    segment = await segment_of(db, job)
    if not segment or not batch.seen:
        return
    project = await db.get(m.Project, task.project_id)
    rules = [r for r in rules_of(project) if r["from"] == segment] if project else []
    for rule in rules:
        hits = await _matching(db, project, rule) & batch.seen
        for asset_id in sorted(hits):
            asset = await db.get(m.Asset, asset_id)
            if asset is not None and asset.status == "confirmed":
                await _finding(db, project, rule, asset, batch, job_id=job.id, task_id=task.id, agent_id=job.agent_id)


async def _latest(db: AsyncSession, asset_ids: list[uuid.UUID], batch_id: uuid.UUID | None = None) -> dict[tuple[uuid.UUID, str], m.AssetObservation]:
    """The latest observation per (asset, segment), from runs that said where they ran."""
    from .assets import SUGGESTING

    q = (select(m.AssetObservation).where(m.AssetObservation.asset_id.in_(asset_ids), m.AssetObservation.segment.is_not(None),
                                          m.AssetObservation.source.not_in(SUGGESTING))
         .order_by(m.AssetObservation.observed_at, m.AssetObservation.id))
    if batch_id is not None:
        q = q.where(m.AssetObservation.job_id.in_(select(m.Job.id).where(m.Job.batch_id == batch_id)))
    out: dict[tuple[uuid.UUID, str], m.AssetObservation] = {}
    for o in (await db.execute(q)).scalars():
        out[(o.asset_id, o.segment or "")] = o
    return out


async def check(db: AsyncSession, project: m.Project) -> dict[str, int]:
    """Apply the rules to what's known: an asset whose latest word from a rule's segment is that it
    was seen there gets its finding (once). Commits, and tells the feed and triggers."""
    from .assets import Batch, settle

    batch = Batch(project.id)
    found = 0
    for rule in rules_of(project):
        ids = await _matching(db, project, rule)
        if not ids:
            continue
        latest = await _latest(db, list(ids))
        for (asset_id, seg), o in sorted(latest.items(), key=lambda x: str(x[0][0])):
            if seg != rule["from"] or o.absent:
                continue
            asset = await db.get(m.Asset, asset_id)
            if asset is not None:
                await _finding(db, project, rule, asset, batch, job_id=o.job_id, task_id=o.task_id, agent_id=o.agent_id)
                found += 1
    created = len(batch.created())
    await settle(db, batch)
    return {"reached": found, "new": created}


def _cell(o: m.AssetObservation, asset: m.Asset, src: dict[str, dict]) -> dict[str, Any]:
    from .asset_history import source_out

    names = COMPARE.get(asset.type)
    fields = {k: v for k, v in (o.fields or {}).items() if (names is None or k in names) and v not in (None, "", [])}
    s = source_out(o, src)
    return {"state": "not_seen" if o.absent else "seen", "at": o.observed_at, "fields": {} if o.absent else fields,
            "agent": s["agent"], "task": s["task"], "job_id": s["job_id"]}


def _differs(cells: list[dict[str, Any]]) -> list[str]:
    """What the segments disagree on: "reach" (seen from some, not others) and the fields seen differently."""
    from .evidence import similar

    out = []
    if len({c["state"] for c in cells}) > 1:
        out.append("reach")
    seen = [c["fields"] for c in cells if c["state"] == "seen"]
    for name in sorted({k for f in seen for k in f}):
        values = [f[name] for f in seen if name in f]
        if len(values) < 2:
            continue
        if name == "look":
            if any(not similar(values[0], v) for v in values[1:]):
                out.append(name)
        elif len({str(v) for v in values}) > 1:
            out.append(name)
    return out


async def matrix(db: AsyncSession, project: m.Project, *, type_name: str | None = None, where: str = "", scope: str | None = None,
                 batch_id: uuid.UUID | None = None, asset_id: uuid.UUID | None = None, differs: bool = False) -> dict[str, Any]:
    """The reachability matrix: segments (columns) and, per asset, each segment's latest word."""
    from .asset_history import sources
    from .assets import query, scope_groups, scope_of

    sc = scope_of(project)
    if asset_id is not None:
        a = await db.get(m.Asset, asset_id)
        assets = [a] if a is not None and a.project_id == project.id else []
    else:
        assets = await query(db, project.id, type_name, where)
        if scope:
            assets = [a for a in assets if (scope == "-" and not scope_groups(project, a, sc)) or (scope == "*" and scope_groups(project, a, sc))
                      or scope in scope_groups(project, a, sc)]
    by_id = {a.id: a for a in assets if a.type != "vulnerability"}
    latest = await _latest(db, list(by_id), batch_id) if by_id else {}
    src = await sources(db, list(latest.values()))
    segments = sorted({seg for _, seg in latest})
    known = sorted({(a.segment or "") for a in (await db.execute(select(m.Agent))).scalars()} - {""})
    rules = rules_of(project)
    breaking: dict[str, set[uuid.UUID]] = {r["id"]: await _matching(db, project, r) for r in rules} if rules else {}
    rows = []
    for aid, a in by_id.items():
        cells = {seg: _cell(o, a, src) for (x, seg), o in latest.items() if x == aid}
        if not cells:
            continue
        diff = _differs(list(cells.values()))
        if differs and not diff:
            continue
        broken = [r["name"] for r in rules if aid in breaking[r["id"]] and cells.get(r["from"], {}).get("state") == "seen"]
        rows.append({"asset": {"id": str(a.id), "type": a.type, "label": a.label, "scope_groups": scope_groups(project, a, sc)},
                     "cells": cells, "differs": diff, "rules": broken})
    rows.sort(key=lambda r: (not r["rules"], not r["differs"], r["asset"]["type"], r["asset"]["label"]))
    return {"segments": segments, "agent_segments": known, "rows": rows[:MAX_ROWS], "truncated": len(rows) > MAX_ROWS,
            "rules": rules}
