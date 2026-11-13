from __future__ import annotations

import json
import re
import uuid
from typing import Annotated, Any

import aiofiles
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select

from .. import models as m
from ..access import check_project, job_or_404, task_or_404
from ..config import settings
from ..deps import DB, CurrentUser, parse_uuid
from ..events import bus
from ..schemas import AttachmentOut
from ..services import actor_of, publish_task, record_activity

router = APIRouter(tags=["attachments"])

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_name(name: str) -> str:
    cleaned = _SAFE.sub("_", name or "file").strip("._") or "file"
    return cleaned[:120]


async def _store(
    db: DB, user: m.User, project_id: uuid.UUID, task_id: uuid.UUID | None, file: UploadFile, kind: str
) -> m.Attachment:
    """Write an upload to the project's uploads directory and add its attachment row."""
    if kind not in ("file", "screenshot", "artifact"):
        kind = "file"
    content_type = file.content_type or "application/octet-stream"
    if kind == "file" and content_type.startswith("image/"):
        kind = "screenshot"
    attachment_id = uuid.uuid4()
    filename = _safe_name(file.filename or "upload")
    directory = settings.uploads_dir / str(project_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{attachment_id}-{filename}"
    size = 0
    async with aiofiles.open(path, "wb") as fh:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            await fh.write(chunk)
    attachment = m.Attachment(
        id=attachment_id,
        project_id=project_id,
        task_id=task_id,
        uploader_id=user.id,
        kind=kind,
        filename=file.filename or filename,
        content_type=content_type,
        size=size,
        storage_path=str(path.relative_to(settings.data_dir)),
    )
    db.add(attachment)
    await db.flush()
    if content_type.startswith("image/"):
        from ..assets_text import after_picture

        after_picture(attachment.id)  # the AI reads it for assets, if the project wants that
    return attachment


@router.post("/projects/{project_id}/attachments", response_model=AttachmentOut, status_code=201)
async def upload_project_attachment(
    project_id: str, db: DB, user: CurrentUser, file: Annotated[UploadFile, File()]
):
    """A file that belongs to the project rather than a card, such as an image pasted into a
    pinned note. It shows wherever it's embedded; the note's edit is what goes in the feed."""
    pid = parse_uuid(project_id)
    await check_project(db, user, pid, write=True)
    attachment = await _store(db, user, pid, None, file, "file")
    await db.commit()
    return AttachmentOut.model_validate(attachment)


@router.get("/tasks/{task_id}/attachments", response_model=list[AttachmentOut])
async def list_task_attachments(task_id: str, db: DB, user: CurrentUser):
    await task_or_404(db, user, parse_uuid(task_id))
    rows = (
        await db.execute(
            select(m.Attachment)
            .where(m.Attachment.task_id == parse_uuid(task_id))
            .order_by(m.Attachment.created_at)
        )
    ).scalars().all()
    return rows


SNAPSHOT_MAX = 512 * 1024  # bytes of the recipe, as JSON (a text snapshot carries its text)
PNG = b"\x89PNG\r\n\x1a\n"


async def _snapshot_of(db: DB, user: m.User, task: m.Task, text: str, job_id: str | None, file: UploadFile) -> dict[str, Any]:
    """A snapshot's recipe, checked: a JSON object of a sensible size, a PNG picture, and (when it
    names one) a job of this card. Without a job it's a picture of text (pasted, a code block)."""
    if len(text.encode()) > SNAPSHOT_MAX:
        raise HTTPException(422, "snapshot: too large")
    try:
        recipe = json.loads(text)
    except ValueError as exc:
        raise HTTPException(422, "snapshot: not JSON") from exc
    if not isinstance(recipe, dict):
        raise HTTPException(422, "snapshot: must be an object")
    head = await file.read(8)
    await file.seek(0)
    if head != PNG:
        raise HTTPException(422, "A snapshot is a PNG picture")
    if job_id:
        job = await job_or_404(db, user, parse_uuid(job_id))
        if job.task_id != task.id:
            raise HTTPException(422, "job_id: that job isn't this card's")
        recipe["job_id"] = str(job.id)
    return recipe


@router.post("/tasks/{task_id}/attachments", response_model=AttachmentOut, status_code=201)
async def upload_task_attachment(
    task_id: str,
    db: DB,
    user: CurrentUser,
    file: Annotated[UploadFile, File()],
    kind: Annotated[str, Form()] = "file",
    snapshot: Annotated[str | None, Form()] = None,
    job_id: Annotated[str | None, Form()] = None,
):
    """A file on a card. With `snapshot` (its recipe as JSON) and `job_id`, a terminal snapshot:
    a PNG of the job's output that the card can open and change again."""
    task = await task_or_404(db, user, parse_uuid(task_id), write=True)
    recipe = await _snapshot_of(db, user, task, snapshot, job_id, file) if snapshot is not None else None
    attachment = await _store(db, user, task.project_id, task.id, file, kind)
    if recipe is not None:
        attachment.snapshot = recipe
        attachment.job_id = uuid.UUID(recipe["job_id"]) if recipe.get("job_id") else None
    await record_activity(
        db,
        type="attachment.created",
        message=f"Attached {'a snapshot, ' if recipe is not None else ''}{attachment.filename} to “{task.title}”",
        project_id=task.project_id,
        task_id=task.id,
        payload={"attachment_id": str(attachment.id), "kind": kind, **({"snapshot": True} if recipe is not None else {})},
        **actor_of(user),
    )
    await db.commit()
    out = AttachmentOut.model_validate(attachment)
    bus.publish("attachment.created", task.project_id, out)
    await publish_task(db, task)
    return out


@router.put("/attachments/{attachment_id}", response_model=AttachmentOut)
async def replace_snapshot(
    attachment_id: str,
    db: DB,
    user: CurrentUser,
    file: Annotated[UploadFile, File()],
    snapshot: Annotated[str, Form()],
):
    """Save a changed terminal snapshot in place: the same attachment (so comments and notes that
    show it show the new picture), with its new recipe. Only snapshots can be replaced."""
    attachment = await db.get(m.Attachment, parse_uuid(attachment_id))
    if attachment is None or attachment.task_id is None:
        raise HTTPException(404, "Attachment not found")
    task = await task_or_404(db, user, attachment.task_id, write=True)
    if attachment.snapshot is None:
        raise HTTPException(409, "Only a snapshot can be changed; upload a new file instead")
    recipe = await _snapshot_of(db, user, task, snapshot, str(attachment.job_id) if attachment.job_id else None, file)
    old = settings.data_dir / attachment.storage_path
    path = old.with_name(f"{attachment.id}-{uuid.uuid4().hex[:8]}-{_safe_name(file.filename or attachment.filename)}")
    size = 0
    async with aiofiles.open(path, "wb") as fh:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            await fh.write(chunk)
    attachment.storage_path = str(path.relative_to(settings.data_dir))
    attachment.size = size
    attachment.content_type = "image/png"
    attachment.snapshot = recipe
    await record_activity(
        db, type="attachment.updated", message=f"Changed the snapshot {attachment.filename} on “{task.title}”",
        project_id=task.project_id, task_id=task.id, payload={"attachment_id": str(attachment.id), "snapshot": True},
        **actor_of(user),
    )
    await db.commit()
    try:
        old.unlink(missing_ok=True)
    except OSError:
        pass
    out = AttachmentOut.model_validate(attachment)
    bus.publish("attachment.updated", task.project_id, out)
    await publish_task(db, task)
    return out


@router.get("/attachments/{attachment_id}/download")
async def download_attachment(attachment_id: str, db: DB, user: CurrentUser, inline: bool = False):
    attachment = await db.get(m.Attachment, parse_uuid(attachment_id))
    if attachment is None:
        raise HTTPException(404, "Attachment not found")
    await check_project(db, user, attachment.project_id)
    path = settings.data_dir / attachment.storage_path
    if not path.exists():
        raise HTTPException(410, "File is missing from storage")
    return FileResponse(
        path,
        media_type=attachment.content_type,
        filename=None if inline else attachment.filename,
        content_disposition_type="inline" if inline else "attachment",
    )


@router.delete("/attachments/{attachment_id}", status_code=204)
async def delete_attachment(attachment_id: str, db: DB, user: CurrentUser):
    attachment = await db.get(m.Attachment, parse_uuid(attachment_id))
    if attachment is None:
        raise HTTPException(404, "Attachment not found")
    await check_project(db, user, attachment.project_id, write=True)
    path = settings.data_dir / attachment.storage_path
    task = await db.get(m.Task, attachment.task_id) if attachment.task_id else None
    project_id = attachment.project_id
    await db.delete(attachment)
    await db.commit()
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    bus.publish(
        "attachment.deleted", project_id, {"id": attachment.id, "task_id": attachment.task_id}
    )
    if task is not None:
        await publish_task(db, task)
