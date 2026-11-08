"""Who can see which project. Admins see every project; everyone else sees the projects
they're a member of (project_members). Anything belonging to a project (its board, cards,
jobs, attachments, playbook runs, activity) follows the project.

Lookups of something the user can't see answer 404, not 403, so ids of other projects'
things don't leak."""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m
from .api_tokens import project_limit
from .deps import ROLE_ERRORS


async def visible_project_ids(db: AsyncSession, user: m.User) -> set[uuid.UUID] | None:
    """The projects the user can see, or None for all of them (admins). An API token
    limited to some projects narrows this to those."""
    limit = project_limit(user)
    if user.is_admin:
        return None if limit is None else {uuid.UUID(p) for p in limit}
    rows = await db.execute(select(m.ProjectMember.project_id).where(m.ProjectMember.user_id == user.id))
    ids = set(rows.scalars())
    return ids if limit is None else {i for i in ids if str(i) in limit}


async def can_see(db: AsyncSession, user: m.User, project_id: uuid.UUID | None) -> bool:
    if project_id is None:
        return True
    limit = project_limit(user)
    if limit is not None and str(project_id) not in limit:
        return False
    if user.is_admin:
        return True
    found = await db.execute(
        select(m.ProjectMember.project_id).where(
            m.ProjectMember.project_id == project_id, m.ProjectMember.user_id == user.id
        )
    )
    return found.first() is not None


def require_writer(user: m.User) -> None:
    if not user.can(m.UserRole.operator):
        raise HTTPException(status.HTTP_403_FORBIDDEN, ROLE_ERRORS[m.UserRole.operator])


async def check_project(db: AsyncSession, user: m.User, project_id: uuid.UUID, *, write: bool = False) -> None:
    if not await can_see(db, user, project_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    if write:
        require_writer(user)


async def project_or_404(db: AsyncSession, user: m.User, project_id: uuid.UUID, *, write: bool = False) -> m.Project:
    project = await db.get(m.Project, project_id)
    if project is None or not await can_see(db, user, project.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    if write:
        require_writer(user)
    return project


async def task_or_404(db: AsyncSession, user: m.User, task_id: uuid.UUID, *, write: bool = False) -> m.Task:
    task = await db.get(m.Task, task_id)
    if task is None or not await can_see(db, user, task.project_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")
    if write:
        require_writer(user)
    return task


async def job_or_404(db: AsyncSession, user: m.User, job_id: uuid.UUID, *, write: bool = False) -> m.Job:
    job = await db.get(m.Job, job_id)
    task = await db.get(m.Task, job.task_id) if job else None
    if job is None or task is None or not await can_see(db, user, task.project_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    if write:
        require_writer(user)
    return job


async def add_member(db: AsyncSession, project_id: uuid.UUID, user_id: uuid.UUID) -> None:
    exists = await db.get(m.ProjectMember, (project_id, user_id))
    if exists is None:
        db.add(m.ProjectMember(project_id=project_id, user_id=user_id))
