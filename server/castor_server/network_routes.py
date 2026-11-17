"""Network routes kept in the database (routes table), and what the rest of Castor asks of them.

- Hops keep their secrets in the vault (`secret`: a password, or an SSH private key whose public half
  is shown so people can authorize it); people never read them back.
- SSH hops' host keys are trusted on first use, like SSH hosts: the first key an agent reports is
  kept, and a different one later is held as `host_key_pending` (the route stops at that hop) until
  someone accepts it.
- Which route a job takes: its card's (`tasks.route`: an id, or "none"), else its playbook run's,
  else the project's default (`projects.settings.route`); an agent's own route (`agents.route`, set by
  admins) goes in front of it. A route that's gone, or not for the project, stops the job: it never
  runs unrouted by mistake.
- Live state per agent comes from the agents (route.status, route.hop) and lives in memory here.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import secrets as pysecrets
import time
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m
from . import routing, vault
from .events import bus
from .routing import HopIn, RouteError

log = logging.getLogger(__name__)

# (route id, agent id) → {state, mode, jobs, broken, hops: {hop_id: {state, error, ms}}, at}
STATES: dict[tuple[str, str], dict[str, Any]] = {}


# ---------- hops ----------

def _new_hop_id() -> str:
    return pysecrets.token_hex(4)


def store_hops(hops_in: list[HopIn], existing: list[dict[str, Any]], comment: str) -> list[dict[str, Any]]:
    """The hops to keep: secrets into the vault (kept when not sent again), host keys reset when a
    hop now points somewhere else."""
    from .orchestrator.ssh import generate_key, import_key

    before = {h.get("id"): h for h in existing}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for hop in hops_in:
        old = before.get(hop.id) if hop.id else None
        hop_id = hop.id if old else _new_hop_id()
        if hop_id in seen:
            hop_id = _new_hop_id()
        seen.add(hop_id)
        port = hop.port or routing.DEFAULT_PORTS[hop.kind]
        same_place = bool(old) and old.get("host") == hop.host and old.get("port") == port and old.get("kind") == hop.kind
        row: dict[str, Any] = {
            "id": hop_id, "kind": hop.kind, "host": hop.host, "port": port, "username": hop.username,
            "label": hop.label, "auth": "none", "secret": "", "public_key": "",
            "host_key": (old or {}).get("host_key") if same_place else None,
            "host_key_pending": (old or {}).get("host_key_pending") if same_place else None,
        }
        if old and old.get("kind") == hop.kind:
            row.update(auth=old.get("auth") or "none", secret=old.get("secret") or "", public_key=old.get("public_key") or "")
        if hop.generate_key:
            private, public = generate_key(comment)
            row.update(auth="key", secret=vault.encrypt(private.encode()), public_key=public)
        elif hop.private_key:
            try:
                private, public = import_key(hop.private_key, hop.passphrase)
            except ValueError as exc:
                raise RouteError(f"hop {len(out) + 1}: {exc}") from exc
            row.update(auth="key", secret=vault.encrypt(private.encode()), public_key=public)
        elif hop.password:
            row.update(auth="password", secret=vault.encrypt(hop.password.encode()), public_key="")
        if hop.auth == "none":
            row.update(auth="none", secret="", public_key="")
        elif hop.auth in ("password", "key") and row["auth"] != hop.auth:
            raise RouteError(f"hop {len(out) + 1}: give the {'password' if hop.auth == 'password' else 'private key'} for it")
        if hop.kind == "ssh" and row["auth"] == "none":
            raise RouteError(f"hop {len(out) + 1}: an SSH hop signs in with a key or a password")
        if hop.kind != "ssh":
            row["host_key"] = row["host_key_pending"] = None
            row["public_key"] = ""
        if hop.host_key:
            row["host_key"], row["host_key_pending"] = hop.host_key, None
        if hop.kind == "http" and row["auth"] == "password" and not hop.username:
            raise RouteError(f"hop {len(out) + 1}: a proxy password needs a user name")
        out.append(row)
    return out


def hop_out(h: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": h.get("id"), "kind": h.get("kind"), "host": h.get("host"), "port": h.get("port"),
        "username": h.get("username") or "", "label": h.get("label") or "", "auth": h.get("auth") or "none",
        "has_secret": bool(h.get("secret")), "public_key": h.get("public_key") or "",
        "host_key": h.get("host_key"), "host_key_fingerprint": routing.fingerprint(h.get("host_key")),
        "host_key_pending": h.get("host_key_pending"),
        "host_key_pending_fingerprint": routing.fingerprint(h.get("host_key_pending")),
    }


def route_out(row: m.Route) -> dict[str, Any]:
    hops = row.hops or []
    states = {agent: st for (rid, agent), st in STATES.items() if rid == str(row.id)}
    return {
        "id": str(row.id), "name": row.name, "description": row.description or "", "tags": row.tags or [],
        "project_ids": row.project_ids or [], "hops": [hop_out(h) for h in hops], "dns": row.dns, "strict": row.strict,
        "segment": row.segment or "", "label": routing.chain_label(hops), "last_test": row.last_test,
        "pending_keys": sum(1 for h in hops if h.get("host_key_pending")),
        "live": [{"agent_id": a, **st} for a, st in states.items()],
        "created_by": str(row.created_by) if row.created_by else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def brief(row: m.Route) -> dict[str, Any]:
    """What a job keeps about the route it took (no secrets)."""
    hops = row.hops or []
    return {"id": str(row.id), "name": row.name, "label": routing.chain_label(hops), "dns": row.dns, "strict": row.strict,
            "segment": row.segment or row.name,
            "hops": [{"kind": h.get("kind"), "host": h.get("host"), "port": h.get("port"), "username": h.get("username") or ""}
                     for h in hops]}


def can_see(row: m.Route, visible: set[uuid.UUID] | None) -> bool:
    if visible is None or not row.project_ids:
        return True
    return any(uuid.UUID(str(p)) in visible for p in row.project_ids)


def for_project(row: m.Route, project_id: uuid.UUID | str) -> bool:
    return not row.project_ids or str(project_id) in [str(p) for p in row.project_ids]


def publish(row: m.Route, name: str = "route.updated", data: dict[str, Any] | None = None) -> None:
    payload = data if data is not None else route_out(row)
    if row.project_ids:
        for pid in row.project_ids:
            bus.publish(name, str(pid), payload)
    else:
        bus.publish(name, None, payload)


# ---------- which route a job takes ----------

async def get(db: AsyncSession, route_id: str | None) -> m.Route | None:
    if not route_id or route_id == routing.NONE:
        return None
    try:
        return await db.get(m.Route, uuid.UUID(str(route_id)))
    except ValueError:
        return None


async def chosen_for(db: AsyncSession, task: m.Task) -> str | None:
    """The route id a card's jobs take (its own, its run's, the project's), or None (direct)."""
    if task.route:
        return None if task.route == routing.NONE else task.route
    if task.playbook_run_id is not None:
        run = await db.get(m.PlaybookRun, task.playbook_run_id)
        if run is not None and run.route:
            return None if run.route == routing.NONE else run.route
    project = await db.get(m.Project, task.project_id)
    default = ((project.settings or {}).get("route") if project else None) or None
    return None if default in (None, "", routing.NONE) else str(default)


async def rows_for(db: AsyncSession, task: m.Task, agent_route: str | None) -> list[m.Route]:
    """The routes a job goes through on an agent: the agent's own first, then the card's. Raises
    RouteError when one is missing or not for the project (the job doesn't run)."""
    out: list[m.Route] = []
    for which, route_id in (("this agent's route", agent_route), ("its route", await chosen_for(db, task))):
        if not route_id:
            continue
        if route_id.startswith("missing:"):
            raise RouteError(f"its playbook step names a route that doesn't exist: “{route_id.removeprefix('missing:')}”")
        row = await get(db, route_id)
        if row is None:
            raise RouteError(f"{which} no longer exists")
        if which == "its route" and not for_project(row, task.project_id):
            raise RouteError(f"route “{row.name}” isn't for this project")
        pending = [i for i, h in enumerate(row.hops or []) if h.get("host_key_pending")]
        if pending:
            raise RouteError(f"route “{row.name}”: hop {pending[0] + 1}'s host key changed; someone has to accept it first")
        if row.id not in {r.id for r in out}:
            out.append(row)
    return out


def payload(rows: list[m.Route]) -> dict[str, Any]:
    """The route as the agent needs it, secrets included: only ever in a job.run, http.request or
    route.test message."""
    hops: list[dict[str, Any]] = []
    for row in rows:
        for h in row.hops or []:
            secret = vault.decrypt(h["secret"]).decode() if h.get("secret") else ""
            hops.append({
                "id": h["id"], "kind": h["kind"], "host": h["host"], "port": h["port"], "username": h.get("username") or "",
                "password": secret if h.get("auth") == "password" else "",
                "private_key": secret if h.get("auth") == "key" else "",
                "host_key": h.get("host_key") or "",
            })
    dns = rows[-1].dns if rows else "remote"
    strict = any(r.strict for r in rows)
    last = rows[-1]
    return {
        "id": str(last.id), "ids": [str(r.id) for r in rows], "name": " + ".join(r.name for r in rows),
        "label": routing.chain_label(hops), "hops": hops, "dns": dns, "strict": strict,
        "key": routing.route_key(hops, dns, strict),
    }


def secrets_of(rows: list[m.Route]) -> list[str]:
    """Values to mask out of a job's logs."""
    out = []
    for row in rows:
        for h in row.hops or []:
            if h.get("secret") and h.get("auth") == "password":
                out.append(vault.decrypt(h["secret"]).decode())
    return [s for s in out if len(s) >= 4]


def info(rows: list[m.Route]) -> dict[str, Any]:
    """jobs.route_info."""
    last = brief(rows[-1])
    if len(rows) > 1:
        hops = [h for r in rows for h in brief(r)["hops"]]
        last.update(name=" + ".join(r.name for r in rows), label=routing.chain_label(hops), hops=hops,
                    strict=any(r.strict for r in rows), via=[str(r.id) for r in rows[:-1]])
    return last


# ---------- what agents report ----------

def set_state(route_id: str | None, agent_id: uuid.UUID, update: dict[str, Any]) -> dict[str, Any] | None:
    if not route_id:
        return None
    key = (str(route_id), str(agent_id))
    state = STATES.setdefault(key, {"hops": {}})
    state.update(update)
    state["at"] = time.time()
    if update.get("state") == "down":
        STATES.pop(key, None)
        return {"agent_id": str(agent_id), "state": "down", "hops": {}}
    return {"agent_id": str(agent_id), **state}


def forget_agent(agent_id: uuid.UUID) -> list[str]:
    """An agent went away: its routes are down there. Returns the route ids that changed."""
    gone = [rid for (rid, a) in STATES if a == str(agent_id)]
    for rid in gone:
        STATES.pop((rid, str(agent_id)), None)
    return gone


async def learn_host_key(db: AsyncSession, route_id: str | None, hop_id: str | None, key: str, changed: bool) -> m.Route | None:
    """An agent saw an SSH hop's host key: trusted if none was (first use), held for a person if
    it's different from the trusted one."""
    from .services import record_activity

    key = " ".join(str(key or "").split()[:2])
    if not key or not hop_id:
        return None
    rows = [await get(db, route_id)] if route_id else []
    rows = [r for r in rows if r is not None and any(h.get("id") == hop_id for h in r.hops or [])]
    if not rows:  # an agent route's hop, in a chain the job route led
        rows = [r for r in (await db.execute(select(m.Route))).scalars() if any(h.get("id") == hop_id for h in r.hops or [])]
    if not rows:
        return None
    row = rows[0]
    hops = [dict(h) for h in row.hops or []]
    for i, h in enumerate(hops):
        if h.get("id") != hop_id or h.get("kind") != "ssh":
            continue
        if not h.get("host_key"):
            h["host_key"], h["host_key_pending"] = key, None
            msg = f"Trusted hop {i + 1}'s host key on route “{row.name}” ({routing.fingerprint(key)}, first seen)"
            kind = "route.host_key_trusted"
        elif h["host_key"] != key and changed:
            if h.get("host_key_pending") == key:
                return row
            h["host_key_pending"] = key
            msg = f"Hop {i + 1} of route “{row.name}” shows a different host key ({routing.fingerprint(key)}): it's stopped until someone accepts it"
            kind = "route.host_key_changed"
        else:
            return row
        row.hops = hops
        await record_activity(db, type=kind, message=msg, project_id=None, payload={"route_id": str(row.id), "hop_id": hop_id})
        if kind == "route.host_key_changed":
            await _tell_about_key(db, row, i)
        await db.commit()
        publish(row)
        return row
    return None


async def _tell_about_key(db: AsyncSession, row: m.Route, index: int) -> None:
    from .notifications import notify

    users = [row.created_by] if row.created_by else []
    admins = (await db.execute(select(m.User.id).where(m.User.role == m.UserRole.admin, m.User.disabled_at.is_(None),
                                                       m.User.plugin_id.is_(None)))).scalars()
    users += list(admins)
    await notify(db, kind="route.host_key", title=f"Route “{row.name}”: hop {index + 1}'s host key changed",
                 body="Jobs on this route wait until someone checks the new key and accepts it (Routes).",
                 link=f"/routes?r={row.id}", severity="warning", project_id=None, subject=f"route-key:{row.id}",
                 user_ids=sorted(set(users), key=str), webhooks=False)


# ---------- testing ----------

async def test(db: AsyncSession, row: m.Route, conn: Any, target: str | None, user: m.User | None) -> dict[str, Any]:
    """Have an agent bring the route up and try it hop by hop (and the target, host:port)."""
    from .orchestrator.dispatcher import tool_way
    from .orchestrator.gateway import gateway
    from .services import actor_of, record_activity
    from .tool_library import tools_by_id

    tgt = None
    if target:
        host, _, port = target.strip().rpartition(":")
        host = host.strip("[]")
        if not host or not port.isdigit() or not routing.HOST.match(host):
            raise RouteError("the target is host:port, like 10.0.0.5:445")
        tgt = [host, int(port)]
    data = payload([row])
    box = (await tools_by_id(db)).get("castor-route")
    data["box"] = tool_way(conn, box) if box else None
    started = time.time()
    try:
        result = await gateway.route_test(conn, data, tgt, timeout=150)
    except TimeoutError:
        result = {"error": f"{conn.name} didn't answer in time"}
    except Exception as exc:  # noqa: BLE001 - the agent went away
        result = {"error": str(exc) or exc.__class__.__name__}
    hops = row.hops or []
    out = {
        "at": started, "agent_id": str(conn.agent_id), "agent_name": conn.name, "target": target or None,
        "error": result.get("error"),
        "hops": [{"hop_id": hops[i].get("id") if i < len(hops) else None, **h} for i, h in enumerate(result.get("hops") or [])],
        "target_result": result.get("target"),
    }
    out["ok"] = not out["error"] and all(h.get("ok") for h in out["hops"]) and (not tgt or bool((out["target_result"] or {}).get("ok")))
    row.last_test = out
    broke = next((h for h in out["hops"] if h.get("ok") is False), None)
    if out["error"]:
        summary = f"couldn't be tested from {conn.name}: {out['error']}"
    elif broke:
        summary = f"breaks at hop {out['hops'].index(broke) + 1} from {conn.name}: {broke.get('error')}"
    else:
        summary = f"works from {conn.name}" + (f" and reaches {target}" if tgt and out["ok"] else
                                              f", but {target}: {(out['target_result'] or {}).get('error')}" if tgt else "")
    await record_activity(db, type="route.tested", message=f"Route “{row.name}” {summary}", project_id=None,
                          payload={"route_id": str(row.id), "ok": out["ok"]}, **actor_of(user))
    await db.commit()
    await db.refresh(row)
    publish(row)
    return out


# ---------- scope ----------

def _bare(target: str) -> str:
    from .asset_history import _bare as bare

    return bare(target).lower()


def target_in(target: str, entries: list[str]) -> bool:
    from .assets import _matches

    t = _bare(target)
    try:
        net = ipaddress.ip_network(t, strict=False)
    except ValueError:
        net = None
    for entry in entries:
        e = entry.strip().lower()
        if not e:
            continue
        if net is not None:
            try:
                if net.subnet_of(ipaddress.ip_network(e, strict=False)):  # type: ignore[arg-type]
                    return True
            except (ValueError, TypeError):
                pass
            continue
        if _matches(t, e):
            return True
    return False


async def outside_scope(db: AsyncSession, task: m.Task) -> list[str]:
    """A routed card's targets that aren't in the project's scope (empty when there's no scope)."""
    from .asset_history import job_targets
    from .assets import scope_of

    project = await db.get(m.Project, task.project_id)
    if project is None:
        return []
    scope = scope_of(project)
    if not scope["include"]:
        return []
    targets, _ = await job_targets(db, task)
    return [t for t in targets if not target_in(t, scope["include"]) or target_in(t, scope["exclude"])]


# ---------- from an SSH host ----------

async def hops_from_ssh_host(db: AsyncSession, host: m.SshHost) -> list[dict[str, Any]]:
    """An SSH host's way in as hops: its jump hosts (the shared SSH config's ProxyJump chain), then
    the host itself, each signing in with the host's key and keeping the host keys Castor trusts."""
    from . import ssh_config
    from .orchestrator.ssh import load_config

    parsed = ssh_config.parse(await load_config(db))
    chain = ssh_config.route(parsed, host.host, host.username or None, host.port)
    hops: list[dict[str, Any]] = []
    def two(key: str | None) -> str | None:
        return " ".join(key.split()[:2]) if key else None

    for hop in chain:
        label = hop.label
        hops.append({
            "id": _new_hop_id(), "kind": "ssh", "host": hop.hostname, "port": hop.port or 22, "username": hop.user or host.username,
            "label": "", "auth": "key", "secret": host.private_key, "public_key": host.public_key or "",
            "host_key": two((host.jump_host_keys or {}).get(label) if hop is not chain[-1] else host.host_key),
            "host_key_pending": None,
        })
    return hops


async def wait_for(predicate, timeout: float = 5.0) -> bool:  # pragma: no cover - test helper
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


async def check_choice(db: AsyncSession, value: str | None, project_id: uuid.UUID | str | None) -> str | None:
    """A route as a card, run or project chooses it: None/"" (the default), "none" (direct), or the
    id of a route that's for the project."""
    if value is None or value == "":
        return None
    if value == routing.NONE:
        return routing.NONE
    row = await get(db, value)
    if row is None:
        raise RouteError("route: no such route")
    if project_id is not None and not for_project(row, project_id):
        raise RouteError(f"route: “{row.name}” isn't for this project")
    return str(row.id)


async def step_choice(db: AsyncSession, value: str, project_id: uuid.UUID) -> str:
    """A playbook step's `route:` (a route's name or id, or "none")."""
    if value == routing.NONE:
        return routing.NONE
    row = await get(db, value)
    if row is None:
        row = (await db.execute(select(m.Route).where(m.Route.name == value))).scalar_one_or_none()
    if row is None:
        raise RouteError(f"no route called “{value}”")
    if not for_project(row, project_id):
        raise RouteError(f"route “{row.name}” isn't for this project")
    return str(row.id)
