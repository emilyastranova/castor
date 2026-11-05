from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from .. import models as m
from .. import variables
from ..access import check_project, project_or_404
from ..command_library import apply_library, load_spec
from ..command_render import ArgumentError
from ..deps import DB, CurrentUser, parse_uuid
from ..events import bus
from ..orchestrator.dispatcher import dispatcher
from ..schemas import (
    ActivityOut,
    CommentIn,
    CommentOut,
    JobOut,
    TaskIn,
    TaskMove,
    TaskOut,
    TaskPatch,
)
from ..services import (
    active_job_for,
    actor_of,
    column_with_role,
    enqueue_job,
    get_task,
    move_task,
    next_position,
    publish_task,
    record_activity,
    start_run,
    task_out,
    utcnow,
)

router = APIRouter(tags=["tasks"])


async def _bind_vars(db, task: m.Task, bindings: dict) -> None:
    spec = await load_spec(db, task.command_ref) if task.command_ref else None
    try:
        checked = await variables.check_bindings(
            db, spec.vars if spec else [], bindings, task.project_id, task
        )
    except variables.VariableError as exc:
        raise HTTPException(422, str(exc)) from exc
    await variables.release_ephemeral(db, task, set(checked.values()))
    task.var_bindings = checked


async def _use_library_command(db, task: m.Task, command_ref: str, args: dict) -> None:
    """Render a library command onto the card, or raise 404/422 with per-argument errors."""
    spec = await load_spec(db, command_ref)
    if spec is None:
        raise HTTPException(404, f"Command “{command_ref}” not found")
    try:
        await apply_library(db, task, spec, args)
    except ArgumentError as exc:
        raise HTTPException(422, {"message": str(exc), "errors": exc.errors}) from exc


async def _task_or_404(db, user: m.User, task_id: str, *, write: bool = False) -> m.Task:
    task = await get_task(db, parse_uuid(task_id))
    if task is None:
        raise HTTPException(404, "Task not found")
    await check_project(db, user, task.project_id, write=write)
    return task


@router.post("/projects/{project_id}/tasks", response_model=TaskOut, status_code=201)
async def create_task(project_id: str, body: TaskIn, db: DB, user: CurrentUser):
    project = await project_or_404(db, user, parse_uuid(project_id), write=True)
    column: m.BoardColumn | None = None
    if body.column_id is not None:
        column = await db.get(m.BoardColumn, body.column_id)
        if column is None or column.project_id != project.id:
            raise HTTPException(404, "Column not found")
    else:
        column = await column_with_role(db, project.id, m.ColumnRole.todo)
        if column is None:
            column = (
                await db.execute(
                    select(m.BoardColumn)
                    .where(m.BoardColumn.project_id == project.id)
                    .order_by(m.BoardColumn.position)
                    .limit(1)
                )
            ).scalar_one_or_none()
        if column is None:
            raise HTTPException(409, "Project has no columns")
    data = body.model_dump(exclude={"column_id", "command_ref", "command_args", "var_bindings"})
    task = m.Task(
        project_id=project.id,
        column_id=column.id,
        position=await next_position(db, column.id),
        created_by=user.id,
        **data,
    )
    if body.command_ref:
        await _use_library_command(db, task, body.command_ref, body.command_args)
    db.add(task)
    await db.flush()
    if body.var_bindings:
        await _bind_vars(db, task, body.var_bindings)
    await record_activity(
        db,
        type="task.created",
        message=f"Created “{task.title}”",
        project_id=project.id,
        task_id=task.id,
        **actor_of(user),
    )
    # Creating a command card directly in the active column runs it immediately.
    if (
        column.role == m.ColumnRole.active
        and task.kind == m.TaskKind.command
        and task.run_on_move
        and task.command.strip()
    ):
        await enqueue_job(db, task, user, reason="created in In Progress")
    await db.commit()
    out = await publish_task(db, task, "task.created")
    dispatcher.wake()
    return out


@router.get("/tasks/{task_id}", response_model=TaskOut)
async def read_task(task_id: str, db: DB, user: CurrentUser):
    return await task_out(db, await _task_or_404(db, user, task_id))


@router.patch("/tasks/{task_id}", response_model=TaskOut)
async def update_task(task_id: str, body: TaskPatch, db: DB, user: CurrentUser):
    task = await _task_or_404(db, user, task_id, write=True)
    changes = body.model_dump(exclude_unset=True)
    bindings = changes.pop("var_bindings", None)
    library_ref = changes.pop("command_ref", task.command_ref)
    library_args = changes.pop("command_args", None)
    detaching = "command_ref" in body.model_fields_set and library_ref is None

    for key, value in changes.items():
        setattr(task, key, value)
    if detaching:
        task.command_ref = None  # keeps the rendered command as a plain shell command
    elif library_ref and (library_ref != task.command_ref or library_args is not None):
        await _use_library_command(
            db, task, library_ref, library_args if library_args is not None else task.command_args
        )
        if "command" in changes:
            task.command = changes["command"]
    if bindings is not None:
        await _bind_vars(db, task, bindings)
    elif detaching:
        await variables.release_ephemeral(db, task, set())
        task.var_bindings = {}
    elif task.var_bindings and task.command_ref:
        # Switching commands keeps only the bindings the new command still has slots for.
        spec = await load_spec(db, task.command_ref)
        slots = {s.name for s in spec.vars} if spec else set()
        task.var_bindings = {k: v for k, v in task.var_bindings.items() if k in slots}
    await db.commit()
    return await publish_task(db, task)


@router.delete("/tasks/{task_id}", status_code=204)
async def delete_task(task_id: str, db: DB, user: CurrentUser):
    task = await _task_or_404(db, user, task_id, write=True)
    project_id = task.project_id
    await record_activity(
        db,
        type="task.deleted",
        message=f"Deleted “{task.title}”",
        project_id=project_id,
        **actor_of(user),
    )
    await db.delete(task)
    await db.commit()
    bus.publish("task.deleted", project_id, {"id": parse_uuid(task_id)})


@router.post("/tasks/{task_id}/move", response_model=TaskOut)
async def move(task_id: str, body: TaskMove, db: DB, user: CurrentUser):
    task = await _task_or_404(db, user, task_id, write=True)
    column = await db.get(m.BoardColumn, body.column_id)
    if column is None or column.project_id != task.project_id:
        raise HTTPException(404, "Column not found")
    job = await move_task(db, task, column, body.position, user)
    await db.commit()
    out = await publish_task(db, task, "task.moved")
    if job is not None:
        dispatcher.wake()
    return out


@router.post("/tasks/{task_id}/run", response_model=JobOut, status_code=201)
async def run(task_id: str, db: DB, user: CurrentUser):
    task = await _task_or_404(db, user, task_id, write=True)
    if task.kind != m.TaskKind.command or not task.command.strip():
        raise HTTPException(400, "This card has no command to run")
    if await active_job_for(db, task.id) is not None:
        raise HTTPException(409, "A job for this card is already queued or running")
    job, moved = await start_run(db, task, user)
    await db.commit()
    await publish_task(db, task, "task.moved" if moved else "task.updated")
    dispatcher.wake()
    return job


@router.get("/tasks/{task_id}/jobs", response_model=list[JobOut])
async def task_jobs(task_id: str, db: DB, user: CurrentUser):
    task = await _task_or_404(db, user, task_id)
    return (
        await db.execute(
            select(m.Job).where(m.Job.task_id == task.id).order_by(m.Job.queued_at.desc())
        )
    ).scalars().all()


@router.get("/tasks/{task_id}/activity", response_model=list[ActivityOut])
async def task_activity(task_id: str, db: DB, user: CurrentUser):
    task = await _task_or_404(db, user, task_id)
    return (
        await db.execute(
            select(m.ActivityEvent)
            .where(m.ActivityEvent.task_id == task.id)
            .order_by(m.ActivityEvent.id.desc())
            .limit(100)
        )
    ).scalars().all()


# ----- comments -----


def _comment_out(comment: m.Comment) -> CommentOut:
    out = CommentOut.model_validate(comment)
    out.author_name = comment.author.display_name if comment.author else "unknown"
    out.author_avatar = comment.author.avatar_url if comment.author else None
    return out


@router.get("/tasks/{task_id}/comments", response_model=list[CommentOut])
async def list_comments(task_id: str, db: DB, user: CurrentUser):
    task = await _task_or_404(db, user, task_id)
    from sqlalchemy.orm import selectinload

    rows = (
        await db.execute(
            select(m.Comment)
            .options(selectinload(m.Comment.author))
            .where(m.Comment.task_id == task.id)
            .order_by(m.Comment.created_at)
        )
    ).scalars().all()
    return [_comment_out(c) for c in rows]


@router.post("/tasks/{task_id}/comments", response_model=CommentOut, status_code=201)
async def create_comment(task_id: str, body: CommentIn, db: DB, user: CurrentUser):
    task = await _task_or_404(db, user, task_id, write=True)
    if not body.body.strip():
        raise HTTPException(400, "Comment is empty")
    comment = m.Comment(task_id=task.id, author_id=user.id, body=body.body)
    comment.author = user
    db.add(comment)
    await db.flush()
    await record_activity(
        db,
        type="comment.created",
        message=f"Commented on “{task.title}”",
        project_id=task.project_id,
        task_id=task.id,
        **actor_of(user),
    )
    await db.commit()
    out = _comment_out(comment)
    bus.publish("comment.created", task.project_id, out)
    await publish_task(db, task)
    return out


@router.patch("/tasks/{task_id}/comments/{comment_id}", response_model=CommentOut)
async def update_comment(
    task_id: str, comment_id: str, body: CommentIn, db: DB, user: CurrentUser
):
    task = await _task_or_404(db, user, task_id, write=True)
    comment = await db.get(m.Comment, parse_uuid(comment_id))
    if comment is None or comment.task_id != task.id:
        raise HTTPException(404, "Comment not found")
    if comment.author_id != user.id and not user.is_admin:
        raise HTTPException(403, "You can only edit your own comments")
    if not body.body.strip():
        raise HTTPException(400, "Comment is empty")
    if body.body != comment.body:
        comment.body = body.body
        comment.edited_at = utcnow()
        await db.commit()
    await db.refresh(comment, ["author"])
    out = _comment_out(comment)
    bus.publish("comment.updated", task.project_id, out)
    return out


@router.delete("/tasks/{task_id}/comments/{comment_id}", status_code=204)
async def delete_comment(task_id: str, comment_id: str, db: DB, user: CurrentUser):
    task = await _task_or_404(db, user, task_id, write=True)
    comment = await db.get(m.Comment, parse_uuid(comment_id))
    if comment is None or comment.task_id != task.id:
        raise HTTPException(404, "Comment not found")
    if comment.author_id != user.id and not user.is_admin:
        raise HTTPException(403, "You can only delete your own comments")
    await db.delete(comment)
    await db.commit()
    bus.publish("comment.deleted", task.project_id, {"id": comment.id, "task_id": task.id})
    await publish_task(db, task)
