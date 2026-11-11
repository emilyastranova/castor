"""Assets (Phase 3f): what a project has found, as things Castor understands.

A host, service, URL, domain, user, credential or share is one record per project, keyed by
an identity (`key`) so every tool that reports on the same thing adds to it. Each report is
an observation (who said what, when); `fields` holds the merged current values, newest
first. Links connect them (a service *on* a host, a domain that *resolves to* a host).

Parsed outputs become assets automatically (`ingest_outputs`, called after a job's outputs
are parsed): nmap hosts and services, NetExec SMB hosts, logins and shares, ffuf and httpx
URLs (with their technologies), and lines a command says are domains, hosts, vulnerabilities…
(an output's `asset:` hint). Triggers then run a command or playbook for new or changed assets:
automatically when the asset is inside the project's scope (`projects.settings.scope`),
otherwise after someone approves it; once per asset and at most N per hour.

Mentions become *suggested* assets (assets_text.py): addresses, names, URLs, CVEs… in job output
no parser read, comments, posts, card descriptions, pinned notes, and pictures the AI read. They
stay apart (not in lists, arguments or triggers) until someone confirms them, or a tool or person
reports the same thing. Every observation carries a confidence; the asset keeps the best.

A host seen by its address and by its name is one asset: `observe` folds them together (and the
services on the name follow the address); people can also merge two assets by hand.

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

from sqlalchemy import func, or_, select, update
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
            F("tls", "TLS"), F("cert_subject", "Certificate subject"), F("cert_expires", "Certificate expires"),
        ), ("host", "port")),
        TypeDef("url", "URL", "URLs", "link", (
            F("url", "URL"), F("status", "Status", "number"), F("title", "Title"), F("length", "Length", "number"),
            F("content_type", "Content type"), F("webserver", "Web server"), F("tech", "Technologies", "list"),
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
        TypeDef("vulnerability", "Vulnerability", "Vulnerabilities", "bug_report", (
            F("id", "ID"), F("target", "Target"), F("title", "Title"), F("severity", "Severity"),
            F("cves", "CVEs", "list"), F("tool", "Found by"), F("detail", "Detail"),
        ), ("id",)),
    ]
}
ALIASES = {"ip": "host", "hosts": "host", "services": "service", "urls": "url", "domains": "domain",
           "users": "user", "credentials": "credential", "creds": "credential", "shares": "share",
           "vulnerabilities": "vulnerability", "vuln": "vulnerability", "vulns": "vulnerability"}
SEVERITIES = ("info", "low", "medium", "high", "critical")
# How sure each kind of source is (0–100). Mentions only suggest; the rest confirm.
CONFIDENCE = {"manual": 100, "import": 90, "parser": 85, "ai": 45, "comment": 50, "post": 50, "card": 50, "note": 50, "asset": 50,
              "output": 40}
SUGGESTING = {"ai", "comment", "post", "card", "note", "asset", "output"}
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
    if t.name == "vulnerability":
        if out.get("id"):
            out["id"] = str(out["id"]).strip()[:200]
            if re.fullmatch(r"(?i)cve-\d{4}-\d{4,}", out["id"]):
                out["id"] = out["id"].upper()
        if out.get("severity"):
            sev = str(out["severity"]).lower()
            out["severity"] = {"informational": "info", "moderate": "medium", "important": "high"}.get(sev, sev)
        if out.get("target"):
            out["target"] = str(out["target"]).strip().lower()
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
    elif t.name == "vulnerability":
        if not f.get("id"):
            raise AssetError("a vulnerability needs an id (a CVE, a template or a check)")
        where = f.get("target") or ""
        key = f"{f['id'].lower()}@{where}" if where else f["id"].lower()
        label = f"{f['id']} on {where}" if where else f["id"]
        if f.get("title") and f["title"].lower() != f["id"].lower():
            label = f"{label} · {f['title']}"
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
    suggested: dict[uuid.UUID, m.Asset] = field(default_factory=dict)
    read: bool = False  # some output was one Castor reads as assets (parsed or hinted)

    def note(self, asset: m.Asset, created: bool, changed: list[str]) -> None:
        prev = self.changes.get(asset.id)
        if prev is None:
            if created or changed:
                self.changes[asset.id] = Change(asset, "created" if created else "updated", changed)
        else:
            prev.fields = sorted({*prev.fields, *changed})

    def created(self) -> list[m.Asset]:
        return [c.asset for c in self.changes.values() if c.event == "created"]

    def suggest(self, asset: m.Asset, created: bool) -> None:
        """A mention: only suggested, so not to triggers; counted for the feed when new."""
        if created:
            self.suggested[asset.id] = asset


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
    confidence: int | None = None,
    ref: dict[str, Any] | None = None,
) -> m.Asset:
    """Record what a source says about an asset: find it by identity (or create it), merge the
    fields (newest wins), keep the observation. A mention (SUGGESTING sources) only suggests a new
    asset, and doesn't bring back a dismissed one; anything else confirms it. Flushes; the caller
    commits."""
    t = type_of(type_name)
    conf = confidence if confidence is not None else CONFIDENCE.get(source, 80)
    suggesting = source in SUGGESTING
    fields = await _resolve_names(db, project_id, t.name, fields)
    key, label, clean = identity(t.name, fields, secret)
    asset = await _find(db, project_id, t.name, key, clean)
    created = asset is None
    if asset is None:
        asset = m.Asset(project_id=project_id, type=t.name, key=key, label=label, fields={},
                        status="suggested" if suggesting else "confirmed", confidence=conf)
        db.add(asset)
    elif asset.status == "dismissed" and suggesting:
        return asset  # someone said it isn't one; a mention doesn't change that
    was = asset.status
    if not suggesting and asset.status != "confirmed":
        asset.status = "confirmed"
    merged = dict(asset.fields or {})
    changed = [k for k, v in clean.items() if merged.get(k) != v]
    merged.update(clean)
    asset.fields = merged
    if not created and t.name == "host":
        # Found by its name, now with an address: key it by the address (unless that's taken:
        # _fold_host then merges the two).
        new_key, label, _ = identity("host", merged)
        taken = (await db.execute(select(m.Asset.id).where(m.Asset.project_id == project_id, m.Asset.type == "host",
                                                           m.Asset.key == new_key, m.Asset.id != asset.id))).first()
        if new_key != asset.key and not taken:
            asset.key = new_key
    asset.label = label if len(label) >= len(asset.label or "") else asset.label  # keep the richest label
    asset.last_seen = utcnow()
    asset.confidence = max(asset.confidence or 0, conf)
    if secret:
        asset.secret = vault.encrypt(secret.encode())
    await db.flush()
    db.add(m.AssetObservation(asset_id=asset.id, source=source, parser=parser, job_id=job_id, task_id=task_id,
                              actor_id=actor_id, fields=clean, confidence=conf, ref=ref))
    if t.name == "host" and clean.get("ip") and clean.get("hostname"):
        asset = await _fold_host(db, asset)
    if asset.status == "suggested":
        batch.suggest(asset, created)
    else:
        # Confirmed just now (it was only suggested): to triggers and the feed it's new.
        batch.note(asset, created or was != "confirmed", changed)
    return asset


async def _find(db: AsyncSession, project_id: uuid.UUID, type_name: str, key: str, clean: dict[str, Any]) -> m.Asset | None:
    asset = (
        await db.execute(select(m.Asset).where(m.Asset.project_id == project_id, m.Asset.type == type_name, m.Asset.key == key))
    ).scalar_one_or_none()
    if asset is None and type_name == "host":
        # Known by its address under another name, or by its name under its address.
        for value in (clean.get("ip"), clean.get("hostname")):
            if value and (found := await _find_host(db, project_id, value)) is not None:
                return found
    return asset


async def _resolve_names(db: AsyncSession, project_id: uuid.UUID, type_name: str, fields: dict[str, Any]) -> dict[str, Any]:
    """A service or share on a host named by hostname goes on the address it's known by."""
    if type_name in ("service", "share") and fields.get("host") and not _ip(fields["host"]):
        host = await _find_host(db, project_id, str(fields["host"]))
        if host is not None and (host.fields or {}).get("ip"):
            return {**fields, "host": host.fields["ip"]}
    return fields


async def _fold_host(db: AsyncSession, asset: m.Asset) -> m.Asset:
    """Now that this host has an address and a name: another host recorded under the other one
    is the same machine."""
    f = asset.fields or {}
    others = (await db.execute(select(m.Asset).where(m.Asset.project_id == asset.project_id, m.Asset.type == "host",
                                                     m.Asset.id != asset.id))).scalars().all()
    for other in others:
        of = other.fields or {}
        same_name = of.get("hostname") == f.get("hostname") and not (of.get("ip") and of.get("ip") != f.get("ip"))
        if other.key == f.get("hostname") or (same_name and not of.get("ip")) or other.key == f.get("ip"):
            await merge(db, other, into=asset)
    return asset


async def merge(db: AsyncSession, drop: m.Asset, *, into: m.Asset) -> m.Asset:
    """Fold one asset into another of the same type: observations, links and trigger history move
    over, fields merge (the more recently seen wins), and what was on a merged host follows it.
    Flushes."""
    if drop.id == into.id:
        return into
    if drop.type != into.type:
        raise AssetError("only assets of the same type can be merged")
    newer, older = (drop, into) if (drop.last_seen or utcnow()) > (into.last_seen or utcnow()) else (into, drop)
    into.fields = {**(older.fields or {}), **(newer.fields or {})}
    into.first_seen = min(x for x in (into.first_seen, drop.first_seen) if x)
    into.last_seen = max(x for x in (into.last_seen, drop.last_seen) if x)
    into.confidence = max(into.confidence or 0, drop.confidence or 0)
    if "confirmed" in (into.status, drop.status):
        into.status = "confirmed"
    into.secret = into.secret or drop.secret
    await db.execute(update(m.AssetObservation).where(m.AssetObservation.asset_id == drop.id).values(asset_id=into.id))
    await db.execute(update(m.AssetTriggerFire).where(m.AssetTriggerFire.asset_id == drop.id).values(asset_id=into.id))
    for row in (await db.execute(select(m.AssetLink).where(or_(m.AssetLink.from_id == drop.id, m.AssetLink.to_id == drop.id)))).scalars().all():
        src = into.id if row.from_id == drop.id else row.from_id
        dst = into.id if row.to_id == drop.id else row.to_id
        dup = (await db.execute(select(m.AssetLink.id).where(m.AssetLink.from_id == src, m.AssetLink.to_id == dst,
                                                             m.AssetLink.kind == row.kind, m.AssetLink.id != row.id))).first()
        if src == dst or dup:
            await db.delete(row)
        else:
            row.from_id, row.to_id = src, dst
    old_names = {drop.key, (drop.fields or {}).get("hostname"), (drop.fields or {}).get("ip")} - {None, ""}
    await db.delete(drop)
    await db.flush()
    key, label, _ = identity(into.type, into.fields or {})
    if key != into.key and (await db.execute(select(m.Asset.id).where(m.Asset.project_id == into.project_id, m.Asset.type == into.type,
                                                                        m.Asset.key == key))).first() is None:
        into.key = key
    into.label = label
    if into.type == "host" and (into.fields or {}).get("ip"):
        await _rehome(db, into, old_names)
    await db.flush()
    return into


async def _rehome(db: AsyncSession, host: m.Asset, names: set[str]) -> None:
    """Services and shares recorded on a merged host's other name move onto its address."""
    ip = host.fields["ip"]
    for kind in ("service", "share"):
        rows = (await db.execute(select(m.Asset).where(m.Asset.project_id == host.project_id, m.Asset.type == kind))).scalars().all()
        for a in rows:
            if (a.fields or {}).get("host") not in names or (a.fields or {}).get("host") == ip:
                continue
            fields = {**a.fields, "host": ip}
            key, label, _ = identity(kind, fields)
            twin = (await db.execute(select(m.Asset).where(m.Asset.project_id == host.project_id, m.Asset.type == kind,
                                                           m.Asset.key == key))).scalar_one_or_none()
            if twin is not None and twin.id != a.id:
                a.fields = fields
                await merge(db, a, into=twin)
            else:
                a.fields, a.key, a.label = fields, key, label


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
                url = await observe(db, project_id, "url", {k: row.get(k) for k in ("url", "status", "length", "title", "content_type",
                                                                                       "webserver", "tech")}, **obs)
                hostname = urlsplit(str(row.get("url") or "")).hostname
                host = await _find_host(db, project_id, hostname) if hostname else None
                if host is not None:
                    await link(db, project_id, url, host, "on")
        except AssetError:
            continue  # a row without an identity (e.g. no address) isn't an asset


# ---------- lines a command says what they are (an output's `asset:` hint) ----------

NUCLEI_LINE = re.compile(r"^\[(?P<id>[^\]]+)\](?:\s*\[(?P<proto>[^\]]+)\])?\s*\[(?P<sev>info|low|medium|high|critical|unknown)\]\s+(?P<target>\S+)(?P<rest>.*)$", re.IGNORECASE)
NETEXEC_LOGIN = re.compile(r"^(?P<proto>[A-Z]+)\s+(?P<host>\S+)\s+(?P<port>\d+)\s+\S+\s+\[\+\]\s+(?:(?P<domain>[^\\\s]+)\\)?(?P<user>[^:\s]+)(?::(?P<secret>\S+))?(?P<rest>.*)$")
CVE = re.compile(r"\b(CVE-\d{4}-\d{4,})\b", re.IGNORECASE)
ANSI = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def default_target(task: m.Task | None) -> str:
    """What a card runs against, for results that don't say: its target/host/url/image argument."""
    args = (task.command_args or {}) if task is not None else {}
    for name in ("target", "targets", "host", "hosts", "url", "urls", "domain", "ip", "image"):
        value = args.get(name)
        if isinstance(value, str) and value.strip():
            return _target_of(re.split(r"[\s,]+", value.strip())[0])
    return ""


async def _map_hint(db: AsyncSession, hint: str, values: Any, project_id: uuid.UUID, batch: Batch, *, target: str = "",
                    tool: str = "", **src: Any) -> None:
    if not isinstance(values, list):
        return
    obs = {"batch": batch, "parser": f"lines:{hint}", **src}
    for raw in values:
        line = ANSI.sub("", str(raw)).strip()
        if not line:
            continue
        try:
            if hint == "host":
                await observe(db, project_id, "host", {"ip": line} if _ip(line) else {"hostname": line}, **obs)
            elif hint == "domain":
                await observe(db, project_id, "domain", {"name": line.split()[0]}, **obs)
            elif hint == "url":
                await observe(db, project_id, "url", {"url": line.split()[0]}, **obs)
            elif hint == "service":
                mt = re.match(r"^\[?([^\]\s]+?)\]?[:\s](\d{1,5})(?:/(tcp|udp))?", line)
                if mt:
                    svc = await observe(db, project_id, "service", {"host": mt[1], "port": mt[2], "proto": mt[3] or "tcp"}, **obs)
                    host = await observe(db, project_id, "host", {"ip": mt[1]} if _ip(mt[1]) else {"hostname": mt[1]}, **obs)
                    await link(db, project_id, svc, host, "on")
            elif hint == "share":
                parts = line.split()
                if len(parts) >= 2:
                    share = await observe(db, project_id, "share", {"host": parts[0], "name": parts[1], "access": " ".join(parts[2:3])}, **obs)
                    host = await observe(db, project_id, "host", {"ip": parts[0]} if _ip(parts[0]) else {"hostname": parts[0]}, **obs)
                    await link(db, project_id, share, host, "on")
            elif hint == "login":
                mt = NETEXEC_LOGIN.match(line)
                if mt:
                    secret = mt["secret"] or None
                    if secret and set(secret) <= {"*"}:
                        secret = None  # masked: Castor's own variable
                    kind = "hash" if secret and re.fullmatch(r"[0-9a-fA-F]{32}(:[0-9a-fA-F]{32})?", secret) else "password"
                    cred = await observe(db, project_id, "credential", {"username": mt["user"], "domain": mt["domain"], "kind": kind,
                                                                        "admin": "Pwn3d" in (mt["rest"] or "") or None}, secret=secret, **obs)
                    host = await observe(db, project_id, "host", {"ip": mt["host"]} if _ip(mt["host"]) else {"hostname": mt["host"]}, **obs)
                    await link(db, project_id, cred, host, "valid_on")
            elif hint == "vulnerability":
                mt = NUCLEI_LINE.match(line)
                if mt:
                    rest = mt["rest"].strip()
                    await observe(db, project_id, "vulnerability", {
                        "id": mt["id"].split(":")[0], "target": _target_of(mt["target"]), "severity": mt["sev"].lower(),
                        "title": mt["id"], "cves": [c.upper() for c in CVE.findall(line)], "tool": tool or "nuclei",
                        "detail": rest[:500]}, **obs)
                else:
                    cves = [c.upper() for c in CVE.findall(line)]
                    ident = cves[0] if cves else line[:120]
                    await observe(db, project_id, "vulnerability", {"id": ident, "target": target, "cves": cves, "tool": tool,
                                                                     "detail": line[:500] if line != ident else None}, **obs)
        except AssetError:
            continue


def _target_of(value: str) -> str:
    """A URL's host:port, or the value itself: what a vulnerability was found on."""
    parts = urlsplit(value) if "://" in value else None
    return (parts.netloc or value) if parts else value


MAPPED_PARSERS = {"nmap.hosts", "nmap.services", "netexec.smb", "ffuf.results", "httpx.results"}
# A parser from the library (written by a person or the AI) produces the same shapes as the
# built-ins, so its results become assets by their type.
MAPPED_TYPES = {"list<host>": "nmap.hosts", "list<service>": "nmap.services", "list<smb_host>": "netexec.smb", "list<url>": "httpx.results"}


def mapping_for(parser: str, type_: str) -> str | None:
    """Which built-in mapping a job output's results go through, if any."""
    if parser in MAPPED_PARSERS:
        return parser
    from .parsers import PARSERS

    return None if parser in PARSERS else MAPPED_TYPES.get(type_)


async def ingest_outputs(db: AsyncSession, job: m.Job, task: m.Task, outputs: list[m.JobOutput]) -> Batch:
    """Turn a job's parsed outputs into assets in its project, then tell browsers, write one
    feed entry and fire triggers. Commits."""
    batch = Batch(task.project_id)
    batch.read = await _ingest(db, job, task, outputs, batch)
    if batch.changes or batch.suggested:
        await settle(db, batch, task_id=task.id)
    return batch


async def _ingest(db: AsyncSession, job: m.Job, task: m.Task, outputs: list[m.JobOutput], batch: Batch) -> bool:
    """Record what a job's outputs found; whether any output was one Castor reads as assets."""
    hints = {o.get("name"): o.get("asset") for o in (job.output_specs or []) if isinstance(o, dict) and o.get("asset")}
    target, tool = default_target(task), task.command_ref or ""
    read = False
    for out in outputs:
        if out.error:
            continue
        kind = mapping_for(out.parser, out.type)
        if kind is not None:
            read = True
            await _map(db, kind, out.value, task.project_id, batch, job_id=job.id, task_id=task.id)
        elif hints.get(out.name):
            read = True
            await _map_hint(db, hints[out.name], out.value, task.project_id, batch, target=target, tool=tool, job_id=job.id, task_id=task.id)
    return read


async def collect_past(db: AsyncSession, project_id: uuid.UUID, actor: m.User | None = None, *, mentions: bool = True) -> Batch:
    """Record assets from every result the project's jobs already produced, oldest first
    (results parsed before assets existed), and suggest the ones mentioned in its comments,
    posts, cards and notes. Triggers don't fire for these. Commits."""
    batch = Batch(project_id)
    rows = (
        await db.execute(
            select(m.JobOutput, m.Job, m.Task)
            .join(m.Job, m.Job.id == m.JobOutput.job_id)
            .join(m.Task, m.Task.id == m.Job.task_id)
            .where(m.Task.project_id == project_id, m.JobOutput.error.is_(None))
            .order_by(m.Job.finished_at, m.JobOutput.created_at)
        )
    ).all()
    by_job: dict[uuid.UUID, tuple[m.Job, m.Task, list[m.JobOutput]]] = {}
    for out, job, task in rows:
        by_job.setdefault(job.id, (job, task, []))[2].append(out)
    for job, task, outs in by_job.values():
        await _ingest(db, job, task, outs, batch)
    if mentions:
        from .assets_text import collect_mentions

        await collect_mentions(db, project_id, batch)
    if batch.changes or batch.suggested:
        from .services import actor_of, record_activity

        created: dict[str, int] = {}
        for a in batch.created():
            created[a.type] = created.get(a.type, 0) + 1
        suggested = _counts(batch.suggested.values())
        said = " and ".join(x for x in (f"found {describe(created)}" if created else "",
                                        f"spotted {describe(suggested)} to confirm" if suggested else "") if x)
        if said:
            await record_activity(db, type="assets.found", message=f"{said[0].upper()}{said[1:]} in past results and notes",
                                  project_id=project_id, payload={"counts": created, "suggested": suggested}, **actor_of(actor))
    await db.commit()
    bus.publish("assets.changed", project_id, {"project_id": str(project_id)})
    return batch


def _counts(assets: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for a in assets:
        out[a.type] = out.get(a.type, 0) + 1
    return out


def describe(counts: dict[str, int]) -> str:
    parts = [f"{n} {TYPES[t].plural.lower() if n != 1 else TYPES[t].label.lower()}" for t, n in counts.items() if n]
    return ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1] if parts else ""


async def settle(db: AsyncSession, batch: Batch, *, task_id: uuid.UUID | None = None, actor: m.User | None = None,
                 where: str = "") -> None:
    """After recording: the feed entry ("Found 3 hosts and 5 services"), the event, triggers."""
    from .services import actor_of, record_activity

    created = _counts(batch.created())
    suggested = _counts(batch.suggested.values())
    if created:
        await record_activity(
            db, type="assets.found", message=f"Found {describe(created)}", project_id=batch.project_id, task_id=task_id,
            payload={"counts": created}, **actor_of(actor),
        )
    if suggested:
        await record_activity(
            db, type="assets.suggested", message=f"Spotted {describe(suggested)} to confirm{f' in {where}' if where else ''}",
            project_id=batch.project_id, task_id=task_id, payload={"counts": suggested}, **actor_of(actor),
        )
    await db.commit()
    bus.publish("assets.changed", batch.project_id, {"project_id": str(batch.project_id), "created": created, "suggested": suggested,
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


async def query(db: AsyncSession, project_id: uuid.UUID, type_name: str | None, where: str = "",
                status: str = "confirmed") -> list[m.Asset]:
    """The project's assets of a type (or all) matching `where`; confirmed ones unless `status`
    says suggested or dismissed. Only confirmed assets reach arguments and triggers."""
    conds, words = parse_where(where)
    stmt = select(m.Asset).where(m.Asset.project_id == project_id, m.Asset.status == status)
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
    return {"host": "ip", "service": "key", "url": "url", "domain": "name", "user": "username", "credential": "username", "share": "key",
            "vulnerability": "id"}.get(type_name, "key")


# ---------- scope ----------


def scope_of(project: m.Project) -> dict[str, Any]:
    """The project's scope: named groups of what's in it (Internal, External, OT…), one list of
    exclusions that wins over every group, and `include` (every group's entries, for the checks
    that don't care which). A scope from before groups is one group called "Scope"."""
    s = (project.settings or {}).get("scope") or {}
    groups = s.get("groups")
    if groups is None:
        groups = [{"name": "Scope", "include": list(s.get("include") or [])}] if s.get("include") else []
    groups = [{"name": str(g.get("name") or "Scope"), "include": [str(x) for x in g.get("include") or []]}
              for g in groups if isinstance(g, dict)]
    return {"groups": groups, "exclude": list(s.get("exclude") or []),
            "include": [e for g in groups for e in g["include"]]}


def scope_groups(project: m.Project, asset: m.Asset, scope: dict[str, Any] | None = None) -> list[str]:
    """The scope groups an asset falls in (none when an exclusion covers it)."""
    scope = scope or scope_of(project)
    targets = _targets(asset)
    if not targets or any(_matches(t, e) for t in targets for e in scope["exclude"]):
        return []
    return [g["name"] for g in scope["groups"] if any(_matches(t, e) for t in targets for e in g["include"])]


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
