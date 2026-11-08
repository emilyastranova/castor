"""Command library: syncing built-in definitions and applying definitions to cards."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
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
    seen: set[str] = set()
    complete = True
    for path in sorted(directory.glob("*.y*ml")):
        text = path.read_text()
        try:
            spec = parse_spec(text)
        except ValueError as exc:
            log.warning("skipping built-in command %s: %s", path.name, exc)
            complete = False
            continue
        seen.add(spec.id)
        row = await db.get(m.CommandDefinition, spec.id)
        if row is not None and row.origin != "builtin":
            log.warning("built-in command %s shadowed by a user definition", spec.id)
            continue
        from .variables import check_choice_sources, check_command_vars, kinds_by_id

        for problem in check_command_vars(spec, await kinds_by_id(db)):
            log.warning("built-in command %s: %s", spec.id, problem)
        for problem in await check_choice_sources(db, spec):
            log.warning("built-in command %s: %s", spec.id, problem)
        if spec.tool and await db.get(m.ToolDefinition, spec.tool) is None:
            log.warning("built-in command %s: no tool %s in the library", spec.id, spec.tool)
        if row is None:
            row = m.CommandDefinition(id=spec.id, origin="builtin", version=1)
            db.add(row)
        definition_from_spec(row, spec, text)
        loaded += 1
    if complete:  # a file that didn't parse might be one of these; retire nothing then
        await _retire_builtins(db, seen)
    await db.commit()
    return loaded


async def _retire_builtins(db: AsyncSession, seen: set[str]) -> None:
    """Remove built-ins whose file is gone from the library. Cards that used one keep their
    rendered command line and become plain command cards."""
    gone = (await db.execute(select(m.CommandDefinition).where(
        m.CommandDefinition.origin == "builtin", m.CommandDefinition.id.not_in(seen)))).scalars().all()
    for row in gone:
        await db.execute(update(m.Task).where(m.Task.command_ref == row.id).values(command_ref=None))
        await db.delete(row)
        log.info("retired built-in command %s (its file is gone)", row.id)


async def load_spec(db: AsyncSession, command_id: str) -> CommandSpec | None:
    """A command's spec, with argument choices taken from variable sets filled in."""
    from .variables import expand_choices

    row = await db.get(m.CommandDefinition, command_id)
    if row is None:
        return None
    return await expand_choices(db, CommandSpec.model_validate(row.spec))


def apply_to_task(task: m.Task, spec: CommandSpec, args: dict[str, Any]) -> None:
    """Point a card at a library command: store its args and the rendered command line.
    Raises command_render.ArgumentError for invalid values."""
    resolved = resolve_args(spec, args)
    task.command = render_command(spec, args)
    task.command_ref = spec.id
    task.command_args = {k: v for k, v in resolved.items() if v is not None}
    task.image = spec.image
    task.tool = spec.tool
    task.container_flags = list(spec.container_flags)
    task.kind = m.TaskKind.command


async def apply_library(db: AsyncSession, task: m.Task, spec: CommandSpec, args: dict[str, Any]) -> None:
    """apply_to_task, with argument values that reference a target list ({"list": ...})
    expanded from the list's current entries. The card keeps the reference, so the next
    render picks up edits to the list. Raises ArgumentError (or VariableError for a bad
    reference, reported against the argument)."""
    from .assets import is_asset_ref
    from .command_render import ArgumentError
    from .variables import VariableError, expand_list_refs, is_list_ref

    try:
        expanded = await expand_list_refs(db, spec, args, task.project_id)
    except VariableError as exc:
        name, _, message = str(exc).partition(": ")
        raise ArgumentError({name: message or str(exc)}) from exc
    apply_to_task(task, spec, expanded)
    refs = {k: v for k, v in (args or {}).items() if is_list_ref(v) or is_asset_ref(v)}
    if refs:
        task.command_args = {**task.command_args, **refs}


async def refresh_list_refs(db: AsyncSession, task: m.Task) -> None:
    """Re-render a library card whose arguments reference target lists, so a job always
    runs with the lists' current entries."""
    from .assets import is_asset_ref
    from .variables import is_list_ref

    if not task.command_ref or not any(is_list_ref(v) or is_asset_ref(v) for v in (task.command_args or {}).values()):
        return
    spec = await load_spec(db, task.command_ref)
    if spec is not None:
        await apply_library(db, task, spec, task.command_args)


async def ids_in_use(db: AsyncSession) -> set[str]:
    rows = await db.execute(select(m.Task.command_ref).where(m.Task.command_ref.is_not(None)))
    return {r for (r,) in rows}
