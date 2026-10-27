"""Domain operations shared by the REST API, the agent gateway and the dispatcher."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from . import models as m
from . import notifications, variables
from .command_library import apply_library, load_spec, refresh_list_refs
from .command_render import ArgumentError
from .events import bus
from .playbook_conditions import MAX_LOG_BYTES, failed_checks
from .playbook_spec import (
    PLACEHOLDER_RE,
    PlaybookSpec,
    PlaybookStepSpec,
    handled_steps,
    has_empty_upstream,
    lookup_ref,
    readiness,
    resolve_step_args,
    resolve_value,
    run_outcome,
    step_statuses,
)
from .playbook_transforms import TransformError, as_items, run_transform
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
    from .command_render import ArgumentError

    try:
        await refresh_list_refs(db, task)
    except ArgumentError:
        pass  # a list that no longer exists: the job runs with the last rendered command
    job = m.Job(
        task_id=task.id,
        command=task.command,
        image=task.image,
        container_flags=list(task.container_flags or []),
        timeout_s=task.timeout_s,
        env={},
        var_bindings=dict(task.var_bindings or {}),
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


def _run_spec(run: m.PlaybookRun) -> PlaybookSpec:
    return PlaybookSpec.model_validate(run.spec_snapshot)


def _run_task_ids(run: m.PlaybookRun) -> dict[str, uuid.UUID]:
    return {step_id: uuid.UUID(tid) for step_id, tid in (run.step_task_ids or {}).items()}


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
    task_ids = _run_task_ids(run)
    latest_jobs = await latest_jobs_for(db, list(task_ids.values()))
    job_status, job_ids = _job_states(task_ids, latest_jobs, await _parsing_jobs(db, latest_jobs))
    status = run.status.value if hasattr(run.status, "value") else str(run.status)
    return PlaybookRunOut(
        id=run.id,
        playbook_id=run.playbook_id,
        playbook_name=run.playbook_name,
        project_id=run.project_id,
        status=status,
        inputs=run.inputs,
        step_task_ids=run.step_task_ids,
        step_results=run.step_results or {},
        step_status=step_statuses(_run_spec(run), job_status, run.step_results or {}, status, job_ids),
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
    """Instantiate a playbook: one card per command step (transform steps have no card),
    then run every step whose dependencies are already met. Caller commits."""
    run = m.PlaybookRun(
        playbook_id=spec.id,
        playbook_name=spec.name,
        project_id=project_id,
        status=m.PlaybookRunStatus.running,
        inputs=inputs,
        step_task_ids={},
        step_results={},
        spec_snapshot=spec.model_dump(mode="json"),
        created_by=user.id if user else None,
    )
    db.add(run)
    await db.flush()

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
    if todo_col is None:
        raise ValueError("project has no columns")

    tasks: list[m.Task] = []
    for step in spec.steps:
        if step.kind != "command":
            continue
        task = m.Task(
            project_id=project_id,
            column_id=todo_col.id,
            position=await next_position(db, todo_col.id),
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
        await db.flush()  # next_position must see this card
        tasks.append(task)
    run.step_task_ids = {t.playbook_step_id: str(t.id) for t in tasks if t.playbook_step_id}
    await db.flush()

    await record_activity(
        db,
        type="playbook_run.started",
        message=f"Started playbook “{spec.name}”",
        project_id=project_id,
        payload={"run_id": str(run.id), "playbook_id": spec.id},
        **actor_of(user),
    )
    jobs, touched = await _advance(db, run, user)
    for task in tasks:
        await publish_task(db, task, "task.created")
    for task in touched:
        if task not in tasks:
            await publish_task(db, task)
    await publish_playbook_run(db, run)
    return run, jobs


def _job_states(
    task_ids: dict[str, uuid.UUID], latest_jobs: dict[uuid.UUID, m.Job], parsing: set[uuid.UUID]
) -> tuple[dict[str, str], dict[str, str]]:
    """(step → job status, step → job id) for steps with a job. A finished job whose outputs
    are still being parsed counts as running, so nothing downstream judges it too early."""
    status: dict[str, str] = {}
    ids: dict[str, str] = {}
    for step_id, tid in task_ids.items():
        job = latest_jobs.get(tid)
        if job is not None:
            status[step_id] = "running" if job.id in parsing else job.status.value
            ids[step_id] = str(job.id)
    return status, ids


async def _parsing_jobs(db: AsyncSession, latest_jobs: dict[uuid.UUID, m.Job]) -> set[uuid.UUID]:
    """Jobs that finished recently, have outputs to parse, and have no parsed rows yet."""
    recent = utcnow() - timedelta(minutes=5)
    candidates = [
        j.id
        for j in latest_jobs.values()
        if j.status in (m.JobStatus.succeeded, m.JobStatus.failed)
        and j.started_at is not None
        and j.finished_at is not None
        and j.finished_at > recent
        and any(o.get("parser") for o in (j.output_specs or []))
    ]
    if not candidates:
        return set()
    parsed = set(
        (await db.execute(select(m.JobOutput.job_id).where(m.JobOutput.job_id.in_(candidates)).distinct())).scalars()
    )
    return set(candidates) - parsed


async def _job_log_tail(db: AsyncSession, job_id: uuid.UUID) -> str:
    """The end of a job's stdout and stderr (up to MAX_LOG_BYTES), for `expect.log`."""
    rows = await db.execute(
        select(m.JobLogChunk.data)
        .where(m.JobLogChunk.job_id == job_id, m.JobLogChunk.stream.in_(("stdout", "stderr")))
        .order_by(m.JobLogChunk.seq.desc())
    )
    parts: list[str] = []
    size = 0
    for (data,) in rows:
        parts.append(data)
        size += len(data)
        if size >= MAX_LOG_BYTES:
            break
    return "".join(reversed(parts))[-MAX_LOG_BYTES:]


def _job_failure(job: m.Job) -> str:
    if job.exit_code not in (None, 0):
        return f"the job failed (exit {job.exit_code})"
    return f"the job {'was lost' if job.status == m.JobStatus.lost else 'failed'}" + (f": {job.error}" if job.error else "")


async def _step_outputs(
    db: AsyncSession,
    spec: PlaybookSpec,
    task_ids: dict[str, uuid.UUID],
    latest_jobs: dict[uuid.UUID, m.Job],
    results: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Values available to bind: parsed outputs of succeeded jobs (and of failed ones a
    person chose to continue with), results of transforms."""
    outputs: dict[str, dict[str, Any]] = {}
    job_step: dict[uuid.UUID, str] = {}
    for step_id, tid in task_ids.items():
        job = latest_jobs.get(tid)
        if job is None:
            continue
        result = results.get(step_id) or {}
        accepted = result.get("status") == "succeeded" and result.get("job_id") == str(job.id)
        if job.status == m.JobStatus.succeeded or accepted:
            job_step[job.id] = step_id
    if job_step:
        rows = (
            await db.execute(select(m.JobOutput).where(m.JobOutput.job_id.in_(list(job_step))))
        ).scalars()
        for row in rows:
            if row.error is None:
                outputs.setdefault(job_step[row.job_id], {})[row.name] = row.value
    for step in spec.steps:
        result = results.get(step.id) or {}
        if step.kind == "transform" and result.get("status") == "succeeded":
            outputs[step.id] = dict(result.get("outputs") or {})
    return outputs


def step_link(run_id: uuid.UUID, step_id: str | None = None) -> str:
    return f"/playbooks?run={run_id}" + (f"&step={step_id}" if step_id else "")


async def _advance(
    db: AsyncSession, run: m.PlaybookRun, user: m.User | None = None
) -> tuple[list[m.Job], list[m.Task]]:
    """Move the run forward as far as it can go, repeating until nothing changes (a
    transform or a condition can make the next step ready at once):

    - judge finished jobs: check `expect`, and pause steps that `ask` on failure;
    - record steps left out because a branch they need wasn't taken;
    - start every ready step whose `when` holds (or mark it not_taken).

    Then settle the run's status. Returns the queued jobs and the cards that changed.
    Does not commit."""
    spec = _run_spec(run)
    by_id = {s.id: s for s in spec.steps}
    handled = handled_steps(spec)
    task_ids = _run_task_ids(run)
    tasks = {
        t.id: t
        for t in (
            await db.execute(select(m.Task).where(m.Task.id.in_(list(task_ids.values()))))
        ).scalars()
    }
    latest_jobs = await latest_jobs_for(db, list(task_ids.values()))
    parsing = await _parsing_jobs(db, latest_jobs)
    results: dict[str, Any] = dict(run.step_results or {})
    outputs = await _step_outputs(db, spec, task_ids, latest_jobs, results)
    active_col = await column_with_role(db, run.project_id, m.ColumnRole.active)
    done_col = await column_with_role(db, run.project_id, m.ColumnRole.done)
    run_status = run.status.value

    def statuses() -> dict[str, str]:
        job_status, job_ids = _job_states(task_ids, latest_jobs, parsing)
        return step_statuses(spec, job_status, results, run_status, job_ids)

    def set_result(step_id: str, **fields: Any) -> None:
        """Record a step's result, keeping how its condition was evaluated."""
        prev = results.get(step_id) or {}
        keep = {k: prev[k] for k in ("condition", "resolution") if k in prev}
        results[step_id] = {**keep, **fields, "finished_at": utcnow().isoformat()}

    jobs: list[m.Job] = []
    touched: list[m.Task] = []

    async def move(task: m.Task, column: m.BoardColumn | None) -> None:
        if column is not None and task.column_id != column.id:
            task.column_id = column.id
            task.position = await next_position(db, column.id)
            await db.flush()
        if task not in touched:
            touched.append(task)

    async def fail_step(step: PlaybookStepSpec, task: m.Task | None, error: str, **extra: Any) -> None:
        """A step went wrong: pause it for a person (on_failure: ask) or record the failure."""
        if step.on_failure != "ask":
            set_result(step.id, status="failed", error=error, **extra)
            return
        set_result(step.id, status="attention", reason=error, **extra)
        if task is not None and task not in touched:
            touched.append(task)
        await record_activity(
            db,
            type="playbook_step.attention",
            message=f"“{step.name}” needs a decision: {error}",
            project_id=run.project_id,
            task_id=task.id if task else None,
            payload={"run_id": str(run.id), "step_id": step.id},
        )
        await notifications.notify(
            db,
            kind="playbook_step.attention",
            severity="warning",
            title=f"{run.playbook_name}: “{step.name}” needs you",
            body=error,
            link=step_link(run.id, step.id),
            project_id=run.project_id,
            subject=f"run:{run.id}:step:{step.id}",
            payload={"run_id": str(run.id), "step_id": step.id},
        )

    async def judge_finished_jobs() -> bool:
        """Check each newly finished job once: its `expect`, or `ask` on failure."""
        changed = False
        for step in spec.steps:
            if step.kind != "command" or step.id not in task_ids:
                continue
            job = latest_jobs.get(task_ids[step.id])
            if job is None or not job.status.is_terminal or job.id in parsing:
                continue
            result = results.get(step.id) or {}
            if result.get("status") and result.get("job_id") == str(job.id):
                continue  # already judged
            task = tasks.get(task_ids[step.id])
            expect = step.expectation()
            if job.status == m.JobStatus.succeeded and expect is not None:
                try:
                    log_text = await _job_log_tail(db, job.id) if expect.log is not None else None
                    met, checks = expect.evaluate(outputs.get(step.id, {}), log_text)
                except TransformError as exc:
                    met, checks = False, [{"text": f"expect: {exc}", "met": False}]
                if met:
                    set_result(step.id, status="succeeded", job_id=str(job.id), expect=checks)
                else:
                    await fail_step(
                        step, task, f"unexpected output: {failed_checks(checks)}", job_id=str(job.id), expect=checks
                    )
                changed = True
            elif job.status in (m.JobStatus.failed, m.JobStatus.lost) and step.on_failure == "ask":
                await fail_step(step, task, _job_failure(job), job_id=str(job.id))
                changed = True
        return changed

    progressed = run_status != "cancelled"
    while progressed:
        progressed = await judge_finished_jobs()
        current = statuses()
        for step in spec.steps:
            task = tasks.get(task_ids[step.id]) if step.id in task_ids else None
            result = results.get(step.id) or {}

            if current[step.id] == "not_taken" and not result.get("status"):
                # Left out because a branch it needs wasn't taken: record it so its card
                # leaves To Do and the reason shows.
                set_result(step.id, status="not_taken", reason="a step it needs was not taken")
                progressed = True
                if task is not None:
                    await move(task, done_col)
                continue
            if current[step.id] != "pending":
                continue
            if readiness(step, current.get, by_id, handled) != "ready":
                continue
            progressed = True

            condition = step.condition()
            if condition is not None:
                try:
                    met, checks = condition.evaluate(
                        lambda v: resolve_value(v, run.inputs, outputs), current
                    )
                except TransformError as exc:
                    await fail_step(step, task, f"condition: {exc}")
                    continue
                results[step.id] = {"condition": {"met": met, "checks": checks}}
                if not met:
                    set_result(step.id, status="not_taken", reason="its condition was not met")
                    if task is not None:
                        await move(task, done_col)
                        await record_activity(
                            db,
                            type="playbook_step.not_taken",
                            message=f"Left out “{task.title}”: its condition was not met",
                            project_id=run.project_id,
                            task_id=task.id,
                            payload={"run_id": str(run.id), "step_id": step.id},
                        )
                    continue

            if step.skip_if_empty and has_empty_upstream(step, run.inputs, outputs):
                set_result(step.id, status="skipped", reason="an upstream value is empty")
                if task is not None:
                    await move(task, done_col)
                    await record_activity(
                        db,
                        type="playbook_step.skipped",
                        message=f"Skipped “{task.title}”: nothing to work on from upstream",
                        project_id=run.project_id,
                        task_id=task.id,
                        payload={"run_id": str(run.id), "step_id": step.id},
                    )
                continue

            if step.transform is not None:
                refs = step.refs()
                values = [lookup_ref(ref, run.inputs, outputs) for ref in refs]
                input_count = sum(len(as_items(v)) for v in values)
                try:
                    items = run_transform(step.transform, values)
                except TransformError as exc:
                    await fail_step(step, None, str(exc), input_count=input_count)
                    continue
                input_type = await _ref_type(db, spec, refs[0], task_ids, latest_jobs, results) if refs else None
                set_result(
                    step.id,
                    status="succeeded",
                    outputs={step.transform.output.name: items},
                    type=step.transform.result_type(input_type),
                    input_count=input_count,
                    count=len(items),
                )
                outputs[step.id] = {step.transform.output.name: items}
                continue

            if task is None:  # card deleted from the board
                set_result(step.id, status="failed", error="the step's card was deleted")
                continue
            error = await _prepare_command_step(db, task, step, run.inputs, outputs)
            if error is not None:
                await record_activity(
                    db,
                    type="playbook_step.failed",
                    message=f"Could not start “{task.title}”: {error}",
                    project_id=run.project_id,
                    task_id=task.id,
                    payload={"run_id": str(run.id), "step_id": step.id},
                )
                await fail_step(step, task, f"could not start: {error}")
                if task not in touched:
                    touched.append(task)
                continue
            # Keep only how it got here; the job carries the status from now on.
            results[step.id] = {k: v for k, v in (results.get(step.id) or {}).items() if k in ("condition", "resolution")}
            if not results[step.id]:
                results.pop(step.id)
            await move(task, active_col)
            reason = (
                "playbook step ready" if step.depends_on else f"playbook started: {step.name}"
            )
            job = await enqueue_job(db, task, user, reason=reason)
            latest_jobs[task.id] = job
            jobs.append(job)

    run.step_results = results  # reassign: JSON columns don't track in-place changes
    await _settle_run(db, run, spec, statuses())
    return jobs, touched


async def _prepare_command_step(
    db: AsyncSession,
    task: m.Task,
    step: PlaybookStepSpec,
    inputs: dict[str, Any],
    outputs: dict[str, dict[str, Any]],
) -> str | None:
    """Render the step's command onto its card. Returns an error message, or None."""
    cmd_spec = await load_spec(db, step.command_ref)
    if cmd_spec is None:
        return f"command {step.command_ref} is not in the library"
    try:
        await apply_library(db, task, cmd_spec, resolve_step_args(step.args, inputs, outputs))
    except ArgumentError as exc:
        return str(exc)
    bindings = {}
    for slot, value in step.vars.items():
        ref = PLACEHOLDER_RE.match(value.strip())
        target = inputs.get(ref.group(1).split(".")[1]) if ref else value
        row = await variables.find_set(db, target)
        if row is None:
            return f"vars.{slot}: variable set {target!r} not found"
        bindings[slot] = str(row.id)
    try:
        task.var_bindings = await variables.check_bindings(
            db, cmd_spec.vars, bindings, task.project_id, task
        )
    except variables.VariableError as exc:
        return str(exc)
    await db.flush()
    return None


async def _ref_type(
    db: AsyncSession,
    spec: PlaybookSpec,
    ref: str,
    task_ids: dict[str, uuid.UUID],
    latest_jobs: dict[uuid.UUID, m.Job],
    results: dict[str, Any],
) -> str | None:
    """The output type behind a `steps.x.outputs.y` reference, for labelling transforms."""
    parts = ref.split(".")
    if parts[0] != "steps":
        return None
    result = results.get(parts[1]) or {}
    if result.get("type"):
        return result["type"]
    tid = task_ids.get(parts[1])
    job = latest_jobs.get(tid) if tid else None
    if job is None:
        return None
    return (
        await db.execute(
            select(m.JobOutput.type).where(m.JobOutput.job_id == job.id, m.JobOutput.name == parts[3])
        )
    ).scalar_one_or_none()


async def _settle_run(db: AsyncSession, run: m.PlaybookRun, spec: PlaybookSpec, current: dict[str, str]) -> None:
    """Finish the run when no step can move any more; reopen it when a step was re-run."""
    if run.status == m.PlaybookRunStatus.cancelled:
        return
    new = m.PlaybookRunStatus(run_outcome(spec, current))
    if new == run.status:
        return
    run.status = new
    run.finished_at = None if new == m.PlaybookRunStatus.running else utcnow()
    if new == m.PlaybookRunStatus.running:
        return
    ok = new == m.PlaybookRunStatus.succeeded
    message = f"Playbook “{run.playbook_name}” " + ("completed successfully" if ok else "failed")
    await record_activity(
        db,
        type=f"playbook_run.{new.value}",
        message=message,
        project_id=run.project_id,
        payload={"run_id": str(run.id)},
    )
    failed = [s.name for s in spec.steps if current.get(s.id) in ("failed", "blocked", "cancelled")]
    await notifications.notify(
        db,
        kind=f"playbook_run.{new.value}",
        severity="success" if ok else "error",
        title=message,
        body="" if ok else "Didn't finish: " + ", ".join(failed[:6]) + ("…" if len(failed) > 6 else ""),
        link=step_link(run.id),
        project_id=run.project_id,
        subject=f"run:{run.id}",
        payload={"run_id": str(run.id)},
    )


async def advance_playbook_run(
    db: AsyncSession, run_id: uuid.UUID
) -> tuple[m.PlaybookRun | None, list[m.Job]]:
    """Called whenever one of the run's jobs finishes (after its outputs are parsed): queue
    the steps that became ready, commit and publish. The caller wakes the dispatcher."""
    run = await db.get(m.PlaybookRun, run_id)
    if run is None or run.status == m.PlaybookRunStatus.cancelled:
        return run, []
    jobs, touched = await _advance(db, run)
    await db.commit()
    for task in touched:
        await publish_task(db, task)
    await publish_playbook_run(db, run)
    return run, jobs


RESOLUTIONS = {
    "retry": "Retry",
    "continue": "Continue",
    "skip": "Skip branch",
    "fail": "Fail",
}


class ResolveError(ValueError):
    pass


async def resolve_step(
    db: AsyncSession,
    run: m.PlaybookRun,
    step_id: str,
    action: str,
    user: m.User | None,
    note: str = "",
    actor: str | None = None,
) -> list[m.Job]:
    """A decision on a step waiting for one (status attention):

    - retry: run it again (a command's card as it is now, so edit its command first if
      that's the fix; a transform or a step that couldn't start is re-evaluated);
    - continue: accept it as succeeded and carry on with whatever outputs it has;
    - skip: leave it and the steps that need it out (not_taken);
    - fail: fail it; dependents are blocked, unless a condition handles its failure.

    `actor` names who decided when it isn't a person (kept for automated resolvers).
    Commits, publishes, and returns the jobs it queued (the caller wakes the dispatcher)."""
    if action not in RESOLUTIONS:
        raise ResolveError(f"action must be one of {', '.join(RESOLUTIONS)}")
    if run.status == m.PlaybookRunStatus.cancelled:
        raise ResolveError("the run was cancelled")
    spec = _run_spec(run)
    step = next((s for s in spec.steps if s.id == step_id), None)
    if step is None:
        raise ResolveError(f"the playbook has no step {step_id}")
    results: dict[str, Any] = dict(run.step_results or {})
    result = results.get(step_id) or {}
    task_id = _run_task_ids(run).get(step_id)
    task = await db.get(m.Task, task_id) if task_id else None
    latest = (await latest_jobs_for(db, [task_id])).get(task_id) if task_id else None
    stale = result.get("job_id") and latest is not None and result["job_id"] != str(latest.id)
    if result.get("status") != "attention" or stale:
        raise ResolveError("this step isn't waiting for a decision")

    who = actor or (user.display_name or user.username if user else "castor")
    resolution = {
        "action": action,
        "by": who,
        "note": note.strip(),
        "reason": result.get("reason", ""),
        "at": utcnow().isoformat(),
    }
    keep = {"condition": result["condition"]} if "condition" in result else {}
    jobs: list[m.Job] = []
    if action == "retry":
        results[step_id] = {**keep, "resolution": resolution}
        if step.kind == "command" and task is not None and result.get("job_id"):
            active_col = await column_with_role(db, run.project_id, m.ColumnRole.active)
            if active_col is not None and task.column_id != active_col.id:
                task.column_id = active_col.id
                task.position = await next_position(db, active_col.id)
            jobs.append(await enqueue_job(db, task, user, reason=f"retried by {who}"))
    elif action == "continue":
        results[step_id] = {
            **result,
            "status": "succeeded",
            "resolution": resolution,
            "finished_at": utcnow().isoformat(),
        }
        results[step_id].pop("reason", None)
    elif action == "skip":
        results[step_id] = {
            **keep,
            "status": "not_taken",
            "reason": f"skipped by {who}",
            "job_id": result.get("job_id"),
            "resolution": resolution,
            "finished_at": utcnow().isoformat(),
        }
        if task is not None:
            done_col = await column_with_role(db, run.project_id, m.ColumnRole.done)
            if done_col is not None and task.column_id != done_col.id:
                task.column_id = done_col.id
                task.position = await next_position(db, done_col.id)
    else:
        results[step_id] = {
            **keep,
            "status": "failed",
            "error": result.get("reason") or "failed by decision",
            "job_id": result.get("job_id"),
            "expect": result.get("expect"),
            "resolution": resolution,
            "finished_at": utcnow().isoformat(),
        }
    run.step_results = results
    await notifications.clear(db, f"run:{run.id}:step:{step_id}")
    await record_activity(
        db,
        type=f"playbook_step.{action}",
        message=f"{who} chose “{RESOLUTIONS[action]}” for “{step.name}”" + (f": {note.strip()}" if note.strip() else ""),
        project_id=run.project_id,
        task_id=task.id if task else None,
        payload={"run_id": str(run.id), "step_id": step_id, "action": action},
        **(actor_of(user) if user and not actor else {"actor_type": "system", "actor_name": who}),
    )
    more, touched = await _advance(db, run, user)
    await db.commit()
    if task is not None and task not in touched:
        touched.append(task)
    for t in touched:
        await publish_task(db, t)
    await publish_playbook_run(db, run)
    return jobs + more


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
    await notifications.clear(db, f"run:{run.id}:step:", prefix=True)
    await db.commit()
    await publish_playbook_run(db, run)

    # Cancel active jobs for all steps
    step_task_ids = run.step_task_ids or {}
    task_uuids = [uuid.UUID(tid) for tid in step_task_ids.values()]
    latest_jobs = await latest_jobs_for(db, task_uuids)

    from .orchestrator.dispatcher import dispatcher

    for job in latest_jobs.values():
        if job and not job.status.is_terminal:
            await dispatcher.cancel(job.id)

    return run

