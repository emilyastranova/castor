"""Domain operations shared by the REST API, the agent gateway and the dispatcher."""

from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from . import alerts, api_tokens, notifications, variables
from . import models as m
from .command_library import apply_library, load_spec, refresh_list_refs
from .command_render import ArgumentError
from .events import bus
from .playbook_conditions import MAX_LOG_BYTES, failed_checks
from .playbook_spec import (
    _NO_ITEM,
    PLACEHOLDER_RE,
    PlaybookSpec,
    PlaybookStepSpec,
    handled_steps,
    has_empty_upstream,
    item_label,
    lookup_ref,
    readiness,
    resolve_step_args,
    resolve_value,
    run_outcome,
    step_statuses,
)
from .playbook_transforms import TransformError, as_items, run_transform
from .schemas import (
    BatchSummary,
    ColumnOut,
    JobOut,
    ParserProposalOut,
    PlaybookRunOut,
    ProjectOut,
    RunSummary,
    TaskOut,
)

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
        payload={**(payload or {}), "via": via} if (via := api_tokens.via.get()) else (payload or {}),
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


LIBRARY_ITEMS = {"command": "command", "tool": "tool", "parser": "parser", "playbook": "playbook", "variable_kind": "variable kind"}


async def library_activity(db: AsyncSession, user: m.User | None, item: str, action: str, item_id: str, name: str | None = None) -> None:
    """A change to the shared library (type `library.<item>.<action>`): in every feed."""
    verb = {"created": "Added", "updated": "Changed", "deleted": "Removed"}[action]
    label = f"{name} ({item_id})" if name and name != item_id else item_id
    await record_activity(db, type=f"library.{item}.{action}", message=f"{verb} the {LIBRARY_ITEMS[item]} {label}",
                          project_id=None, payload={"item": item, "id": item_id}, **actor_of(user))


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
            .execution_options(populate_existing=True)
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
    return (await tasks_out(db, [task]))[0]


async def tasks_out(db: AsyncSession, tasks: list[m.Task]) -> list[TaskOut]:
    ids = [t.id for t in tasks]
    latest = await latest_jobs_for(db, ids)
    comments, attachments = await counts_for(db, ids)
    # Only cards that don't belong to a step can be a run's card.
    runs = await run_summaries(db, [t.id for t in tasks if t.playbook_step_id is None and t.kind == m.TaskKind.manual])
    batches = await batch_summaries(db, [j.batch_id for j in latest.values() if j.batch_id])
    result = []
    for task in tasks:
        out = TaskOut.model_validate(task)
        job = latest.get(task.id)
        out.latest_job = JobOut.model_validate(job) if job else None
        out.batch = batches.get(job.batch_id) if job and job.batch_id else None
        out.comment_count = comments.get(task.id, 0)
        out.attachment_count = attachments.get(task.id, 0)
        out.run = runs.get(task.id)
        result.append(out)
    return result


async def batch_summaries(db: AsyncSession, batch_ids: list[uuid.UUID]) -> dict[uuid.UUID, BatchSummary]:
    if not batch_ids:
        return {}
    rows = (await db.execute(
        select(m.Job.batch_id, m.Job.id, m.Job.status).where(m.Job.batch_id.in_(set(batch_ids))).order_by(m.Job.queued_at)
    )).all()
    grouped: dict[uuid.UUID, list[tuple[uuid.UUID, m.JobStatus]]] = {}
    for batch_id, job_id, status in rows:
        grouped.setdefault(batch_id, []).append((job_id, status))
    out = {}
    for batch_id, jobs in grouped.items():
        statuses = [s for _, s in jobs]
        active = sum(not s.is_terminal for s in statuses)
        succeeded = sum(s == m.JobStatus.succeeded for s in statuses)
        failed = len(statuses) - active - succeeded
        out[batch_id] = BatchSummary(
            total=len(statuses), active=active, succeeded=succeeded, failed=failed,
            status="running" if active else "failed" if failed else "succeeded", job_ids=[j for j, _ in jobs],
        )
    return out


async def run_summaries(db: AsyncSession, card_ids: list[uuid.UUID]) -> dict[uuid.UUID, RunSummary]:
    """The runs whose cards these are, summarised for the board."""
    if not card_ids:
        return {}
    rows = (await db.execute(select(m.PlaybookRun).where(m.PlaybookRun.card_id.in_(card_ids)))).scalars().all()
    out = {}
    for run in rows:
        statuses = await _run_statuses(db, run)
        values = list(statuses.values())
        out[run.card_id] = RunSummary(
            run_id=run.id,
            playbook_id=run.playbook_id,
            status=run.status.value,
            done=sum(s in ("succeeded", "skipped", "not_taken") for s in values),
            total=len(values),
            running=sum(s in ("running", "queued") for s in values),
            attention=sum(s == "attention" for s in values),
            failed=sum(s in ("failed", "blocked") for s in values),
            step_task_ids=run.step_task_ids or {},
        )
    return out


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


async def _spec_wants_tty(db: AsyncSession, task: m.Task) -> bool:
    if not task.command_ref:
        return False
    spec = await load_spec(db, task.command_ref)
    return bool(spec and spec.tty)


async def enqueue_job(
    db: AsyncSession, task: m.Task, user: m.User | None, *, reason: str = "manual"
) -> m.Job:
    """Create a queued Job for a command task. Caller must commit and then pump the dispatcher.

    A card set to run on every agent with some tags (`agent_selector: {tags, all: true}`) gets
    one job per connected agent that has them and can run it, as one batch; the first is
    returned. With none connected it queues one job for the first such agent to come."""
    from .command_render import ArgumentError

    try:
        await refresh_list_refs(db, task)
    except ArgumentError:
        pass  # a list that no longer exists: the job runs with the last rendered command

    def new_job(**extra: Any) -> m.Job:
        return m.Job(
            task_id=task.id,
            command=task.command,
            image=task.image,
            tool=task.tool,
            container_flags=list(task.container_flags or []),
            timeout_s=task.timeout_s,
            env={},
            var_bindings=dict(task.var_bindings or {}),
            created_by=user.id if user else None,
            tty=tty,
            **extra,
        )

    tty = task.tty or await _spec_wants_tty(db, task)
    targets = await broadcast_targets(db, task, new_job()) if is_broadcast(task) else []
    if len(targets) > 1:
        batch = uuid.uuid4()
        jobs = [new_job(batch_id=batch, target_agent_id=conn.agent_id) for conn in targets]
        names = ", ".join(c.name for c in targets)
        detail = f"on {len(jobs)} agents: {names}"
    else:  # one agent (or none yet): an ordinary job for whichever agent with the tags is free
        jobs = [new_job()]
        detail = ""
    for job in jobs:
        db.add(job)
    await db.flush()
    await record_activity(
        db,
        type="job.queued",
        message=f"Queued “{task.title}” ({reason})" + (f" {detail}" if detail else ""),
        project_id=task.project_id,
        task_id=task.id,
        payload={"job_id": str(jobs[0].id), **({"job_ids": [str(j.id) for j in jobs]} if len(jobs) > 1 else {})},
        **actor_of(user),
    )
    for job in jobs:
        publish_job(job, task.project_id)
    return jobs[0]


class TagError(ValueError):
    pass


TAG_MAX = 40


def clean_tag(raw: str) -> str:
    """A tag as stored (as the web's TagInput makes them): no spaces or commas, no leading #."""
    tag = re.sub(r"[\s,]+", "", str(raw).strip().lstrip("#"))[:TAG_MAX]
    if not tag:
        raise TagError("a tag can't be empty")
    return tag


def _retag(tags: list[str] | None, sources: set[str], target: str) -> list[str] | None:
    """The tags with every source replaced by the target, in place and without duplicates;
    None when nothing changes."""
    tags = list(tags or [])
    if not sources & set(tags):
        return None
    out: list[str] = []
    for tag in tags:
        tag = target if tag in sources else tag
        if tag not in out:
            out.append(tag)
    return out


async def rename_tags(
    db: AsyncSession, kind: str, sources: list[str], target: str, user: m.User, project_id: uuid.UUID | None = None
) -> dict[str, int]:
    """Rename a tag, or merge several into one (renaming onto an existing tag merges them).
    kind "cards": the cards of one project. kind "agents": agents and SSH hosts, enrollment
    tokens, and every card, step card and run that targets agents by those tags (so they keep
    matching). Returns how many of each changed. Caller commits; events are published here."""
    target = clean_tag(target)
    names = {str(t) for t in sources if str(t)} - {target}
    if not names:
        raise TagError("choose a tag to rename (other than the new name)")
    counts: dict[str, int] = {}
    changed_tasks: list[m.Task] = []
    if kind == "cards":
        rows = (await db.execute(select(m.Task).where(m.Task.project_id == project_id))).scalars().all()
        for task in rows:
            if (new := _retag(task.tags, names, target)) is not None:
                task.tags = new
                changed_tasks.append(task)
        counts["cards"] = len(changed_tasks)
    elif kind == "agents":
        from .orchestrator.gateway import gateway

        agents = (await db.execute(select(m.Agent))).scalars().all()
        changed_agents = []
        for agent in agents:
            if (new := _retag(agent.tags, names, target)) is not None:
                agent.tags = new
                changed_agents.append(agent)
                if conn := gateway.get(agent.id):
                    conn.tags = new
        tokens = (await db.execute(select(m.AgentToken))).scalars().all()
        changed_tokens = 0
        for token in tokens:
            if (new := _retag(token.tags, names, target)) is not None:
                token.tags = new
                changed_tokens += 1
        for task in (await db.execute(select(m.Task))).scalars().all():
            selector = task.agent_selector or {}
            if (new := _retag(selector.get("tags"), names, target)) is not None:
                task.agent_selector = {**selector, "tags": new}
                changed_tasks.append(task)
        runs = 0
        for run in (await db.execute(select(m.PlaybookRun))).scalars().all():
            selector = run.agent_selector or {}
            if (new := _retag(selector.get("tags"), names, target)) is not None:
                run.agent_selector = {**selector, "tags": new}
                runs += 1
        counts = {"agents": len(changed_agents), "tokens": changed_tokens, "cards": len(changed_tasks), "runs": runs}
    else:
        raise TagError(f"unknown kind of tag {kind!r}")

    what = ", ".join(sorted(names))
    verb = "Merged" if len(names) > 1 else "Renamed"
    await record_activity(
        db, type="tags.renamed", message=f"{verb} {kind[:-1]} tag{'s' if len(names) > 1 else ''} {what} → {target}",
        project_id=project_id, payload={"kind": kind, "from": sorted(names), "to": target, "counts": counts},
        **actor_of(user),
    )
    await db.flush()
    for task in changed_tasks:
        await publish_task(db, task)
    if kind == "agents":
        from .orchestrator.gateway import gateway

        for agent in changed_agents:
            bus.publish("agent.updated", None, await gateway.agent_out(agent))
        bus.publish("agent_tokens.changed", None, {})
        gateway._notify()  # queued jobs may match differently now
    return counts


class SelectorError(ValueError):
    pass


async def check_selector(db: AsyncSession, selector: dict[str, Any] | None) -> dict[str, Any]:
    """An agent selector as stored: {} (any agent), {agent_id} (one that exists), {tags} (any
    one agent with all of them) or {tags, all: true} (every connected agent with them)."""
    selector = selector or {}
    if selector.get("agent_id"):
        try:
            agent_id = uuid.UUID(str(selector["agent_id"]))
        except ValueError:
            agent_id = None
        if agent_id is None or await db.get(m.Agent, agent_id) is None:
            raise SelectorError("agent_selector: no such agent")
        return {"agent_id": str(agent_id)}
    tags = sorted({str(t).strip() for t in selector.get("tags") or [] if str(t).strip()})
    if selector.get("all") and not tags:
        raise SelectorError("agent_selector: running on every agent needs tags")
    return {"tags": tags, **({"all": True} if selector.get("all") else {})} if tags else {}


def is_broadcast(task: m.Task) -> bool:
    """Set to run on every agent with its tags (not for playbook step cards: a run picks
    one agent per run instead)."""
    selector = task.agent_selector or {}
    return bool(selector.get("all") and selector.get("tags")) and task.playbook_step_id is None


async def broadcast_targets(db: AsyncSession, task: m.Task, job: m.Job) -> list:
    """The connected agents a broadcast card runs on: every one with the tags that can run it."""
    from .orchestrator.dispatcher import agent_can_run, selector_matches
    from .orchestrator.gateway import gateway
    from .tool_library import tools_by_id

    tool = (await tools_by_id(db)).get(task.tool) if task.tool else None
    selector = {"tags": (task.agent_selector or {}).get("tags") or []}
    found = [c for c in gateway.connections.values() if selector_matches(c, selector) and agent_can_run(c, job, tool)]
    return sorted(found, key=lambda c: c.name)


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
        if column.role == m.ColumnRole.done:
            await alerts.card_done(db, task, user)
            if task.kind == m.TaskKind.manual:
                from .summaries import after_card_done

                after_card_done(task.id)  # once committed, if the project wants AI summaries
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


# Columns a duplicate never copies: identity, place, provenance, and playbook membership.
_NOT_COPIED = {"id", "column_id", "position", "created_by", "created_at", "updated_at",
               "playbook_run_id", "playbook_step_id", "playbook_item", "depends_on"}
_FINISHED = (m.JobStatus.succeeded, m.JobStatus.failed, m.JobStatus.cancelled, m.JobStatus.lost)


async def duplicate_task(
    db: AsyncSession, src: m.Task, user: m.User, *, assignees: bool = True, comments: bool = False,
    attachments: bool = False, output: bool = False,
) -> m.Task:
    """Copy a card to the top of its project's To Do column (Phase 3m). The blueprint always
    comes; comments, files and finished runs (logs, parsed results, their artifacts) only when
    asked. Everything copied gets new ids, and links to copied files in the description and
    comments follow them. Nothing runs. The caller commits."""
    import copy as _copy
    import shutil

    from .config import settings
    from .transfer import insert_rows, remap, row_of, values_for

    column = await column_with_role(db, src.project_id, m.ColumnRole.todo) or (
        await db.execute(select(m.BoardColumn).where(m.BoardColumn.project_id == src.project_id).order_by(m.BoardColumn.position).limit(1))
    ).scalar_one()
    top = (await db.execute(select(func.min(m.Task.position)).where(m.Task.column_id == column.id))).scalar()
    task = m.Task(
        **{c.key: _copy.deepcopy(getattr(src, c.key)) for c in m.Task.__table__.columns if c.key not in _NOT_COPIED},
        column_id=column.id, position=(top - 10.0) if top is not None else 0.0, created_by=user.id,
    )
    task.title = f"{src.title} (copy)"[:300]
    if not assignees:
        task.assignees = []
        task.group_assignees = []
    db.add(task)
    await db.flush()
    ids = {str(src.id): str(task.id)}

    # One-off variable sets belong to one card: the copy gets its own.
    for slot, set_id in list((task.var_bindings or {}).items()):
        row = await variables.find_set(db, set_id)
        if row is None or not row.ephemeral or row.task_id != src.id:
            continue
        clone = m.VariableSet(**{c.key: _copy.deepcopy(getattr(row, c.key)) for c in m.VariableSet.__table__.columns
                                 if c.key not in ("id", "created_at", "updated_at", "name", "task_id")})
        clone.name = f"{row.name} (copy {str(task.id)[:8]})"[:200]
        clone.task_id = task.id
        db.add(clone)
        await db.flush()
        task.var_bindings = {**task.var_bindings, slot: str(clone.id)}

    jobs: list[m.Job] = []
    if output:
        jobs = list((await db.execute(select(m.Job).where(m.Job.task_id == src.id, m.Job.status.in_(_FINISHED)).order_by(m.Job.queued_at))).scalars())
        for j in jobs:
            ids[str(j.id)] = str(uuid.uuid4())
            # Same instance: the agent and the person who ran it are still the same rows.
            ids |= {str(x): str(x) for x in (j.agent_id, j.created_by) if x}
    files = list((await db.execute(select(m.Attachment).where(m.Attachment.task_id == src.id))).scalars())
    # Artifacts come with the runs they came from; uploads and screenshots with "attachments".
    files = [a for a in files if (a.job_id is not None and str(a.job_id) in ids) or (a.job_id is None and attachments)]
    for a in files:
        ids[str(a.id)] = str(uuid.uuid4())

    if jobs:
        job_rows = [values_for(m.Job, row_of(j, skip=("artifact_token_hash",)), ids) for j in jobs]
        await insert_rows(db, m.Job, [r for r in job_rows if r])
        job_ids = [j.id for j in jobs]
        outs = (await db.execute(select(m.JobOutput).where(m.JobOutput.job_id.in_(job_ids)))).scalars()
        await insert_rows(db, m.JobOutput, [r for o in outs if (r := values_for(m.JobOutput, row_of(o, skip=("id",)), ids))])
        chunks = (await db.execute(select(m.JobLogChunk).where(m.JobLogChunk.job_id.in_(job_ids)).order_by(m.JobLogChunk.id))).scalars()
        await insert_rows(db, m.JobLogChunk, [r for c in chunks if (r := values_for(m.JobLogChunk, row_of(c, skip=("id",)), ids))])
    for a in files:
        source = settings.data_dir / a.storage_path
        if not source.is_file():
            continue
        target = source.parent / f"{ids[str(a.id)]}-{source.name.split('-', 5)[-1]}"
        shutil.copyfile(source, target)
        db.add(m.Attachment(
            id=uuid.UUID(ids[str(a.id)]), project_id=a.project_id, task_id=task.id,
            job_id=uuid.UUID(ids[str(a.job_id)]) if a.job_id else None, uploader_id=a.uploader_id, kind=a.kind,
            filename=a.filename, content_type=a.content_type, size=a.size,
            storage_path=str(target.relative_to(settings.data_dir)), created_at=a.created_at,
        ))
    task.description = remap(task.description or "", ids)
    if comments:
        for c in (await db.execute(select(m.Comment).where(m.Comment.task_id == src.id).order_by(m.Comment.created_at))).scalars():
            db.add(m.Comment(task_id=task.id, author_id=c.author_id, body=remap(c.body, ids), mentions=list(c.mentions or []),
                             edited_at=c.edited_at, created_at=c.created_at, updated_at=c.updated_at))
    await db.flush()

    extras = [w for w, on in (("comments", comments), ("files", attachments), ("output", output)) if on]
    await record_activity(
        db, type="task.created", project_id=task.project_id, task_id=task.id,
        message=f"Duplicated “{src.title}”" + (f" with its {', '.join(extras)}" if extras else ""),
        payload={"duplicated_from": str(src.id)}, **actor_of(user),
    )
    await record_activity(
        db, type="task.duplicated", project_id=src.project_id, task_id=src.id,
        message=f"Duplicated “{src.title}” as a new card", payload={"copy": str(task.id)}, **actor_of(user),
    )
    return task


# ---------- playbooks ----------


def _run_spec(run: m.PlaybookRun) -> PlaybookSpec:
    return PlaybookSpec.model_validate(run.spec_snapshot)


def _run_task_ids(run: m.PlaybookRun) -> dict[str, uuid.UUID]:
    return {step_id: uuid.UUID(tid) for step_id, tid in (run.step_task_ids or {}).items()}


def _group_items(tasks: list[m.Task]) -> dict[str, list[m.Task]]:
    """A run's for_each item cards by step, in item order."""
    items: dict[str, list[m.Task]] = {}
    for t in sorted((t for t in tasks if t.playbook_item is not None), key=lambda t: t.playbook_item or 0):
        items.setdefault(t.playbook_step_id or "", []).append(t)
    return items


async def _item_tasks(db: AsyncSession, run: m.PlaybookRun) -> dict[str, list[m.Task]]:
    rows = (
        await db.execute(
            select(m.Task)
            .where(m.Task.playbook_run_id == run.id, m.Task.playbook_item.is_not(None))
            .execution_options(populate_existing=True)
        )
    ).scalars().all()
    return _group_items(list(rows))


async def _run_tasks(db: AsyncSession, run: m.PlaybookRun) -> list[m.Task]:
    return list(
        (
            await db.execute(
                select(m.Task)
                .options(selectinload(m.Task.column))
                .where(m.Task.playbook_run_id == run.id)
                .order_by(m.Task.created_at)
            )
        ).scalars()
    )


async def _run_statuses(db: AsyncSession, run: m.PlaybookRun, tasks: list[m.Task] | None = None) -> dict[str, str]:
    """Every step's status, from its cards' latest jobs and the run's stored results."""
    tasks = tasks if tasks is not None else await _run_tasks(db, run)
    task_ids = _run_task_ids(run)
    items = _group_items(tasks)
    latest_jobs = await latest_jobs_for(db, [*task_ids.values(), *(t.id for ts in items.values() for t in ts)])
    job_status, job_ids = _job_states(task_ids, latest_jobs, await _parsing_jobs(db, latest_jobs), items)
    status = run.status.value if hasattr(run.status, "value") else str(run.status)
    return step_statuses(_run_spec(run), job_status, run.step_results or {}, status, job_ids)


async def playbook_run_out(db: AsyncSession, run: m.PlaybookRun) -> PlaybookRunOut:
    tasks = await _run_tasks(db, run)
    tasks_o = await tasks_out(db, tasks)
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
        step_status=await _run_statuses(db, run, tasks),
        spec_snapshot=run.spec_snapshot,
        finished_at=run.finished_at,
        created_at=run.created_at,
        card_id=run.card_id,
        tasks=tasks_o,
        agent_selector=run.agent_selector or {},
    )


async def publish_playbook_run(
    db: AsyncSession, run: m.PlaybookRun, name: str = "playbook_run.updated"
) -> PlaybookRunOut:
    out = await playbook_run_out(db, run)
    bus.publish(name, run.project_id, out)
    # The run's card shows its progress, so the board needs it too.
    card = await db.get(m.Task, run.card_id) if run.card_id else None
    if card is not None:
        await publish_task(db, card)
    return out


async def start_playbook_run(
    db: AsyncSession,
    spec: PlaybookSpec,
    project_id: uuid.UUID,
    inputs: dict[str, Any],
    user: m.User | None,
    agent_selector: dict[str, Any] | None = None,
    title: str | None = None,
) -> tuple[m.PlaybookRun, list[m.Job]]:
    """Instantiate a playbook: one card per command step (transform steps have no card),
    then run every step whose dependencies are already met. Caller commits.
    `agent_selector` ({agent_id} or {tags}) is where every step runs."""
    selector = {k: v for k, v in (agent_selector or {}).items() if k in ("agent_id", "tags") and v}
    run = m.PlaybookRun(
        agent_selector=selector,
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

    # The run's one card on the board, in In Progress while it runs (its steps are hidden).
    run_col = await column_with_role(db, project_id, m.ColumnRole.active) or todo_col
    card = m.Task(
        project_id=project_id,
        column_id=run_col.id,
        position=await next_position(db, run_col.id),
        title=title or spec.name,
        description=spec.description,
        kind=m.TaskKind.manual,
        created_by=user.id if user else None,
        tags=list(spec.tags or [])[:5],
    )
    db.add(card)
    await db.flush()
    run.card_id = card.id

    tasks: list[m.Task] = []
    for step in spec.steps:
        if step.kind != "command" or step.for_each is not None:
            continue  # transforms have no card; for_each steps get one per item later
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
            agent_selector=dict(selector),
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
    await publish_task(db, card, "task.created")
    for task in tasks:
        await publish_task(db, task, "task.created")
    for task in touched:
        if task not in tasks:
            await publish_task(db, task)
    await publish_playbook_run(db, run)
    return run, jobs


class FanOut:
    """Where a for_each step's items stand, summed up as one job status."""

    def __init__(self, items: list[m.Task], latest_jobs: dict[uuid.UUID, m.Job], parsing: set[uuid.UUID]):
        self.items = items
        self.states: list[str] = []  # per item: waiting (no job yet) or a job status
        job_ids = []
        for t in items:
            job = latest_jobs.get(t.id)
            if job is None:
                self.states.append("waiting")
                continue
            job_ids.append(str(job.id))
            self.states.append("running" if job.id in parsing else job.status.value)
        # Changes whenever any item gets a new job, so a judgement on the whole set (an
        # expect, a decision) is superseded by re-running an item.
        self.id = "items:" + hashlib.sha1(",".join(job_ids).encode()).hexdigest()[:16]

    @property
    def active(self) -> int:
        return sum(s in ("queued", "dispatched", "running") for s in self.states)

    @property
    def failed(self) -> int:
        return sum(s in ("failed", "lost", "cancelled") for s in self.states)

    @property
    def status(self) -> str:
        if any(s in ("waiting", "queued", "dispatched", "running") for s in self.states):
            started = any(s not in ("waiting", "queued") for s in self.states)
            return "running" if started else "queued"
        if all(s == "succeeded" for s in self.states):
            return "succeeded"
        if all(s == "cancelled" for s in self.states):
            return "cancelled"
        return "failed"


def _job_states(
    task_ids: dict[str, uuid.UUID],
    latest_jobs: dict[uuid.UUID, m.Job],
    parsing: set[uuid.UUID],
    items: dict[str, list[m.Task]] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """(step → job status, step → job id) for steps with a job. A finished job whose outputs
    are still being parsed counts as running, so nothing downstream judges it too early.
    A for_each step's items count as one job (FanOut)."""
    status: dict[str, str] = {}
    ids: dict[str, str] = {}
    for step_id, tid in task_ids.items():
        job = latest_jobs.get(tid)
        if job is not None:
            status[step_id] = "running" if job.id in parsing else job.status.value
            ids[step_id] = str(job.id)
    for step_id, tasks in (items or {}).items():
        if tasks:
            fan = FanOut(tasks, latest_jobs, parsing)
            status[step_id] = fan.status
            ids[step_id] = fan.id
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
    items: dict[str, list[m.Task]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Values available to bind: parsed outputs of succeeded jobs (and of failed ones a
    person chose to continue with), results of transforms. A for_each step's output is its
    succeeded items' outputs merged in item order (lists concatenated)."""
    outputs: dict[str, dict[str, Any]] = {}
    item_jobs: dict[uuid.UUID, tuple[str, int]] = {}
    for step_id, tasks in (items or {}).items():
        for t in tasks:
            job = latest_jobs.get(t.id)
            if job is not None and job.status == m.JobStatus.succeeded:
                item_jobs[job.id] = (step_id, t.playbook_item or 0)
    if item_jobs:
        rows = sorted(
            (await db.execute(select(m.JobOutput).where(m.JobOutput.job_id.in_(list(item_jobs))))).scalars(),
            key=lambda r: item_jobs[r.job_id][1],
        )
        for row in rows:
            if row.error is not None:
                continue
            merged = outputs.setdefault(item_jobs[row.job_id][0], {}).setdefault(row.name, [])
            merged.extend(row.value if isinstance(row.value, list) else [row.value])
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


def step_link(run: m.PlaybookRun, step_id: str | None = None) -> str:
    """Where to look at a run (and answer its decisions): its card on the board, or the
    Playbooks page for a run without one."""
    if run.card_id:
        # A step with its own card opens on top of the run's card (?step=<its card>).
        step_card = (run.step_task_ids or {}).get(step_id) if step_id else None
        return f"/projects/{run.project_id}?task={run.card_id}" + (f"&step={step_card}" if step_card else "")
    return f"/playbooks?run={run.id}" + (f"&step={step_id}" if step_id else "")


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
            await db.execute(
                select(m.Task)
                .where(m.Task.id.in_(list(task_ids.values())))
                .execution_options(populate_existing=True)
            )
        ).scalars()
    }
    items = await _item_tasks(db, run)
    item_ids = [t.id for ts in items.values() for t in ts]
    latest_jobs = await latest_jobs_for(db, [*task_ids.values(), *item_ids])
    parsing = await _parsing_jobs(db, latest_jobs)
    results: dict[str, Any] = dict(run.step_results or {})
    outputs = await _step_outputs(db, spec, task_ids, latest_jobs, results, items)
    todo_col = await column_with_role(db, run.project_id, m.ColumnRole.todo)
    active_col = await column_with_role(db, run.project_id, m.ColumnRole.active)
    done_col = await column_with_role(db, run.project_id, m.ColumnRole.done)
    run_status = run.status.value

    def statuses() -> dict[str, str]:
        job_status, job_ids = _job_states(task_ids, latest_jobs, parsing, items)
        return step_statuses(spec, job_status, results, run_status, job_ids)

    def set_result(step_id: str, **fields: Any) -> None:
        """Record a step's result, keeping how its condition was evaluated."""
        prev = results.get(step_id) or {}
        keep = {k: prev[k] for k in ("condition", "resolution", "history", "items") if k in prev}
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
        if step.on_failure not in ("ask", "ai"):
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
            link=step_link(run, step.id),
            project_id=run.project_id,
            subject=f"run:{run.id}:step:{step.id}",
            payload={"run_id": str(run.id), "step_id": step.id},
        )
        await alerts.decision(db, run, step.name, error, step_link(run, step.id))
        parse = extra.get("parse")
        if parse:
            # Its output wasn't in the format its parser reads: have a parser drafted for it
            # now, so the decision is one click when someone looks (or, for `ai`, so the run
            # can carry on by itself).
            from . import ai, llm

            if await llm.enabled(db):
                proposal = m.ParserProposal(
                    job_id=uuid.UUID(extra["job_id"]), project_id=run.project_id, output_name=parse["output"],
                    failed_parser=parse["parser"], failure=error, status="drafting",
                )
                db.add(proposal)
                await db.flush()
                ai.schedule_parser(proposal.id, run.id, step.id)
        elif step.on_failure == "ai":
            from . import ai

            ai.schedule_resolve(run.id, step.id)

    async def check_expect(step: PlaybookStepSpec, task: m.Task | None, job_id: str, log_job: m.Job | None) -> None:
        expect = step.expectation()
        assert expect is not None
        try:
            log_text = await _job_log_tail(db, log_job.id) if expect.log is not None and log_job else None
            if log_text and log_job is not None and log_job.tty:
                from .interactive import plain

                log_text = plain(log_text)  # terminal output: match the text, not the escapes
            met, checks = expect.evaluate(outputs.get(step.id, {}), log_text)
        except TransformError as exc:
            met, checks = False, [{"text": f"expect: {exc}", "met": False}]
        if met:
            set_result(step.id, status="succeeded", job_id=job_id, expect=checks)
        else:
            await fail_step(step, task, f"unexpected output: {failed_checks(checks)}", job_id=job_id, expect=checks)

    async def judge_fan_out(step: PlaybookStepSpec) -> bool:
        """A for_each step whose items have all ended: check `expect` on the merged
        outputs (logs aren't merged: the expectation's log test is checked per item), or
        ask if any item failed."""
        fan = FanOut(items[step.id], latest_jobs, parsing)
        result = results.get(step.id) or {}
        if fan.status not in ("succeeded", "failed", "cancelled") or (result.get("status") and result.get("job_id") == fan.id):
            return False
        if fan.status == "succeeded" and step.expect is not None:
            expect = step.expectation()
            assert expect is not None
            if expect.log is not None:
                for t in items[step.id]:
                    job = latest_jobs[t.id]
                    ok, checks = expect.model_copy(update={"outputs": {}}).evaluate({}, await _job_log_tail(db, job.id))
                    if not ok:
                        await fail_step(step, None, f"unexpected output from {t.title}: {failed_checks(checks)}", job_id=fan.id, expect=checks)
                        return True
            await check_expect(step, None, fan.id, None)
            return True
        if fan.status == "failed" and step.on_failure in ("ask", "ai"):
            await fail_step(step, None, f"{fan.failed} of {len(fan.items)} items failed", job_id=fan.id)
            return True
        return False

    async def feed_items() -> bool:
        """Queue waiting items of running for_each steps, up to their concurrency."""
        changed = False
        current = statuses()
        for step in spec.steps:
            if step.for_each is None or step.id not in items or current.get(step.id) not in ("queued", "running"):
                continue
            fan = FanOut(items[step.id], latest_jobs, parsing)
            room = (step.for_each.concurrency or len(fan.items)) - fan.active
            for t, state in zip(fan.items, fan.states, strict=True):
                if room <= 0:
                    break
                if state == "waiting" and await active_job_for(db, t.id) is None:
                    await move(t, active_col)
                    job = await enqueue_job(db, t, user, reason=f"playbook item of {step.name}")
                    latest_jobs[t.id] = job
                    jobs.append(job)
                    room -= 1
                    changed = True
        return changed

    async def start_fan_out(step: PlaybookStepSpec) -> None:
        """Create one card per item and queue them (up to the step's concurrency)."""
        assert step.for_each is not None
        ref = PLACEHOLDER_RE.match(step.for_each.items)
        values = as_items(lookup_ref(ref.group(1), run.inputs, outputs)) if ref else []
        if not values:
            set_result(step.id, status="skipped", reason="for_each has no items")
            return
        if len(values) > step.for_each.max_items:
            await fail_step(
                step, None,
                f"for_each has {len(values)} items, more than max_items ({step.for_each.max_items}); "
                "narrow the list with a transform first",
            )
            return
        column = todo_col or active_col
        if column is None:
            await fail_step(step, None, "the project has no To Do column for the item cards")
            return
        created: list[m.Task] = []
        for index, value in enumerate(values):
            label = item_label(value)
            card = m.Task(
                project_id=run.project_id,
                column_id=column.id,
                position=await next_position(db, column.id),
                title=f"[{run.playbook_name}] {step.name} · {label}",
                description=step.description,
                kind=m.TaskKind.command,
                playbook_run_id=run.id,
                playbook_step_id=step.id,
                playbook_item=index,
                depends_on=list(step.depends_on),
                command_ref=step.command_ref,
                created_by=run.created_by,
                agent_selector=dict(run.agent_selector or {}),
            )
            db.add(card)
            await db.flush()
            error = await _prepare_command_step(db, card, step, run.inputs, outputs, item=value)
            if error is not None:
                for c in [*created, card]:
                    await db.delete(c)
                await db.flush()
                await fail_step(step, None, f"item {index + 1} ({label}) could not start: {error}")
                return
            created.append(card)
        items[step.id] = created
        touched.extend(created)
        keep = {k: v for k, v in (results.get(step.id) or {}).items() if k in ("condition", "resolution")}
        results[step.id] = {**keep, "items": len(created)}
        await record_activity(
            db,
            type="playbook_step.fan_out",
            message=f"“{step.name}” runs once for each of {len(created)} item{'s' if len(created) != 1 else ''}",
            project_id=run.project_id,
            payload={"run_id": str(run.id), "step_id": step.id, "items": len(created)},
        )
        await feed_items()

    async def judge_finished_jobs() -> bool:
        """Check each newly finished job once: its `expect`, or `ask` on failure."""
        changed = False
        for step in spec.steps:
            if step.id in items:
                changed |= await judge_fan_out(step)
                continue
            if step.kind != "command" or step.id not in task_ids:
                continue
            job = latest_jobs.get(task_ids[step.id])
            if job is None or not job.status.is_terminal or job.id in parsing:
                continue
            result = results.get(step.id) or {}
            if result.get("status") and result.get("job_id") == str(job.id):
                continue  # already judged
            task = tasks.get(task_ids[step.id])
            if job.status == m.JobStatus.succeeded and step.on_failure in ("ask", "ai"):
                unreadable = await _unreadable_output(db, job, spec, step)
                if unreadable is not None:
                    name, parser, error = unreadable
                    await fail_step(step, task, f"its {name} output couldn't be read ({parser}: {error})", job_id=str(job.id),
                                    parse={"output": name, "parser": parser})
                    changed = True
                    continue
            if job.status == m.JobStatus.succeeded and step.expect is not None:
                await check_expect(step, task, str(job.id), job)
                changed = True
            elif job.status in (m.JobStatus.failed, m.JobStatus.lost) and step.on_failure in ("ask", "ai"):
                await fail_step(step, task, _job_failure(job), job_id=str(job.id))
                changed = True
        return changed

    progressed = run_status != "cancelled"
    while progressed:
        progressed = await judge_finished_jobs()
        progressed |= await feed_items()
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
                    produced = run_transform(step.transform, values)
                except TransformError as exc:
                    await fail_step(step, None, str(exc), input_count=input_count)
                    continue
                input_type = await _ref_type(db, spec, refs[0], task_ids, latest_jobs, results) if refs else None
                set_result(
                    step.id,
                    status="succeeded",
                    outputs={step.transform.output.name: produced},
                    type=step.transform.result_type(input_type),
                    input_count=input_count,
                    count=len(produced),
                )
                outputs[step.id] = {step.transform.output.name: produced}
                continue

            if step.for_each is not None:
                await start_fan_out(step)
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
            existing = await active_job_for(db, task.id)
            if existing is not None:  # backstop: never queue a card twice
                latest_jobs[task.id] = existing
                continue
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
    item: Any = _NO_ITEM,
) -> str | None:
    """Render the step's command onto its card (for one item of a for_each step, `item`).
    Returns an error message, or None."""
    cmd_spec = await load_spec(db, step.command_ref)
    if cmd_spec is None:
        return f"command {step.command_ref} is not in the library"
    try:
        await apply_library(db, task, cmd_spec, resolve_step_args(step.args, inputs, outputs, item))
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


async def _card_to_review(db: AsyncSession, run: m.PlaybookRun) -> None:
    """A run ended: its card moves from In Progress to Review, once. A card someone has
    already moved is left where they put it."""
    card = await db.get(m.Task, run.card_id) if run.card_id else None
    active = await column_with_role(db, run.project_id, m.ColumnRole.active)
    review = await column_with_role(db, run.project_id, m.ColumnRole.review) or await column_with_role(db, run.project_id, m.ColumnRole.done)
    if card is None or active is None or review is None or card.column_id != active.id:
        return
    card.column_id = review.id
    card.position = await next_position(db, review.id)
    await db.flush()


async def ensure_run_cards(db: AsyncSession) -> int:
    """Give every run from before run cards (Phase 3h) its card: in In Progress if it's still
    running, else in Review. Idempotent; commits."""
    runs = (await db.execute(select(m.PlaybookRun).where(m.PlaybookRun.card_id.is_(None)).order_by(m.PlaybookRun.created_at))).scalars().all()
    for run in runs:
        running = run.status == m.PlaybookRunStatus.running
        role = m.ColumnRole.active if running else m.ColumnRole.review
        column = (
            await column_with_role(db, run.project_id, role)
            or await column_with_role(db, run.project_id, m.ColumnRole.todo)
            or (await db.execute(select(m.BoardColumn).where(m.BoardColumn.project_id == run.project_id).order_by(m.BoardColumn.position).limit(1))).scalar_one_or_none()
        )
        if column is None:
            continue
        spec = run.spec_snapshot or {}
        card = m.Task(
            project_id=run.project_id,
            column_id=column.id,
            position=await next_position(db, column.id),
            title=run.playbook_name,
            description=spec.get("description", "") or "",
            kind=m.TaskKind.manual,
            created_by=run.created_by,
            created_at=run.created_at,
        )
        db.add(card)
        await db.flush()
        run.card_id = card.id
    await db.commit()
    return len(runs)


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
    await _card_to_review(db, run)
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
        link=step_link(run),
        project_id=run.project_id,
        subject=f"run:{run.id}",
        payload={"run_id": str(run.id)},
    )
    await alerts.run_finished(db, run, ok, "" if ok else "Didn't finish: " + ", ".join(failed[:6]) + ("…" if len(failed) > 6 else ""), step_link(run))
    from .summaries import after_run

    after_run(run.id)  # once this is committed, if the project wants AI summaries


# Advancing a run is one at a time: several jobs finishing together have their outputs
# parsed in parallel, and each would otherwise queue the same ready step. A lock per run
# (per event loop) serialises this process; the run row's FOR UPDATE does the same across
# server processes on Postgres (SQLite ignores it).
_run_locks: dict[tuple[int, uuid.UUID], asyncio.Lock] = {}


def _run_lock(run_id: uuid.UUID) -> asyncio.Lock:
    return _run_locks.setdefault((id(asyncio.get_running_loop()), run_id), asyncio.Lock())


async def _load_run_for_update(db: AsyncSession, run_id: uuid.UUID) -> m.PlaybookRun | None:
    """The run, re-read now (not a copy this session cached earlier) and locked for update."""
    return (
        await db.execute(
            select(m.PlaybookRun)
            .where(m.PlaybookRun.id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def advance_playbook_run(
    db: AsyncSession, run_id: uuid.UUID
) -> tuple[m.PlaybookRun | None, list[m.Job]]:
    """Called whenever one of the run's jobs finishes (after its outputs are parsed): queue
    the steps that became ready, commit and publish. The caller wakes the dispatcher."""
    async with _run_lock(run_id):
        run = await _load_run_for_update(db, run_id)
        if run is None or run.status == m.PlaybookRunStatus.cancelled:
            await db.rollback()
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


async def _unreadable_output(db: AsyncSession, job: m.Job, spec: PlaybookSpec, step: PlaybookStepSpec) -> tuple[str, str, str] | None:
    """An output of the step's job that its parser couldn't read (the input was there, the
    format wasn't what the parser knows) and that the playbook uses later: (name, parser, error)."""
    from .outputs import is_mismatch

    rows = (await db.execute(select(m.JobOutput).where(m.JobOutput.job_id == job.id, m.JobOutput.error.is_not(None)))).scalars()
    bad = [r for r in rows if is_mismatch(r.error)]
    if not bad:
        return None
    others = [s.model_dump(mode="json") for s in spec.steps if s.id != step.id]
    expected = set(((step.expect or {}).get("outputs") or {}).keys())
    for row in bad:
        ref = f"steps.{step.id}.outputs.{row.name}"
        if row.name in expected or any(ref in str(o) for o in others):
            return row.name, row.parser, str(row.error)[:300]
    return None


def proposal_out(p: m.ParserProposal) -> ParserProposalOut:
    out = ParserProposalOut.model_validate(p)
    out.automatic = p.requested_by is None
    return out


async def publish_parser_proposal(db: AsyncSession, p: m.ParserProposal) -> None:
    bus.publish("job.parser_proposal", p.project_id, proposal_out(p).model_dump(mode="json"))


async def apply_parser_proposal(db: AsyncSession, p: m.ParserProposal, user: m.User | None, *, actor: str | None = None,
                                save_as: str | None = None, use_in_command: bool = False) -> tuple[list[m.Job], str]:
    """Read the job's output again with the drafted parser; with `save_as`, also keep it in
    the library under that id (a person's choice). A playbook step that paused on this
    output carries on with what it reads now. With `use_in_command` (and `save_as`), the card's
    library command reads that output with it from now on, if the command is yours. Commits;
    returns the jobs that queued and a note about the command."""
    import yaml

    from . import parser_library
    from .outputs import parse_job
    from .parser_spec import ParserSpec, parse_parser_spec

    if p.status not in ("ready", "applied") or not p.spec:
        raise ValueError("this draft can't be used (it didn't read the output)")
    spec = ParserSpec.model_validate(p.spec)
    job = await db.get(m.Job, p.job_id)
    if job is None:
        raise ValueError("the job no longer exists")
    specs = [dict(s) for s in (job.output_specs or [])]
    target = next((s for s in specs if s.get("name") == p.output_name), None)
    if target is None:
        raise ValueError(f"the job has no output {p.output_name}")
    if save_as:
        if await db.get(m.ParserDefinition, save_as) is not None:
            raise ValueError(f"there is already a parser {save_as}")
        ParserSpec.model_validate({**spec.model_dump(mode="json"), "id": save_as})  # the id is usable
        blobs, _ = await _proposal_sample(db, job, p.output_name)
        text = _with_sample(p.yaml, save_as, blobs)
        try:
            spec = parse_parser_spec(text)
        except ValueError:  # the AI's YAML had something the spec drops: write it out from the spec
            text = yaml.safe_dump({**p.spec, "id": save_as}, sort_keys=False)
            spec = parse_parser_spec(text)
        row = m.ParserDefinition(id=save_as, origin="user", version=1)
        parser_library.definition_from_spec(row, spec, text)
        db.add(row)
        parser_library.register(spec)
        target.update(parser=save_as, type=spec.type, options={})
        target.pop("parser_spec", None)
    else:
        target.update(parser=spec.id, type=spec.type, options={}, parser_spec=spec.model_dump(mode="json"))
    job.output_specs = specs
    command_note = ""
    if save_as and use_in_command:
        command_note = await _use_in_command(db, job, p.output_name, save_as)
    p.status = "applied"
    p.applied_by = actor or (user.display_name or user.username if user else "castor")
    p.saved_as = save_as or ""
    await db.commit()
    await parse_job(db, job)
    jobs: list[m.Job] = []
    task = await db.get(m.Task, job.task_id)
    if task is not None and task.playbook_run_id is not None and task.playbook_step_id and task.playbook_item is None:
        run = await db.get(m.PlaybookRun, task.playbook_run_id, populate_existing=True)
        result = ((run.step_results or {}).get(task.playbook_step_id) or {}) if run else {}
        if result.get("status") == "attention" and (result.get("parse") or {}).get("output") == p.output_name and result.get("job_id") == str(job.id):
            what = f"the parser {save_as}" if save_as else "a parser the AI drafted"
            jobs = await resolve_step(db, run, task.playbook_step_id, "continue", user, note=f"Read {p.output_name} with {what}",
                                      actor=actor, extra={"parser": save_as or "draft", "model": p.model})
    await db.refresh(p)
    await publish_parser_proposal(db, p)
    return jobs, command_note


async def _use_in_command(db: AsyncSession, job: m.Job, output: str, parser_id: str) -> str:
    """Point the card's library command at the parser; a note says what happened."""
    from .command_library import definition_from_spec
    from .command_spec import parse_spec
    from .parser_library import point_output_at

    task = await db.get(m.Task, job.task_id)
    row = await db.get(m.CommandDefinition, task.command_ref) if task and task.command_ref else None
    if row is None:
        return "The card doesn't run a library command, so only this job uses it."
    if row.origin == "builtin":
        return f"{row.name} is built in: duplicate it in the Library to use {parser_id} from now on."
    try:
        text = point_output_at(row.source_yaml, output, parser_id)
        spec = parse_spec(text)
    except ValueError as exc:
        return f"Couldn't change {row.name}: {exc}"
    definition_from_spec(row, spec, text)
    return f"{row.name} reads {output} with {parser_id} from now on."


async def _proposal_sample(db: AsyncSession, job: m.Job, output_name: str) -> tuple[list[bytes], list[str]]:
    from .outputs import _specs, read_inputs

    spec = next((s for s in await _specs(db, job) if s.name == output_name), None)
    return await read_inputs(db, job, spec) if spec else ([], [])


def _with_sample(text: str, parser_id: str, blobs: list[bytes], limit: int = 4000) -> str:
    """The draft's YAML under its library id, with the start of the output it was written
    for as its sample (the Parsers page and later edits use it)."""
    import re as _re

    text = _re.sub(r"(?m)^id:.*$", f"id: {parser_id}", text.strip(), count=1)
    text = _re.sub(r"(?ms)^sample:.*", "", text).rstrip()
    sample = b"\n".join(blobs).decode("utf-8", errors="replace")[:limit].rstrip()
    if not sample:
        return text + "\n"
    return text + "\nsample: |\n" + "\n".join(f"  {line}" for line in sample.splitlines()) + "\n"


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
    extra: dict[str, Any] | None = None,
) -> list[m.Job]:
    """Apply a decision to a paused step (see _resolve_step), one at a time per run.
    `extra` is kept on the decision (e.g. the AI's model, confidence and command change)."""
    async with _run_lock(run.id):
        fresh = await _load_run_for_update(db, run.id)
        if fresh is None:
            raise ResolveError("the run no longer exists")
        try:
            return await _resolve_step(db, fresh, step_id, action, user, note, actor, extra)
        except ResolveError:
            await db.rollback()
            raise


async def _resolve_step(
    db: AsyncSession,
    run: m.PlaybookRun,
    step_id: str,
    action: str,
    user: m.User | None,
    note: str = "",
    actor: str | None = None,
    extra: dict[str, Any] | None = None,
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
        **(extra or {}),
    }
    # Every decision on the step stays in its history (the AI resolver counts its retries).
    keep = {k: result[k] for k in ("condition", "items") if k in result}
    keep["history"] = [*(result.get("history") or []), resolution][-20:]
    jobs: list[m.Job] = []
    if action == "retry":
        results[step_id] = {**keep, "resolution": resolution}
        if step.for_each is not None and result.get("job_id"):
            # Re-run only the items that didn't succeed.
            item_cards = (await _item_tasks(db, run)).get(step_id, [])
            results[step_id]["items"] = len(item_cards)
            latest_items = await latest_jobs_for(db, [t.id for t in item_cards])
            active_col = await column_with_role(db, run.project_id, m.ColumnRole.active)
            for card in item_cards:
                job = latest_items.get(card.id)
                if job is not None and job.status in (m.JobStatus.failed, m.JobStatus.lost, m.JobStatus.cancelled):
                    if active_col is not None and card.column_id != active_col.id:
                        card.column_id = active_col.id
                        card.position = await next_position(db, active_col.id)
                    jobs.append(await enqueue_job(db, card, user, reason=f"retried by {who}"))
        elif step.kind == "command" and task is not None and result.get("job_id"):
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
            "history": keep["history"],
            "finished_at": utcnow().isoformat(),
        }
        results[step_id].pop("reason", None)
        results[step_id].pop("ai", None)
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
    await _card_to_review(db, run)

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
    task_uuids = list((await db.execute(select(m.Task.id).where(m.Task.playbook_run_id == run.id))).scalars())
    latest_jobs = await latest_jobs_for(db, task_uuids)

    from .orchestrator.dispatcher import dispatcher

    for job in latest_jobs.values():
        if job and not job.status.is_terminal:
            await dispatcher.cancel(job.id)

    return run

