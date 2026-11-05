"""People (Phase 3c): a page per person and a team overview.

Everyone signed in can see who else uses Castor (as the user directory already allows), but
what a person did, has on their plate or was mentioned in only shows from projects the
viewer can see. Their activity timeline is the dashboard feed filtered by actor."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import String, and_, cast, func, select, true

from .. import models as m
from ..access import visible_project_ids
from ..deps import DB, CurrentUser
from ..models import utcnow
from .dashboard import first_line

router = APIRouter(prefix="/people", tags=["people"])

DONE_ROLES = (m.ColumnRole.done, m.ColumnRole.reported)


class PersonBrief(BaseModel):
    id: uuid.UUID
    username: str
    display_name: str
    role: str
    avatar_url: str | None
    open_assigned: int
    last_active: datetime | None


class PersonOut(PersonBrief):
    created_at: datetime
    projects: list[dict[str, str]]
    counts: dict[str, int]
    assigned: list[dict[str, Any]]
    mentions: list[dict[str, Any]]


def _scoped(column, scope: set[uuid.UUID] | None):
    return column.in_(scope) if scope is not None else true()


def _assigned_to(user_id: uuid.UUID):
    """Cards whose assignees (a JSON list of id strings) include the user."""
    return cast(m.Task.assignees, String).like(f'%"{user_id}"%')


async def _open_assigned(db: DB, scope, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    rows = (
        await db.execute(
            select(m.Task.assignees)
            .join(m.BoardColumn, m.BoardColumn.id == m.Task.column_id)
            .where(_scoped(m.Task.project_id, scope), m.BoardColumn.role.is_(None) | m.BoardColumn.role.not_in(DONE_ROLES))
        )
    ).scalars().all()
    wanted = {str(u): u for u in user_ids}
    counts = dict.fromkeys(user_ids, 0)
    for assignees in rows:
        for a in assignees or []:
            if a in wanted:
                counts[wanted[a]] += 1
    return counts


async def _last_active(db: DB, scope, user_ids: list[uuid.UUID]) -> dict[str, datetime]:
    where = [m.ActivityEvent.actor_type == "user", m.ActivityEvent.actor_id.in_([str(u) for u in user_ids])]
    if scope is not None:
        where.append(m.ActivityEvent.project_id.in_(scope))
    rows = await db.execute(
        select(m.ActivityEvent.actor_id, func.max(m.ActivityEvent.created_at)).where(*where).group_by(m.ActivityEvent.actor_id)
    )
    return {actor: at for actor, at in rows.all()}


@router.get("", response_model=list[PersonBrief])
async def team(db: DB, user: CurrentUser):
    """Everyone active, with their open assigned cards and when they last did something
    (both counted in the projects the viewer can see)."""
    scope = await visible_project_ids(db, user)
    people = (await db.execute(select(m.User).where(m.User.disabled_at.is_(None)).order_by(m.User.display_name))).scalars().all()
    ids = [p.id for p in people]
    assigned = await _open_assigned(db, scope, ids)
    active = await _last_active(db, scope, ids)
    return [
        PersonBrief(
            id=p.id, username=p.username, display_name=p.display_name, role=p.role.value, avatar_url=p.avatar_url,
            open_assigned=assigned[p.id], last_active=active.get(str(p.id)),
        )
        for p in people
    ]


@router.get("/{username}", response_model=PersonOut)
async def person(username: str, db: DB, user: CurrentUser):
    person = (await db.execute(select(m.User).where(func.lower(m.User.username) == username.lower()))).scalar_one_or_none()
    if person is None:
        raise HTTPException(404, "No such person")
    scope = await visible_project_ids(db, user)

    # Their projects, among the viewer's.
    if person.is_admin:
        project_rows = (await db.execute(select(m.Project).where(_scoped(m.Project.id, scope)).order_by(m.Project.codename))).scalars().all()
    else:
        project_rows = (
            await db.execute(
                select(m.Project)
                .join(m.ProjectMember, m.ProjectMember.project_id == m.Project.id)
                .where(m.ProjectMember.user_id == person.id, _scoped(m.Project.id, scope))
                .order_by(m.Project.codename)
            )
        ).scalars().all()
    all_projects = {p.id: p for p in (await db.execute(select(m.Project).where(_scoped(m.Project.id, scope)))).scalars()}

    def ref(pid):
        p = all_projects.get(pid)
        return {"id": str(p.id), "codename": p.codename, "name": p.name} if p else None

    # On their plate: open cards assigned to them.
    assigned_rows = (
        await db.execute(
            select(m.Task, m.BoardColumn)
            .join(m.BoardColumn, m.BoardColumn.id == m.Task.column_id)
            .where(
                _scoped(m.Task.project_id, scope),
                _assigned_to(person.id),
                m.BoardColumn.role.is_(None) | m.BoardColumn.role.not_in(DONE_ROLES),
            )
            .order_by(m.Task.priority.desc(), m.Task.updated_at.desc())
            .limit(50)
        )
    ).all()
    latest = {}
    if assigned_rows:
        jobs = (
            await db.execute(
                select(m.Job.task_id, m.Job.status, m.Job.queued_at)
                .where(m.Job.task_id.in_([t.id for t, _ in assigned_rows]))
                .order_by(m.Job.queued_at)
            )
        ).all()
        latest = {tid: status.value for tid, status, _ in jobs}  # the last one wins
    assigned = [
        {
            "id": str(t.id), "title": t.title, "kind": t.kind.value, "priority": t.priority, "column": c.name,
            "column_role": c.role.value if c.role else None, "status": latest.get(t.id), "project": ref(t.project_id),
            "updated_at": t.updated_at,
        }
        for t, c in assigned_rows
    ]

    # Comments that mention them.
    mention_rows = (
        await db.execute(
            select(m.Comment, m.Task)
            .join(m.Task, m.Task.id == m.Comment.task_id)
            .where(_scoped(m.Task.project_id, scope), cast(m.Comment.mentions, String).like(f'%"{person.id}"%'))
            .order_by(m.Comment.created_at.desc())
            .limit(20)
        )
    ).all()
    authors = {
        u.id: u for u in (await db.execute(select(m.User).where(m.User.id.in_({c.author_id for c, _ in mention_rows if c.author_id})))).scalars()
    }

    def author(c: m.Comment) -> dict[str, Any]:
        a = authors.get(c.author_id)
        return {"name": a.display_name, "username": a.username, "avatar_url": a.avatar_url} if a else {"name": "Someone", "username": None, "avatar_url": None}

    mentions = [
        {
            "id": str(c.id), "body": first_line(c.body, 200) or c.body[:200], "created_at": c.created_at,
            "author": author(c),
            "task": {"id": str(t.id), "title": t.title}, "project": ref(t.project_id),
            "link": f"/projects/{t.project_id}?task={t.id}&comment={c.id}",
        }
        for c, t in mention_rows
    ]

    # Numbers for the last 30 days.
    since = utcnow() - timedelta(days=30)

    async def count(model, *where) -> int:
        return (await db.execute(select(func.count()).select_from(model).where(*where))).scalar_one()

    task_scope = _scoped(m.Task.project_id, scope)
    counts = {
        "open_assigned": len(assigned),
        "comments_30d": await count(m.Comment.__table__.join(m.Task.__table__, m.Task.id == m.Comment.task_id),
                                    m.Comment.author_id == person.id, m.Comment.created_at >= since, task_scope),
        "cards_30d": await count(m.Task, m.Task.created_by == person.id, m.Task.created_at >= since, task_scope),
        "runs_30d": await count(
            m.ActivityEvent,
            and_(m.ActivityEvent.actor_type == "user", m.ActivityEvent.actor_id == str(person.id)),
            m.ActivityEvent.type == "job.queued", m.ActivityEvent.created_at >= since,
            _scoped(m.ActivityEvent.project_id, scope),
        ),
        "mentions_30d": sum(1 for x in mentions if x["created_at"] >= since),
    }
    active = await _last_active(db, scope, [person.id])
    return PersonOut(
        id=person.id, username=person.username, display_name=person.display_name, role=person.role.value,
        avatar_url=person.avatar_url, created_at=person.created_at, last_active=active.get(str(person.id)),
        open_assigned=len(assigned), projects=[{"id": str(p.id), "codename": p.codename, "name": p.name} for p in project_rows],
        counts=counts, assigned=assigned, mentions=mentions,
    )
