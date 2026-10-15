from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from .. import models as m
from ..deps import DB, CurrentUser, parse_uuid
from ..events import bus
from ..schemas import (
    ActivityOut,
    BoardOut,
    ColumnIn,
    ColumnOut,
    ColumnPatch,
    ProjectIn,
    ProjectOut,
    ProjectPatch,
    ProjectSummary,
)
from ..services import (
    actor_of,
    board_payload,
    create_default_columns,
    record_activity,
    tasks_out,
)

router = APIRouter(prefix="/projects", tags=["projects"])


async def _project_or_404(db, project_id: str) -> m.Project:
    project = await db.get(m.Project, parse_uuid(project_id))
    if project is None:
        raise HTTPException(404, "Project not found")
    return project


@router.get("", response_model=list[ProjectSummary])
async def list_projects(db: DB, _: CurrentUser):
    projects = (
        await db.execute(
            select(m.Project)
            .options(selectinload(m.Project.client))
            .order_by(m.Project.updated_at.desc())
        )
    ).scalars().all()
    if not projects:
        return []
    ids = [p.id for p in projects]
    task_counts = dict(
        (
            await db.execute(
                select(m.Task.project_id, func.count())
                .where(m.Task.project_id.in_(ids))
                .group_by(m.Task.project_id)
            )
        ).all()
    )
    done_counts = dict(
        (
            await db.execute(
                select(m.Task.project_id, func.count())
                .join(m.BoardColumn, m.BoardColumn.id == m.Task.column_id)
                .where(m.Task.project_id.in_(ids), m.BoardColumn.role == m.ColumnRole.done)
                .group_by(m.Task.project_id)
            )
        ).all()
    )
    running_counts = dict(
        (
            await db.execute(
                select(m.Task.project_id, func.count())
                .join(m.Job, m.Job.task_id == m.Task.id)
                .where(
                    m.Task.project_id.in_(ids),
                    m.Job.status.in_([m.JobStatus.dispatched, m.JobStatus.running]),
                )
                .group_by(m.Task.project_id)
            )
        ).all()
    )
    result = []
    for project in projects:
        out = ProjectSummary.model_validate(project)
        out.client_name = project.client.name if project.client else None
        out.task_count = task_counts.get(project.id, 0)
        out.done_count = done_counts.get(project.id, 0)
        out.running_count = running_counts.get(project.id, 0)
        result.append(out)
    return result


@router.post("", response_model=ProjectOut, status_code=201)
async def create_project(body: ProjectIn, db: DB, user: CurrentUser):
    exists = (
        await db.execute(select(m.Project).where(m.Project.codename == body.codename))
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(409, "A project with that codename already exists")
    project = m.Project(**body.model_dump(), created_by=user.id)
    if "auto_advance" not in project.settings:
        project.settings = {**project.settings, "auto_advance": True}
    db.add(project)
    await db.flush()
    await create_default_columns(db, project)
    await record_activity(
        db,
        type="project.created",
        message=f"Created project {project.codename}",
        project_id=project.id,
        **actor_of(user),
    )
    await db.commit()
    return project


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: str, db: DB, _: CurrentUser):
    return await _project_or_404(db, project_id)


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(project_id: str, body: ProjectPatch, db: DB, user: CurrentUser):
    project = await _project_or_404(db, project_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(project, key, value)
    await db.commit()
    bus.publish("project.updated", project.id, ProjectOut.model_validate(project))
    return project


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: str, db: DB, _: CurrentUser):
    project = await _project_or_404(db, project_id)
    await db.delete(project)
    await db.commit()
    bus.publish("project.deleted", project.id, {"id": project.id})


@router.get("/{project_id}/board", response_model=BoardOut)
async def get_board(project_id: str, db: DB, _: CurrentUser):
    project = await _project_or_404(db, project_id)
    columns = (
        await db.execute(
            select(m.BoardColumn)
            .where(m.BoardColumn.project_id == project.id)
            .order_by(m.BoardColumn.position)
        )
    ).scalars().all()
    tasks = (
        await db.execute(
            select(m.Task).where(m.Task.project_id == project.id).order_by(m.Task.position)
        )
    ).scalars().all()
    return board_payload(project, list(columns), await tasks_out(db, list(tasks)))


@router.get("/{project_id}/activity", response_model=list[ActivityOut])
async def project_activity(project_id: str, db: DB, _: CurrentUser, limit: int = 50):
    project = await _project_or_404(db, project_id)
    rows = (
        await db.execute(
            select(m.ActivityEvent)
            .where(m.ActivityEvent.project_id == project.id)
            .order_by(m.ActivityEvent.id.desc())
            .limit(min(limit, 200))
        )
    ).scalars().all()
    return rows


# ----- columns -----


@router.post("/{project_id}/columns", response_model=ColumnOut, status_code=201)
async def create_column(project_id: str, body: ColumnIn, db: DB, _: CurrentUser):
    project = await _project_or_404(db, project_id)
    position = body.position
    if position is None:
        current = (
            await db.execute(
                select(func.max(m.BoardColumn.position)).where(
                    m.BoardColumn.project_id == project.id
                )
            )
        ).scalar()
        position = (current or 0) + 10.0
    column = m.BoardColumn(
        project_id=project.id,
        name=body.name,
        role=body.role,
        position=position,
        wip_limit=body.wip_limit,
    )
    db.add(column)
    await db.commit()
    bus.publish("column.created", project.id, ColumnOut.model_validate(column))
    return column


@router.patch("/{project_id}/columns/{column_id}", response_model=ColumnOut)
async def update_column(project_id: str, column_id: str, body: ColumnPatch, db: DB, _: CurrentUser):
    project = await _project_or_404(db, project_id)
    column = await db.get(m.BoardColumn, parse_uuid(column_id))
    if column is None or column.project_id != project.id:
        raise HTTPException(404, "Column not found")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(column, key, value)
    await db.commit()
    bus.publish("column.updated", project.id, ColumnOut.model_validate(column))
    return column


@router.delete("/{project_id}/columns/{column_id}", status_code=204)
async def delete_column(project_id: str, column_id: str, db: DB, _: CurrentUser):
    project = await _project_or_404(db, project_id)
    column = await db.get(m.BoardColumn, parse_uuid(column_id))
    if column is None or column.project_id != project.id:
        raise HTTPException(404, "Column not found")
    has_tasks = (
        await db.execute(select(func.count()).where(m.Task.column_id == column.id))
    ).scalar()
    if has_tasks:
        raise HTTPException(409, "Move or delete the cards in this column first")
    await db.delete(column)
    await db.commit()
    bus.publish("column.deleted", project.id, {"id": column.id})
