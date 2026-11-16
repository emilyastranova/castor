"""Agent types from plugins (plugin_agents.py): what plugins offer, the machines each reaches, and
bringing the chosen ones in as agents. Admins only, like enrolling agents and adding SSH hosts."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .. import models as m
from ..deps import DB, Admin
from ..events import bus
from ..orchestrator.gateway import gateway
from ..plugin_agents import PluginAgentError, manager
from ..plugins import manifest_of
from ..schemas import AgentOut
from ..services import actor_of, existing_projects, record_activity
from .plugins import icon_url

router = APIRouter(tags=["agents"])


async def _type_or_404(db: DB, plugin_id: str, agent_type: str) -> tuple[m.Plugin, Any]:
    p = await db.get(m.Plugin, plugin_id)
    spec = next((t for t in manifest_of(p).agent_types if t.name == agent_type), None) if p is not None else None
    if p is None or spec is None:
        raise HTTPException(404, "No such agent type")
    return p, spec


@router.get("/plugin-agent-types")
async def list_types(db: DB, _: Admin) -> list[dict[str, Any]]:
    """Every agent type installed plugins add, with whether its system answered and how many of its
    machines are agents."""
    counts = dict((await db.execute(select(m.Agent.plugin_id + ":" + m.Agent.agent_type, func.count())
                                    .where(m.Agent.plugin_id.is_not(None)).group_by(m.Agent.plugin_id, m.Agent.agent_type))).all())
    out = []
    for p in (await db.execute(select(m.Plugin).order_by(m.Plugin.name))).scalars():
        for t in manifest_of(p).agent_types:
            s = manager.status.get((p.id, t.name), {})
            out.append({
                "plugin": {"id": p.id, "name": p.name, "enabled": p.enabled, "icon_url": icon_url(p)},
                "name": t.name, "label": t.label, "description": t.description, "icon": t.icon, "containers": t.containers,
                "agents": counts.get(f"{p.id}:{t.name}", 0),
                "status": {"ok": s.get("ok"), "message": s.get("message") or "", "checked_at": s.get("checked_at"),
                           "machines": s.get("machines")},
            })
    return out


@router.get("/plugin-agent-types/{plugin_id}/{agent_type}/machines")
async def machines(plugin_id: str, agent_type: str, db: DB, _: Admin) -> dict[str, Any]:
    """The machines the type reaches now (asked of the plugin), each with the agent it already is."""
    p, _spec = await _type_or_404(db, plugin_id, agent_type)
    if not p.enabled:
        raise HTTPException(409, f"{p.name} is turned off")
    try:
        found = await manager.machines(plugin_id, agent_type)
    except PluginAgentError as exc:
        raise HTTPException(502, f"{p.name} couldn't list its machines: {exc}") from exc
    have = dict((await db.execute(select(m.Agent.machine_ref, m.Agent.id).where(m.Agent.plugin_id == plugin_id,
                                                                               m.Agent.agent_type == agent_type))).all())
    return {"machines": [{**x, "agent_id": str(have[x["id"]]) if x["id"] in have else None} for x in found]}


class ImportIn(BaseModel):
    machines: list[str] = Field(min_length=1, max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=50)
    # Limit them to these projects' jobs; null: any project.
    project_ids: list[uuid.UUID] | None = None


@router.post("/plugin-agent-types/{plugin_id}/{agent_type}/import", response_model=list[AgentOut], status_code=201)
async def import_machines(plugin_id: str, agent_type: str, body: ImportIn, db: DB, user: Admin):
    """Bring machines in as agents (ones already in are skipped). They connect while the plugin runs
    and lists them; their tags are yours plus the ones their system gives them."""
    p, spec = await _type_or_404(db, plugin_id, agent_type)
    if not p.enabled:
        raise HTTPException(409, f"{p.name} is turned off")
    tags = sorted({t.strip() for t in body.tags if t.strip()})
    try:
        made = await manager.import_machines(db, p, agent_type, body.machines, tags=tags,
                                             project_ids=await existing_projects(db, body.project_ids))
    except PluginAgentError as exc:
        raise HTTPException(422, str(exc)) from exc
    if made:
        names = ", ".join(a.name for a in made[:5]) + (f" and {len(made) - 5} more" if len(made) > 5 else "")
        await record_activity(db, type="agent.imported", message=f"Added {len(made)} {spec.label} agent{'s' if len(made) != 1 else ''} "
                                                                 f"from {p.name}: {names}", project_id=None, **actor_of(user))
    await db.commit()
    await manager.sync(plugin_id)
    out = []
    for a in made:
        await db.refresh(a)
        o = await gateway.agent_out(a)
        bus.publish("agent.updated", None, o)
        out.append(o)
    return out


@router.post("/plugin-agent-types/{plugin_id}/refresh")
async def refresh(plugin_id: str, db: DB, _: Admin) -> dict[str, Any]:
    """Ask the plugin for its machines now: tags and hostnames updated, the ones it lists connected."""
    p = await db.get(m.Plugin, plugin_id)
    if p is None:
        raise HTTPException(404, "No such plugin")
    await manager.sync(plugin_id)
    for a in (await db.execute(select(m.Agent).where(m.Agent.plugin_id == plugin_id))).scalars():
        await db.refresh(a)
        bus.publish("agent.updated", None, await gateway.agent_out(a))
    return {t.name: manager.status.get((plugin_id, t.name), {}) for t in manifest_of(p).agent_types}
