from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import models as m
from .. import variables
from ..access import visible_project_ids
from ..command_library import definition_from_spec, forget_definition, ids_in_use, load_spec, usage
from ..command_render import ArgumentError, render_command
from ..command_spec import CommandSpec, parse_spec
from ..deps import DB, CurrentUser, Operator
from ..plugin_library import cant_delete, read_only, why_read_only
from ..schemas import CommandDefinitionOut, CommandUsageOut, CommandYamlIn, RenderIn, RenderOut
from ..services import library_activity
from ..tool_library import load_tool

router = APIRouter(prefix="/commands", tags=["commands"])


async def _out(db, row: m.CommandDefinition, used: set[str], uses=None) -> CommandDefinitionOut:
    out = CommandDefinitionOut.model_validate(row)
    out.in_use = row.id in used
    if uses is not None:
        cards, playbooks = uses
        out.card_count = cards.get(row.id, 0)
        out.playbooks = sorted(pid for pid, _ in playbooks.get(row.id, []))
    # Argument choices from variable sets are filled in for the forms.
    out.spec = (await variables.expand_choices(db, CommandSpec.model_validate(row.spec))).model_dump(mode="json")
    return out


def _parse_or_422(text: str):
    try:
        return parse_spec(text)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("", response_model=list[CommandDefinitionOut])
async def list_commands(db: DB, user: CurrentUser):
    rows = (await db.execute(select(m.CommandDefinition).order_by(m.CommandDefinition.name))).scalars()
    used = await ids_in_use(db)
    uses = await usage(db, await visible_project_ids(db, user))
    return [await _out(db, r, used, uses) for r in rows]


@router.get("/{command_id}", response_model=CommandDefinitionOut)
async def get_command(command_id: str, db: DB, user: CurrentUser):
    row = await db.get(m.CommandDefinition, command_id)
    if row is None:
        raise HTTPException(404, "Command not found")
    return await _out(db, row, await ids_in_use(db), await usage(db, await visible_project_ids(db, user)))


@router.get("/{command_id}/usage", response_model=CommandUsageOut)
async def command_usage(command_id: str, db: DB, user: CurrentUser, limit: int = 50):
    """The board cards (in projects you can see, newest first) and playbooks that run it."""
    if await db.get(m.CommandDefinition, command_id) is None:
        raise HTTPException(404, "Command not found")
    visible = await visible_project_ids(db, user)
    cards, playbooks = await usage(db, visible)
    query = (
        select(m.Task.id, m.Task.title, m.Task.project_id, m.Project.codename)
        .join(m.Project, m.Project.id == m.Task.project_id)
        .where(m.Task.command_ref == command_id, m.Task.playbook_step_id.is_(None))
        .order_by(m.Task.updated_at.desc())
        .limit(max(1, min(limit, 200)))
    )
    if visible is not None:
        query = query.where(m.Task.project_id.in_(visible))
    rows = (await db.execute(query)).all()
    return CommandUsageOut(
        cards=[{"id": i, "title": t, "project_id": p, "project_codename": c} for i, t, p, c in rows],
        card_count=cards.get(command_id, 0),
        playbooks=[{"id": pid, "name": name} for pid, name in sorted(playbooks.get(command_id, []), key=lambda x: x[1].lower())],
    )


async def _check_vars(db, spec) -> None:
    problems = variables.check_command_vars(spec, await variables.kinds_by_id(db))
    problems += await variables.check_choice_sources(db, spec)
    if spec.tool and await load_tool(db, spec.tool) is None:
        problems.append(f"tool: no tool {spec.tool} in the library (see Library)")
    if problems:
        raise HTTPException(422, "; ".join(problems))


@router.post("", response_model=CommandDefinitionOut, status_code=201)
async def create_command(body: CommandYamlIn, db: DB, user: Operator):
    spec = _parse_or_422(body.yaml)
    await _check_vars(db, spec)
    if await db.get(m.CommandDefinition, spec.id) is not None:
        raise HTTPException(409, f"A command with id “{spec.id}” already exists")
    row = m.CommandDefinition(id=spec.id, origin="user", version=1)
    definition_from_spec(row, spec, body.yaml)
    db.add(row)
    await library_activity(db, user, "command", "created", spec.id, row.name)
    await db.commit()
    return await _out(db, row, set())


@router.put("/{command_id}", response_model=CommandDefinitionOut)
async def update_command(command_id: str, body: CommandYamlIn, db: DB, user: Operator):
    row = await db.get(m.CommandDefinition, command_id)
    if row is None:
        raise HTTPException(404, "Command not found")
    if read_only(row.origin):
        raise HTTPException(409, why_read_only(row.origin, "command"))
    spec = _parse_or_422(body.yaml)
    await _check_vars(db, spec)
    if spec.id != command_id:
        raise HTTPException(422, "id: cannot be changed (create a new command instead)")
    definition_from_spec(row, spec, body.yaml)
    await library_activity(db, user, "command", "updated", command_id, row.name)
    await db.commit()
    return await _out(db, row, await ids_in_use(db))


@router.delete("/{command_id}", status_code=204)
async def delete_command(command_id: str, db: DB, user: Operator):
    row = await db.get(m.CommandDefinition, command_id)
    if row is None:
        raise HTTPException(404, "Command not found")
    if read_only(row.origin):
        raise HTTPException(409, cant_delete(row.origin, "command"))
    # Cards that used it keep their rendered command line and become plain shell commands
    # (an HTTP action's cards become manual cards).
    await forget_definition(db, row)
    await db.delete(row)
    await library_activity(db, user, "command", "deleted", command_id, row.name)
    await db.commit()


@router.post("/{command_id}/render", response_model=RenderOut)
async def render(command_id: str, body: RenderIn, db: DB, _: CurrentUser):
    spec = await load_spec(db, command_id)
    if spec is None:
        raise HTTPException(404, "Command not found")
    try:
        args = await variables.expand_list_refs(db, spec, body.args)
    except variables.VariableError as exc:
        name, _, message = str(exc).partition(": ")
        raise HTTPException(422, {"message": str(exc), "errors": {name: message}}) from exc
    if spec.kind in ("http", "mcp", "plugin"):
        import json

        from ..command_render import resolve_args
        from ..http_render import display_lookup, mcp_arguments, preview_line, request_preview

        try:
            resolved = resolve_args(spec, args)
        except ArgumentError as exc:
            raise HTTPException(422, {"message": str(exc), "errors": exc.errors}) from exc
        if spec.kind == "plugin":
            call = {"method": "PLUGIN", "url": f"{spec.plugin.id} · {spec.plugin.action}", "headers": {},
                    "body": json.dumps({k: v for k, v in resolved.items() if v is not None}, ensure_ascii=False, indent=2)}
            return RenderOut(command=preview_line(spec, resolved), image=None, kind="plugin", request=call)
        if spec.kind == "mcp":
            call = {"method": "MCP", "url": f"{spec.mcp.server} · {spec.mcp.tool}", "headers": {},
                    "body": json.dumps(mcp_arguments(spec, resolved, display_lookup, preview=True), ensure_ascii=False, indent=2)}
            return RenderOut(command=preview_line(spec, resolved), image=None, kind="mcp", request=call)
        return RenderOut(command=preview_line(spec, resolved), image=None, kind="http", request=request_preview(spec, resolved))
    try:
        return RenderOut(
            command=render_command(spec, args),
            image=spec.image,
            tool=spec.tool,
            container_flags=spec.container_flags,
        )
    except ArgumentError as exc:
        raise HTTPException(422, {"message": str(exc), "errors": exc.errors}) from exc


class TryIn(BaseModel):
    project_id: uuid.UUID
    args: dict[str, Any] = Field(default_factory=dict)
    # Variable slot → set id (the credentials the action uses).
    var_bindings: dict[str, uuid.UUID | None] = Field(default_factory=dict)


class TryOut(BaseModel):
    task_id: uuid.UUID
    job_id: uuid.UUID


@router.post("/{command_id}/try", response_model=TryOut, status_code=201)
async def try_action(command_id: str, body: TryIn, db: DB, user: Operator):
    """Run an HTTP, MCP or plugin action once, from the Library, in a project: on a card made
    for it and archived straight away (off the board, kept with its run for the record). It runs
    like any card's: limits, approvals, masked secrets, parsed results."""
    from ..access import project_or_404
    from ..api.tasks import _bind_vars, _use_library_command
    from ..command_library import ACTION_KINDS
    from ..models import utcnow
    from ..orchestrator.dispatcher import dispatcher
    from ..services import (
        actor_of,
        column_with_role,
        enqueue_job,
        next_position,
        publish_task,
        record_activity,
    )

    spec = await load_spec(db, command_id)
    if spec is None:
        raise HTTPException(404, "Command not found")
    if spec.kind not in ACTION_KINDS:
        raise HTTPException(422, "Try it is for HTTP, MCP and plugin actions; a command runs on an agent from a card")
    project = await project_or_404(db, user, body.project_id, write=True)
    column = await column_with_role(db, project.id, m.ColumnRole.todo) or (
        await db.execute(select(m.BoardColumn).where(m.BoardColumn.project_id == project.id).order_by(m.BoardColumn.position).limit(1))
    ).scalar_one_or_none()
    if column is None:
        raise HTTPException(409, "Project has no columns")
    now = utcnow()
    task = m.Task(project_id=project.id, column_id=column.id, position=await next_position(db, column.id), created_by=user.id,
                  title=f"Try: {spec.name}"[:300], description=f"Tried from the Library ({spec.id}).", kind=m.TaskKind.command,
                  run_on_move=False, archived_at=now, archived_by=user.id)
    await _use_library_command(db, task, spec.id, body.args)
    db.add(task)
    await db.flush()
    if body.var_bindings:
        await _bind_vars(db, task, {k: v for k, v in body.var_bindings.items()})
    await record_activity(db, type="command.tried", message=f"Tried “{spec.name}” from the Library", project_id=project.id,
                          task_id=task.id, payload={"command": spec.id}, **actor_of(user))
    job = await enqueue_job(db, task, user, reason="tried from the Library")
    await db.commit()
    await publish_task(db, task)
    dispatcher.wake()
    return TryOut(task_id=task.id, job_id=job.id)
