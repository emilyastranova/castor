from __future__ import annotations

import re
import uuid
from typing import Annotated

import aiofiles
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select

from .. import models as m
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


@router.get("/tasks/{task_id}/attachments", response_model=list[AttachmentOut])
async def list_task_attachments(task_id: str, db: DB, _: CurrentUser):
    rows = (
        await db.execute(
            select(m.Attachment)
            .where(m.Attachment.task_id == parse_uuid(task_id))
            .order_by(m.Attachment.created_at)
        )
    ).scalars().all()
    return rows


@router.post("/tasks/{task_id}/attachments", response_model=AttachmentOut, status_code=201)
async def upload_task_attachment(
    task_id: str,
    db: DB,
    user: CurrentUser,
    file: Annotated[UploadFile, File()],
    kind: Annotated[str, Form()] = "file",
):
    task = await db.get(m.Task, parse_uuid(task_id))
    if task is None:
        raise HTTPException(404, "Task not found")
    if kind not in ("file", "screenshot", "artifact"):
        kind = "file"
    content_type = file.content_type or "application/octet-stream"
    if kind == "file" and content_type.startswith("image/"):
        kind = "screenshot"
    attachment_id = uuid.uuid4()
    filename = _safe_name(file.filename or "upload")
    directory = settings.uploads_dir / str(task.project_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{attachment_id}-{filename}"
    size = 0
    async with aiofiles.open(path, "wb") as fh:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            await fh.write(chunk)
    attachment = m.Attachment(
        id=attachment_id,
        project_id=task.project_id,
        task_id=task.id,
        uploader_id=user.id,
        kind=kind,
        filename=file.filename or filename,
        content_type=content_type,
        size=size,
        storage_path=str(path.relative_to(settings.data_dir)),
    )
    db.add(attachment)
    await db.flush()
    await record_activity(
        db,
        type="attachment.created",
        message=f"Attached {attachment.filename} to “{task.title}”",
        project_id=task.project_id,
        task_id=task.id,
        payload={"attachment_id": str(attachment.id), "kind": kind},
        **actor_of(user),
    )
    await db.commit()
    out = AttachmentOut.model_validate(attachment)
    bus.publish("attachment.created", task.project_id, out)
    await publish_task(db, task)
    return out


@router.get("/attachments/{attachment_id}/download")
async def download_attachment(attachment_id: str, db: DB, _: CurrentUser, inline: bool = False):
    attachment = await db.get(m.Attachment, parse_uuid(attachment_id))
    if attachment is None:
        raise HTTPException(404, "Attachment not found")
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
async def delete_attachment(attachment_id: str, db: DB, _: CurrentUser):
    attachment = await db.get(m.Attachment, parse_uuid(attachment_id))
    if attachment is None:
        raise HTTPException(404, "Attachment not found")
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
