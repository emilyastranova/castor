"""Playbook library: syncing built-in playbooks and loading playbook specs."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m
from .command_library import load_spec
from .command_render import _normalise
from .command_spec import CommandSpec
from .config import settings
from .playbook_spec import INLINE_PLACEHOLDER_RE, PlaybookSpec, parse_playbook_spec

log = logging.getLogger(__name__)


def builtin_dir() -> Path:
    return Path(settings.playbooks_dir)


def definition_from_spec(row: m.PlaybookDefinition, spec: PlaybookSpec, source_yaml: str) -> None:
    changed = row.spec != spec.model_dump(mode="json")
    row.name = spec.name
    row.description = spec.description
    row.tags = spec.tags
    row.spec = spec.model_dump(mode="json")
    row.source_yaml = source_yaml
    if changed and row.version:
        row.version += 1


async def sync_builtin_playbooks(db: AsyncSession) -> int:
    """Upsert every playbooks/*.yaml as a built-in playbook definition. Returns how many were loaded."""
    directory = builtin_dir()
    if not directory.is_dir():
        log.info("no built-in playbook directory at %s", directory)
        return 0
    loaded = 0
    for path in sorted(directory.glob("*.y*ml")):
        text = path.read_text()
        try:
            spec = parse_playbook_spec(text)
        except ValueError as exc:
            log.warning("skipping built-in playbook %s: %s", path.name, exc)
            continue
        for problem in await check_against_library(db, spec):
            log.warning("built-in playbook %s: %s", spec.id, problem)
        row = await db.get(m.PlaybookDefinition, spec.id)
        if row is not None and row.origin != "builtin":
            log.warning("built-in playbook %s shadowed by a user definition", spec.id)
            continue
        if row is None:
            row = m.PlaybookDefinition(id=spec.id, origin="builtin", version=1)
            db.add(row)
        definition_from_spec(row, spec, text)
        loaded += 1
    await db.commit()
    return loaded


async def load_playbook_spec(db: AsyncSession, playbook_id: str) -> PlaybookSpec | None:
    row = await db.get(m.PlaybookDefinition, playbook_id)
    return PlaybookSpec.model_validate(row.spec) if row is not None else None


async def _check_step_vars(db: AsyncSession, spec: PlaybookSpec, step, cmd: CommandSpec) -> list[str]:
    from .variables import find_set

    problems = []
    slots = {s.name: s for s in cmd.vars}
    inputs = {i.name: i for i in spec.inputs}
    for name, value in step.vars.items():
        slot = slots.get(name)
        if slot is None:
            problems.append(
                f"step {step.id}: {cmd.id} has no variable slot {name} "
                f"(slots: {', '.join(slots) or 'none'})"
            )
            continue
        ref = INLINE_PLACEHOLDER_RE.search(value)
        if ref:
            inp = inputs.get(ref.group(1).split(".")[1])
            if inp is not None and inp.kind != slot.kind:
                problems.append(
                    f"step {step.id}: vars.{name} needs a {slot.kind} set, input {inp.name} is {inp.kind}"
                )
            continue
        row = await find_set(db, value)
        if row is None:
            problems.append(f"step {step.id}: vars.{name}: no variable set named {value}")
        elif row.kind_id != slot.kind:
            problems.append(f"step {step.id}: vars.{name}: {row.name} is a {row.kind_id} set, not {slot.kind}")
    for slot in cmd.vars:
        if slot.required and slot.name not in step.vars:
            problems.append(f"step {step.id}: variable slot {slot.name} ({slot.kind}) is not bound")
    return problems


async def check_against_library(db: AsyncSession, spec: PlaybookSpec) -> list[str]:
    """What parse_playbook_spec can't see: that each command exists, args are its
    arguments (literal values valid, required ones bound), and every referenced output
    is one the upstream command declares. Returns problems; empty means fine."""
    problems: list[str] = []
    commands: dict[str, CommandSpec] = {}
    for step in spec.steps:
        if step.command_ref and step.command_ref not in commands:
            found = await load_spec(db, step.command_ref)
            if found is None:
                problems.append(f"step {step.id}: unknown command {step.command_ref}")
            else:
                commands[step.command_ref] = found
    by_id = {s.id: s for s in spec.steps}

    for step in spec.steps:
        if step.kind == "command":
            cmd = commands.get(step.command_ref or "")
            if cmd is None:
                continue
            known = {a.name: a for a in cmd.args}
            for name, value in step.args.items():
                arg = known.get(name)
                if arg is None:
                    problems.append(
                        f"step {step.id}: {cmd.id} has no argument {name} "
                        f"(arguments: {', '.join(known) or 'none'})"
                    )
                elif not (isinstance(value, str) and INLINE_PLACEHOLDER_RE.search(value)):
                    try:
                        _normalise(arg, value)
                    except ValueError as exc:
                        problems.append(f"step {step.id}: {name} {exc}")
            for arg in cmd.args:
                if arg.required and arg.default is None and arg.name not in step.args:
                    problems.append(f"step {step.id}: required argument {arg.name} is not bound")
            problems += await _check_step_vars(db, spec, step, cmd)
        for ref in step.refs():
            parts = ref.split(".")
            if parts[0] != "steps":
                continue
            source = by_id.get(parts[1])
            cmd = commands.get(source.command_ref or "") if source else None
            if cmd is not None:
                outputs = [o.name for o in cmd.outputs if o.parser]
                if parts[3] not in outputs:
                    problems.append(
                        f"step {step.id}: {parts[1]} ({cmd.id}) has no parsed output {parts[3]} "
                        f"(outputs: {', '.join(outputs) or 'none'})"
                    )
    return problems
