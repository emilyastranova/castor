"""Command library: syncing built-in definitions and applying definitions to cards."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m
from .command_render import render_command, resolve_args
from .command_spec import CommandSpec, parse_spec
from .config import settings

log = logging.getLogger(__name__)


def builtin_dir() -> Path:
    return Path(settings.commands_dir)


def definition_from_spec(row: m.CommandDefinition, spec: CommandSpec, source_yaml: str) -> None:
    changed = row.spec != spec.model_dump(mode="json")
    row.name = spec.name
    row.description = spec.description
    row.tags = spec.tags
    row.spec = spec.model_dump(mode="json")
    row.source_yaml = source_yaml
    if changed and row.version:
        row.version += 1


async def sync_builtin_definitions(db: AsyncSession) -> int:
    """Upsert every commands/*.yaml as a built-in definition. Returns how many were loaded.

    A user definition with the same id is left alone (and the built-in skipped)."""
    directory = builtin_dir()
    if not directory.is_dir():
        log.info("no built-in command directory at %s", directory)
        return 0
    loaded = 0
    for path in sorted(directory.glob("*.y*ml")):
        text = path.read_text()
        try:
            spec = parse_spec(text)
        except ValueError as exc:
            log.warning("skipping built-in command %s: %s", path.name, exc)
            continue
        row = await db.get(m.CommandDefinition, spec.id)
        if row is not None and row.origin != "builtin":
            log.warning("built-in command %s shadowed by a user definition", spec.id)
            continue
        if row is None:
            row = m.CommandDefinition(id=spec.id, origin="builtin", version=1)
            db.add(row)
        definition_from_spec(row, spec, text)
        loaded += 1
    await db.commit()
    return loaded


async def load_spec(db: AsyncSession, command_id: str) -> CommandSpec | None:
    row = await db.get(m.CommandDefinition, command_id)
    return CommandSpec.model_validate(row.spec) if row is not None else None


def apply_to_task(task: m.Task, spec: CommandSpec, args: dict[str, Any]) -> None:
    """Point a card at a library command: store its args and the rendered command line.
    Raises command_render.ArgumentError for invalid values."""
    resolved = resolve_args(spec, args)
    task.command = render_command(spec, args)
    task.command_ref = spec.id
    task.command_args = {k: v for k, v in resolved.items() if v is not None}
    task.image = spec.image
    task.kind = m.TaskKind.command


async def ids_in_use(db: AsyncSession) -> set[str]:
    rows = await db.execute(select(m.Task.command_ref).where(m.Task.command_ref.is_not(None)))
    return {r for (r,) in rows}
