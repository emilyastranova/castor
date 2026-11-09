"""SSH hosts (Agents → Add SSH host): hosts Castor connects to and runs jobs on as agents.
Admin only. The private key goes into the vault and never comes back out; the public key
is shown so it can go into the account's authorized_keys."""

from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import models as m
from .. import ssh_config, vault
from ..deps import DB, Admin, parse_uuid
from ..events import bus
from ..orchestrator.gateway import gateway
from ..orchestrator.ssh import fingerprint, generate_key, import_key, load_config, ssh_hosts
from ..services import record_activity

router = APIRouter(prefix="/ssh-hosts", tags=["ssh hosts"])
config_router = APIRouter(prefix="/ssh-config", tags=["ssh hosts"])

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class SshHostIn(BaseModel):
    name: str
    host: str = Field(min_length=1, max_length=255)
    # Host can be an alias from the SSH config; empty port and username come from it too.
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str = Field(default="", max_length=128)
    workdir: str = Field(default="castor-jobs", min_length=1, max_length=500)
    network: str = Field(default="host", max_length=64)
    allow_host: bool = True
    allow_host_install: bool = False
    enabled: bool = True
    tags: list[str] = Field(default_factory=list)
    max_concurrency: int = Field(default=1, ge=1, le=64)
    # A new key: generate one, or paste a private key (with its passphrase, if any). On an
    # update, leave both out to keep the current key.
    generate_key: bool = False
    private_key: str | None = None
    passphrase: str | None = None


class SshHostOut(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    name: str
    host: str
    port: int | None
    username: str
    workdir: str
    network: str
    allow_host: bool
    allow_host_install: bool
    enabled: bool
    tags: list[str]
    max_concurrency: int
    public_key: str
    key_fingerprint: str | None
    host_key_fingerprint: str | None
    # A different key the host presented, waiting for an admin to accept it.
    host_key_pending_fingerprint: str | None
    # How it's reached per the SSH config: each hop as user@host:port, the host itself last.
    route: list[str]
    route_error: str | None
    # Jump hosts' keys (fingerprints by hop), trusted and waiting to be accepted.
    jump_host_keys: dict[str, str | None]
    jump_host_keys_pending: dict[str, str | None]
    # connecting | connected | error | host_key_changed | disabled
    state: str
    error: str | None
    connected: bool


def _route(row: m.SshHost, parsed: ssh_config.Parsed) -> tuple[list[str], str | None]:
    try:
        return [h.label for h in ssh_config.route(parsed, row.host, row.username or None, row.port)], None
    except ssh_config.ConfigError as exc:
        return [], str(exc)


def _out(row: m.SshHost, agent: m.Agent, parsed: ssh_config.Parsed) -> SshHostOut:
    link = ssh_hosts.links.get(row.id)
    route, route_error = _route(row, parsed)
    state = link.state if link else ("disabled" if not row.enabled else "connecting")
    return SshHostOut(
        id=row.id, agent_id=row.agent_id, name=agent.name, host=row.host, port=row.port, username=row.username,
        workdir=row.workdir, network=row.network, allow_host=row.allow_host, allow_host_install=row.allow_host_install,
        enabled=row.enabled, tags=agent.tags or [], max_concurrency=agent.max_concurrency, public_key=row.public_key,
        key_fingerprint=fingerprint(row.public_key), host_key_fingerprint=fingerprint(row.host_key),
        host_key_pending_fingerprint=fingerprint(row.host_key_pending), state=state, route=route, route_error=route_error,
        jump_host_keys={k: fingerprint(v) for k, v in (row.jump_host_keys or {}).items()},
        jump_host_keys_pending={k: fingerprint(v) for k, v in (row.jump_host_keys_pending or {}).items()},
        error=(link.error if link else row.last_error), connected=gateway.get(row.agent_id) is not None,
    )


async def _row(db, host_id: str) -> tuple[m.SshHost, m.Agent]:
    row = await db.get(m.SshHost, parse_uuid(host_id))
    agent = await db.get(m.Agent, row.agent_id) if row else None
    if row is None or agent is None:
        raise HTTPException(404, "SSH host not found")
    return row, agent


def _check(body: SshHostIn) -> None:
    if not NAME.fullmatch(body.name):
        raise HTTPException(422, "name: letters, digits, dots, dashes and underscores")
    for label, value in (("host", body.host), ("username", body.username), ("workdir", body.workdir), ("network", body.network)):
        if any(c in value for c in "\0\n\r"):
            raise HTTPException(422, f"{label}: no line breaks")
    if body.host.startswith("-") or body.username.startswith("-") or "@" in body.host or " " in body.host.strip():
        raise HTTPException(422, "host: a hostname, address or SSH config alias (the user goes in username)")


def _key(body: SshHostIn, name: str) -> tuple[str, str] | None:
    if body.private_key and body.private_key.strip():
        try:
            return import_key(body.private_key, body.passphrase)
        except ValueError as exc:
            raise HTTPException(422, f"private_key: {exc}") from exc
    if body.generate_key:
        return generate_key(f"castor@{name}")
    return None


def _apply(row: m.SshHost, agent: m.Agent, body: SshHostIn) -> None:
    host_changed = (row.host, row.port) != (body.host, body.port)
    row.host, row.port, row.username = body.host.strip(), body.port, body.username.strip()
    row.workdir, row.network = body.workdir.strip(), body.network.strip()
    row.allow_host, row.allow_host_install, row.enabled = body.allow_host, body.allow_host_install, body.enabled
    if host_changed:  # another machine: trust its key on first use again
        row.host_key = row.host_key_pending = None
    agent.tags = sorted({t.strip() for t in body.tags if t.strip()})
    agent.max_concurrency = body.max_concurrency


async def _name_free(db, name: str, agent_id: uuid.UUID | None = None) -> None:
    other = (await db.execute(select(m.Agent).where(m.Agent.name == name))).scalar_one_or_none()
    if other is not None and other.id != agent_id:
        raise HTTPException(409, f"An agent named {name} already exists")


def _changed(row: m.SshHost) -> None:
    bus.publish("ssh_hosts.changed", None, {"id": row.id})


@router.get("", response_model=list[SshHostOut])
async def list_hosts(db: DB, _: Admin):
    rows = (await db.execute(select(m.SshHost, m.Agent).join(m.Agent, m.Agent.id == m.SshHost.agent_id).order_by(m.Agent.name))).all()
    parsed = ssh_config.parse(await load_config(db))
    return [_out(row, agent, parsed) for row, agent in rows]


@router.post("", response_model=SshHostOut, status_code=201)
async def create_host(body: SshHostIn, db: DB, user: Admin):
    _check(body)
    await _name_free(db, body.name)
    key = _key(body, body.name)
    if key is None:
        raise HTTPException(422, "Generate a key or paste a private key")
    agent = m.Agent(name=body.name, hostname=body.host, username=body.username)
    db.add(agent)
    await db.flush()
    row = m.SshHost(agent_id=agent.id, host=body.host, port=body.port, username=body.username,
                    private_key=vault.encrypt(key[0].encode()), public_key=key[1])
    _apply(row, agent, body)
    db.add(row)
    await record_activity(db, type="ssh_host.added", message=f"SSH host {body.name} added ({body.username}@{body.host})",
                          project_id=None, actor_type="user", actor_id=str(user.id), actor_name=user.username)
    await db.commit()
    await ssh_hosts.reload(row.id, agent.id)
    bus.publish("agent.updated", None, await gateway.agent_out(agent))
    _changed(row)
    return _out(row, agent, ssh_config.parse(await load_config(db)))


class SshHostPatch(BaseModel):
    """Any of SshHostIn's fields; the rest stay as they are."""

    name: str | None = None
    host: str | None = Field(default=None, min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = Field(default=None, max_length=128)
    workdir: str | None = Field(default=None, min_length=1, max_length=500)
    network: str | None = Field(default=None, max_length=64)
    allow_host: bool | None = None
    allow_host_install: bool | None = None
    enabled: bool | None = None
    tags: list[str] | None = None
    max_concurrency: int | None = Field(default=None, ge=1, le=64)
    generate_key: bool = False
    private_key: str | None = None
    passphrase: str | None = None


@router.patch("/{host_id}", response_model=SshHostOut)
async def patch_host(host_id: str, body: SshHostPatch, db: DB, user: Admin):
    """Change some of a host's settings (port: null to take it from the SSH config)."""
    row, agent = await _row(db, host_id)
    current = {"name": agent.name, "host": row.host, "port": row.port, "username": row.username, "workdir": row.workdir,
               "network": row.network, "allow_host": row.allow_host, "allow_host_install": row.allow_host_install,
               "enabled": row.enabled, "tags": agent.tags or [], "max_concurrency": agent.max_concurrency}
    return await update_host(host_id, SshHostIn(**{**current, **body.model_dump(exclude_unset=True)}), db, user)


@router.put("/{host_id}", response_model=SshHostOut)
async def update_host(host_id: str, body: SshHostIn, db: DB, _: Admin):
    _check(body)
    row, agent = await _row(db, host_id)
    await _name_free(db, body.name, agent.id)
    key = _key(body, body.name)
    before = (agent.name, row.host, row.port, row.username, row.workdir, row.network, row.allow_host,
              row.allow_host_install, row.enabled)
    if key is not None:
        row.private_key, row.public_key = vault.encrypt(key[0].encode()), key[1]
    agent.name = body.name
    _apply(row, agent, body)
    await db.commit()
    after = (agent.name, row.host, row.port, row.username, row.workdir, row.network, row.allow_host,
             row.allow_host_install, row.enabled)
    if key is not None or before != after:
        await ssh_hosts.reload(row.id, agent.id)  # reconnects: jobs running there are lost
    elif conn := gateway.get(agent.id):  # tags and slots apply at once
        conn.tags, conn.max_concurrency = agent.tags, agent.max_concurrency
        gateway._notify()
    bus.publish("agent.updated", None, await gateway.agent_out(agent))
    _changed(row)
    return _out(row, agent, ssh_config.parse(await load_config(db)))


@router.post("/{host_id}/accept-host-key", response_model=SshHostOut)
async def accept_host_key(host_id: str, db: DB, user: Admin):
    """Trust the different key the host presented (after checking it's the right machine)."""
    row, agent = await _row(db, host_id)
    jumps = {k: v for k, v in (row.jump_host_keys_pending or {}).items() if v}
    if not row.host_key_pending and not jumps:
        raise HTTPException(409, "No new host key to accept")
    changes = []
    if row.host_key_pending:
        changes.append(f"{agent.name}: {fingerprint(row.host_key_pending)}, was {fingerprint(row.host_key)}")
        row.host_key, row.host_key_pending = row.host_key_pending, None
    for label, key in jumps.items():
        changes.append(f"jump host {label}: {fingerprint(key)}, was {fingerprint((row.jump_host_keys or {}).get(label))}")
    row.jump_host_keys = {**(row.jump_host_keys or {}), **jumps}
    row.jump_host_keys_pending = {}
    await record_activity(db, type="ssh_host.key_accepted",
                          message="New host key accepted (" + "; ".join(changes) + ")",
                          project_id=None, actor_type="user", actor_id=str(user.id), actor_name=user.username)
    await db.commit()
    await ssh_hosts.reload(row.id, agent.id)
    _changed(row)
    return _out(row, agent, ssh_config.parse(await load_config(db)))


@router.post("/{host_id}/reconnect", response_model=SshHostOut)
async def reconnect(host_id: str, db: DB, _: Admin):
    """Try to connect now instead of waiting for the next retry (e.g. after adding the key)."""
    row, agent = await _row(db, host_id)
    link = ssh_hosts.links.get(row.id)
    if link is None or link.state == "connected":
        await ssh_hosts.reload(row.id, agent.id)
    else:
        link.retry_now()
    return _out(row, agent, ssh_config.parse(await load_config(db)))


@router.delete("/{host_id}", status_code=204)
async def delete_host(host_id: str, db: DB, user: Admin):
    """Disconnect and forget the host (its agent too; jobs it ran keep their output)."""
    await ssh_hosts.remove(parse_uuid(host_id))  # first: it writes the agent's offline state
    row, agent = await _row(db, host_id)
    agent_id, name = agent.id, agent.name
    await db.delete(row)
    await db.delete(agent)
    await record_activity(db, type="ssh_host.removed", message=f"SSH host {name} removed", project_id=None,
                          actor_type="user", actor_id=str(user.id), actor_name=user.username)
    await db.commit()
    bus.publish("agent.deleted", None, {"id": agent_id})
    bus.publish("ssh_hosts.changed", None, {"id": row.id})


# ---------- the SSH config they share ----------


class SshConfigIn(BaseModel):
    text: str = Field(default="", max_length=200_000)


class SshConfigOut(BaseModel):
    text: str
    errors: list[str]
    warnings: list[str]
    # Each SSH host's route with this config (hops as user@host:port), or why it has none.
    routes: dict[str, list[str]]
    route_errors: dict[str, str]


async def _config_out(db, text: str) -> SshConfigOut:
    parsed = ssh_config.parse(text)
    rows = (await db.execute(select(m.SshHost, m.Agent).join(m.Agent, m.Agent.id == m.SshHost.agent_id))).all()
    routes, route_errors = {}, {}
    for row, agent in rows:
        route, error = _route(row, parsed)
        if error and not parsed.errors:
            route_errors[agent.name] = error
        elif not error:
            routes[agent.name] = route
    return SshConfigOut(text=text, errors=parsed.errors, warnings=parsed.warnings, routes=routes, route_errors=route_errors)


@config_router.get("", response_model=SshConfigOut)
async def get_config(db: DB, _: Admin):
    """The SSH config (ssh_config format) SSH hosts are reached with: aliases, users, ports
    and jump hosts (ProxyJump)."""
    return await _config_out(db, await load_config(db))


@config_router.post("/check", response_model=SshConfigOut)
async def check_config(body: SshConfigIn, db: DB, _: Admin):
    """What a config would do, without saving it."""
    return await _config_out(db, body.text)


@config_router.put("", response_model=SshConfigOut)
async def save_config(body: SshConfigIn, db: DB, user: Admin):
    out = await _config_out(db, body.text)
    if out.errors:
        raise HTTPException(422, "; ".join(out.errors))
    row = await db.get(m.AppSetting, ssh_config.SETTING)
    if row is None:
        db.add(m.AppSetting(key=ssh_config.SETTING, value=body.text, updated_by=user.id))
    else:
        row.value, row.updated_by = body.text, user.id
    await record_activity(db, type="ssh_config.changed", message="SSH config changed", project_id=None,
                          actor_type="user", actor_id=str(user.id), actor_name=user.username)
    hosts = (await db.execute(select(m.SshHost.id, m.SshHost.agent_id))).all()
    await db.commit()  # before reconnecting: a link writes too, and SQLite has one writer
    for host_id, agent_id in hosts:  # reconnect each the new way
        await ssh_hosts.reload(host_id, agent_id)
    bus.publish("ssh_hosts.changed", None, {"config": True})
    return out
