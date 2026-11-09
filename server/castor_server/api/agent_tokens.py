"""Per-agent tokens: create (enroll), rotate, revoke. A token is returned only once."""

from __future__ import annotations

import re
import shlex

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from .. import agent_tokens
from .. import models as m
from ..config import settings
from ..deps import DB, Admin, parse_uuid
from ..events import bus
from ..models import utcnow
from ..orchestrator.gateway import gateway
from ..schemas import AgentTokenCreated, AgentTokenIn, AgentTokenList, AgentTokenOut
from ..services import actor_of, record_activity

router = APIRouter(prefix="/agent-tokens", tags=["agents"])


async def _out(db: DB, row: m.AgentToken, token: str | None = None) -> AgentTokenOut:
    agent = await db.get(m.Agent, row.agent_id) if row.agent_id else None
    data = {**AgentTokenOut.model_validate(row).model_dump(), "agent_name": agent.name if agent else None}
    return AgentTokenCreated(**data, token=token, command=_command(row, agent, token)) if token else AgentTokenOut(**data)


def _command(row: m.AgentToken, agent: m.Agent | None, token: str) -> str:
    base = settings.public_url.rstrip("/") or "http://<castor-host>"
    server = re.sub(r"^http", "ws", base) + "/ws/agent"
    name = shlex.quote(agent.name if agent else (row.label or "<agent-name>"))
    tags = f" --tags {shlex.quote(','.join(row.tags))}" if row.tags and not agent else ""
    return f"castor-agent --server {server} --token {token} --name {name}{tags}"


async def _publish_agent(db: DB, agent_id) -> None:
    agent = await db.get(m.Agent, agent_id) if agent_id else None
    if agent is not None:
        bus.publish("agent.updated", None, await gateway.agent_out(agent))


async def _get(db: DB, token_id: str) -> m.AgentToken:
    row = await db.get(m.AgentToken, parse_uuid(token_id))
    if row is None:
        raise HTTPException(404, "Token not found")
    return row


@router.get("", response_model=AgentTokenList)
async def list_tokens(db: DB, _: Admin):
    rows = (await db.execute(select(m.AgentToken).order_by(m.AgentToken.created_at.desc()))).scalars().all()
    return AgentTokenList(
        tokens=[await _out(db, r) for r in rows],
        shared_secret_allowed=settings.allow_shared_agent_secret and bool(settings.agent_secret),
    )


@router.post("", response_model=AgentTokenCreated, status_code=201)
async def create_token(body: AgentTokenIn, db: DB, user: Admin):
    """An enrollment token: the first agent that connects with it becomes its agent."""
    token, digest, prefix = agent_tokens.new_token()
    row = m.AgentToken(
        label=body.label.strip(),
        token_hash=digest,
        prefix=prefix,
        tags=sorted({t.strip() for t in body.tags if t.strip()}),
        created_by=user.id,
    )
    db.add(row)
    await db.flush()
    await record_activity(
        db,
        type="agent_token.created",
        message=f"Created agent token {prefix}…" + (f" for {row.label}" if row.label else ""),
        project_id=None,
        payload={"token_id": str(row.id)},
        **actor_of(user),
    )
    await db.commit()
    bus.publish("agent_tokens.changed", None, {"id": row.id})
    return await _out(db, row, token)


@router.post("/{token_id}/rotate", response_model=AgentTokenCreated)
async def rotate_token(token_id: str, db: DB, user: Admin):
    """Replace the token; the old one stops working and its agent is disconnected."""
    row = await _get(db, token_id)
    if row.revoked_at is not None:
        raise HTTPException(409, "The token was revoked; create a new one")
    token, digest, prefix = agent_tokens.new_token()
    row.token_hash, row.prefix, row.rotated_at = digest, prefix, utcnow()
    agent = await db.get(m.Agent, row.agent_id) if row.agent_id else None
    await record_activity(
        db,
        type="agent_token.rotated",
        message=f"Rotated the token of {agent.name if agent else row.label or 'an unused token'} (now {prefix}…)",
        project_id=None,
        payload={"token_id": str(row.id)},
        **actor_of(user),
    )
    await db.commit()
    bus.publish("agent_tokens.changed", None, {"id": row.id})
    if agent is not None:
        await gateway.disconnect(agent.id, "token rotated")
        await _publish_agent(db, agent.id)
    return await _out(db, row, token)


@router.post("/{token_id}/revoke", response_model=AgentTokenOut)
async def revoke_token(token_id: str, db: DB, user: Admin):
    """Stop the token working now. Its agent is disconnected and can't reconnect with it
    (nor with the shared secret, until it's given a new token or forgotten)."""
    row = await _get(db, token_id)
    if row.revoked_at is None:
        row.revoked_at = utcnow()
        agent = await db.get(m.Agent, row.agent_id) if row.agent_id else None
        await record_activity(
            db,
            type="agent_token.revoked",
            message=f"Revoked token {row.prefix}…" + (f" of {agent.name}" if agent else ""),
            project_id=None,
            payload={"token_id": str(row.id)},
            **actor_of(user),
        )
        await db.commit()
        bus.publish("agent_tokens.changed", None, {"id": row.id})
        if agent is not None:
            await gateway.disconnect(agent.id, "token revoked")
            await _publish_agent(db, agent.id)
    return await _out(db, row)


@router.delete("/{token_id}", status_code=204)
async def delete_token(token_id: str, db: DB, _: Admin):
    """Remove a token that was revoked or never used."""
    row = await _get(db, token_id)
    if row.revoked_at is None and row.agent_id is not None:
        raise HTTPException(409, "Revoke the token first")
    await db.delete(row)
    await db.commit()
    bus.publish("agent_tokens.changed", None, {"id": row.id})
