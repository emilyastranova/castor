"""Domain operations shared by the REST API, the agent gateway and the dispatcher."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from . import models as m
from .command_library import apply_to_task, load_spec
from .events import bus
from .playbook_spec import PlaybookSpec, resolve_step_args
from .schemas import ColumnOut, JobOut, PlaybookRunOut, ProjectOut, TaskOut

DEFAULT_COLUMNS: list[tuple[str, m.ColumnRole | None]] = [
    ("Backlog", m.ColumnRole.backlog),
    ("To Do", m.ColumnRole.todo),
    ("In Progress", m.ColumnRole.active),
    ("Review", m.ColumnRole.review),
    ("Done", m.ColumnRole.done),
    ("Reported", m.ColumnRole.reported),
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
        container_flags=list(task.container_flags or []),
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


async def start_run(db: AsyncSession, task: m.Task, user: m.User | None) -> tuple[m.Job, bool]:
    """Run a command task now: move it into the project's active column (if it isn't there
    already and the project has one), then queue a job. Returns (job, moved).

    Deliberately does not go through move_task, which would queue a second job when the
    card enters the active column with run_on_move set.
    """
    moved = False
    active = await column_with_role(db, task.project_id, m.ColumnRole.active)
    if active is not None and task.column_id != active.id:
        previous_column_id = task.column_id
        task.column_id = active.id
        task.position = await next_position(db, active.id)
        await db.flush()
        await record_activity(
            db,
            type="task.moved",
            message=f"Moved “{task.title}” to {active.name}",
            project_id=task.project_id,
            task_id=task.id,
            payload={"from": str(previous_column_id), "to": str(active.id), "reason": "run"},
            **actor_of(user),
        )
        moved = True
    job = await enqueue_job(db, task, user, reason="run")
    return job, moved


def board_payload(project: m.Project, columns: list[m.BoardColumn], tasks: list[TaskOut]):
    return {
        "project": ProjectOut.model_validate(project),
        "columns": [ColumnOut.model_validate(c) for c in columns],
        "tasks": tasks,
    }


# ---------- playbooks ----------


async def playbook_run_out(db: AsyncSession, run: m.PlaybookRun) -> PlaybookRunOut:
    tasks = (
        await db.execute(
            select(m.Task)
            .options(selectinload(m.Task.column))
            .where(m.Task.playbook_run_id == run.id)
            .order_by(m.Task.created_at)
        )
    ).scalars().all()
    tasks_o = await tasks_out(db, list(tasks))
    return PlaybookRunOut(
        id=run.id,
        playbook_id=run.playbook_id,
        playbook_name=run.playbook_name,
        project_id=run.project_id,
        status=run.status.value if hasattr(run.status, "value") else str(run.status),
        inputs=run.inputs,
        step_task_ids=run.step_task_ids,
        spec_snapshot=run.spec_snapshot,
        finished_at=run.finished_at,
        created_at=run.created_at,
        tasks=tasks_o,
    )


async def publish_playbook_run(
    db: AsyncSession, run: m.PlaybookRun, name: str = "playbook_run.updated"
) -> PlaybookRunOut:
    out = await playbook_run_out(db, run)
    bus.publish(name, run.project_id, out)
    return out


async def start_playbook_run(
    db: AsyncSession,
    spec: PlaybookSpec,
    project_id: uuid.UUID,
    inputs: dict[str, Any],
    user: m.User | None,
) -> tuple[m.PlaybookRun, list[m.Job]]:
    """Instantiate a playbook as linked cards on the project board and queue ready steps."""
    run = m.PlaybookRun(
        playbook_id=spec.id,
        playbook_name=spec.name,
        project_id=project_id,
        status=m.PlaybookRunStatus.running,
        inputs=inputs,
        step_task_ids={},
        spec_snapshot=spec.model_dump(mode="json"),
        created_by=user.id if user else None,
    )
    db.add(run)
    await db.flush()

    active_col = await column_with_role(db, project_id, m.ColumnRole.active)
    todo_col = await column_with_role(db, project_id, m.ColumnRole.todo)
    if todo_col is None:
        todo_col = (
            await db.execute(
                select(m.BoardColumn)
                .where(m.BoardColumn.project_id == project_id)
                .order_by(m.BoardColumn.position)
                .limit(1)
            )
        ).scalar_one_or_none()
    if active_col is None:
        active_col = todo_col

    step_task_map: dict[str, uuid.UUID] = {}
    tasks_to_add: list[m.Task] = []
    for step in spec.steps:
        col = todo_col if todo_col else active_col
        pos = await next_position(db, col.id) if col else 0.0
        task = m.Task(
            project_id=project_id,
            column_id=col.id if col else active_col.id,
            position=pos,
            title=f"[{spec.name}] {step.name}",
            description=step.description,
            kind=m.TaskKind.command,
            playbook_run_id=run.id,
            playbook_step_id=step.id,
            depends_on=list(step.depends_on),
            command_ref=step.command_ref,
            created_by=user.id if user else None,
        )
        db.add(task)
        tasks_to_add.append(task)

    await db.flush()
    for task in tasks_to_add:
        if task.playbook_step_id:
            step_task_map[task.playbook_step_id] = task.id
    run.step_task_ids = {k: str(v) for k, v in step_task_map.items()}
    await db.flush()

    enqueued_jobs: list[m.Job] = []
    for step in spec.steps:
        if not step.depends_on:
            task_id = step_task_map[step.id]
            task = next(t for t in tasks_to_add if t.id == task_id)
            resolved_args = resolve_step_args(step.args, inputs, {})
            cmd_spec = await load_spec(db, step.command_ref)
            if cmd_spec is not None:
                apply_to_task(task, cmd_spec, resolved_args)
            if active_col is not None and task.column_id != active_col.id:
                task.column_id = active_col.id
                task.position = await next_position(db, active_col.id)
            await db.flush()
            job = await enqueue_job(db, task, user, reason=f"playbook initial step: {step.name}")
            enqueued_jobs.append(job)

    await record_activity(
        db,
        type="playbook_run.started",
        message=f"Started playbook “{spec.name}”",
        project_id=project_id,
        payload={"run_id": str(run.id), "playbook_id": spec.id},
        **actor_of(user),
    )
    await publish_playbook_run(db, run)
    return run, enqueued_jobs


async def advance_playbook_run(
    db: AsyncSession, run_id: uuid.UUID
) -> tuple[m.PlaybookRun | None, list[m.Job]]:
    """Check dependencies of steps in a running playbook and enqueue ready steps."""
    run = await db.get(m.PlaybookRun, run_id)
    if run is None or run.status != m.PlaybookRunStatus.running:
        return run, []

    spec_dict = run.spec_snapshot
    steps = spec_dict.get("steps", [])
    step_task_ids = run.step_task_ids or {}

    task_uuids = [uuid.UUID(tid) for tid in step_task_ids.values()]
    tasks_rows = (
        await db.execute(select(m.Task).where(m.Task.id.in_(task_uuids)))
    ).scalars().all()
    tasks_by_id = {t.id: t for t in tasks_rows}
    latest_jobs = await latest_jobs_for(db, task_uuids)

    step_outputs: dict[str, dict[str, Any]] = {}
    for step in steps:
        s_id = step["id"]
        tid_str = step_task_ids.get(s_id)
        if not tid_str:
            continue
        tid = uuid.UUID(tid_str)
        job = latest_jobs.get(tid)
        if job and job.status == m.JobStatus.succeeded:
            outputs_rows = (
                await db.execute(select(m.JobOutput).where(m.JobOutput.job_id == job.id))
            ).scalars().all()
            step_outputs[s_id] = {o.name: o.value for o in outputs_rows}

    active_col = await column_with_role(db, run.project_id, m.ColumnRole.active)
    enqueued_jobs: list[m.Job] = []

    for step in steps:
        s_id = step["id"]
        tid_str = step_task_ids.get(s_id)
        if not tid_str:
            continue
        tid = uuid.UUID(tid_str)
        task = tasks_by_id.get(tid)
        job = latest_jobs.get(tid)

        if job is not None:
            continue

        deps = step.get("depends_on", [])
        deps_succeeded = True
        for dep_id in deps:
            dep_tid_str = step_task_ids.get(dep_id)
            if not dep_tid_str:
                deps_succeeded = False
                break
            dep_tid = uuid.UUID(dep_tid_str)
            dep_job = latest_jobs.get(dep_tid)
            if dep_job is None or dep_job.status != m.JobStatus.succeeded:
                deps_succeeded = False
                break

        if deps_succeeded and task is not None:
            resolved_args = resolve_step_args(step.get("args", {}), run.inputs, step_outputs)
            cmd_spec = await load_spec(db, step["command_ref"])
            if cmd_spec is not None:
                apply_to_task(task, cmd_spec, resolved_args)
            if active_col is not None and task.column_id != active_col.id:
                task.column_id = active_col.id
                task.position = await next_position(db, active_col.id)
            await db.flush()
            new_job = await enqueue_job(
                db, task, None, reason=f"playbook step {step.get('name', s_id)} dependencies ready"
            )
            enqueued_jobs.append(new_job)
            latest_jobs[tid] = new_job

    all_succeeded = True
    any_failed = False
    any_running = False

    for step in steps:
        s_id = step["id"]
        tid_str = step_task_ids.get(s_id)
        if not tid_str:
            all_succeeded = False
            continue
        tid = uuid.UUID(tid_str)
        job = latest_jobs.get(tid)
        if job is None:
            all_succeeded = False
        elif job.status == m.JobStatus.succeeded:
            pass
        elif job.status in (m.JobStatus.queued, m.JobStatus.dispatched, m.JobStatus.running):
            all_succeeded = False
            any_running = True
        elif job.status in (m.JobStatus.failed, m.JobStatus.cancelled, m.JobStatus.lost):
            all_succeeded = False
            any_failed = True

    if all_succeeded:
        run.status = m.PlaybookRunStatus.succeeded
        run.finished_at = utcnow()
        await record_activity(
            db,
            type="playbook_run.succeeded",
            message=f"Playbook “{run.playbook_name}” completed successfully",
            project_id=run.project_id,
            payload={"run_id": str(run.id)},
        )
    elif any_failed and not any_running:
        run.status = m.PlaybookRunStatus.failed
        run.finished_at = utcnow()
        await record_activity(
            db,
            type="playbook_run.failed",
            message=f"Playbook “{run.playbook_name}” failed",
            project_id=run.project_id,
            payload={"run_id": str(run.id)},
        )

    await db.commit()
    await publish_playbook_run(db, run)
    return run, enqueued_jobs


async def cancel_playbook_run(
    db: AsyncSession, run_id: uuid.UUID, user: m.User | None = None
) -> m.PlaybookRun | None:
    run = await db.get(m.PlaybookRun, run_id)
    if run is None or run.status != m.PlaybookRunStatus.running:
        return run

    run.status = m.PlaybookRunStatus.cancelled
    run.finished_at = utcnow()

    await record_activity(
        db,
        type="playbook_run.cancelled",
        message=f"Playbook “{run.playbook_name}” was cancelled",
        project_id=run.project_id,
        payload={"run_id": str(run.id)},
        **actor_of(user),
    )
    await db.commit()
    await publish_playbook_run(db, run)

    # Cancel active jobs for all steps
    step_task_ids = run.step_task_ids or {}
    task_uuids = [uuid.UUID(tid) for tid in step_task_ids.values()]
    latest_jobs = await latest_jobs_for(db, task_uuids)

    from .orchestrator.dispatcher import dispatcher

    for tid, job in latest_jobs.items():
        if job and not job.status.is_terminal:
            await dispatcher.cancel(job.id)

    return run

