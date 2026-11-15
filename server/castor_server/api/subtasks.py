"""Cards with subtasks (subtasks.py): an activity card's subtasks, adding existing cards to it,
making a subtask its own card again, ordering and running them, and turning several cards into a
new activity (for tidying a board, from the UI or over MCP)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import models as m
from .. import subtasks as S
from ..access import project_or_404, task_or_404
from ..deps import DB, CurrentUser, parse_uuid
from ..schemas import TaskOut
from ..services import active_job_for, publish_task, start_run, tasks_out, unfinished_blockers

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


class RunAllOut(BaseModel):
    started: list[uuid.UUID]
    skipped: list[dict[str, str]]


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
    Progress). Ones that wait on unfinished cards are left out unless `force`."""
    if user.role == m.UserRole.viewer:
        raise HTTPException(403, "Viewers can't run cards")
    parent = await task_or_404(db, user, parse_uuid(task_id), write=True)
    kids = await S.children(db, parent.id, archived=False)
    roles = dict((await db.execute(select(m.BoardColumn.id, m.BoardColumn.role)
                                   .where(m.BoardColumn.project_id == parent.project_id))).all())
    blockers = await unfinished_blockers(db, kids)
    started, skipped = [], []
    for kid in kids:
        if kid.kind != m.TaskKind.command or not kid.command.strip():
            continue
        if roles.get(kid.column_id) in S.DONE_ROLES:
            continue
        if await active_job_for(db, kid.id) is not None:
            skipped.append({"id": str(kid.id), "title": kid.title, "reason": "already running"})
            continue
        if blockers.get(kid.id) and not body.force:
            skipped.append({"id": str(kid.id), "title": kid.title, "reason": "waits on " + ", ".join(b["title"] for b in blockers[kid.id])})
            continue
        await start_run(db, kid, user)
        started.append(kid.id)
    await db.commit()
    from ..orchestrator.dispatcher import dispatcher

    for kid in kids:
        if kid.id in started:
            await publish_task(db, kid)
    await publish_task(db, parent)
    dispatcher.wake()
    return RunAllOut(started=started, skipped=skipped)


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
