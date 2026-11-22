"""Hooks: decisions Castor asks a plugin's code for while it works (manifest `hooks`, SDK
`ctx.hooks.on(kind, handler)`), each with a deadline.

- `job.prepare`: just before one of the plugin's own commands is sent (the dispatcher, through
  job_inputs.prepare), its input files and environment. The Ansible plugin builds an inventory from
  the project's assets here. A hook that fails or misses its deadline fails the job with the reason:
  it never runs without what it was meant to get.
- `assets.map`: after one of the plugin's own parsers read a run (outputs.parse_job), the assets and
  relationships that is, recorded like a bulk import but credited to the run and its parser. A hook
  that fails is logged; the run's results stand.

Only for projects the plugin is on, and only while it's on. The plugin is asked with what it needs and
nothing secret: no variable values, no tokens."""

from __future__ import annotations

import logging
import re
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m
from .plugin_library import plugin_of

if TYPE_CHECKING:
    from .command_spec import CommandSpec
    from .job_inputs import Prepared

log = logging.getLogger(__name__)

ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
INPUT_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
MAX_ENV_VALUE = 64 * 1024


async def _hooked(db: AsyncSession, origin: str | None, kind: str, item_id: str, project_id: uuid.UUID) -> tuple[m.Plugin, Any] | None:
    """The plugin whose `kind` hook covers this item of its library, if it's on in the project."""
    from . import plugins

    plugin_id = plugin_of(origin)
    if plugin_id is None:
        return None
    p = await db.get(m.Plugin, plugin_id)
    if p is None:
        return None
    hook = plugins.manifest_of(p).hook(kind)
    if hook is None:
        return None
    wanted = hook.commands if kind == "job.prepare" else hook.parsers
    if wanted and item_id not in wanted:
        return None
    if not p.enabled or not await plugins.enabled_in(db, p, project_id):
        return (p, None)  # it would be asked, but can't be
    return p, hook


async def _ask(p: m.Plugin, hook: Any, params: dict[str, Any]) -> Any:
    from . import plugins
    from .plugin_runtime import PluginRuntimeError

    try:
        proc = await plugins.ensure_running(p.id)
        return await proc.request("hook", {"kind": hook.kind, **params}, timeout=hook.timeout_seconds)
    except TimeoutError as exc:
        raise HookError(f"{p.name} didn't answer within {hook.timeout_seconds} s") from exc
    except (plugins.PluginError, PluginRuntimeError) as exc:
        raise HookError(f"{p.name}: {exc}") from exc


class HookError(Exception):
    pass


# ---------- job.prepare ----------

async def job_prepare(db: AsyncSession, job: m.Job, task: m.Task, spec: CommandSpec, out: Prepared, agent: Any = None) -> None:
    """Ask the command's plugin for the job's inputs and environment (raises job_inputs.InputError)."""
    from .job_inputs import InputError

    row = await db.get(m.CommandDefinition, task.command_ref)
    found = await _hooked(db, row.origin if row else None, "job.prepare", spec.id, task.project_id)
    if found is None:
        return
    p, hook = found
    if hook is None:
        raise InputError(f"{p.name} prepares this command, and it isn't on in this project")
    from . import variables
    from .command_render import ArgumentError, resolve_args

    try:
        args = resolve_args(spec, await variables.expand_list_refs(db, spec, task.command_args or {}, task.project_id))
    except (ArgumentError, variables.VariableError) as exc:
        raise InputError(f"arguments: {exc}") from exc
    project = await db.get(m.Project, task.project_id)
    params = {
        "command": spec.id, "args": args,
        "inputs": {name: item["content"] for name, item in out.inputs.items()},
        "job": {"id": str(job.id), "task_id": str(task.id)},
        "card": {"id": str(task.id), "title": task.title},
        "project": {"id": str(project.id), "codename": project.codename, "name": project.name} if project else None,
        "agent": {"id": str(agent.agent_id), "name": agent.name, "tags": list(agent.tags)} if agent is not None else None,
    }
    try:
        answer = await _ask(p, hook, params)
    except HookError as exc:
        raise InputError(f"preparing it: {exc}") from exc
    _apply_prepared(p, answer, out)


def _apply_prepared(p: m.Plugin, answer: Any, out: Prepared) -> None:
    from .command_spec import input_env
    from .job_inputs import InputError

    if answer is None:
        return
    if not isinstance(answer, dict):
        raise InputError(f"{p.name}: its job.prepare answer isn't an object")
    for name, value in (answer.get("inputs") or {}).items():
        if not INPUT_NAME.match(str(name)):
            raise InputError(f"{p.name}: {name!r} isn't an input name (lowercase letters, digits and _)")
        current = out.inputs.get(name, {"file": name, "content": "", "executable": False})
        if isinstance(value, str):
            value = {"content": value}
        if not isinstance(value, dict) or not isinstance(value.get("content", ""), str):
            raise InputError(f"{p.name}: input {name} needs its content as text")
        file = str(value.get("filename") or current["file"])
        if not FILE_NAME.match(file):
            raise InputError(f"{p.name}: {file!r} isn't a file name")
        out.inputs[name] = {"file": file, "content": value.get("content", ""), "executable": bool(value.get("executable", current["executable"]))}
    for key, value in (answer.get("env") or {}).items():
        if not ENV_NAME.match(str(key)) or str(key).startswith("CASTOR_") or key == input_env(""):
            raise InputError(f"{p.name}: {key!r} isn't an environment name it may set")
        text = "" if value is None else str(value)
        if len(text) > MAX_ENV_VALUE:
            raise InputError(f"{p.name}: {key} is too long")
        out.env[str(key)] = text


# ---------- assets.map ----------

async def assets_map(db: AsyncSession, job: m.Job, task: m.Task, outputs: list[m.JobOutput]) -> None:
    """For each output read by a plugin's parser whose assets.map hook covers it, record what the
    plugin says it is. Commits (settles the batch); never raises."""
    from . import assets as A

    for out in outputs:
        if out.error or out.value is None:
            continue
        try:
            row = await db.get(m.ParserDefinition, out.parser)
            found = await _hooked(db, row.origin if row else None, "assets.map", out.parser, task.project_id)
            if found is None or found[1] is None:
                continue
            p, hook = found
            project = await db.get(m.Project, task.project_id)
            answer = await _ask(p, hook, {
                "parser": out.parser, "output": out.name, "type": out.type, "value": out.value,
                "job": {"id": str(job.id), "task_id": str(task.id), "finished_at": job.finished_at.isoformat() if job.finished_at else None},
                "card": {"id": str(task.id), "title": task.title},
                "project": {"id": str(project.id), "codename": project.codename, "name": project.name} if project else None,
            })
            if not isinstance(answer, dict):
                continue
            items = [a for a in (answer.get("assets") or []) if isinstance(a, dict)][:5000]
            links = [lk for lk in (answer.get("links") or []) if isinstance(lk, dict)][:20000]
            batch, _refs, skipped, _ = await A.record_bulk(db, task.project_id, items, links, source="parser",
                                                         parser=out.parser, task_id=task.id, job_id=job.id)
            if skipped:
                log.info("assets.map of %s for job %s skipped %d: %s", p.id, job.id, len(skipped), skipped[:3])
            await A.settle(db, batch, task_id=task.id)
        except HookError as exc:
            log.warning("assets.map for job %s: %s", job.id, exc)
            await db.rollback()
        except Exception:  # never lose the run's results over a plugin's mapping
            log.exception("assets.map for job %s failed", job.id)
            await db.rollback()
