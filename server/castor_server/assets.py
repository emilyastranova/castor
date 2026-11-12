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
            F("mac", "MAC"), F("netbios_name", "NetBIOS name"), F("site", "AD site"),
            # What it is to the engagement: dc, server, workstation, jumpbox, pivot, compromised…
            F("roles", "Roles", "list"), F("gateway", "Gateway"), F("dns_servers", "DNS servers", "list"),
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
            F("name", "Name"), F("ips", "Addresses", "list"), F("netbios_name", "NetBIOS name"),
            F("forest_root", "Forest root"), F("functional_level", "Functional level"), F("sid", "Domain SID"),
        ), ("name",)),
        TypeDef("user", "User", "Users", "person", (
            F("username", "Username"), F("domain", "Domain"), F("enabled", "Enabled", "bool"), F("groups", "Groups", "list"),
            F("sid", "SID"), F("admin_count", "AdminCount", "bool"), F("kerberoastable", "Kerberoastable (has an SPN)", "bool"),
            F("spns", "SPNs", "list"), F("asreproastable", "AS-REP roastable", "bool"), F("privileges", "Privileges", "list"),
            F("ad_description", "AD description"),
        ), ("username",)),
        TypeDef("credential", "Credential", "Credentials", "key", (
            # kind: password, ntlm_hash, kerberos_ticket, ssh_key, certificate, token…
            F("username", "Username"), F("domain", "Domain"), F("kind", "Kind"), F("fingerprint", "Fingerprint"),
            F("valid_until", "Valid until"), F("elevated", "Elevated", "bool"),
        ), ("username",)),
        TypeDef("share", "Share", "Shares", "folder_shared", (
            F("host", "Host"), F("name", "Share"), F("access", "Access"), F("remark", "Remark"),
        ), ("host", "name")),
        TypeDef("vulnerability", "Vulnerability", "Vulnerabilities", "bug_report", (
            F("id", "ID"), F("target", "Target"), F("title", "Title"), F("severity", "Severity"),
            F("cves", "CVEs", "list"), F("tool", "Found by"), F("detail", "Detail"),
        ), ("id",)),
        # A file found or taken somewhere: on a share (\\host\share\path), a host or a web server.
        TypeDef("file", "File", "Files", "description", (
            F("path", "Path"), F("name", "Name"), F("host", "Host"), F("share", "Share"), F("size", "Size", "number"),
            F("sha256", "SHA-256"), F("attachment", "Downloaded copy"),
        ), ("path",)),
    ]
}
ALIASES = {"ip": "host", "hosts": "host", "services": "service", "urls": "url", "domains": "domain",
           "users": "user", "credentials": "credential", "creds": "credential", "shares": "share",
           "vulnerabilities": "vulnerability", "vuln": "vulnerability", "vulns": "vulnerability", "files": "file"}
SEVERITIES = ("info", "low", "medium", "high", "critical")
# How sure each kind of source is (0–100). Mentions only suggest; the rest confirm.
CONFIDENCE = {"manual": 100, "import": 90, "parser": 85, "plugin": 85, "ai": 45, "comment": 50, "post": 50, "card": 50, "note": 50, "asset": 50,
              "output": 40}
SUGGESTING = {"ai", "comment", "post", "card", "note", "asset", "output"}


@dataclass(frozen=True)
class LinkKind:
    """A kind of relationship, read from the "from" asset to the "to" one ("works on") and back
    ("accepts"). `pairs` are the (from type, to type) it connects; none means any two assets."""

    name: str
    forward: str
    reverse: str
    pairs: tuple[tuple[str, str], ...]
    description: str
    # Details people (and tools) usually record on it, as (field, label): offered when adding one by hand.
    details: tuple[tuple[str, str], ...] = ()

    @property
    def reverse_name(self) -> str:
        return self.reverse.lower().replace(" ", "_")

    def fits(self, src: str, dst: str) -> bool:
        return not self.pairs or (src, dst) in self.pairs


L = LinkKind
LINK_KINDS: dict[str, LinkKind] = {
    k.name: k
    for k in [
        L("on", "on", "has", (("service", "host"), ("share", "host"), ("url", "host"), ("file", "share"), ("file", "host")),
          "Where something is: a service or share on a host, a file on a share."),
        L("resolves_to", "resolves to", "resolved from", (("domain", "host"),), "A name that resolves to a host."),
        L("valid_on", "works on", "accepts", (("credential", "host"), ("credential", "service"), ("credential", "url"),
                                              ("credential", "share")),
          "A credential that logged in somewhere.", (("protocol", "Protocol"), ("port", "Port"))),
        L("admin_on", "admin on", "has admin", (("user", "host"), ("credential", "host"), ("user", "service"),
                                                ("credential", "service")),
          "Administrator rights on a host (NetExec's Pwn3d!).", (("protocol", "Protocol"),)),
        # Active Directory: membership, infrastructure, trusts, sessions.
        L("joined_to", "joined to", "has computer", (("host", "domain"),),
          "A computer that's a member of a domain.", (("role", "Role (workstation, server, jumpbox)"),)),
        L("dc_of", "domain controller of", "has domain controller", (("host", "domain"),),
          "A domain controller of a domain.", (("site", "AD site"), ("is_pdc", "PDC emulator"))),
        L("in_domain", "account in", "has account", (("user", "domain"), ("credential", "domain")),
          "A domain account.", (("role", "Role (standard_user, domain_admin, service_account)"),)),
        L("trusts", "trusts", "trusted by", (("domain", "domain"),),
          "A domain trust: the first domain trusts the second (its users can sign in to the first).",
          (("type", "Type (parent_child, tree_root, external, forest)"), ("direction", "Direction (outbound, inbound, bidirectional)"),
           ("transitive", "Transitive"))),
        L("session_on", "has a session on", "has a session of", (("user", "host"), ("credential", "host")),
          "A user logged in on a host (interactive, RDP, network, service).",
          (("session_type", "Session type (interactive, rdp, network, service)"), ("logon_id", "Logon id"))),
        L("authenticates_to", "authenticates to", "authenticates", (("user", "host"), ("user", "service"), ("credential", "service")),
          "Where an account signs in: a domain controller, LDAP, Kerberos.", (("protocol", "Protocol (Kerberos, NTLM, LDAP)"),)),
        # Network: how a host reaches others.
        L("uses_dns", "uses DNS server", "DNS server for", (("host", "host"),),
          "A host's configured DNS resolver.", (("order", "Order (primary, secondary)"),)),
        L("routes_via", "routes through", "gateway for", (("host", "host"),),
          "A host's gateway or a pivot it's reached through.", (("interface", "Interface"), ("network", "Network"))),
        L("belongs_to", "belongs to", "has credential", (("credential", "user"),), "A credential of a user."),
        L("downloaded_from", "downloaded from", "gave", (("file", "share"), ("file", "host"), ("file", "url"), ("file", "service")),
          "A file taken from a share, a host or a web server."),
        L("affects", "affects", "affected by", (("vulnerability", "host"), ("vulnerability", "service"), ("vulnerability", "url"),
                                                ("vulnerability", "domain")),
          "Where a vulnerability was found."),
        L("related", "related to", "related to", (), "Anything else people want to note."),
    ]
}
# The words each kind is filtered by (`where`): its name, and its reverse ("accepts", "has_admin").
LINK_WORDS: dict[str, tuple[str, bool]] = {
    **{k.name: (k.name, True) for k in LINK_KINDS.values()},
    **{k.reverse_name: (k.name, False) for k in LINK_KINDS.values() if k.reverse_name != k.name},
}


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
    if t.name == "file":
        _clean_file(out)
    return out


UNC = re.compile(r"^(?:smb:)?[\\/]{2}(?P<host>[^\\/\s]+)[\\/](?P<share>[^\\/]+)(?:[\\/](?P<path>.*))?$", re.IGNORECASE)


def _clean_file(out: dict[str, Any]) -> None:
    """A file on a share is \\\\host\\share\\path, whichever way it was written (//host/share/path,
    smb://…, or a path plus host and share); its name is the last part."""
    path = str(out.get("path") or "").strip()
    mt = UNC.match(path)
    if mt:
        out.setdefault("host", mt["host"])
        out.setdefault("share", mt["share"])
        path = (mt["path"] or "").replace("/", "\\")
        out["path"] = f"\\\\{mt['host']}\\{mt['share']}" + (f"\\{path}" if path else "")
    elif path and out.get("host") and out.get("share") and "://" not in path:
        out["path"] = f"\\\\{out['host']}\\{out['share']}\\" + path.replace("/", "\\").lstrip("\\")
    if out.get("host"):
        out["host"] = _ip(out["host"]) or str(out["host"]).lower()
    if out.get("path") and not out.get("name"):
        out["name"] = re.split(r"[\\/]", str(out["path"]).rstrip("\\/"))[-1][:300]


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
    elif t.name == "file":
        if not f.get("path"):
            raise AssetError("a file needs its path")
        label = f["path"]
        if f.get("host") and not label.startswith("\\\\"):  # a path on a host, not on a share
            label = f"{f['host']}:{label}"
        key = label.lower()
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
    # Relationships recorded for the first time: (link, from asset, to asset).
    links: list[tuple[m.AssetLink, m.Asset, m.Asset]] = field(default_factory=list)
    read: bool = False  # some output was one Castor reads as assets (parsed or hinted)

    def note(self, asset: m.Asset, created: bool, changed: list[str]) -> None:
        prev = self.changes.get(asset.id)
        if prev is None:
            if created or changed:
                self.changes[asset.id] = Change(asset, "created" if created else "updated", changed)
        else:
            prev.fields = sorted({*prev.fields, *changed})

    def new_links(self) -> list[tuple[m.AssetLink, m.Asset, m.Asset]]:
        """New relationships between confirmed assets (the ones the feed and triggers see)."""
        return [x for x in self.links if x[1].status == "confirmed" and x[2].status == "confirmed"]

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
    await db.execute(update(m.AssetTriggerFire).where(m.AssetTriggerFire.other_id == drop.id).values(other_id=into.id))
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


async def link(db: AsyncSession, project_id: uuid.UUID, src: m.Asset, dst: m.Asset, kind: str, *, batch: Batch | None = None,
               source: str = "parser", parser: str | None = None, job_id: uuid.UUID | None = None, task_id: uuid.UUID | None = None,
               actor_id: uuid.UUID | None = None, confidence: int | None = None, fields: dict[str, Any] | None = None,
               note: str | None = None) -> m.AssetLink | None:
    """Record that `src` relates to `dst` (LINK_KINDS): new ones go to the batch (feed, triggers);
    one seen again is seen later, as sure as its surest source, with any new details. Flushes."""
    if src.id == dst.id or kind not in LINK_KINDS:
        return None
    conf = confidence if confidence is not None else CONFIDENCE.get(source, 80)
    details = {k: v for k, v in (fields or {}).items() if v not in (None, "", [])}
    row = (await db.execute(select(m.AssetLink).where(m.AssetLink.from_id == src.id, m.AssetLink.to_id == dst.id,
                                                      m.AssetLink.kind == kind))).scalar_one_or_none()
    if row is None:
        row = m.AssetLink(project_id=project_id, from_id=src.id, to_id=dst.id, kind=kind, source=source, parser=parser, job_id=job_id,
                          task_id=task_id, actor_id=actor_id, confidence=conf, fields=details, note=note or "", last_seen=utcnow())
        db.add(row)
        await db.flush()
        if batch is not None:
            batch.links.append((row, src, dst))
        return row
    row.last_seen = utcnow()
    row.confidence = max(row.confidence or 0, conf)
    if details:
        row.fields = {**(row.fields or {}), **details}
    if note is not None:
        row.note = note
    await db.flush()
    return row


def _link_src(src: dict[str, Any]) -> dict[str, Any]:
    """The parts of an observation's source a relationship keeps."""
    return {k: src[k] for k in ("batch", "source", "parser", "job_id", "task_id", "actor_id") if k in src}


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
                    await link(db, project_id, domain, host, "resolves_to", **_link_src(obs))
            elif parser == "nmap.services":
                if row.get("state", "open") != "open":
                    continue
                host = await observe(db, project_id, "host", {"ip": row.get("host"), "status": "up"}, **obs)
                svc = await observe(db, project_id, "service", {k: row.get(k) for k in ("host", "port", "proto", "name", "product", "version", "state")}, **obs)
                await link(db, project_id, svc, host, "on", **_link_src(obs))
            elif parser == "netexec.smb":
                host = await observe(db, project_id, "host", {
                    "ip": row.get("host"), "hostname": row.get("name"), "domain": row.get("domain"), "os": row.get("os"),
                    "smb_signing": row.get("signing"), "smbv1": row.get("smbv1"), "status": "up",
                }, **obs)
                svc = await observe(db, project_id, "service", {"host": row.get("host"), "port": row.get("port") or 445, "proto": "tcp", "name": "microsoft-ds", "state": "open"}, **obs)
                await link(db, project_id, svc, host, "on", **_link_src(obs))
            elif parser in ("ffuf.results", "httpx.results"):
                url = await observe(db, project_id, "url", {k: row.get(k) for k in ("url", "status", "length", "title", "content_type",
                                                                                       "webserver", "tech")}, **obs)
                hostname = urlsplit(str(row.get("url") or "")).hostname
                host = await _find_host(db, project_id, hostname) if hostname else None
                if host is not None:
                    await link(db, project_id, url, host, "on", **_link_src(obs))
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
                    await link(db, project_id, svc, host, "on", **_link_src(obs))
            elif hint == "share":
                parts = line.split()
                if len(parts) >= 2:
                    share = await observe(db, project_id, "share", {"host": parts[0], "name": parts[1], "access": " ".join(parts[2:3])}, **obs)
                    host = await observe(db, project_id, "host", {"ip": parts[0]} if _ip(parts[0]) else {"hostname": parts[0]}, **obs)
                    await link(db, project_id, share, host, "on", **_link_src(obs))
            elif hint == "login":
                mt = NETEXEC_LOGIN.match(line)
                if mt:
                    secret = mt["secret"] or None
                    if secret and set(secret) <= {"*"}:
                        secret = None  # masked: Castor's own variable
                    kind = "hash" if secret and re.fullmatch(r"[0-9a-fA-F]{32}(:[0-9a-fA-F]{32})?", secret) else "password"
                    admin = "Pwn3d" in (mt["rest"] or "")
                    proto = mt["proto"].lower()
                    cred = await observe(db, project_id, "credential", {"username": mt["user"], "domain": mt["domain"], "kind": kind,
                                                                        "admin": admin or None}, secret=secret, **obs)
                    host = await observe(db, project_id, "host", {"ip": mt["host"]} if _ip(mt["host"]) else {"hostname": mt["host"]}, **obs)
                    svc = await observe(db, project_id, "service", {"host": mt["host"], "port": mt["port"], "proto": "tcp", "state": "open"},
                                        **obs)
                    user = await observe(db, project_id, "user", {"username": mt["user"], "domain": mt["domain"]}, **obs)
                    how = {"protocol": proto, "port": int(mt["port"]), "admin": admin or None}
                    await link(db, project_id, svc, host, "on", **_link_src(obs))
                    await link(db, project_id, cred, host, "valid_on", fields=how, **_link_src(obs))
                    await link(db, project_id, cred, svc, "valid_on", fields=how, **_link_src(obs))
                    await link(db, project_id, cred, user, "belongs_to", **_link_src(obs))
                    if admin:
                        await link(db, project_id, user, host, "admin_on", fields={"protocol": proto}, **_link_src(obs))
            elif hint in ("file", "download"):
                await _file_line(db, project_id, line, hint, obs, target=target)
            elif hint == "vulnerability":
                mt = NUCLEI_LINE.match(line)
                if mt:
                    rest = mt["rest"].strip()
                    vuln = await observe(db, project_id, "vulnerability", {
                        "id": mt["id"].split(":")[0], "target": _target_of(mt["target"]), "severity": mt["sev"].lower(),
                        "title": mt["id"], "cves": [c.upper() for c in CVE.findall(line)], "tool": tool or "nuclei",
                        "detail": rest[:500]}, **obs)
                    await _affects(db, project_id, vuln, mt["target"], obs)
                else:
                    cves = [c.upper() for c in CVE.findall(line)]
                    ident = cves[0] if cves else line[:120]
                    vuln = await observe(db, project_id, "vulnerability", {"id": ident, "target": target, "cves": cves, "tool": tool,
                                                                            "detail": line[:500] if line != ident else None}, **obs)
                    if target:
                        await _affects(db, project_id, vuln, target, obs)
        except AssetError:
            continue


async def _affects(db: AsyncSession, project_id: uuid.UUID, vuln: m.Asset, target: str, obs: dict[str, Any]) -> None:
    """A vulnerability affects what it was found on, when that's known: the URL, else the
    service (host:port), else the host."""
    target = target.strip()
    found: m.Asset | None = None
    if "://" in target:
        found = (await db.execute(select(m.Asset).where(m.Asset.project_id == project_id, m.Asset.type == "url",
                                                        m.Asset.key == target))).scalar_one_or_none()
        target = urlsplit(target).netloc or target
    if found is None:
        mt = re.match(r"^\[?([^\]\s]+?)\]?:(\d{1,5})$", target)
        name = mt[1] if mt else target
        if mt:
            host = _ip(mt[1]) or mt[1].lower()
            found = (await db.execute(select(m.Asset).where(m.Asset.project_id == project_id, m.Asset.type == "service",
                                                            m.Asset.key == f"{host}:{int(mt[2])}/tcp"))).scalar_one_or_none()
        if found is None and name:
            found = await _find_host(db, project_id, name)
    if found is not None:
        await link(db, project_id, vuln, found, "affects", **_link_src(obs))


async def _file_line(db: AsyncSession, project_id: uuid.UUID, line: str, hint: str, obs: dict[str, Any], *, target: str = "") -> None:
    """A file a command listed (`file`) or took (`download`): a share path (\\\\host\\share\\path,
    //host/share/path, NetExec's spider lines) or a URL. On a share, the share is on its host."""
    mt = re.search(r"(?:smb:)?(?:\\\\|//)[^\s\\/]+[\\/][^\s\\/]+(?:[\\/][^\[\r\n]*?)?(?=\s*\[|\s*$)", line)
    parts = UNC.match(mt[0].rstrip()) if mt else None
    if parts:
        host_name = parts["host"]
        host = await observe(db, project_id, "host", {"ip": host_name} if _ip(host_name) else {"hostname": host_name}, **obs)
        share = await observe(db, project_id, "share", {"host": host_name, "name": parts["share"]}, **obs)
        await link(db, project_id, share, host, "on", **_link_src(obs))
        if (parts["path"] or "").strip("\\/"):
            size = re.search(r"\bsize:\s*(\d+)", line)
            file = await observe(db, project_id, "file", {"path": mt[0].rstrip(), "size": size[1] if size else None}, **obs)
            await link(db, project_id, file, share, "downloaded_from" if hint == "download" else "on", **_link_src(obs))
            if hint == "download":
                await _keep_copy(db, file, obs.get("job_id"))
        return
    word = line.split()[0]
    if "://" in word:
        file = await observe(db, project_id, "file", {"path": word, "host": urlsplit(word).hostname}, **obs)
        url = (await db.execute(select(m.Asset).where(m.Asset.project_id == project_id, m.Asset.type == "url",
                                                      m.Asset.key == word))).scalar_one_or_none()
        if hint == "download" and url is not None:
            await link(db, project_id, file, url, "downloaded_from", **_link_src(obs))
    elif target and word.startswith("/"):
        file = await observe(db, project_id, "file", {"path": word, "host": target}, **obs)
        host = await _find_host(db, project_id, target)
        if host is not None:
            await link(db, project_id, file, host, "downloaded_from" if hint == "download" else "on", **_link_src(obs))


async def _keep_copy(db: AsyncSession, file: m.Asset, job_id: uuid.UUID | None) -> None:
    """A downloaded file the job also kept as an artifact (same name): the asset points at the copy."""
    name = str((file.fields or {}).get("name") or "").lower()
    if not job_id or not name:
        return
    for att in (await db.execute(select(m.Attachment).where(m.Attachment.job_id == job_id))).scalars():
        if re.split(r"[\\/]", att.filename)[-1].lower() == name:
            file.fields = {**(file.fields or {}), "attachment": str(att.id), "size": (file.fields or {}).get("size") or att.size}
            await db.flush()
            return


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
        person = await db.get(m.User, job.created_by) if job.created_by else None
        await settle(db, batch, task_id=task.id, person=person)
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


def describe_links(counts: dict[str, int]) -> str:
    """"3 relationships (works on ×2, admin on ×1)"."""
    n = sum(counts.values())
    parts = ", ".join(f"{LINK_KINDS[k].forward} ×{c}" for k, c in sorted(counts.items(), key=lambda x: -x[1]) if k in LINK_KINDS)
    return f"{n} relationship{'s' if n != 1 else ''}" + (f" ({parts})" if parts else "")


async def settle(db: AsyncSession, batch: Batch, *, task_id: uuid.UUID | None = None, actor: m.User | None = None,
                 where: str = "", feed_links: bool = True, person: m.User | None = None) -> None:
    """After recording: the feed entry ("Found 3 hosts and 5 services"), the event, triggers."""
    from .services import actor_of, record_activity

    created = _counts(batch.created())
    suggested = _counts(batch.suggested.values())
    if created:
        await record_activity(
            db, type="assets.found", message=f"Found {describe(created)}", project_id=batch.project_id, task_id=task_id,
            payload={"counts": created}, **actor_of(actor),
        )
    linked: dict[str, int] = {}
    for lk, _, _ in batch.new_links():
        linked[lk.kind] = linked.get(lk.kind, 0) + 1
    if linked and feed_links:
        await record_activity(
            db, type="assets.linked", message=f"Found {describe_links(linked)}", project_id=batch.project_id, task_id=task_id,
            payload={"links": linked}, **actor_of(actor),
        )
    from .plugin_events import asset_data, emit, link_data

    project = await db.get(m.Project, batch.project_id)
    # Who caused it, for plugins: the actor, or the person whose run found it.
    for a in batch.created():
        emit(db, "asset.created", batch.project_id, asset_data(a, project), by=actor or person)
    for lk, src, dst in batch.new_links():
        emit(db, "asset.linked", batch.project_id, link_data(lk, src, dst), by=actor or person)
    if suggested:
        await record_activity(
            db, type="assets.suggested", message=f"Spotted {describe(suggested)} to confirm{f' in {where}' if where else ''}",
            project_id=batch.project_id, task_id=task_id, payload={"counts": suggested}, **actor_of(actor),
        )
    await db.commit()
    bus.publish("assets.changed", batch.project_id, {"project_id": str(batch.project_id), "created": created, "suggested": suggested,
                                                     "linked": sum(linked.values()),
                                                     "updated": sum(1 for c in batch.changes.values() if c.event == "updated")})
    try:
        await fire_triggers(db, batch)
    except Exception:  # a trigger problem must never lose the assets
        log.exception("asset triggers for project %s failed", batch.project_id)


# ---------- queries ("hosts where smb_signing:false") ----------

TOKEN = re.compile(r'(\w+)(:|!=|>=|<=|>|<|=)("[^"]*"|\S+)|("[^"]+"|\S+)')


def parse_where(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """`field:value` (text contains it; yes/no and numbers match exactly), `field=value`
    (exactly), `field!=value`, `port>1000` filters and free words. A relationship's name works as
    a field too (`valid_on:dc01`, `has_admin:*`, `linked!=*`): see `query`."""
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
    rels = [c for c in conds if c["field"].lower() in LINK_WORDS or c["field"].lower() == "linked"]
    conds = [c for c in conds if c not in rels]
    stmt = select(m.Asset).where(m.Asset.project_id == project_id, m.Asset.status == status)
    if type_name:
        stmt = stmt.where(m.Asset.type == type_of(type_name).name)
    rows = (await db.execute(stmt)).scalars().all()
    related = await _relations(db, project_id, rels) if rels else None
    out = []
    for a in rows:
        hay = f"{a.label} {a.key} {' '.join(str(v) for v in (a.fields or {}).values())}".lower()
        if all(w in hay for w in words) and conditions_met(conds, a.fields or {}) and (related is None or related(a.id)):
            out.append(a)
    return sorted(out, key=sort_key)


async def _relations(db: AsyncSession, project_id: uuid.UUID, rels: list[dict[str, Any]]) -> Any:
    """A test for relationship filters: `valid_on:dc01` (works on something that says dc01),
    `accepts:*` (anything works on it), `linked:x` (any relationship), `!=` for none."""
    names = {a_id: f"{label} {key} {type_}".lower() for a_id, label, key, type_ in
             (await db.execute(select(m.Asset.id, m.Asset.label, m.Asset.key, m.Asset.type)
                               .where(m.Asset.project_id == project_id, m.Asset.status == "confirmed"))).all()}
    edges = (await db.execute(select(m.AssetLink.from_id, m.AssetLink.to_id, m.AssetLink.kind)
                              .where(m.AssetLink.project_id == project_id))).all()
    tests = []
    for c in rels:
        word, value = c["field"].lower(), str(c["value"]).lower()
        kind, forward = LINK_WORDS.get(word, (None, True))
        either = kind is None or kind == "related"
        hits: set[uuid.UUID] = set()
        for src, dst, k in edges:
            if kind is not None and k != kind:
                continue
            for me, other in ([(src, dst), (dst, src)] if either else [(src, dst) if forward else (dst, src)]):
                if other in names and (value in ("*", "any", "") or value in names[other]):
                    hits.add(me)
        tests.append((hits, c["op"] == "not_equals"))
    return lambda asset_id: all((asset_id in hits) != negate for hits, negate in tests)


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
            "vulnerability": "id", "file": "path"}.get(type_name, "key")


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

TEMPLATE = re.compile(r"\{\{\s*(asset|other|link)\.(\w+)\s*\}\}")


def _value(which: str, name: str, asset: m.Asset, other: m.Asset | None, lk: m.AssetLink | None) -> Any:
    """`asset.ip`; for a relationship trigger also `other.ip` (the asset at the other end) and
    `link.protocol` (the relationship's details, its `kind` and `note`)."""
    if which == "link":
        if lk is None:
            return None
        return getattr(lk, name) if name in ("kind", "note") else (lk.fields or {}).get(name)
    a = other if which == "other" else asset
    if a is None:
        return None
    return getattr(a, name) if name in ("key", "label", "type") else (a.fields or {}).get(name)


def render(template: Any, asset: m.Asset, other: m.Asset | None = None, lk: m.AssetLink | None = None) -> Any:
    if not isinstance(template, str):
        return template
    whole = TEMPLATE.fullmatch(template.strip())
    if whole:
        v = _value(whole.group(1), whole.group(2), asset, other, lk)
        return v if v is not None else ""

    def one(mt: re.Match[str]) -> str:
        v = _value(mt.group(1), mt.group(2), asset, other, lk)
        return ", ".join(str(x) for x in v) if isinstance(v, list) else ("" if v is None else str(v))

    return TEMPLATE.sub(one, template)


async def fire_triggers(db: AsyncSession, batch: Batch) -> None:
    triggers = (
        await db.execute(select(m.AssetTrigger).where(m.AssetTrigger.project_id == batch.project_id, m.AssetTrigger.enabled.is_(True)))
    ).scalars().all()
    if not triggers:
        return
    project = await db.get(m.Project, batch.project_id)
    started = False
    for trig in triggers:
        if trig.on_event == "linked":
            # A new relationship of its kind from an asset of its type ("a credential works on a new host").
            matches = [(src, dst, lk) for lk, src, dst in batch.new_links()
                       if lk.kind == trig.link_kind and src.type == trig.asset_type and (not trig.other_type or dst.type == trig.other_type)]
        else:
            matches = [(c.asset, None, None) for c in batch.changes.values() if trig.on_event in ("any", c.event)
                       and c.asset.type == trig.asset_type]
        for asset, other, lk in matches:
            if not conditions_met(trig.conditions, asset.fields or {}):
                continue
            if trig.once_per_asset:
                done = (
                    await db.execute(
                        select(m.AssetTriggerFire.id).where(
                            m.AssetTriggerFire.trigger_id == trig.id, m.AssetTriggerFire.asset_id == asset.id,
                            m.AssetTriggerFire.other_id == other.id if other is not None else m.AssetTriggerFire.other_id.is_(None),
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
            other_id = other.id if other is not None else None
            if hour >= trig.max_per_hour:
                db.add(m.AssetTriggerFire(trigger_id=trig.id, asset_id=asset.id, other_id=other_id, status="limited",
                                          reason=f"over {trig.max_per_hour} runs in the last hour"))
                continue
            fire = m.AssetTriggerFire(trigger_id=trig.id, asset_id=asset.id, other_id=other_id, status="waiting")
            db.add(fire)
            await db.flush()
            # What it runs against: the other end of a relationship when that's somewhere (the host).
            subject = other if other is not None and _targets(other) else asset
            on = asset.label if other is None else f"{asset.label} → {other.label}"
            if project is not None and in_scope(project, subject):
                started = await run_fire(db, fire, trig, asset, other=other, lk=lk) or started
            else:
                fire.reason = "outside the project's scope" if scope_of(project)["include"] else "no scope is set for this project"
                await notifications.notify(
                    db, kind="asset_trigger.approval", severity="warning",
                    title=f"“{trig.name}” wants to run on {on}",
                    body=f"It's {fire.reason}; approve it to run it.",
                    link=f"/projects/{batch.project_id}/assets?tab=triggers", project_id=batch.project_id,
                    subject=f"trigger-fire:{fire.id}", webhooks=False,
                )
                await alerts.approval(db, batch.project_id, f"“{trig.name}” wants to run on {on}",
                                      f"It's {fire.reason}; approve it to run it.", f"/projects/{batch.project_id}/assets?tab=triggers")
    await db.commit()
    bus.publish("asset_triggers.changed", batch.project_id, {"project_id": str(batch.project_id)})
    if started:
        from .orchestrator.dispatcher import dispatcher

        dispatcher.wake()


async def run_fire(db: AsyncSession, fire: m.AssetTriggerFire, trig: m.AssetTrigger, asset: m.Asset, user: m.User | None = None, *,
                   other: m.Asset | None = None, lk: m.AssetLink | None = None) -> bool:
    """Start what a trigger runs for an asset (and, for a relationship, the asset at its other
    end): a command card (queued) or a playbook run."""
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

    args = {k: render(v, asset, other, lk) for k, v in (trig.args or {}).items()}
    on = asset.label if other is None else f"{asset.label} → {other.label}"
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
                title=f"{trig.name}: {on}"[:300], kind=m.TaskKind.command, tags=["trigger"],
                description=(f"Started by the trigger “{trig.name}” for the {asset.type} {asset.label}." if other is None else
                             f"Started by the trigger “{trig.name}”: the {asset.type} {asset.label} "
                             f"{LINK_KINDS[lk.kind].forward if lk is not None else 'is linked to'} the {other.type} {other.label}."),
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
