"""MCP servers Castor calls tools on (Settings → MCP servers; docs/ACTIONS.md phase 3). Admins
add and change them; anyone who writes library definitions can see them and their tools, and
draft an MCP action from a tool."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select

from .. import mcp_client, mcp_connections, variables
from .. import models as m
from ..deps import DB, Admin, CurrentUser, Operator
from ..services import actor_of, record_activity

router = APIRouter(prefix="/mcp-connections", tags=["mcp"])


class ConnectionIn(BaseModel):
    id: str = Field(min_length=1, max_length=63)
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    transport: Literal["http", "stdio"] = "http"
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)
    variable_set_id: uuid.UUID | None = None
    command: list[str] = Field(default_factory=list)
    tool: str | None = None
    image: str | None = None
    agent_selector: dict[str, Any] = Field(default_factory=dict)
    idle_s: int = Field(300, ge=10, le=86400)
    project_ids: list[str] | None = None
    enabled: bool = True

    @field_validator("id")
    @classmethod
    def check_id(cls, value: str) -> str:
        if not mcp_connections.ID.match(value):
            raise ValueError("lowercase letters, digits and dashes (definitions name it in mcp.server)")
        return value

    @field_validator("headers", "env")
    @classmethod
    def names(cls, value: dict[str, str]) -> dict[str, str]:
        for name in value:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", name):
                raise ValueError(f"{name!r} isn't a header or variable name")
        return value

    @model_validator(mode="after")
    def fits(self) -> ConnectionIn:
        if self.transport == "http":
            if not re.match(r"^https?://[^\s/]+", self.url.strip()):
                raise ValueError("url: an http:// or https:// address")
        else:
            if self.tool and self.image:
                raise ValueError("give a tool or an image, not both")
            if not (self.command or self.tool or self.image):
                raise ValueError("command: what to run (or a tool or image whose entrypoint is the server)")
        return self


class ConnectionOut(BaseModel):
    id: str
    name: str
    description: str
    transport: str
    url: str
    headers: dict[str, str]
    env: dict[str, str]
    variable_set_id: uuid.UUID | None
    variable_set: str | None = None
    command: list[str]
    tool: str | None
    image: str | None
    agent_selector: dict[str, Any]
    idle_s: int
    project_ids: list[str] | None
    enabled: bool
    server_info: dict[str, Any] | None
    tools: list[dict[str, Any]]
    checked_at: datetime | None
    last_error: str | None
    # Definitions (kind: mcp) that call it.
    used_by: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


async def _out(db, row: m.McpConnection) -> ConnectionOut:
    vset = await db.get(m.VariableSet, row.variable_set_id) if row.variable_set_id else None
    defs = (await db.execute(select(m.CommandDefinition.id, m.CommandDefinition.spec))).all()
    used = sorted(i for i, spec in defs if (spec or {}).get("kind") == "mcp" and ((spec or {}).get("mcp") or {}).get("server") == row.id)
    return ConnectionOut(
        **{k: getattr(row, k) for k in ConnectionOut.model_fields if hasattr(row, k) and k not in ("variable_set", "used_by")},
        variable_set=vset.name if vset else None, used_by=used,
    )


async def _check(db, body: ConnectionIn) -> None:
    fields: set[str] | None = set()
    if body.variable_set_id is not None:
        vset = await db.get(m.VariableSet, body.variable_set_id)
        if vset is None or vset.ephemeral:
            raise HTTPException(422, "variable_set_id: no such variable set")
        kind = await db.get(m.VariableKind, vset.kind_id)
        fields = {f.name for f in variables.kind_spec(kind).fields if not f.is_file} if kind else set()
    unknown = mcp_connections.check_templates(body.headers if body.transport == "http" else body.env, fields)
    if unknown:
        where = "the variable set" if body.variable_set_id else "a variable set (choose one)"
        raise HTTPException(422, f"{'headers' if body.transport == 'http' else 'env'}: {{{{ {unknown[0]} }}}} isn't a field of {where}")
    if body.tool and await db.get(m.ToolDefinition, body.tool) is None:
        raise HTTPException(422, f"tool: no tool {body.tool} in the library")


async def _get(db, connection_id: str) -> m.McpConnection:
    row = await db.get(m.McpConnection, connection_id)
    if row is None:
        raise HTTPException(404, "MCP server not found")
    return row


@router.get("", response_model=list[ConnectionOut])
async def list_connections(db: DB, _: CurrentUser):
    rows = (await db.execute(select(m.McpConnection).order_by(m.McpConnection.name))).scalars().all()
    return [await _out(db, r) for r in rows]


@router.get("/{connection_id}", response_model=ConnectionOut)
async def get_connection(connection_id: str, db: DB, _: CurrentUser):
    return await _out(db, await _get(db, connection_id))


@router.post("", response_model=ConnectionOut, status_code=201)
async def create_connection(body: ConnectionIn, db: DB, user: Admin):
    if await db.get(m.McpConnection, body.id) is not None:
        raise HTTPException(409, f"An MCP server with id “{body.id}” already exists")
    await _check(db, body)
    row = m.McpConnection(**body.model_dump())
    db.add(row)
    await record_activity(db, type="mcp.connection_created", message=f"Added the MCP server {row.name}", project_id=None, **actor_of(user))
    await db.commit()
    return await _out(db, row)


@router.put("/{connection_id}", response_model=ConnectionOut)
async def update_connection(connection_id: str, body: ConnectionIn, db: DB, user: Admin):
    row = await _get(db, connection_id)
    if body.id != connection_id:
        raise HTTPException(422, "id: can't be changed (definitions name it)")
    await _check(db, body)
    for key, value in body.model_dump().items():
        setattr(row, key, value)
    mcp_client.hub.forget(connection_id)
    await record_activity(db, type="mcp.connection_updated", message=f"Changed the MCP server {row.name}", project_id=None, **actor_of(user))
    await db.commit()
    return await _out(db, row)


@router.delete("/{connection_id}", status_code=204)
async def delete_connection(connection_id: str, db: DB, user: Admin):
    row = await _get(db, connection_id)
    mcp_client.hub.forget(connection_id)
    await db.delete(row)
    await record_activity(db, type="mcp.connection_deleted", message=f"Removed the MCP server {row.name}", project_id=None, **actor_of(user))
    await db.commit()


@router.post("/{connection_id}/check", response_model=ConnectionOut)
async def check_connection(connection_id: str, db: DB, _: Admin):
    """Connect, and read its name and tools (stdio: an agent starts it first)."""
    row = await _get(db, connection_id)
    await mcp_connections.check(db, row)
    await db.commit()
    return await _out(db, row)


class Draft(BaseModel):
    yaml: str


@router.get("/{connection_id}/tools/{tool}/draft", response_model=Draft)
async def draft(connection_id: str, tool: str, db: DB, _: Operator):
    """A kind: mcp definition for one of its tools (from the tools last read), to edit and save."""
    row = await _get(db, connection_id)
    found = next((t for t in row.tools or [] if t.get("name") == tool), None)
    if found is None:
        raise HTTPException(404, f"{row.name} has no tool {tool} (check it again in Settings → MCP servers)")
    return Draft(yaml=mcp_connections.draft_definition(row, found))
