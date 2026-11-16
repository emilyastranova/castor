"""MCP tools from plugins (manifest `mcp_tools`, answered by `ctx.mcp.on`): the tools a caller may use,
and calling one. Castor's MCP server (mcp.py) lists them as `<plugin>__<tool>` and calls them
through here, so they get the same checks as anything else: the plugin enabled in the project, the
tool approved by an admin, read access for a read-only tool and write access otherwise, the
plugin's rate limit, and the feed for anything that isn't read-only. What a plugin answers is its
own: the MCP server marks it untrusted."""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import api_tokens
from .. import models as m
from .. import plugins as P
from ..access import project_or_404, visible_project_ids
from ..deps import DB, CurrentUser
from ..plugin_manifest import mcp_tool_name
from ..plugin_runtime import PluginRuntimeError

router = APIRouter(tags=["plugins"])

MAX_RESULT = 200_000  # characters of a tool's answer


def _approved(p: m.Plugin) -> set[str]:
    return set((p.granted or {}).get("mcp_tools") or [])


@router.get("/plugin-tools")
async def list_tools(db: DB, user: CurrentUser) -> list[dict[str, Any]]:
    """The MCP tools plugins add that this person can use: from plugins that are on, in projects
    they can see, approved by an admin. `project_ids`: where each can be called."""
    if P.safe_mode():
        return []
    visible = await visible_project_ids(db, user)
    out = []
    for p in (await db.execute(select(m.Plugin).where(m.Plugin.enabled.is_(True)).order_by(m.Plugin.id))).scalars():
        man = P.manifest_of(p)
        tools = [t for t in man.mcp_tools if t.name in _approved(p)]
        if not tools:
            continue
        if p.all_projects:
            where = None if visible is None else sorted(str(i) for i in visible)
        else:
            mine = await P.project_ids(db, p.id)
            where = sorted(str(i) for i in mine if visible is None or i in visible)
            if not where:
                continue
        for t in tools:
            out.append({"name": mcp_tool_name(p.id, t.name), "plugin": p.id, "plugin_name": p.name, "tool": t.name,
                        "description": t.description, "input_schema": t.input_schema, "read_only": t.read_only,
                        "destructive": t.destructive, "project_ids": where})
    return out


class CallIn(BaseModel):
    project_id: uuid.UUID
    arguments: dict[str, Any] = Field(default_factory=dict)


@router.post("/plugin-tools/{plugin_id}/{tool}")
async def call_tool(plugin_id: str, tool: str, body: CallIn, db: DB, user: CurrentUser) -> dict[str, Any]:
    """Call a plugin's MCP tool in a project; `result` is what the plugin answered (untrusted)."""
    from ..rate_limits import Scope, _rate, limiter
    from ..services import actor_of, record_activity

    p = await db.get(m.Plugin, plugin_id)
    spec = P.manifest_of(p).mcp_tool(tool) if p is not None else None
    if p is None or spec is None or not p.enabled or tool not in _approved(p) or P.safe_mode():
        raise HTTPException(404, "No such tool")
    if len(json.dumps(body.arguments)) > 64_000:
        raise HTTPException(422, "arguments: too large")
    project = await project_or_404(db, user, body.project_id, write=not spec.read_only)
    if not await P.enabled_in(db, p, project.id):
        raise HTTPException(404, f"{p.name} isn't on in this project")
    if not spec.read_only:
        token = getattr(user, "api_token", None)
        if user.role == m.UserRole.viewer or (token is not None and not api_tokens.grants(token.scopes or [], "projects:write")):
            raise HTTPException(403, f"{mcp_tool_name(p.id, tool)} changes things: it needs someone who can change the project "
                                     "(and a token with projects:write)")
    rate = _rate(p.actions_rate_limit)
    scopes = [Scope(f"plugin:{p.id}", f"the {p.name} plugin's rate limit", rate)] if rate else []
    wait, _full = await limiter.take(scopes)
    if wait > 0:
        raise HTTPException(429, f"{p.name}'s rate limit ({p.actions_rate_limit}) is used up: try again in {int(wait) + 1} s")
    try:
        try:
            proc = await P.ensure_running(p.id)
        except (ValueError, PluginRuntimeError) as exc:
            raise HTTPException(409, str(exc)) from exc
        if tool not in proc.mcp_handlers:
            raise HTTPException(409, f"{p.name} doesn't answer {tool} (no ctx.mcp.on handler)")
        params = {
            "name": tool, "args": body.arguments,
            "user": {"id": str(user.id), "username": user.username, "display_name": user.display_name,
                     "can_write": not spec.read_only},
            "project": {"id": str(project.id), "codename": project.codename, "name": project.name},
            "settings": await P.settings_of(db, p, user.id),
        }
        try:
            result = await proc.request("mcp", params, timeout=spec.timeout_seconds)
        except (P.PluginError, PluginRuntimeError) as exc:  # its handler threw, or it didn't answer in time
            raise HTTPException(422, f"{p.name}: {exc}") from exc
    finally:
        limiter.release(scopes)
    if not spec.read_only:
        await record_activity(db, type="plugin.tool.called", project_id=project.id,
                              message=f"Called {spec.name} ({p.name})",
                              payload={"plugin": p.id, "tool": tool},  # record_activity marks MCP calls itself
                              **actor_of(user))
        await db.commit()
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    if len(text) > MAX_RESULT:
        text = text[:MAX_RESULT] + "\n…(truncated)"
        result = text
    return {"result": result}
