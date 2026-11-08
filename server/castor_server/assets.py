"""Assets (Phase 3f): what a project has found, as things Castor understands.

A host, service, URL, domain, user, credential or share is one record per project, keyed by
an identity (`key`) so every tool that reports on the same thing adds to it. Each report is
an observation (who said what, when); `fields` holds the merged current values, newest
first. Links connect them (a service *on* a host, a domain that *resolves to* a host).

Parsed outputs become assets automatically (`ingest_outputs`, called after a job's outputs
are parsed): nmap hosts and services, NetExec SMB hosts, ffuf URLs. Triggers then run a
command or playbook for new or changed assets: automatically when the asset is inside the
project's scope (`projects.settings.scope`), otherwise after someone approves it; once per
asset and at most N per hour.

"ip" is accepted as a type and stored as a host keyed by its address.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import alerts, notifications, vault
from . import models as m
from .events import bus
from .models import utcnow

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FieldDef:
    name: str
    label: str
    type: str = "text"  # text | number | bool | list


@dataclass(frozen=True)
class TypeDef:
    name: str
    label: str
    plural: str
    icon: str
    fields: tuple[FieldDef, ...]
    # The fields that identify it (one of them must be set).
    identity: tuple[str, ...]


F = FieldDef
TYPES: dict[str, TypeDef] = {
    t.name: t
    for t in [
        TypeDef("host", "Host", "Hosts", "dns", (
            F("ip", "Address"), F("hostname", "Hostname"), F("os", "OS"), F("domain", "Domain"),
            F("status", "Status"), F("smb_signing", "SMB signing required", "bool"), F("smbv1", "SMBv1", "bool"),
            F("mac", "MAC"),
        ), ("ip", "hostname")),
        TypeDef("service", "Service", "Services", "lan", (
            F("host", "Host"), F("port", "Port", "number"), F("proto", "Protocol"), F("name", "Service"),
            F("product", "Product"), F("version", "Version"), F("state", "State"),
        ), ("host", "port")),
        TypeDef("url", "URL", "URLs", "link", (
            F("url", "URL"), F("status", "Status", "number"), F("title", "Title"), F("length", "Length", "number"),
            F("content_type", "Content type"),
        ), ("url",)),
        TypeDef("domain", "Domain", "Domains", "language", (
            F("name", "Name"), F("ips", "Addresses", "list"),
        ), ("name",)),
        TypeDef("user", "User", "Users", "person", (
            F("username", "Username"), F("domain", "Domain"), F("enabled", "Enabled", "bool"), F("groups", "Groups", "list"),
        ), ("username",)),
        TypeDef("credential", "Credential", "Credentials", "key", (
            F("username", "Username"), F("domain", "Domain"), F("kind", "Kind"), F("fingerprint", "Fingerprint"),
        ), ("username",)),
        TypeDef("share", "Share", "Shares", "folder_shared", (
            F("host", "Host"), F("name", "Share"), F("access", "Access"), F("remark", "Remark"),
        ), ("host", "name")),
    ]
}
ALIASES = {"ip": "host", "hosts": "host", "services": "service", "urls": "url", "domains": "domain",
           "users": "user", "credentials": "credential", "creds": "credential", "shares": "share"}
# Link kinds, as shown from the "from" side and the "to" side.
LINKS = {"on": ("on", "has"), "resolves_to": ("resolves to", "resolved from"), "valid_on": ("valid on", "accepts")}


class AssetError(ValueError):
    pass


def type_of(name: str) -> TypeDef:
    t = TYPES.get(ALIASES.get(name.lower(), name.lower()))
    if t is None:
        raise AssetError(f"unknown asset type {name}")
    return t


def _ip(value: Any) -> str | None:
    try:
        return str(ipaddress.ip_address(str(value).strip()))
    except ValueError:
        return None


def _clean(t: TypeDef, fields: dict[str, Any]) -> dict[str, Any]:
    """Typed core fields, free extra fields; drops empty values."""
    known = {f.name: f for f in t.fields}
    out: dict[str, Any] = {}
    for k, v in (fields or {}).items():
        if v is None or v == "" or v == []:
            continue
        name = str(k).strip()[:64]
        f = known.get(name)
        if f and f.type == "number":
            try:
                v = int(v)
            except (TypeError, ValueError):
                continue
        elif f and f.type == "bool":
            v = v if isinstance(v, bool) else str(v).strip().lower() in ("true", "yes", "1", "on")
        elif f and f.type == "list":
            v = sorted({str(x).strip() for x in (v if isinstance(v, list) else str(v).split(",")) if str(x).strip()})
        elif isinstance(v, str):
            v = v.strip()[:2000]
        out[name] = v
    if t.name == "host":
        if out.get("ip"):
            out["ip"] = _ip(out["ip"]) or out["ip"]
        if out.get("hostname"):
            out["hostname"] = str(out["hostname"]).lower().rstrip(".")
    if t.name == "domain" and out.get("name"):
        out["name"] = str(out["name"]).lower().rstrip(".")
    if t.name == "service":
        out.setdefault("proto", "tcp")
        if out.get("host"):
            out["host"] = _ip(out["host"]) or str(out["host"]).lower()
    return out


def identity(type_name: str, fields: dict[str, Any], secret: str | None = None) -> tuple[str, str, dict[str, Any]]:
    """(key, label, cleaned fields) for an asset, or AssetError if it can't be identified."""
    t = type_of(type_name)
    f = _clean(t, fields)
    if t.name == "host":
        if not (f.get("ip") or f.get("hostname")):
            raise AssetError("a host needs an address or a hostname")
        key = f.get("ip") or f["hostname"]
        label = f.get("ip") or f["hostname"]
        if f.get("ip") and f.get("hostname"):
            label = f"{f['ip']} · {f['hostname']}"
    elif t.name == "service":
        if not (f.get("host") and f.get("port")):
            raise AssetError("a service needs a host and a port")
        key = f"{f['host']}:{f['port']}/{f['proto']}"
        label = key + (f" · {f['name']}" if f.get("name") else "")
    elif t.name == "url":
        if not f.get("url"):
            raise AssetError("a URL needs its url")
        key = label = f["url"]
    elif t.name == "domain":
        if not f.get("name"):
            raise AssetError("a domain needs a name")
        key = label = f["name"]
    elif t.name in ("user", "credential"):
        if not f.get("username"):
            raise AssetError(f"a {t.name} needs a username")
        who = f"{f['domain']}\\{f['username']}" if f.get("domain") else f["username"]
        key = label = who.lower()
        if t.name == "credential":
            f.setdefault("kind", "password")
            if secret:
                f["fingerprint"] = hashlib.sha256(secret.encode()).hexdigest()[:12]
            key = f"{key}:{f['kind']}:{f.get('fingerprint', '')}"
            label = f"{who} ({f['kind']})"
    elif t.name == "share":
        if not (f.get("host") and f.get("name")):
            raise AssetError("a share needs a host and a name")
        key = label = f"\\\\{f['host']}\\{f['name']}"
        key = key.lower()
    else:  # pragma: no cover - TYPES is closed
        raise AssetError(f"unknown asset type {type_name}")
    return key[:400], label[:400], f


# ---------- recording ----------


@dataclass
class Change:
    asset: m.Asset
    event: str  # created | updated
    fields: list[str] = field(default_factory=list)


@dataclass
class Batch:
    """What one ingest did, for events, the activity feed and triggers."""

    project_id: uuid.UUID
    changes: dict[uuid.UUID, Change] = field(default_factory=dict)

    def note(self, asset: m.Asset, created: bool, changed: list[str]) -> None:
        prev = self.changes.get(asset.id)
        if prev is None:
            if created or changed:
                self.changes[asset.id] = Change(asset, "created" if created else "updated", changed)
        else:
            prev.fields = sorted({*prev.fields, *changed})

    def created(self) -> list[m.Asset]:
        return [c.asset for c in self.changes.values() if c.event == "created"]


async def observe(
    db: AsyncSession,
    project_id: uuid.UUID,
    type_name: str,
    fields: dict[str, Any],
    *,
    batch: Batch,
    source: str = "parser",
    parser: str | None = None,
    job_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    secret: str | None = None,
) -> m.Asset:
    """Record what a source says about an asset: find it by identity (or create it), merge the
    fields (newest wins), keep the observation. Flushes; the caller commits."""
    t = type_of(type_name)
    key, label, clean = identity(t.name, fields, secret)
    asset = (
        await db.execute(select(m.Asset).where(m.Asset.project_id == project_id, m.Asset.type == t.name, m.Asset.key == key))
    ).scalar_one_or_none()
    created = asset is None
    if asset is None:
        asset = m.Asset(project_id=project_id, type=t.name, key=key, label=label, fields={})
        db.add(asset)
    merged = dict(asset.fields or {})
    changed = [k for k, v in clean.items() if merged.get(k) != v]
    merged.update(clean)
    asset.fields = merged
    asset.label = label if len(label) >= len(asset.label or "") else asset.label  # keep the richest label
    asset.last_seen = utcnow()
    if secret:
        asset.secret = vault.encrypt(secret.encode())
    await db.flush()
    db.add(m.AssetObservation(asset_id=asset.id, source=source, parser=parser, job_id=job_id, task_id=task_id,
                              actor_id=actor_id, fields=clean))
    batch.note(asset, created, changed)
    return asset


async def link(db: AsyncSession, project_id: uuid.UUID, src: m.Asset, dst: m.Asset, kind: str) -> None:
    if src.id == dst.id:
        return
    exists = (
        await db.execute(select(m.AssetLink.id).where(m.AssetLink.from_id == src.id, m.AssetLink.to_id == dst.id, m.AssetLink.kind == kind))
    ).first()
    if exists is None:
        db.add(m.AssetLink(project_id=project_id, from_id=src.id, to_id=dst.id, kind=kind))
        await db.flush()


# ---------- from parsed outputs ----------


async def _find_host(db: AsyncSession, project_id: uuid.UUID, name: str) -> m.Asset | None:
    """A host known by this address or hostname."""
    ip = _ip(name)
    rows = (await db.execute(select(m.Asset).where(m.Asset.project_id == project_id, m.Asset.type == "host"))).scalars()
    for a in rows:
        f = a.fields or {}
        if (ip and f.get("ip") == ip) or (not ip and f.get("hostname") == name.lower()):
            return a
    return None


async def _map(db: AsyncSession, parser: str, rows: Any, project_id: uuid.UUID, batch: Batch, **src: Any) -> None:
    if not isinstance(rows, list):
        return
    obs = {"batch": batch, "parser": parser, **src}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            if parser == "nmap.hosts":
                if row.get("status", "up") != "up":
                    continue
                names = row.get("hostnames") or []
                host = await observe(db, project_id, "host", {"ip": row.get("ip"), "hostname": names[0] if names else None, "status": "up"}, **obs)
                for name in names:
                    domain = await observe(db, project_id, "domain", {"name": name, "ips": [row.get("ip")]}, **obs)
                    await link(db, project_id, domain, host, "resolves_to")
            elif parser == "nmap.services":
                if row.get("state", "open") != "open":
                    continue
                host = await observe(db, project_id, "host", {"ip": row.get("host"), "status": "up"}, **obs)
                svc = await observe(db, project_id, "service", {k: row.get(k) for k in ("host", "port", "proto", "name", "product", "version", "state")}, **obs)
                await link(db, project_id, svc, host, "on")
            elif parser == "netexec.smb":
                host = await observe(db, project_id, "host", {
                    "ip": row.get("host"), "hostname": row.get("name"), "domain": row.get("domain"), "os": row.get("os"),
                    "smb_signing": row.get("signing"), "smbv1": row.get("smbv1"), "status": "up",
                }, **obs)
                svc = await observe(db, project_id, "service", {"host": row.get("host"), "port": row.get("port") or 445, "proto": "tcp", "name": "microsoft-ds", "state": "open"}, **obs)
                await link(db, project_id, svc, host, "on")
            elif parser in ("ffuf.results", "httpx.results"):
                url = await observe(db, project_id, "url", {k: row.get(k) for k in ("url", "status", "length", "title", "content_type")}, **obs)
                hostname = urlsplit(str(row.get("url") or "")).hostname
                host = await _find_host(db, project_id, hostname) if hostname else None
                if host is not None:
                    await link(db, project_id, url, host, "on")
        except AssetError:
            continue  # a row without an identity (e.g. no address) isn't an asset


MAPPED_PARSERS = {"nmap.hosts", "nmap.services", "netexec.smb", "ffuf.results", "httpx.results"}


async def ingest_outputs(db: AsyncSession, job: m.Job, task: m.Task, outputs: list[m.JobOutput]) -> Batch:
    """Turn a job's parsed outputs into assets in its project, then tell browsers, write one
    feed entry and fire triggers. Commits."""
    batch = Batch(task.project_id)
    for out in outputs:
        if out.error or out.parser not in MAPPED_PARSERS:
            continue
        await _map(db, out.parser, out.value, task.project_id, batch, job_id=job.id, task_id=task.id)
    if batch.changes:
        await settle(db, batch, task_id=task.id)
    return batch


async def collect_past(db: AsyncSession, project_id: uuid.UUID, actor: m.User | None = None) -> Batch:
    """Record assets from every result the project's jobs already produced, oldest first
    (results parsed before assets existed). Triggers don't fire for these. Commits."""
    batch = Batch(project_id)
    rows = (
        await db.execute(
            select(m.JobOutput, m.Job)
            .join(m.Job, m.Job.id == m.JobOutput.job_id)
            .join(m.Task, m.Task.id == m.Job.task_id)
            .where(m.Task.project_id == project_id, m.JobOutput.error.is_(None), m.JobOutput.parser.in_(MAPPED_PARSERS))
            .order_by(m.Job.finished_at, m.JobOutput.created_at)
        )
    ).all()
    for out, job in rows:
        await _map(db, out.parser, out.value, project_id, batch, job_id=job.id, task_id=job.task_id)
    if batch.changes:
        from .services import actor_of, record_activity

        created: dict[str, int] = {}
        for a in batch.created():
            created[a.type] = created.get(a.type, 0) + 1
        if created:
            await record_activity(db, type="assets.found", message=f"Found {describe(created)} in past results",
                                  project_id=project_id, payload={"counts": created}, **actor_of(actor))
    await db.commit()
    bus.publish("assets.changed", project_id, {"project_id": str(project_id)})
    return batch


def describe(counts: dict[str, int]) -> str:
    parts = [f"{n} {TYPES[t].plural.lower() if n != 1 else TYPES[t].label.lower()}" for t, n in counts.items() if n]
    return ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1] if parts else ""


async def settle(db: AsyncSession, batch: Batch, *, task_id: uuid.UUID | None = None, actor: m.User | None = None) -> None:
    """After recording: the feed entry ("Found 3 hosts and 5 services"), the event, triggers."""
    from .services import actor_of, record_activity

    created: dict[str, int] = {}
    for a in batch.created():
        created[a.type] = created.get(a.type, 0) + 1
    if created:
        await record_activity(
            db, type="assets.found", message=f"Found {describe(created)}", project_id=batch.project_id, task_id=task_id,
            payload={"counts": created}, **actor_of(actor),
        )
    await db.commit()
    bus.publish("assets.changed", batch.project_id, {"project_id": str(batch.project_id), "created": created,
                                                     "updated": sum(1 for c in batch.changes.values() if c.event == "updated")})
    try:
        await fire_triggers(db, batch)
    except Exception:  # a trigger problem must never lose the assets
        log.exception("asset triggers for project %s failed", batch.project_id)


# ---------- queries ("hosts where smb_signing:false") ----------

TOKEN = re.compile(r'(\w+)(:|!=|>=|<=|>|<|=)("[^"]*"|\S+)|("[^"]+"|\S+)')


def parse_where(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """`field:value` (text contains it; yes/no and numbers match exactly), `field=value`
    (exactly), `field!=value`, `port>1000` filters and free words."""
    conds, words = [], []
    for fieldname, op, value, word in TOKEN.findall(text or ""):
        if word:
            words.append(word.strip('"').lower())
            continue
        value = value.strip('"')
        conds.append({"field": fieldname, "op": {":": "match", "=": "equals", "!=": "not_equals", ">": "gt", "<": "lt", ">=": "gte", "<=": "lte"}[op], "value": value})
    return conds, words


def _as(value: Any, like: Any) -> Any:
    if isinstance(like, bool):
        return str(value).lower() in ("true", "yes", "1", "on")
    if isinstance(like, int | float):
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    return str(value).lower()


def conditions_met(conds: list[dict[str, Any]], fields: dict[str, Any]) -> bool:
    for c in conds or []:
        name, op, want = c.get("field"), c.get("op", "equals"), c.get("value")
        have = fields.get(name)
        if op == "exists":
            ok = have not in (None, "", [])
        elif op == "missing":
            ok = have in (None, "", [])
        elif have is None:
            ok = op == "not_equals"
        elif isinstance(have, list):
            ok = (str(want).lower() in [str(x).lower() for x in have]) == (op != "not_equals")
        elif op in ("gt", "lt", "gte", "lte"):
            try:
                a, b = float(have), float(want)
            except (TypeError, ValueError):
                return False
            ok = {"gt": a > b, "lt": a < b, "gte": a >= b, "lte": a <= b}[op]
        elif op == "contains" or (op == "match" and isinstance(have, str)):
            ok = str(want).lower() in str(have).lower()
        else:
            same = _as(want, have) == (have if isinstance(have, bool | int | float) else str(have).lower())
            ok = not same if op == "not_equals" else same
        if not ok:
            return False
    return True


async def query(db: AsyncSession, project_id: uuid.UUID, type_name: str | None, where: str = "") -> list[m.Asset]:
    conds, words = parse_where(where)
    stmt = select(m.Asset).where(m.Asset.project_id == project_id)
    if type_name:
        stmt = stmt.where(m.Asset.type == type_of(type_name).name)
    rows = (await db.execute(stmt)).scalars().all()
    out = []
    for a in rows:
        hay = f"{a.label} {a.key} {' '.join(str(v) for v in (a.fields or {}).values())}".lower()
        if all(w in hay for w in words) and conditions_met(conds, a.fields or {}):
            out.append(a)
    return sorted(out, key=sort_key)


ORDER = {name: i for i, name in enumerate(TYPES)}


def sort_key(a: m.Asset) -> tuple:
    """Hosts first (then services, URLs…), addresses in numeric order."""
    f = a.fields or {}
    addr = _ip(f.get("ip") or f.get("host") or "")
    num = (0, int(ipaddress.ip_address(addr))) if addr else (1, 0)
    return (ORDER.get(a.type, 99), num, int(f.get("port") or 0), a.label)


def value_of(asset: m.Asset, name: str | None) -> str:
    if not name or name in ("key", "label"):
        return asset.key if name == "key" or not name else asset.label
    v = (asset.fields or {}).get(name)
    return ",".join(map(str, v)) if isinstance(v, list) else ("" if v is None else str(v))


def is_asset_ref(value: Any) -> bool:
    """A command argument taken from the project's assets: {"assets": {type, where, field}}."""
    return isinstance(value, dict) and set(value) == {"assets"} and isinstance(value["assets"], dict) and bool(value["assets"].get("type"))


async def ref_values(db: AsyncSession, project_id: uuid.UUID, ref: dict[str, Any]) -> list[str]:
    spec = ref["assets"]
    rows = await query(db, project_id, spec.get("type"), spec.get("where") or "")
    seen, out = set(), []
    for a in rows:
        v = value_of(a, spec.get("field") or default_field(a.type))
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def default_field(type_name: str) -> str:
    return {"host": "ip", "service": "key", "url": "url", "domain": "name", "user": "username", "credential": "username", "share": "key"}.get(type_name, "key")


# ---------- scope ----------


def scope_of(project: m.Project) -> dict[str, list[str]]:
    s = (project.settings or {}).get("scope") or {}
    return {"include": list(s.get("include") or []), "exclude": list(s.get("exclude") or [])}


def _targets(asset: m.Asset) -> list[str]:
    """The addresses and names an asset is about, for scope checks."""
    f = asset.fields or {}
    out = [f.get("ip"), f.get("hostname"), f.get("host"), f.get("name") if asset.type == "domain" else None]
    if asset.type == "url" and f.get("url"):
        out.append(urlsplit(str(f["url"])).hostname)
    return [str(x).lower() for x in out if x]


def _matches(target: str, entry: str) -> bool:
    entry = entry.strip().lower()
    if not entry:
        return False
    ip = _ip(target)
    if ip:
        try:
            return ipaddress.ip_address(ip) in ipaddress.ip_network(entry, strict=False)
        except ValueError:
            return False
    host = entry.lstrip("*.")
    return target == host or target.endswith("." + host)


def in_scope(project: m.Project, asset: m.Asset) -> bool:
    """Inside the project's declared scope (and not excluded). No scope: nothing is."""
    scope = scope_of(project)
    targets = _targets(asset)
    if not targets or not scope["include"]:
        return False
    if any(_matches(t, e) for t in targets for e in scope["exclude"]):
        return False
    return any(_matches(t, e) for t in targets for e in scope["include"])


# ---------- triggers ----------

TEMPLATE = re.compile(r"\{\{\s*asset\.(\w+)\s*\}\}")


def render(template: Any, asset: m.Asset) -> Any:
    if not isinstance(template, str):
        return template
    whole = TEMPLATE.fullmatch(template.strip())
    if whole:
        name = whole.group(1)
        v = (asset.fields or {}).get(name) if name not in ("key", "label", "type") else getattr(asset, name)
        return v if v is not None else ""
    return TEMPLATE.sub(lambda mt: value_of(asset, mt.group(1)), template)


async def fire_triggers(db: AsyncSession, batch: Batch) -> None:
    triggers = (
        await db.execute(select(m.AssetTrigger).where(m.AssetTrigger.project_id == batch.project_id, m.AssetTrigger.enabled.is_(True)))
    ).scalars().all()
    if not triggers:
        return
    project = await db.get(m.Project, batch.project_id)
    started = False
    for trig in triggers:
        for change in batch.changes.values():
            asset = change.asset
            if asset.type != trig.asset_type or (trig.on_event not in ("any", change.event)):
                continue
            if not conditions_met(trig.conditions, asset.fields or {}):
                continue
            if trig.once_per_asset:
                done = (
                    await db.execute(
                        select(m.AssetTriggerFire.id).where(
                            m.AssetTriggerFire.trigger_id == trig.id, m.AssetTriggerFire.asset_id == asset.id,
                            m.AssetTriggerFire.status.in_(["started", "waiting", "skipped"]),
                        )
                    )
                ).first()
                if done:
                    continue
            hour = (
                await db.execute(
                    select(func.count()).select_from(m.AssetTriggerFire).where(
                        m.AssetTriggerFire.trigger_id == trig.id, m.AssetTriggerFire.status == "started",
                        m.AssetTriggerFire.created_at >= utcnow() - timedelta(hours=1),
                    )
                )
            ).scalar_one()
            if hour >= trig.max_per_hour:
                db.add(m.AssetTriggerFire(trigger_id=trig.id, asset_id=asset.id, status="limited",
                                          reason=f"over {trig.max_per_hour} runs in the last hour"))
                continue
            fire = m.AssetTriggerFire(trigger_id=trig.id, asset_id=asset.id, status="waiting")
            db.add(fire)
            await db.flush()
            if project is not None and in_scope(project, asset):
                started = await run_fire(db, fire, trig, asset) or started
            else:
                fire.reason = "outside the project's scope" if scope_of(project)["include"] else "no scope is set for this project"
                await notifications.notify(
                    db, kind="asset_trigger.approval", severity="warning",
                    title=f"“{trig.name}” wants to run on {asset.label}",
                    body=f"It's {fire.reason}; approve it to run it.",
                    link=f"/projects/{batch.project_id}/assets?tab=triggers", project_id=batch.project_id,
                    subject=f"trigger-fire:{fire.id}", webhooks=False,
                )
                await alerts.approval(db, batch.project_id, f"“{trig.name}” wants to run on {asset.label}",
                                      f"It's {fire.reason}; approve it to run it.", f"/projects/{batch.project_id}/assets?tab=triggers")
    await db.commit()
    bus.publish("asset_triggers.changed", batch.project_id, {"project_id": str(batch.project_id)})
    if started:
        from .orchestrator.dispatcher import dispatcher

        dispatcher.wake()


async def run_fire(db: AsyncSession, fire: m.AssetTriggerFire, trig: m.AssetTrigger, asset: m.Asset, user: m.User | None = None) -> bool:
    """Start what a trigger runs for an asset: a command card (queued) or a playbook run."""
    from .command_library import apply_library, load_spec
    from .command_render import ArgumentError
    from .playbook_library import load_playbook_spec
    from .services import (
        column_with_role,
        enqueue_job,
        next_position,
        publish_task,
        start_playbook_run,
    )

    args = {k: render(v, asset) for k, v in (trig.args or {}).items()}
    try:
        if trig.action == "playbook":
            spec = await load_playbook_spec(db, trig.target)
            if spec is None:
                raise AssetError(f"no playbook {trig.target}")
            for inp in spec.inputs:
                if args.get(inp.name) in (None, "") and inp.default is not None:
                    args[inp.name] = inp.default
            run, _ = await start_playbook_run(db, spec, asset.project_id, args, user)
            fire.run_id = run.id
        else:
            spec = await load_spec(db, trig.target)
            if spec is None:
                raise AssetError(f"no command {trig.target}")
            column = await column_with_role(db, asset.project_id, m.ColumnRole.active) or await column_with_role(db, asset.project_id, m.ColumnRole.todo)
            if column is None:
                raise AssetError("the project has no In Progress column")
            task = m.Task(
                project_id=asset.project_id, column_id=column.id, position=await next_position(db, column.id),
                title=f"{trig.name}: {asset.label}"[:300], kind=m.TaskKind.command, tags=["trigger"],
                description=f"Started by the trigger “{trig.name}” for the {asset.type} {asset.label}.",
                created_by=user.id if user else trig.created_by,
            )
            await apply_library(db, task, spec, args)
            db.add(task)
            await db.flush()
            await enqueue_job(db, task, user, reason=f"trigger {trig.name}")
            fire.task_id = task.id
            await publish_task(db, task, "task.created")
        fire.status = "started"
        fire.reason = ""
        fire.decided_by = user.id if user else None
        return True
    except (AssetError, ArgumentError, ValueError) as exc:
        fire.status = "failed"
        fire.reason = str(exc)[:500]
        return False
