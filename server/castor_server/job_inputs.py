"""A command job's input files (CommandSpec.inputs): filled from its card's arguments as it's sent,
then by its plugin's job.prepare hook if it has one (plugin_hooks.py), and delivered like variable
files (`job.run` files, in .castor/inputs/, removed after the job). What each one held is kept on
the job (`jobs.inputs`), so people can see what a run was given (an Ansible inventory, say).

The dispatcher calls `prepare` before it changes anything about the job: a plugin's hook can take a
few seconds, and nothing may hold the database's write lock while it waits."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m
from . import variables
from .command_spec import INPUT_DIR, CommandSpec, input_env

MAX_INPUT = 1024 * 1024  # one input file


class InputError(ValueError):
    pass


@dataclass
class Prepared:
    # name: {"file": file name, "content": text, "executable": bool}
    inputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Extra (plain) environment from a plugin's hook.
    env: dict[str, str] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.inputs or self.env)


async def prepare(db: AsyncSession, job: m.Job, task: m.Task, spec: CommandSpec | None, agent: Any = None) -> Prepared:
    """The job's inputs: rendered from the card's arguments, then whatever its plugin's hook adds."""
    from .command_render import ArgumentError, render_inputs

    out = Prepared()
    if spec is not None and spec.inputs and spec.kind == "command":
        try:
            args = await variables.expand_list_refs(db, spec, task.command_args or {}, task.project_id)
            contents = render_inputs(spec, args)
        except (ArgumentError, variables.VariableError) as exc:
            raise InputError(f"inputs: {exc}") from exc
        for item in spec.inputs:
            out.inputs[item.name] = {"file": item.file, "content": contents.get(item.name, ""), "executable": item.executable}
    if spec is not None and task.command_ref:
        from .plugin_hooks import job_prepare

        await job_prepare(db, job, task, spec, out, agent)
    for name, item in out.inputs.items():
        if len(item["content"].encode()) > MAX_INPUT:
            raise InputError(f"inputs: {name} is larger than {MAX_INPUT // 1024} KB")
    return out


def attach(prepared: Prepared, resolved: variables.Resolved, job: m.Job) -> None:
    """Hand the inputs to the runtime with the job's variable files, and keep what they held."""
    for name, item in prepared.inputs.items():
        path = f"{INPUT_DIR}/{item['file']}"
        resolved.files.append({
            "env": input_env(name), "path": path, "data": base64.b64encode(item["content"].encode()).decode(),
            "mode": 0o755 if item.get("executable") else 0o644,
        })
        resolved.display[input_env(name)] = path
    for key, value in prepared.env.items():
        resolved.env[key] = value
        resolved.display.setdefault(key, value)
    if prepared.inputs:
        job.inputs = {name: {"file": item["file"], "content": item["content"]} for name, item in prepared.inputs.items()}
