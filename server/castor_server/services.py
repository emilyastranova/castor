"""Domain operations shared by the REST API, the agent gateway and the dispatcher."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from . import models as m
from .events import bus
from .schemas import ColumnOut, JobOut, ProjectOut, TaskOut

DEFAULT_COLUMNS: list[tuple[str, m.ColumnRole | None]] = [
    ("Backlog", m.ColumnRole.backlog),
    ("To Do", m.ColumnRole.todo),
    ("In Progress", m.ColumnRole.active),
    ("Review", m.ColumnRole.review),
    ("Done", m.ColumnRole.done),
]


def utcnow() -> datetime:
    return datetime.now(UTC)


# ---------- activity ----------


async def record_activity(
    db: AsyncSession,
    *,
    type: str,
    message: str,
    project_id: uuid.UUID | None,
    task_id: uuid.UUID | None = None,
    actor_type: str = "system",
    actor_id: str | None = None,
    actor_name: str = "castor",
    payload: dict[str, Any] | None = None,
) -> m.ActivityEvent:
    event = m.ActivityEvent(
        project_id=project_id,
        task_id=task_id,
        actor_type=actor_type,
        actor_id=actor_id,
        actor_name=actor_name,
        type=type,
        message=message,
        payload=payload or {},
    )
    db.add(event)
    await db.flush()
    bus.publish(
        "activity.created",
        project_id,
        {
            "id": event.id,
            "project_id": project_id,
            "task_id": task_id,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "actor_name": actor_name,
            "type": type,
            "message": message,
            "payload": event.payload,
            "created_at": event.created_at,
        },
    )
    return event


def actor_of(user: m.User | None) -> dict[str, Any]:
    if user is None:
        return {"actor_type": "system", "actor_id": None, "actor_name": "castor"}
    return {"actor_type": "user", "actor_id": str(user.id), "actor_name": user.display_name}


# ---------- serialisation ----------


async def latest_jobs_for(db: AsyncSession, task_ids: list[uuid.UUID]) -> dict[uuid.UUID, m.Job]:
    """Return the most recent job per task (by queued_at)."""
    if not task_ids:
        return {}
    rows = (
        await db.execute(
            select(m.Job)
            .where(m.Job.task_id.in_(task_ids))
            .order_by(m.Job.task_id, m.Job.queued_at.desc())
        )
    ).scalars()
    latest: dict[uuid.UUID, m.Job] = {}
    for job in rows:
        latest.setdefault(job.task_id, job)
    return latest


async def counts_for(
    db: AsyncSession, task_ids: list[uuid.UUID]
) -> tuple[dict[uuid.UUID, int], dict[uuid.UUID, int]]:
    if not task_ids:
        return {}, {}
    comment_rows = await db.execute(
        select(m.Comment.task_id, func.count())
        .where(m.Comment.task_id.in_(task_ids))
        .group_by(m.Comment.task_id)
    )
    attach_rows = await db.execute(
        select(m.Attachment.task_id, func.count())
        .where(m.Attachment.task_id.in_(task_ids))
        .group_by(m.Attachment.task_id)
    )
    return dict(comment_rows.all()), dict(attach_rows.all())  # type: ignore[arg-type]


async def task_out(db: AsyncSession, task: m.Task) -> TaskOut:
    latest = await latest_jobs_for(db, [task.id])
    comments, attachments = await counts_for(db, [task.id])
    out = TaskOut.model_validate(task)
    job = latest.get(task.id)
    out.latest_job = JobOut.model_validate(job) if job else None
    out.comment_count = comments.get(task.id, 0)
    out.attachment_count = attachments.get(task.id, 0)
    return out


async def tasks_out(db: AsyncSession, tasks: list[m.Task]) -> list[TaskOut]:
    ids = [t.id for t in tasks]
    latest = await latest_jobs_for(db, ids)
    comments, attachments = await counts_for(db, ids)
    result = []
    for task in tasks:
        out = TaskOut.model_validate(task)
        job = latest.get(task.id)
        out.latest_job = JobOut.model_validate(job) if job else None
        out.comment_count = comments.get(task.id, 0)
        out.attachment_count = attachments.get(task.id, 0)
        result.append(out)
    return result


async def publish_task(db: AsyncSession, task: m.Task, name: str = "task.updated") -> TaskOut:
    out = await task_out(db, task)
    bus.publish(name, task.project_id, out)
    return out


def publish_job(job: m.Job, project_id: uuid.UUID) -> None:
    bus.publish("job.updated", project_id, JobOut.model_validate(job))


# ---------- projects / columns ----------


async def create_default_columns(db: AsyncSession, project: m.Project) -> list[m.BoardColumn]:
    columns = []
    for index, (name, role) in enumerate(DEFAULT_COLUMNS):
        column = m.BoardColumn(project_id=project.id, name=name, role=role, position=index * 10.0)
        db.add(column)
        columns.append(column)
    await db.flush()
    return columns


async def column_with_role(
    db: AsyncSession, project_id: uuid.UUID, role: m.ColumnRole
) -> m.BoardColumn | None:
    return (
        await db.execute(
            select(m.BoardColumn)
            .where(m.BoardColumn.project_id == project_id, m.BoardColumn.role == role)
            .order_by(m.BoardColumn.position)
            .limit(1)
        )
    ).scalar_one_or_none()


async def next_position(db: AsyncSession, column_id: uuid.UUID) -> float:
    current = (
        await db.execute(select(func.max(m.Task.position)).where(m.Task.column_id == column_id))
    ).scalar()
    return (current or 0) + 10.0


# ---------- tasks / jobs ----------


async def get_task(db: AsyncSession, task_id: uuid.UUID) -> m.Task | None:
    return (
        await db.execute(
            select(m.Task).options(selectinload(m.Task.column)).where(m.Task.id == task_id)
        )
    ).scalar_one_or_none()


async def active_job_for(db: AsyncSession, task_id: uuid.UUID) -> m.Job | None:
    return (
        await db.execute(
            select(m.Job).where(
                m.Job.task_id == task_id,
                m.Job.status.in_(
                    [m.JobStatus.queued, m.JobStatus.dispatched, m.JobStatus.running]
                ),
            )
        )
    ).scalars().first()


async def enqueue_job(
    db: AsyncSession, task: m.Task, user: m.User | None, *, reason: str = "manual"
) -> m.Job:
    """Create a queued Job for a command task. Caller must commit and then pump the dispatcher."""
    job = m.Job(
        task_id=task.id,
        command=task.command,
        image=task.image,
        timeout_s=task.timeout_s,
        env={},
        created_by=user.id if user else None,
    )
    db.add(job)
    await db.flush()
    await record_activity(
        db,
        type="job.queued",
        message=f"Queued “{task.title}” ({reason})",
        project_id=task.project_id,
        task_id=task.id,
        payload={"job_id": str(job.id)},
        **actor_of(user),
    )
    publish_job(job, task.project_id)
    return job


async def move_task(
    db: AsyncSession,
    task: m.Task,
    column: m.BoardColumn,
    position: float,
    user: m.User | None,
) -> m.Job | None:
    """Move a task. Returns a newly queued job if the move triggered a run."""
    previous_column_id = task.column_id
    task.column_id = column.id
    task.position = position
    await db.flush()

    job: m.Job | None = None
    entering_active = column.role == m.ColumnRole.active and previous_column_id != column.id
    if (
        entering_active
        and task.kind == m.TaskKind.command
        and task.run_on_move
        and task.command.strip()
        and await active_job_for(db, task.id) is None
    ):
        job = await enqueue_job(db, task, user, reason="moved to In Progress")

    if previous_column_id != column.id:
        await record_activity(
            db,
            type="task.moved",
            message=f"Moved “{task.title}” to {column.name}",
            project_id=task.project_id,
            task_id=task.id,
            payload={"from": str(previous_column_id), "to": str(column.id)},
            **actor_of(user),
        )
    return job


def board_payload(project: m.Project, columns: list[m.BoardColumn], tasks: list[TaskOut]):
    return {
        "project": ProjectOut.model_validate(project),
        "columns": [ColumnOut.model_validate(c) for c in columns],
        "tasks": tasks,
    }
