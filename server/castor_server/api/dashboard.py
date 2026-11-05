"""The home dashboard: what needs attention, what's running, and a timeline of what
happened, across the projects the user can see (or one of them)."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, true

from .. import models as m
from ..access import can_see, visible_project_ids
from ..deps import DB, CurrentUser, parse_uuid
from ..models import utcnow
from ..orchestrator.gateway import gateway
from ..services import playbook_run_out, step_link

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

# Feed kinds, from activity types. Job queue/dispatch steps are noise and never shown.
HIDDEN_TYPES = {"job.queued", "job.dispatched"}
ADMIN_KINDS = {"admin"}


def kind_of(event_type: str) -> str:
    head = event_type.split(".", 1)[0]
    if head == "comment":
        return "comment"
    if head == "job":
        return "job"
    if event_type == "task.moved":
        return "move"
    if head == "task":
        return "card"
    if head == "attachment":
        return "file"
    if head.startswith("playbook"):
        return "playbook"
    if head in ("agent", "agent_token"):
        return "agent"
    if head == "note":
        return "note"
    return "admin"  # users, projects, variables


class Ref(BaseModel):
    id: str
    name: str


class FeedItem(BaseModel):
    id: int
    kind: str
    type: str
    created_at: datetime
    message: str
    project: dict[str, str] | None = None
    task: Ref | None = None
    actor: dict[str, Any]
    comment: dict[str, Any] | None = None
    job: dict[str, Any] | None = None
    run_id: str | None = None
    step_id: str | None = None


class FeedOut(BaseModel):
    items: list[FeedItem]
    # Pass as `before` for the next (older) page; None when there's no more.
    next_before: int | None


async def _scope(db: DB, user: m.User, project_id: str | None) -> set[uuid.UUID] | None:
    """The projects to include: one (if given and visible), else all visible (None: all)."""
    if project_id:
        pid = parse_uuid(project_id)
        if not await can_see(db, user, pid):
            raise HTTPException(404, "Project not found")
        return {pid}
    return await visible_project_ids(db, user)


@router.get("/feed", response_model=FeedOut)
async def feed(
    db: DB,
    user: CurrentUser,
    project_id: str | None = None,
    kinds: str | None = None,
    actor: str | None = None,
    mine: bool = False,
    before: int | None = None,
    limit: int = 40,
):
    scope = await _scope(db, user, project_id)
    my_tasks, mentioning = await _mine(db, user, scope) if mine else (set(), set())
    wanted = {k for k in (kinds or "").split(",") if k} or None
    query = select(m.ActivityEvent).where(m.ActivityEvent.type.not_in(HIDDEN_TYPES)).order_by(m.ActivityEvent.id.desc())
    if project_id:
        query = query.where(m.ActivityEvent.project_id.in_(scope))
    elif scope is not None:
        query = query.where(m.ActivityEvent.project_id.is_(None) | m.ActivityEvent.project_id.in_(scope))
    if before:
        query = query.where(m.ActivityEvent.id < before)
    if actor:
        query = query.where(m.ActivityEvent.actor_id == actor)

    # Kinds are derived from types, so filter while reading, in batches.
    limit = max(1, min(limit, 100))
    events: list[m.ActivityEvent] = []
    cursor_done = False
    offset = 0
    while len(events) < limit and not cursor_done:
        batch = (await db.execute(query.offset(offset).limit(200))).scalars().all()
        offset += len(batch)
        cursor_done = len(batch) < 200
        for e in batch:
            kind = kind_of(e.type)
            if kind in ADMIN_KINDS and not user.is_admin:
                continue
            if wanted and kind not in wanted:
                continue
            if mine and not _is_mine(e, user, my_tasks, mentioning):
                continue
            events.append(e)
            if len(events) == limit:
                break
    return FeedOut(items=await _enrich(db, events), next_before=events[-1].id if len(events) == limit else None)


async def _mine(db: DB, user: m.User, scope: set[uuid.UUID] | None) -> tuple[set[uuid.UUID], set[str]]:
    """For the "Mine" timeline: the cards that are yours (assigned to you, opened by you, or
    commented on by you) and the comments that mention you."""
    def in_scope(column):
        return column.in_(scope) if scope is not None else true()

    me = str(user.id)
    rows = (await db.execute(select(m.Task.id, m.Task.assignees, m.Task.created_by).where(in_scope(m.Task.project_id)))).all()
    tasks = {tid for tid, assignees, creator in rows if me in (assignees or []) or creator == user.id}
    commented = await db.execute(
        select(m.Comment.task_id).join(m.Task, m.Task.id == m.Comment.task_id)
        .where(in_scope(m.Task.project_id), m.Comment.author_id == user.id).distinct()
    )
    tasks |= set(commented.scalars())
    mentions = await db.execute(
        select(m.Comment.id, m.Comment.mentions).join(m.Task, m.Task.id == m.Comment.task_id).where(in_scope(m.Task.project_id))
    )
    return tasks, {str(cid) for cid, who in mentions.all() if me in (who or [])}


def _is_mine(e: m.ActivityEvent, user: m.User, tasks: set[uuid.UUID], mentioning: set[str]) -> bool:
    """Something about you that someone (or something) else did."""
    if e.actor_type == "user" and e.actor_id == str(user.id):
        return False
    return e.task_id in tasks or (e.payload or {}).get("comment_id") in mentioning


async def _enrich(db: DB, events: list[m.ActivityEvent]) -> list[FeedItem]:
    project_ids = {e.project_id for e in events if e.project_id}
    task_ids = {e.task_id for e in events if e.task_id}
    user_ids = {uuid.UUID(e.actor_id) for e in events if e.actor_type == "user" and e.actor_id and _is_uuid(e.actor_id)}
    job_ids = {uuid.UUID(e.payload["job_id"]) for e in events if _is_uuid((e.payload or {}).get("job_id"))}
    comment_ids = {uuid.UUID(e.payload["comment_id"]) for e in events if _is_uuid((e.payload or {}).get("comment_id"))}

    async def by_id(model, ids):
        if not ids:
            return {}
        return {r.id: r for r in (await db.execute(select(model).where(model.id.in_(ids)))).scalars()}

    projects = await by_id(m.Project, project_ids)
    tasks = await by_id(m.Task, task_ids)
    users = await by_id(m.User, user_ids)
    jobs = await by_id(m.Job, job_ids)
    comments = await by_id(m.Comment, comment_ids)
    agents = await by_id(m.Agent, {j.agent_id for j in jobs.values() if j.agent_id})
    # Comment events from before comment ids were recorded: that author's comment on that
    # card nearest the event.
    legacy = [e for e in events if e.type == "comment.created" and not (e.payload or {}).get("comment_id") and e.task_id]
    if legacy:
        rows = (await db.execute(select(m.Comment).where(m.Comment.task_id.in_({e.task_id for e in legacy})))).scalars().all()
        for e in legacy:
            mine = [c for c in rows if c.task_id == e.task_id and str(c.author_id) == e.actor_id]
            if mine:
                best = min(mine, key=lambda c: abs((c.created_at - e.created_at).total_seconds()))
                comments[best.id] = best
                e.payload = {**(e.payload or {}), "comment_id": str(best.id)}

    out = []
    for e in events:
        payload = e.payload or {}
        project = projects.get(e.project_id) if e.project_id else None
        task = tasks.get(e.task_id) if e.task_id else None
        author = users.get(uuid.UUID(e.actor_id)) if e.actor_type == "user" and _is_uuid(e.actor_id) else None
        item = FeedItem(
            id=e.id,
            kind=kind_of(e.type),
            type=e.type,
            created_at=e.created_at,
            message=e.message,
            project={"id": str(project.id), "codename": project.codename, "name": project.name} if project else None,
            task=Ref(id=str(task.id), name=task.title) if task else None,
            actor={
                "type": e.actor_type,
                "name": author.display_name if author else e.actor_name,
                "username": author.username if author else None,
                "avatar_url": author.avatar_url if author else None,
            },
            run_id=payload.get("run_id"),
            step_id=payload.get("step_id"),
        )
        comment = comments.get(uuid.UUID(payload["comment_id"])) if _is_uuid(payload.get("comment_id")) else None
        if comment is not None:
            body = comment.body
            item.comment = {
                "id": str(comment.id),
                "body": body[:800] + ("…" if len(body) > 800 else ""),
                "edited": comment.edited_at is not None,
                "mentions": list(comment.mentions or []),
            }
        job = jobs.get(uuid.UUID(payload["job_id"])) if _is_uuid(payload.get("job_id")) else None
        if job is not None:
            agent = agents.get(job.agent_id) if job.agent_id else None
            item.job = {
                "id": str(job.id),
                "status": job.status.value,
                "exit_code": job.exit_code,
                "seconds": (job.finished_at - job.started_at).total_seconds() if job.started_at and job.finished_at else None,
                "agent": agent.name if agent else None,
                "command": job.command[:300],
            }
        out.append(item)
    return out


def first_line(markdown: str, limit: int = 140) -> str:
    """The first line with text, as plain text (no list/heading/quote markers or emphasis)."""
    for line in markdown.splitlines():
        text = re.sub(r"^(?:(?:#{1,6}|>|[-*+]|\d+[.)])\s+)+", "", line.strip())
        text = re.sub(r"^\[([ xX])\]\s*", lambda c: "☐ " if c[1] == " " else "☑ ", text)  # task lists
        text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)  # links and images: their text
        text = re.sub(r"(\*\*|~~|`)(.+?)\1", r"\2", text)
        text = re.sub(r"(?<!\w)(__|\*|_)(\S.*?)\1(?!\w)", r"\2", text).strip()  # not snake_case
        if text and not re.fullmatch(r"[-*_=\s]+", text):  # skip rules
            return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
    return ""


async def _pinned(db: DB, in_scope, project_ref) -> list[dict[str, Any]]:
    """Every pinned note in scope as one line, grouped by project (the project edited most
    recently first, its notes in their order)."""
    notes = (
        await db.execute(
            select(m.ProjectNote)
            .where(in_scope(m.ProjectNote.project_id))
            .order_by(m.ProjectNote.position, m.ProjectNote.created_at)
        )
    ).scalars().all()
    latest: dict[uuid.UUID, datetime] = {}
    for note in notes:
        latest[note.project_id] = max(latest.get(note.project_id, note.updated_at), note.updated_at)
    ordered = sorted(
        (n for n in notes if project_ref(n.project_id)), key=lambda n: (-latest[n.project_id].timestamp(), str(n.project_id))
    )
    return [
        {
            "project": project_ref(note.project_id),
            "note_id": str(note.id),
            "title": note.title,
            "color": note.color or "default",
            "line": first_line(note.body),
            "updated_at": note.updated_at,
        }
        for note in ordered[:60]
    ]


def _n(count: int, noun: str) -> str:
    return f"{count:,} {noun}{'' if count == 1 else 's'}"


def _duration(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60}s"
    return f"{s // 3600}h {s % 3600 // 60}m"


async def _facts(db: DB, in_scope) -> list[str]:
    """A few facts about the jobs and cards in scope (each only when there's data for it)."""
    facts: list[str] = []
    finished = select(m.Job).join(m.Task, m.Task.id == m.Job.task_id).where(
        in_scope(m.Task.project_id), m.Job.finished_at.is_not(None)
    )
    total = (await db.execute(select(func.count()).select_from(finished.subquery()))).scalar_one()
    if total:
        succeeded = (
            await db.execute(select(func.count()).select_from(finished.where(m.Job.status == m.JobStatus.succeeded).subquery()))
        ).scalar_one()
        facts.append(f"{_n(total, 'job')} {'has' if total == 1 else 'have'} run so far; {round(100 * succeeded / total)}% succeeded.")

    top = (
        await db.execute(
            select(m.Task.command_ref, func.count(m.Job.id).label("n"))
            .join(m.Job, m.Job.task_id == m.Task.id)
            .where(in_scope(m.Task.project_id), m.Task.command_ref.is_not(None))
            .group_by(m.Task.command_ref)
            .order_by(func.count(m.Job.id).desc())
            .limit(1)
        )
    ).first()
    if top:
        definition = await db.get(m.CommandDefinition, top[0])
        facts.append(f"The most-run command is {definition.name if definition else top[0]}, with {_n(top[1], 'run')}.")

    # Durations are compared in Python: date arithmetic differs between SQLite and Postgres.
    recent = (
        await db.execute(
            select(m.Job.started_at, m.Job.finished_at, m.Task.title)
            .join(m.Task, m.Task.id == m.Job.task_id)
            .where(in_scope(m.Task.project_id), m.Job.started_at.is_not(None), m.Job.finished_at.is_not(None))
            .order_by(m.Job.finished_at.desc())
            .limit(2000)
        )
    ).all()
    if recent:
        started, ended, title = max(recent, key=lambda r: r[1] - r[0])
        if (ended - started).total_seconds() >= 1:
            facts.append(f"The longest recent job was “{title}”: {_duration((ended - started).total_seconds())}.")

    week = utcnow() - timedelta(days=7)
    busiest = (
        await db.execute(
            select(m.Agent.name, func.count(m.Job.id))
            .join(m.Job, m.Job.agent_id == m.Agent.id)
            .join(m.Task, m.Task.id == m.Job.task_id)
            .where(in_scope(m.Task.project_id), m.Job.queued_at >= week)
            .group_by(m.Agent.name)
            .order_by(func.count(m.Job.id).desc())
            .limit(1)
        )
    ).first()
    if busiest:
        facts.append(f"The busiest agent this week is {busiest[0]}, with {_n(busiest[1], 'job')}.")

    comments = (
        await db.execute(select(func.count()).select_from(m.Comment).join(m.Task, m.Task.id == m.Comment.task_id).where(in_scope(m.Task.project_id)))
    ).scalar_one()
    if comments:
        facts.append(f"{_n(comments, 'comment')} {'has' if comments == 1 else 'have'} been written on these cards.")
    return facts


def _is_uuid(value: Any) -> bool:
    try:
        uuid.UUID(str(value))
    except ValueError:
        return False
    return value is not None


# ---------- summary: headline numbers, needs attention, live now ----------


class Summary(BaseModel):
    counts: dict[str, int]
    attention: list[dict[str, Any]]
    live_jobs: list[dict[str, Any]]
    live_runs: list[dict[str, Any]]
    agents: dict[str, int]
    # The user's own open cards: assigned to them, and opened by them.
    mine: dict[str, int]
    # Short facts about the work in scope, for the dashboard's rotating headline.
    facts: list[str]
    # Pinned notes in scope as one line each, grouped by project (the home dashboard lists them).
    pinned: list[dict[str, Any]]


@router.get("/summary", response_model=Summary)
async def summary(db: DB, user: CurrentUser, project_id: str | None = None):
    scope = await _scope(db, user, project_id)

    def in_scope(column):
        return column.in_(scope) if scope is not None else true()

    since = utcnow() - timedelta(hours=24)
    done_roles = (m.ColumnRole.done, m.ColumnRole.reported)
    open_cards = (
        await db.execute(
            select(func.count())
            .select_from(m.Task)
            .join(m.BoardColumn, m.BoardColumn.id == m.Task.column_id)
            .where(
                in_scope(m.Task.project_id),
                m.Task.playbook_step_id.is_(None),  # a run counts once, as its card
                m.BoardColumn.role.is_(None) | m.BoardColumn.role.not_in(done_roles),
            )
        )
    ).scalar_one()

    def recent(*types: str):
        return select(func.count()).where(
            in_scope(m.ActivityEvent.project_id), m.ActivityEvent.created_at >= since, m.ActivityEvent.type.in_(types)
        )

    counts = {
        "open_cards": open_cards,
        "comments_24h": (await db.execute(recent("comment.created"))).scalar_one(),
        "cards_24h": (await db.execute(recent("task.created"))).scalar_one(),
        "jobs_succeeded_24h": (await db.execute(recent("job.succeeded"))).scalar_one(),
        "jobs_failed_24h": (await db.execute(recent("job.failed", "job.lost"))).scalar_one(),
    }

    projects = {
        p.id: p
        for p in (await db.execute(select(m.Project).where(in_scope(m.Project.id)))).scalars()
    }

    def project_ref(pid):
        p = projects.get(pid)
        return {"id": str(pid), "codename": p.codename, "name": p.name} if p else None

    # Live: jobs queued or running, and playbook runs in progress.
    active = (
        await db.execute(
            select(m.Job, m.Task)
            .join(m.Task, m.Task.id == m.Job.task_id)
            .where(
                in_scope(m.Task.project_id),
                m.Job.status.in_([m.JobStatus.queued, m.JobStatus.dispatched, m.JobStatus.running]),
            )
            .order_by(m.Job.queued_at)
            .limit(50)
        )
    ).all()
    agent_names = {a.id: a.name for a in (await db.execute(select(m.Agent))).scalars()}
    live_jobs = [
        {
            "job_id": str(job.id),
            "status": job.status.value,
            "task": {"id": str(task.id), "name": task.title},
            "project": project_ref(task.project_id),
            "agent": agent_names.get(job.agent_id) if job.agent_id else None,
            "queued_at": job.queued_at,
            "started_at": job.started_at,
        }
        for job, task in active
    ]

    runs = (
        await db.execute(
            select(m.PlaybookRun)
            .where(in_scope(m.PlaybookRun.project_id), m.PlaybookRun.status == m.PlaybookRunStatus.running)
            .order_by(m.PlaybookRun.created_at.desc())
            .limit(20)
        )
    ).scalars().all()
    live_runs = []
    attention: list[dict[str, Any]] = []
    for run in runs:
        out = await playbook_run_out(db, run)
        statuses = out.step_status
        done = sum(s in ("succeeded", "skipped", "not_taken") for s in statuses.values())
        names = {s["id"]: s["name"] for s in run.spec_snapshot.get("steps", [])}
        live_runs.append({
            "run_id": str(run.id),
            "name": run.playbook_name,
            "project": project_ref(run.project_id),
            "done": done,
            "total": len(statuses),
            "running": sum(s in ("running", "queued") for s in statuses.values()),
            "waiting": sum(s == "attention" for s in statuses.values()),
            "started_at": run.created_at,
        })
        for step_id, status in statuses.items():
            if status == "attention":
                result = (run.step_results or {}).get(step_id) or {}
                attention.append({
                    "kind": "decision",
                    "title": f"{run.playbook_name}: “{names.get(step_id, step_id)}” needs a decision",
                    "detail": result.get("reason", ""),
                    "since": result.get("finished_at"),
                    "project": project_ref(run.project_id),
                    "link": step_link(run, step_id),
                    "for_you": True,  # everyone who can see the run is asked
                })

    # Cards whose latest run failed in the last week and that aren't done yet.
    week = utcnow() - timedelta(days=7)
    failed = (
        await db.execute(
            select(m.Job, m.Task)
            .join(m.Task, m.Task.id == m.Job.task_id)
            .join(m.BoardColumn, m.BoardColumn.id == m.Task.column_id)
            .where(
                in_scope(m.Task.project_id),
                m.Job.status.in_([m.JobStatus.failed, m.JobStatus.lost]),
                m.Job.finished_at >= week,
                m.BoardColumn.role.is_(None) | m.BoardColumn.role.not_in(done_roles),
            )
            .order_by(m.Job.finished_at.desc())
            .limit(40)
        )
    ).all()
    newest = {}
    if failed:
        latest_ids = (
            await db.execute(
                select(m.Job.task_id, func.max(m.Job.queued_at)).where(m.Job.task_id.in_({t.id for _, t in failed})).group_by(m.Job.task_id)
            )
        ).all()
        newest = {tid: q for tid, q in latest_ids}
    for job, task in failed:
        if newest.get(task.id) != job.queued_at or any(a.get("task_id") == str(task.id) for a in attention):
            continue  # re-run since, or already listed
        attention.append({
            "kind": "failed",
            "title": f"“{task.title}” failed",
            "detail": f"exit {job.exit_code}" if job.exit_code not in (None, 0) else (job.error or job.status.value),
            "since": job.finished_at,
            "project": project_ref(task.project_id),
            "task_id": str(task.id),
            "link": f"/projects/{task.project_id}?task={task.id}",
            "for_you": str(user.id) in (task.assignees or []) or task.created_by == user.id,
        })

    # Unread mentions of you come first: someone is waiting on you. Opening one marks it read.
    mention_rows = (
        await db.execute(
            select(m.Notification)
            .where(
                m.Notification.user_id == user.id,
                m.Notification.kind == "comment.mention",
                m.Notification.read_at.is_(None),
                in_scope(m.Notification.project_id),
            )
            .order_by(m.Notification.created_at.desc())
            .limit(10)
        )
    ).scalars().all()
    attention[:0] = [
        {
            "kind": "mention",
            "title": n.title,
            "detail": n.body,
            "since": n.created_at,
            "project": project_ref(n.project_id),
            "link": n.link,
            "for_you": True,
            "notification_id": str(n.id),
        }
        for n in mention_rows
    ]

    online = [c for c in gateway.connections.values()]
    open_rows = (
        await db.execute(
            select(m.Task.assignees, m.Task.created_by)
            .join(m.BoardColumn, m.BoardColumn.id == m.Task.column_id)
            .where(in_scope(m.Task.project_id), m.BoardColumn.role.is_(None) | m.BoardColumn.role.not_in(done_roles))
        )
    ).all()
    me = str(user.id)
    unread_mentions = (
        await db.execute(
            select(func.count()).where(
                m.Notification.user_id == user.id, m.Notification.kind == "comment.mention", m.Notification.read_at.is_(None)
            )
        )
    ).scalar_one()
    mine = {
        "assigned": sum(me in (a or []) for a, _ in open_rows),
        "opened": sum(c == user.id for _, c in open_rows),
        "mentions": unread_mentions,
    }
    return Summary(
        mine=mine,
        facts=await _facts(db, in_scope),
        pinned=await _pinned(db, in_scope, project_ref),
        counts=counts,
        attention=attention[:20],
        live_jobs=live_jobs,
        live_runs=live_runs,
        agents={
            "online": len(online),
            "slots": sum(c.max_concurrency for c in online),
            "busy": sum(len(c.running) for c in online),
        },
    )
