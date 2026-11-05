from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from .. import models as m
from ..deps import DB, Admin, CurrentUser, parse_uuid
from ..events import bus
from ..orchestrator.gateway import gateway
from ..schemas import AgentOut, AgentPatch

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("", response_model=list[AgentOut])
async def list_agents(db: DB, _: CurrentUser):
    agents = (await db.execute(select(m.Agent).order_by(m.Agent.name))).scalars().all()
    return [await gateway.agent_out(a) for a in agents]


@router.patch("/{agent_id}", response_model=AgentOut)
async def update_agent(agent_id: str, body: AgentPatch, db: DB, _: Admin):
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
    await db.commit()
    out = await gateway.agent_out(agent)
    bus.publish("agent.updated", None, out)
    return out


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(agent_id: str, db: DB, _: Admin):
    agent = await db.get(m.Agent, parse_uuid(agent_id))
    if agent is None:
        raise HTTPException(404, "Agent not found")
    if gateway.get(agent.id) is not None:
        raise HTTPException(409, "Agent is connected; stop it first")
    await db.delete(agent)
    await db.commit()
    bus.publish("agent.deleted", None, {"id": agent.id})
