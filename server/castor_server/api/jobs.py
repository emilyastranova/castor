from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from sqlalchemy import select

from .. import artifacts
from .. import models as m
from ..access import job_or_404, visible_project_ids
from ..artifacts import token_matches
from ..deps import DB, CurrentUser, parse_uuid
from ..orchestrator.dispatcher import dispatcher
from ..outputs import parse_job
from ..schemas import AttachmentOut, JobDetail, JobOut, JobOutputOut, LogChunkOut

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

    async def chunks():
        while chunk := await file.read(1024 * 1024):
            yield chunk

    try:
        return await artifacts.store(db, job, path or file.filename or "artifact", file.content_type, chunks())
    except artifacts.ArtifactRefused as exc:
        raise HTTPException(exc.status, str(exc)) from exc
