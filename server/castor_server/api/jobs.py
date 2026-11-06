from __future__ import annotations

import uuid
from typing import Annotated

import aiofiles
from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select

from .. import models as m
from ..access import job_or_404, visible_project_ids
from ..artifacts import token_matches
from ..config import settings
from ..deps import DB, CurrentUser, parse_uuid
from ..events import bus
from ..orchestrator.dispatcher import dispatcher
from ..outputs import parse_job
from ..schemas import AttachmentOut, JobDetail, JobOut, JobOutputOut, LogChunkOut
from ..services import publish_task, utcnow
from .attachments import _safe_name

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=list[JobOut])
async def list_jobs(db: DB, user: CurrentUser, status: str | None = None, limit: int = 100):
    query = select(m.Job).order_by(m.Job.queued_at.desc()).limit(min(limit, 500))
    visible = await visible_project_ids(db, user)
    if visible is not None:
        query = query.join(m.Task, m.Task.id == m.Job.task_id).where(m.Task.project_id.in_(visible))
    if status:
        query = query.where(m.Job.status == status)
    return (await db.execute(query)).scalars().all()


@router.get("/{job_id}", response_model=JobDetail)
async def get_job(job_id: str, db: DB, user: CurrentUser, after_seq: int = -1):
    job = await job_or_404(db, user, parse_uuid(job_id))
    chunks = (
        await db.execute(
            select(m.JobLogChunk)
            .where(m.JobLogChunk.job_id == job.id, m.JobLogChunk.seq > after_seq)
            .order_by(m.JobLogChunk.seq)
        )
    ).scalars().all()
    out = JobDetail.model_validate(job)
    out.logs = [LogChunkOut.model_validate(c) for c in chunks]
    out.outputs = [JobOutputOut.model_validate(o) for o in await _outputs(db, job.id)]
    return out


async def _outputs(db, job_id: uuid.UUID) -> list[m.JobOutput]:
    rows = await db.execute(
        select(m.JobOutput).where(m.JobOutput.job_id == job_id).order_by(m.JobOutput.name)
    )
    return list(rows.scalars())


@router.get("/{job_id}/outputs", response_model=list[JobOutputOut])
async def job_outputs(job_id: str, db: DB, user: CurrentUser):
    job = await job_or_404(db, user, parse_uuid(job_id))
    return await _outputs(db, job.id)


@router.post("/{job_id}/outputs/parse", response_model=list[JobOutputOut])
async def reparse_outputs(job_id: str, db: DB, user: CurrentUser):
    """Run the parsers again (after a parser fix, or for a job from before Phase 2b)."""
    job = await job_or_404(db, user, parse_uuid(job_id), write=True)
    if not job.status.is_terminal:
        raise HTTPException(409, "The job is still running")
    return sorted(await parse_job(db, job), key=lambda o: o.name)


@router.get("/{job_id}/output", response_class=PlainTextResponse)
async def job_output(job_id: str, db: DB, user: CurrentUser, stream: str = "both", raw: bool = False):
    """The job's output as text. An interactive (terminal) job's escape sequences are
    removed unless ?raw=1 (the exact bytes, e.g. to replay in a terminal)."""
    job = await job_or_404(db, user, parse_uuid(job_id))
    query = select(m.JobLogChunk).where(m.JobLogChunk.job_id == job.id)
    if stream in ("stdout", "stderr"):
        query = query.where(m.JobLogChunk.stream == stream)
    chunks = (await db.execute(query.order_by(m.JobLogChunk.seq))).scalars().all()
    text = "".join(c.data for c in chunks)
    if job.tty and not raw:
        from ..interactive import plain

        text = plain(text)
    return text


@router.post("/{job_id}/cancel", response_model=JobOut)
async def cancel_job(job_id: str, db: DB, user: CurrentUser):
    await job_or_404(db, user, parse_uuid(job_id), write=True)
    job = await dispatcher.cancel(parse_uuid(job_id))
    if job is None:
        raise HTTPException(404, "Job not found")
    return job


@router.get("/{job_id}/artifacts", response_model=list[AttachmentOut])
async def list_artifacts(job_id: str, db: DB, user: CurrentUser):
    await job_or_404(db, user, parse_uuid(job_id))
    rows = await db.execute(
        select(m.Attachment)
        .where(m.Attachment.job_id == parse_uuid(job_id))
        .order_by(m.Attachment.created_at)
    )
    return rows.scalars().all()


@router.post("/{job_id}/artifacts", response_model=AttachmentOut, status_code=201)
async def upload_artifact(
    job_id: str,
    db: DB,
    file: Annotated[UploadFile, File()],
    path: Annotated[str, Form()] = "",
    x_castor_job_token: Annotated[str | None, Header()] = None,
):
    """Called by the agent that ran the job, with the one-off token it got in job.run."""
    try:
        job = await db.get(m.Job, uuid.UUID(job_id))
    except ValueError:
        job = None
    # Same answer for an unknown job and a bad token, so the endpoint can't probe job ids.
    if job is None or not token_matches(x_castor_job_token, job.artifact_token_hash):
        raise HTTPException(401, "Invalid job or upload token")
    window_closed = (
        job.status.is_terminal
        and job.finished_at is not None
        and (utcnow() - job.finished_at).total_seconds() > settings.artifact_upload_window_s
    )
    if window_closed:
        raise HTTPException(410, "The upload window for this job has closed")
    count = (
        await db.execute(select(func.count()).where(m.Attachment.job_id == job.id))
    ).scalar_one()
    if count >= settings.artifact_max_files:
        raise HTTPException(413, f"A job can keep at most {settings.artifact_max_files} files")
    task = await db.get(m.Task, job.task_id)
    if task is None:
        raise HTTPException(410, "The card for this job no longer exists")

    display_name = (path or file.filename or "artifact").lstrip("/")[:255]
    attachment_id = uuid.uuid4()
    directory = settings.uploads_dir / str(task.project_id) / "artifacts" / str(job.id)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{attachment_id}-{_safe_name(display_name)}"
    size = 0
    try:
        async with aiofiles.open(target, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.artifact_max_bytes:
                    raise HTTPException(413, "File is larger than the artifact size limit")
                await out.write(chunk)
    except HTTPException:
        target.unlink(missing_ok=True)
        raise

    attachment = m.Attachment(
        id=attachment_id,
        project_id=task.project_id,
        task_id=task.id,
        job_id=job.id,
        kind="artifact",
        filename=display_name,
        content_type=file.content_type or "application/octet-stream",
        size=size,
        storage_path=str(target.relative_to(settings.data_dir)),
    )
    db.add(attachment)
    await db.commit()
    out = AttachmentOut.model_validate(attachment)
    bus.publish("attachment.created", task.project_id, out)
    await publish_task(db, task)
    return out
