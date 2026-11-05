"""Tool library: definitions, which agents have each tool ready, and preparing tools on
agents ahead of time."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import models as m
from ..deps import DB, CurrentUser, Operator
from ..events import bus
from ..orchestrator.dispatcher import tool_ready, tool_way
from ..orchestrator.gateway import gateway
from ..tool_library import definition_from_spec, tools_in_use
from ..tool_spec import ToolSpec, parse_tool_spec

router = APIRouter(prefix="/tools", tags=["tools"])


class ToolYamlIn(BaseModel):
    yaml: str


class AgentToolState(BaseModel):
    agent_id: str
    agent_name: str
    # How it would get the tool (image / build / host), or None if it can't.
    way: str | None
    # build: the recipe's image is cached there; image/host: unknown until a job or prepare.
    ready: bool | None
    # The last "prepare" result, if any: preparing / ready / failed.
    state: str | None = None
    error: str | None = None
    at: datetime | None = None


class ToolOut(BaseModel):
    id: str
    origin: str
    name: str
    description: str
    spec: dict[str, Any]
    source_yaml: str
    version: int
    used_by: list[str] = Field(default_factory=list)
    recipe_tag: str | None = None
    agents: list[AgentToolState] = Field(default_factory=list)


class PrepareIn(BaseModel):
    agent_ids: list[str] = Field(default_factory=list)  # empty: every connected agent that can


class PrepareOut(BaseModel):
    started: list[str]
    skipped: dict[str, str]


async def _used_by(db: DB) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for cid, spec in (await db.execute(select(m.CommandDefinition.id, m.CommandDefinition.spec))).all():
        if spec.get("tool"):
            out.setdefault(spec["tool"], []).append(cid)
    return out


def _agents(spec: ToolSpec) -> list[AgentToolState]:
    states = []
    for conn in sorted(gateway.connections.values(), key=lambda c: c.name):
        way = tool_way(conn, spec)
        status = gateway.tool_status.get((conn.agent_id, spec.id)) or {}
        ready = (tool_ready(conn, spec) if way and way["kind"] == "build" else None) if way else False
        if status.get("state") == "ready":
            ready = True
        states.append(AgentToolState(
            agent_id=str(conn.agent_id), agent_name=conn.name, way=way["kind"] if way else None,
            ready=ready, state=status.get("state"), error=status.get("error"), at=status.get("at"),
        ))
    return states


def _out(row: m.ToolDefinition, used_by: dict[str, list[str]]) -> ToolOut:
    spec = ToolSpec.model_validate(row.spec)
    return ToolOut(
        id=row.id, origin=row.origin, name=row.name, description=row.description, spec=row.spec,
        source_yaml=row.source_yaml, version=row.version, used_by=sorted(used_by.get(row.id, [])),
        recipe_tag=spec.build_tag() if spec.build else None, agents=_agents(spec),
    )


def _parse_or_422(text: str) -> ToolSpec:
    try:
        return parse_tool_spec(text)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("", response_model=list[ToolOut])
async def list_tools(db: DB, _: CurrentUser):
    rows = (await db.execute(select(m.ToolDefinition).order_by(m.ToolDefinition.name))).scalars().all()
    used = await _used_by(db)
    return [_out(r, used) for r in rows]


@router.get("/{tool_id}", response_model=ToolOut)
async def get_tool(tool_id: str, db: DB, _: CurrentUser):
    row = await db.get(m.ToolDefinition, tool_id)
    if row is None:
        raise HTTPException(404, "Tool not found")
    return _out(row, await _used_by(db))


@router.post("/validate")
async def validate_tool(body: ToolYamlIn, _: CurrentUser):
    try:
        spec = parse_tool_spec(body.yaml)
    except ValueError as exc:
        return {"ok": False, "errors": str(exc).split("; ")}
    return {"ok": True, "errors": [], "recipe_tag": spec.build_tag() if spec.build else None,
            "dockerfile": spec.build_files()["Dockerfile"] if spec.build else None}


@router.post("", response_model=ToolOut, status_code=201)
async def create_tool(body: ToolYamlIn, db: DB, _: Operator):
    spec = _parse_or_422(body.yaml)
    if await db.get(m.ToolDefinition, spec.id) is not None:
        raise HTTPException(409, f"A tool with id “{spec.id}” already exists")
    row = m.ToolDefinition(id=spec.id, origin="user", version=1)
    definition_from_spec(row, spec, body.yaml)
    db.add(row)
    await db.commit()
    bus.publish("tools.changed", None, {"id": spec.id})
    return _out(row, await _used_by(db))


@router.put("/{tool_id}", response_model=ToolOut)
async def update_tool(tool_id: str, body: ToolYamlIn, db: DB, _: Operator):
    row = await db.get(m.ToolDefinition, tool_id)
    if row is None:
        raise HTTPException(404, "Tool not found")
    if row.origin == "builtin":
        raise HTTPException(409, "Built-in tools are read-only; duplicate it to customise")
    spec = _parse_or_422(body.yaml)
    if spec.id != tool_id:
        raise HTTPException(422, "id: cannot be changed (create a new tool instead)")
    definition_from_spec(row, spec, body.yaml)
    await db.commit()
    bus.publish("tools.changed", None, {"id": tool_id})
    return _out(row, await _used_by(db))


@router.delete("/{tool_id}", status_code=204)
async def delete_tool(tool_id: str, db: DB, _: Operator):
    row = await db.get(m.ToolDefinition, tool_id)
    if row is None:
        raise HTTPException(404, "Tool not found")
    if row.origin == "builtin":
        raise HTTPException(409, "Built-in tools cannot be deleted")
    if tool_id in await tools_in_use(db):
        raise HTTPException(409, "Commands use this tool; change them first")
    await db.delete(row)
    await db.commit()
    bus.publish("tools.changed", None, {"id": tool_id})


@router.post("/{tool_id}/prepare", response_model=PrepareOut)
async def prepare_tool(tool_id: str, body: PrepareIn, db: DB, _: Operator):
    """Pull/build/check the tool on agents now, so jobs don't wait for it later."""
    row = await db.get(m.ToolDefinition, tool_id)
    if row is None:
        raise HTTPException(404, "Tool not found")
    spec = ToolSpec.model_validate(row.spec)
    wanted = set(body.agent_ids)
    started, skipped = [], {}
    for conn in gateway.connections.values():
        if wanted and str(conn.agent_id) not in wanted:
            continue
        way = tool_way(conn, spec)
        if way is None:
            skipped[conn.name] = "can't run this tool (needs " + " or ".join(spec.needs()) + ")"
            continue
        await gateway.prepare_tool(conn, way)
        started.append(conn.name)
    return PrepareOut(started=started, skipped=skipped)
