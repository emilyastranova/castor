from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from .. import models as m
from ..deps import DB, Admin, CurrentUser, parse_uuid
from ..events import bus
from ..orchestrator.gateway import gateway
from ..schemas import AgentOut, AgentPatch
from ..services import actor_of, record_activity

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("", response_model=list[AgentOut])
async def list_agents(db: DB, _: CurrentUser):
    agents = (await db.execute(select(m.Agent).order_by(m.Agent.name))).scalars().all()
    return [await gateway.agent_out(a) for a in agents]


@router.patch("/{agent_id}", response_model=AgentOut)
async def update_agent(agent_id: str, body: AgentPatch, db: DB, user: Admin):
    agent = await db.get(m.Agent, parse_uuid(agent_id))
    if agent is None:
        raise HTTPException(404, "Agent not found")
    if body.tags is not None:
        agent.tags = sorted(set(body.tags))
        conn = gateway.get(agent.id)
        if conn:
            conn.tags = agent.tags
    if body.max_concurrency is not None:
        agent.max_concurrency = max(1, body.max_concurrency)
        conn = gateway.get(agent.id)
        if conn:
            conn.max_concurrency = agent.max_concurrency
    await record_activity(db, type="agent.updated", message=f"Changed the agent {agent.name}", project_id=None, **actor_of(user))
    await db.commit()
    out = await gateway.agent_out(agent)
    bus.publish("agent.updated", None, out)
    return out


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(agent_id: str, db: DB, user: Admin):
    agent = await db.get(m.Agent, parse_uuid(agent_id))
    if agent is None:
        raise HTTPException(404, "Agent not found")
    if await db.scalar(select(m.SshHost.id).where(m.SshHost.agent_id == agent.id)):
        raise HTTPException(409, "This is an SSH host: remove it as one (DELETE /api/ssh-hosts/{id})")
    if gateway.get(agent.id) is not None:
        raise HTTPException(409, "Agent is connected; stop it first")
    await db.delete(agent)
    await record_activity(db, type="agent.deleted", message=f"Removed the agent {agent.name}", project_id=None, **actor_of(user))
    await db.commit()
    bus.publish("agent.deleted", None, {"id": agent.id})
