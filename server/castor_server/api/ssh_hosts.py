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
from .. import vault
from ..deps import DB, Admin, parse_uuid
from ..events import bus
from ..orchestrator.gateway import gateway
from ..orchestrator.ssh import fingerprint, generate_key, import_key, ssh_hosts
from ..services import record_activity

router = APIRouter(prefix="/ssh-hosts", tags=["ssh hosts"])

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class SshHostIn(BaseModel):
    name: str
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=128)
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
    port: int
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
    # connecting | connected | error | host_key_changed | disabled
    state: str
    error: str | None
    connected: bool


def _out(row: m.SshHost, agent: m.Agent) -> SshHostOut:
    link = ssh_hosts.links.get(row.id)
    state = link.state if link else ("disabled" if not row.enabled else "connecting")
    return SshHostOut(
        id=row.id, agent_id=row.agent_id, name=agent.name, host=row.host, port=row.port, username=row.username,
        workdir=row.workdir, network=row.network, allow_host=row.allow_host, allow_host_install=row.allow_host_install,
        enabled=row.enabled, tags=agent.tags or [], max_concurrency=agent.max_concurrency, public_key=row.public_key,
        key_fingerprint=fingerprint(row.public_key), host_key_fingerprint=fingerprint(row.host_key),
        host_key_pending_fingerprint=fingerprint(row.host_key_pending), state=state,
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
    if body.host.startswith("-") or body.username.startswith("-"):
        raise HTTPException(422, "host and username can't start with a dash")


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
    return [_out(row, agent) for row, agent in rows]


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
    return _out(row, agent)


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
    return _out(row, agent)


@router.post("/{host_id}/accept-host-key", response_model=SshHostOut)
async def accept_host_key(host_id: str, db: DB, user: Admin):
    """Trust the different key the host presented (after checking it's the right machine)."""
    row, agent = await _row(db, host_id)
    if not row.host_key_pending:
        raise HTTPException(409, "No new host key to accept")
    old = fingerprint(row.host_key)
    row.host_key, row.host_key_pending = row.host_key_pending, None
    await record_activity(db, type="ssh_host.key_accepted",
                          message=f"New host key for {agent.name} accepted ({fingerprint(row.host_key)}, was {old})",
                          project_id=None, actor_type="user", actor_id=str(user.id), actor_name=user.username)
    await db.commit()
    await ssh_hosts.reload(row.id, agent.id)
    _changed(row)
    return _out(row, agent)


@router.post("/{host_id}/reconnect", response_model=SshHostOut)
async def reconnect(host_id: str, db: DB, _: Admin):
    """Try to connect now instead of waiting for the next retry (e.g. after adding the key)."""
    row, agent = await _row(db, host_id)
    link = ssh_hosts.links.get(row.id)
    if link is None or link.state == "connected":
        await ssh_hosts.reload(row.id, agent.id)
    else:
        link.retry_now()
    return _out(row, agent)


@router.delete("/{host_id}", status_code=204)
async def delete_host(host_id: str, db: DB, user: Admin):
    """Disconnect and forget the host (its agent too; jobs it ran keep their output)."""
    row, agent = await _row(db, host_id)
    await ssh_hosts.remove(row.id)
    agent_id, name = agent.id, agent.name
    await db.delete(row)
    await db.delete(agent)
    await record_activity(db, type="ssh_host.removed", message=f"SSH host {name} removed", project_id=None,
                          actor_type="user", actor_id=str(user.id), actor_name=user.username)
    await db.commit()
    bus.publish("agent.deleted", None, {"id": agent_id})
    bus.publish("ssh_hosts.changed", None, {"id": row.id})
