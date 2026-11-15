"""Archived cards (services.archive_task): off the board until restored, one at a time or a
whole column, and the list of a project's archived cards."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Text, cast, func, or_, select

from .. import models as m
from ..access import check_project, project_or_404
from ..deps import DB, CurrentUser, parse_uuid
from ..schemas import TaskOut
from ..services import (
    ArchiveError,
    archive_task,
    get_task,
    publish_task,
    publish_waiting,
    restore_task,
    tasks_out,
)

router = APIRouter(tags=["tasks"])


async def _task(db: DB, user: m.User, task_id: str) -> m.Task:
    task = await get_task(db, parse_uuid(task_id))
    if task is None:
        raise HTTPException(404, "Task not found")
    await check_project(db, user, task.project_id, write=True)
    return task


@router.post("/tasks/{task_id}/archive", response_model=TaskOut)
async def archive_card(task_id: str, db: DB, user: CurrentUser):
    """Take a card off the board. It keeps everything (comments, files, runs) and can be
    restored; search and links still find it. Not while it's running."""
    task = await _task(db, user, task_id)
    try:
        changed = await archive_task(db, task, user)
    except ArchiveError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not changed:
        return await _out(db, task)  # already archived
    await db.commit()
    await publish_waiting(db, task.project_id, task.id)
    return await publish_task(db, task)


@router.post("/tasks/{task_id}/restore", response_model=TaskOut)
async def restore_card(task_id: str, db: DB, user: CurrentUser):
    """Put an archived card back on the board, in the column it was in."""
    task = await _task(db, user, task_id)
    if not await restore_task(db, task, user):
        return await _out(db, task)  # wasn't archived
    await db.commit()
    await publish_waiting(db, task.project_id, task.id)
    return await publish_task(db, task)


async def _out(db: DB, task: m.Task) -> TaskOut:
    return (await tasks_out(db, [task]))[0]


class ColumnArchived(BaseModel):
    archived: int  # cards archived
    skipped: list[str] = Field(default_factory=list)  # why the others weren't (running…)


@router.post("/projects/{project_id}/columns/{column_id}/archive", response_model=ColumnArchived)
async def archive_column(project_id: str, column_id: str, db: DB, user: CurrentUser):
    """Archive every card in a column (Done, say), except those that are running."""
    project = await project_or_404(db, user, parse_uuid(project_id), write=True)
    column = await db.get(m.BoardColumn, parse_uuid(column_id))
    if column is None or column.project_id != project.id:
        raise HTTPException(404, "Column not found")
    cards = (await db.execute(
        select(m.Task).where(m.Task.column_id == column.id, m.Task.archived_at.is_(None), m.Task.playbook_step_id.is_(None))
        .order_by(m.Task.position)
    )).scalars().all()
    done, skipped = [], []
    for task in cards:
        try:
            if await archive_task(db, task, user):
                done.append(task)
        except ArchiveError as exc:
            skipped.append(str(exc))
    await db.commit()
    for task in done:
        await publish_task(db, task)
        await publish_waiting(db, project.id, task.id)
    return ColumnArchived(archived=len(done), skipped=skipped)


class ArchivedList(BaseModel):
    items: list[TaskOut]
    total: int


@router.get("/projects/{project_id}/archived", response_model=ArchivedList)
async def archived_cards(project_id: str, db: DB, user: CurrentUser, q: str = "", limit: int = 50, offset: int = 0):
    """A project's archived cards, most recently archived first; `q` matches title, description and tags."""
    project = await project_or_404(db, user, parse_uuid(project_id))
    where = [m.Task.project_id == project.id, m.Task.archived_at.is_not(None), m.Task.playbook_step_id.is_(None)]
    if q.strip():
        like = f"%{q.strip().lower()}%"
        where.append(or_(func.lower(m.Task.title).like(like), func.lower(m.Task.description).like(like),
                         func.lower(cast(m.Task.tags, Text)).like(like)))
    total = (await db.execute(select(func.count()).select_from(m.Task).where(*where))).scalar_one()
    rows = (await db.execute(
        select(m.Task).where(*where).order_by(m.Task.archived_at.desc()).limit(max(1, min(limit, 200))).offset(max(0, offset))
    )).scalars().all()
    return ArchivedList(items=await tasks_out(db, list(rows)), total=total)
