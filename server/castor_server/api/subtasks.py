"""Cards with subtasks (subtasks.py): an activity card's subtasks, adding existing cards to it,
making a subtask its own card again, ordering and running them, and turning several cards into a
new activity (for tidying a board, from the UI or over MCP)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import models as m
from .. import subtasks as S
from ..access import project_or_404, task_or_404
from ..deps import DB, CurrentUser, parse_uuid
from ..schemas import TaskOut
from ..services import publish_task, tasks_out

router = APIRouter(tags=["subtasks"])


class CardIds(BaseModel):
    task_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


class Consolidate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = ""
    task_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    # Where the new card goes; none: the column most of the cards are in.
    column_id: uuid.UUID | None = None


class RunAll(BaseModel):
    # Start ones that wait on unfinished cards too.
    force: bool = False
    # How many run at once (the rest wait their turn; 1: one after another); none: all now.
    at_once: int | None = Field(None, ge=1, le=100)


class RunAllOut(BaseModel):
    started: list[uuid.UUID]
    skipped: list[dict[str, str]]
    queued: int = 0  # waiting their turn


class Many(BaseModel):
    """One subtask per target, all running the same library command (like a playbook's for_each)."""

    command_ref: str = Field(min_length=1, max_length=64)
    # The other arguments, the same for every subtask.
    args: dict[str, Any] = Field(default_factory=dict)
    # The argument each target goes into.
    target_arg: str = Field(min_length=1, max_length=64)
    # A list, text (commas or lines), a saved list ({"list": …}) or the project's assets ({"assets": …}).
    targets: Any
    # Each subtask's title; {{ target }} is the target. Default: "<command name>: <target>".
    title: str = Field("", max_length=300)
    var_bindings: dict[str, uuid.UUID | None] = Field(default_factory=dict)


async def _cards(db, user, project_id: uuid.UUID, ids: list[uuid.UUID]) -> list[m.Task]:
    cards = []
    for task_id in dict.fromkeys(ids):
        card = await task_or_404(db, user, task_id, write=True)
        if card.project_id != project_id:
            raise HTTPException(422, f"“{card.title}” is in another project")
        cards.append(card)
    return cards


async def _publish(db, cards: list[m.Task], parent: m.Task | None) -> None:
    for card in cards:
        await publish_task(db, card)  # with its parent_id: the board drops it (or gets it back)
    if parent is not None:
        await publish_task(db, parent)


@router.get("/tasks/{task_id}/subtasks", response_model=list[TaskOut])
async def list_subtasks(task_id: str, db: DB, user: CurrentUser, archived: bool = False):
    """An activity card's subtasks, in order (archived ones only when asked)."""
    parent = await task_or_404(db, user, parse_uuid(task_id))
    return await tasks_out(db, await S.children(db, parent.id, archived=archived))


@router.post("/tasks/{task_id}/subtasks", response_model=list[TaskOut])
async def add_subtasks(task_id: str, body: CardIds, db: DB, user: CurrentUser):
    """Put existing cards of the project into this card, as its subtasks (after the ones it has).
    All or nothing: a card that can't be one (a playbook run's, one with subtasks of its own, one in
    another card) stops it."""
    parent = await task_or_404(db, user, parse_uuid(task_id), write=True)
    cards = await _cards(db, user, parent.project_id, body.task_ids)
    try:
        await S.adopt(db, parent, cards, user)
        await S.follow(db, parent.id, user)
    except S.SubtaskError as exc:
        raise HTTPException(422, str(exc)) from exc
    await db.commit()
    await _publish(db, cards, parent)
    return await tasks_out(db, await S.children(db, parent.id, archived=False))


@router.post("/tasks/{task_id}/subtasks/order", response_model=list[TaskOut])
async def order_subtasks(task_id: str, body: CardIds, db: DB, user: CurrentUser):
    """Put its subtasks in this order (any it has that aren't listed go after)."""
    parent = await task_or_404(db, user, parse_uuid(task_id), write=True)
    await S.reorder(db, parent, body.task_ids)
    await db.commit()
    await publish_task(db, parent)
    return await tasks_out(db, await S.children(db, parent.id, archived=False))


@router.post("/tasks/{task_id}/release", response_model=TaskOut)
async def release_subtask(task_id: str, db: DB, user: CurrentUser):
    """Make a subtask its own card on the board again."""
    card = await task_or_404(db, user, parse_uuid(task_id), write=True)
    try:
        parent = await S.release(db, card, user)
    except S.SubtaskError as exc:
        raise HTTPException(422, str(exc)) from exc
    await db.commit()
    await _publish(db, [card], parent)
    return await publish_task(db, card)


@router.post("/tasks/{task_id}/subtasks/run", response_model=RunAllOut)
async def run_subtasks(task_id: str, body: RunAll, db: DB, user: CurrentUser):
    """Run every command subtask that isn't finished or already running (moving each to In
    Progress): all now, or `at_once` at a time, the rest starting as others end (1: one after
    another). Ones that wait on unfinished cards are left out unless `force`."""
    if user.role == m.UserRole.viewer:
        raise HTTPException(403, "Viewers can't run cards")
    parent = await task_or_404(db, user, parse_uuid(task_id), write=True)
    started, skipped, queued = await S.run_all(db, parent, user, force=body.force, at_once=body.at_once)
    await db.commit()
    await _started(db, started, parent)
    return RunAllOut(started=[k.id for k in started], skipped=skipped, queued=queued)


@router.post("/tasks/{task_id}/subtasks/stop")
async def stop_subtasks(task_id: str, db: DB, user: CurrentUser) -> dict[str, int]:
    """Stop a Run all with a limit from starting more; what's running carries on."""
    parent = await task_or_404(db, user, parse_uuid(task_id), write=True)
    waiting = S.stop(parent)
    await db.commit()
    await publish_task(db, parent)
    return {"not_started": waiting}


@router.post("/tasks/{task_id}/subtasks/many", response_model=list[TaskOut], status_code=201)
async def many_subtasks(task_id: str, body: Many, db: DB, user: CurrentUser):
    """One subtask per target (a list, a saved list or an asset query), each running the library
    command with the target in `target_arg`: one per LXC, one per host in a scope group. Run them
    with Run all."""
    from ..api.tasks import _bind_vars, _use_library_command
    from ..command_library import load_spec
    from ..services import column_with_role

    parent = await task_or_404(db, user, parse_uuid(task_id), write=True)
    spec = await load_spec(db, body.command_ref)
    if spec is None:
        raise HTTPException(404, f"Command “{body.command_ref}” not found")
    if body.target_arg not in {a.name for a in spec.args}:
        raise HTTPException(422, f"target_arg: {spec.id} has no argument {body.target_arg}")
    try:
        await S.check_parent(db, parent)
        targets = await S.targets_of(db, parent.project_id, body.targets)
    except S.SubtaskError as exc:
        raise HTTPException(422, str(exc)) from exc
    column = await column_with_role(db, parent.project_id, m.ColumnRole.todo) or await db.get(m.BoardColumn, parent.column_id)
    cards = []
    for target in targets:
        title = (body.title.replace("{{ target }}", target).replace("{{target}}", target) if body.title else f"{spec.name}: {target}")[:300]
        card = m.Task(project_id=parent.project_id, column_id=column.id, position=0, title=title, kind=m.TaskKind.command,
                      created_by=user.id)
        await _use_library_command(db, card, spec.id, {**body.args, body.target_arg: target})
        db.add(card)
        await db.flush()
        if body.var_bindings:
            await _bind_vars(db, card, dict(body.var_bindings))
        cards.append(card)
    try:
        await S.adopt(db, parent, cards, user)
    except S.SubtaskError as exc:
        raise HTTPException(422, str(exc)) from exc
    await db.commit()
    await _publish(db, cards, parent)
    return await tasks_out(db, cards)


async def _started(db, started: list[m.Task], parent: m.Task | None) -> None:
    from ..orchestrator.dispatcher import dispatcher

    for kid in started:
        await publish_task(db, kid)
    if parent is not None:
        await publish_task(db, parent)
    dispatcher.wake()


@router.post("/projects/{project_id}/consolidate", response_model=TaskOut, status_code=201)
async def consolidate(project_id: str, body: Consolidate, db: DB, user: CurrentUser):
    """Turn several cards into one activity card: a new card (`title`) holding them as its
    subtasks, so the board shows one card instead of many. For tidying a board."""
    project = await project_or_404(db, user, parse_uuid(project_id), write=True)
    cards = await _cards(db, user, project.id, body.task_ids)
    column = None
    if body.column_id is not None:
        column = await db.get(m.BoardColumn, body.column_id)
        if column is None or column.project_id != project.id:
            raise HTTPException(404, "Column not found")
    try:
        parent = await S.consolidate(db, project, body.title, cards, user, description=body.description, column=column)
        await S.follow(db, parent.id, user)
    except S.SubtaskError as exc:
        raise HTTPException(422, str(exc)) from exc
    await db.commit()
    await _publish(db, cards, None)
    return await publish_task(db, parent, "task.created")


class PlaybookDraft(BaseModel):
    yaml: str
    # Subtasks a playbook step can't be (manual ones, commands typed by hand).
    left_out: list[str]


@router.get("/tasks/{task_id}/subtasks/playbook", response_model=PlaybookDraft)
async def subtasks_as_playbook(task_id: str, db: DB, user: CurrentUser):
    """An activity's subtasks as a playbook draft (YAML) to save in the Library and run again."""
    parent = await task_or_404(db, user, parse_uuid(task_id))
    try:
        text, left_out = await S.as_playbook(db, parent)
    except S.SubtaskError as exc:
        raise HTTPException(422, str(exc)) from exc
    return PlaybookDraft(yaml=text, left_out=left_out)


class FromPlaybook(BaseModel):
    project_id: uuid.UUID
    inputs: dict[str, Any] = Field(default_factory=dict)


class FromPlaybookOut(BaseModel):
    task: TaskOut
    # Steps left out (they need a playbook run), with why.
    left_out: list[str]


@router.post("/playbooks/{playbook_id}/activity", response_model=FromPlaybookOut, status_code=201)
async def activity_from_playbook(playbook_id: str, body: FromPlaybook, db: DB, user: CurrentUser):
    """A new activity card whose subtasks are the playbook's command steps (inputs filled in, depends_on
    as "waits on"), to work through by hand rather than as a run. Nothing starts."""
    from ..playbook_library import load_playbook_spec

    project = await project_or_404(db, user, body.project_id, write=True)
    spec = await load_playbook_spec(db, playbook_id)
    if spec is None:
        raise HTTPException(404, f"Playbook “{playbook_id}” not found")
    inputs = dict(body.inputs)
    for inp in spec.inputs:
        if inputs.get(inp.name) in (None, ""):
            if inp.default is not None:
                inputs[inp.name] = inp.default
            elif inp.required:
                raise HTTPException(422, f"Missing required input “{inp.name}”")
    try:
        parent, notes = await S.from_playbook(db, spec, project, inputs, user)
    except S.SubtaskError as exc:
        raise HTTPException(422, str(exc)) from exc
    await db.commit()
    kids = await S.children(db, parent.id, archived=False)
    await _publish(db, kids, None)
    return FromPlaybookOut(task=await publish_task(db, parent, "task.created"), left_out=notes)
