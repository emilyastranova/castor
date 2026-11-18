from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from .. import models as m
from ..access import visible_project_ids
from ..deps import DB, Admin, CurrentUser, parse_uuid
from ..events import bus
from ..orchestrator.gateway import gateway
from ..schemas import AgentOut, AgentPatch
from ..services import actor_of, existing_projects, record_activity

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("", response_model=list[AgentOut])
async def list_agents(db: DB, user: CurrentUser):
    """Every agent for admins; for others, the ones that may run work in a project they can see."""
    agents = (await db.execute(select(m.Agent).order_by(m.Agent.name))).scalars().all()
    visible = await visible_project_ids(db, user)
    if visible is not None:
        mine = {str(p) for p in visible}
        agents = [a for a in agents if a.project_ids is None or mine & set(a.project_ids)]
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
    message = f"Changed the agent {agent.name}"
    if body.segment is not None and body.segment.strip() != (agent.segment or ""):
        agent.segment = body.segment.strip()
        message = f"Put the agent {agent.name} in the segment “{agent.segment}”" if agent.segment else f"Took the agent {agent.name} out of its segment"
    if body.route is not None and (body.route or None) != (agent.route or None):
        from .. import network_routes

        if agent.plugin_id or (await db.execute(select(m.SshHost.id).where(m.SshHost.agent_id == agent.id))).first():
            raise HTTPException(422, "Only agents that connect to Castor can have a route (not SSH hosts or plugin machines)")
        row = await network_routes.get(db, body.route) if body.route else None
        if body.route and row is None:
            raise HTTPException(422, "route: no such route")
        if row is not None and not network_routes.allows(row, agent.id, agent.tags):
            raise HTTPException(422, f"route: “{row.name}” only works from {network_routes.agents_words(row)}, and {agent.name} "
                                     "isn't one of them")
        agent.route = str(row.id) if row else None
        message = (f"Sent everything the agent {agent.name} runs through route “{row.name}”" if row
                   else f"Took the agent {agent.name} off its route")
    if "project_ids" in body.model_fields_set:  # null is a value here: any project
        agent.project_ids = await existing_projects(db, body.project_ids)
        if conn := gateway.get(agent.id):
            conn.project_ids = list(agent.project_ids) if agent.project_ids is not None else None
        if agent.project_ids is None:
            message = f"Let the agent {agent.name} run any project's jobs"
        else:
            names = (await db.execute(select(m.Project.codename).where(m.Project.id.in_([uuid.UUID(p) for p in agent.project_ids])))).scalars().all()
            message = f"Limited the agent {agent.name} to {', '.join(sorted(names)) or 'no projects'}"
    if "people" in body.model_fields_set:  # null is a value here: everyone
        from .. import agent_people

        given = body.people.model_dump(mode="json") if body.people is not None else None
        try:
            agent.people = await agent_people.check(db, given, agent.people)
        except agent_people.PeopleError as exc:
            raise HTTPException(422, str(exc)) from exc
        if conn := gateway.get(agent.id):
            conn.people = agent.people
        message = (f"Let everyone use the agent {agent.name}" if agent.people is None
                   else f"Limited the agent {agent.name} to {await agent_people.words(db, agent.people)}")
    await record_activity(db, type="agent.updated", message=message, project_id=None, **actor_of(user))
    await db.commit()
    out = await gateway.agent_out(agent)
    bus.publish("agent.updated", None, out)
    from ..orchestrator.dispatcher import dispatcher

    dispatcher.wake()  # queued jobs it may now run (or no longer)
    return out


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(agent_id: str, db: DB, user: Admin):
    agent = await db.get(m.Agent, parse_uuid(agent_id))
    if agent is None:
        raise HTTPException(404, "Agent not found")
    if await db.scalar(select(m.SshHost.id).where(m.SshHost.agent_id == agent.id)):
        raise HTTPException(409, "This is an SSH host: remove it as one (DELETE /api/ssh-hosts/{id})")
    if agent.plugin_id:  # a plugin's machine: let it go here (its system keeps running it)
        from ..plugin_agents import manager

        link = manager.links.pop(agent.id, None)
        if link is not None:
            await link.disconnect()
    elif gateway.get(agent.id) is not None:
        raise HTTPException(409, "Agent is connected; stop it first")
    await db.delete(agent)
    await record_activity(db, type="agent.deleted", message=f"Removed the agent {agent.name}", project_id=None, **actor_of(user))
    await db.commit()
    bus.publish("agent.deleted", None, {"id": agent.id})
