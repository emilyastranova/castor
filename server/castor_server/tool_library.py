"""Tool library: built-ins from tools/*.yaml, user definitions from the UI."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m
from .config import settings
from .tool_spec import ToolSpec, parse_tool_spec

log = logging.getLogger(__name__)


def definition_from_spec(row: m.ToolDefinition, spec: ToolSpec, source_yaml: str) -> None:
    changed = row.spec != spec.model_dump(mode="json")
    row.name = spec.name
    row.description = spec.description
    row.spec = spec.model_dump(mode="json")
    row.source_yaml = source_yaml
    if changed and row.version:
        row.version += 1


async def sync_builtin_tools(db: AsyncSession) -> int:
    directory = Path(settings.tools_dir)
    if not directory.is_dir():
        log.info("no built-in tool directory at %s", directory)
        return 0
    loaded = 0
    seen: set[str] = set()
    complete = True
    for path in sorted(directory.glob("*.y*ml")):
        text = path.read_text()
        try:
            spec = parse_tool_spec(text)
        except ValueError as exc:
            log.warning("skipping built-in tool %s: %s", path.name, exc)
            complete = False
            continue
        seen.add(spec.id)
        row = await db.get(m.ToolDefinition, spec.id)
        if row is not None and row.origin != "builtin":
            log.warning("built-in tool %s shadowed by a user definition", spec.id)
            continue
        if row is None:
            row = m.ToolDefinition(id=spec.id, origin="builtin", version=1)
            db.add(row)
        definition_from_spec(row, spec, text)
        loaded += 1
    if complete:  # a file that didn't parse might be one of these; retire nothing then
        await _retire_builtins(db, seen)
    await db.commit()
    return loaded


async def _retire_builtins(db: AsyncSession, seen: set[str]) -> None:
    """Remove built-in tools whose file is gone, unless a command still uses one: that one
    stays, as a user tool (editable, deletable)."""
    gone = (await db.execute(select(m.ToolDefinition).where(
        m.ToolDefinition.origin == "builtin", m.ToolDefinition.id.not_in(seen)))).scalars().all()
    if not gone:
        return
    used = {(c.spec or {}).get("tool") for c in (await db.execute(select(m.CommandDefinition))).scalars()}
    for row in gone:
        if row.id in used:
            row.origin = "user"
            log.info("built-in tool %s is gone from the library; kept as a user tool (commands use it)", row.id)
        else:
            await db.delete(row)
            log.info("retired built-in tool %s (its file is gone)", row.id)


async def load_tool(db: AsyncSession, tool_id: str) -> ToolSpec | None:
    row = await db.get(m.ToolDefinition, tool_id)
    return ToolSpec.model_validate(row.spec) if row is not None else None


async def tools_by_id(db: AsyncSession) -> dict[str, ToolSpec]:
    """Every tool that loads. A stored spec that doesn't (an older format, a bad import) is
    skipped with a warning: the dispatcher calls this, and one bad row mustn't stop every job."""
    out: dict[str, ToolSpec] = {}
    for r in (await db.execute(select(m.ToolDefinition))).scalars():
        try:
            out[r.id] = ToolSpec.model_validate(r.spec)
        except ValueError as exc:
            log.warning("tool %s can't be loaded, skipping it: %s", r.id, exc)
    return out


async def tools_in_use(db: AsyncSession) -> set[str]:
    rows = await db.execute(select(m.CommandDefinition.spec))
    return {spec.get("tool") for (spec,) in rows if spec.get("tool")}
