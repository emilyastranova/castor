"""MCP connections (Settings → MCP servers): checking them, opening sessions to them, and
turning one of their tools into a draft MCP action (docs/ACTIONS.md phase 3)."""

from __future__ import annotations

import re
import uuid
from typing import Any

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from . import mcp_client, outbound, variables
from . import models as m
from .models import utcnow

FIELD = re.compile(r"\{\{\s*([a-z][a-z0-9_]*)\s*\}\}")
ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class Unusable(Exception):
    """The connection can't be used as it is (no agent, its variable set can't be read...)."""


def allowed(row: m.McpConnection, project_id: uuid.UUID | str | None) -> bool:
    return row.project_ids is None or (project_id is not None and str(project_id) in row.project_ids)


async def secrets(db: AsyncSession, row: m.McpConnection, project_id: uuid.UUID | None) -> variables.Resolved:
    """The connection's variable set, as values to fill `{{ field }}` with (and to mask)."""
    if row.variable_set_id is None:
        return variables.Resolved()
    vset = await db.get(m.VariableSet, row.variable_set_id)
    if vset is None:
        raise Unusable("its variable set no longer exists")
    from .command_spec import VarSlot

    slot = VarSlot(name="conn", kind=vset.kind_id)
    try:
        return await variables.resolve(db, [slot], {"conn": str(vset.id)}, project_id)
    except variables.VariableError as exc:
        raise Unusable(f"its variable set: {exc}") from exc


def fill(templates: dict[str, str], values: variables.Resolved) -> dict[str, str]:
    """`{{ field }}` in header or environment templates, from the connection's set. A
    line break in a header value is refused."""
    out = {}
    for name, template in (templates or {}).items():
        value = FIELD.sub(lambda mt: values.env.get(variables.env_name("conn", mt.group(1)), ""), str(template))
        if "\r" in value or "\n" in value:
            raise Unusable(f"{name} would contain a line break")
        if value.strip():
            out[name] = value
    return out


def check_templates(templates: dict[str, str], fields: set[str] | None) -> list[str]:
    """Placeholders that name no field of the variable set's kind."""
    used = {f for t in (templates or {}).values() for f in FIELD.findall(str(t))}
    return sorted(used - (fields or set()))


async def pick_agent(db: AsyncSession, row: m.McpConnection, project_id: uuid.UUID | None) -> Any:
    """The agent a stdio connection's program runs on: connected, able to run it (a container
    runtime for a tool or image, host commands otherwise), allowed in the project, matching the
    connection's selector; one that already runs it first."""
    from .orchestrator.dispatcher import project_allowed, selector_matches, tool_way
    from .orchestrator.gateway import gateway
    from .tool_library import tools_by_id

    tool = (await tools_by_id(db)).get(row.tool) if row.tool else None
    if row.tool and tool is None:
        raise Unusable(f"the tool {row.tool} isn't in the library")
    able = []
    for conn in gateway.connections.values():
        if not conn.capabilities.get("mcp_stdio") or not selector_matches(conn, row.agent_selector or {}):
            continue
        if project_id is not None and not project_allowed(conn, project_id):
            continue
        if tool is not None and tool_way(conn, tool) is None:
            continue
        if tool is None and row.image and not conn.runtimes & {"podman", "docker"}:
            continue
        if tool is None and not row.image and "host" not in conn.runtimes:
            continue
        able.append(conn)
    if not able:
        raise Unusable("no connected agent can run it (it needs one that matches its agent choice"
                               + (", has a container runtime" if row.tool or row.image else ", allows host commands")
                               + " and is up to date)")
    warm = (row.id, None, str(project_id or ""))
    return min(able, key=lambda c: ((warm[0], c.agent_id, warm[2]) not in mcp_client.hub.programs, len(c.running))), tool


async def open_session(db: AsyncSession, row: m.McpConnection, project_id: uuid.UUID | None,
                       timeout: float = 120) -> tuple[mcp_client.Session, variables.Resolved, str]:
    """A session to the connection, initialized. Returns it, the secrets (to mask) and where it
    runs ("the server", or the agent's name)."""
    if not row.enabled:
        raise Unusable("it's turned off in Settings → MCP servers")
    if project_id is not None and not allowed(row, project_id):
        raise Unusable("it isn't allowed in this project (Settings → MCP servers)")
    values = await secrets(db, row, project_id)
    if row.transport in ("http", "sse"):
        kind = mcp_client.SseSession if row.transport == "sse" else mcp_client.HttpSession
        session = kind(row.url, fill(row.headers, values), await outbound.policy(db), timeout)
        try:
            await mcp_client.initialize(session)
        except BaseException:
            await session.close()
            raise
        return session, values, "the server"
    conn, tool = await pick_agent(db, row, project_id)
    from .orchestrator.dispatcher import tool_way

    launch = {
        "connection": row.id,
        "command": list(row.command or []),
        "image": row.image,
        "tool": tool_way(conn, tool) if tool is not None else None,
        "env": fill(row.env, values),
        "idle_s": row.idle_s,
    }
    session = await mcp_client.hub.session(conn, row.id, launch, project_id=project_id)
    return session, values, conn.name


async def check(db: AsyncSession, row: m.McpConnection) -> m.McpConnection:
    """Connect, read the server's name and tools, and remember them (or the error)."""
    try:
        session, _values, _where = await open_session(db, row, None)
        try:
            row.tools = await mcp_client.list_tools(session)
            row.server_info = session.server_info
        finally:
            await session.close()
        row.last_error = None
    except (mcp_client.McpError, Unusable, outbound.OutboundRefused) as exc:
        row.last_error = str(exc)[:1000]
    row.checked_at = utcnow()
    return row


# ---------- a draft action from a tool ----------


def _arg(name: str, schema: dict[str, Any], required: bool) -> dict[str, Any] | None:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return None
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), None)
    arg: dict[str, Any] = {"name": name}
    if kind in ("number", "integer"):
        arg["type"] = "number"
    elif kind == "boolean":
        arg["type"] = "boolean"
    elif kind == "array":
        arg["type"] = "list"
    elif kind in ("object",):
        return None  # objects can't be typed in a field; write them in `arguments`
    if schema.get("description"):
        arg["description"] = str(schema["description"])[:300]
    if required:
        arg["required"] = True
    if "default" in schema and not isinstance(schema["default"], (dict, list)):
        arg["default"] = schema["default"]
    enum = schema.get("enum") or ((schema.get("items") or {}).get("enum") if kind == "array" else None)
    if enum and all(isinstance(e, (str, int, float)) for e in enum) and kind != "array":
        arg["choices"] = [str(e) for e in enum]
    if schema.get("examples") and not isinstance(schema["examples"][0], (dict, list)):
        arg["example"] = schema["examples"][0]
    return arg


def draft_definition(row: m.McpConnection, tool: dict[str, Any]) -> str:
    """A kind: mcp definition for one of the connection's tools, as YAML to edit and save:
    the tool's input schema turned into typed arguments."""
    schema = tool.get("inputSchema") or {}
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    args, skipped = [], []
    for name, prop in props.items():
        arg = _arg(name, prop if isinstance(prop, dict) else {}, name in required)
        (args.append(arg) if arg else skipped.append(name))
    slug = re.sub(r"[^a-z0-9]+", "-", f"{row.id}-{tool['name']}".lower()).strip("-")[:63]
    spec: dict[str, Any] = {
        "id": slug or "mcp-tool",
        "name": tool.get("title") or tool["name"].replace("_", " ").capitalize(),
        "kind": "mcp",
        "description": str(tool.get("description") or "").strip().split("\n")[0][:300],
        "tags": ["mcp", row.id],
        "args": args,
        "mcp": {"server": row.id, "tool": tool["name"]},
        "outputs": [{"name": "result", "parser": "json-records" if tool.get("outputSchema") else "text"}],
    }
    text = yaml.safe_dump(spec, sort_keys=False, allow_unicode=True, width=100)
    if skipped:
        text += ("# Not turned into arguments (objects or unusual names): " + ", ".join(skipped)
                 + ".\n# Add them under mcp.arguments if the tool needs them.\n")
    return text
