from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy import select

from .. import models as m
from ..deps import DB, CurrentUser, parse_uuid
from ..orchestrator.dispatcher import dispatcher
from ..schemas import JobDetail, JobOut, LogChunkOut

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=list[JobOut])
async def list_jobs(db: DB, _: CurrentUser, status: str | None = None, limit: int = 100):
    query = select(m.Job).order_by(m.Job.queued_at.desc()).limit(min(limit, 500))
    if status:
        query = query.where(m.Job.status == status)
    return (await db.execute(query)).scalars().all()


@router.get("/{job_id}", response_model=JobDetail)
async def get_job(job_id: str, db: DB, _: CurrentUser, after_seq: int = -1):
    job = await db.get(m.Job, parse_uuid(job_id))
    if job is None:
        raise HTTPException(404, "Job not found")
    chunks = (
        await db.execute(
            select(m.JobLogChunk)
            .where(m.JobLogChunk.job_id == job.id, m.JobLogChunk.seq > after_seq)
            .order_by(m.JobLogChunk.seq)
        )
    ).scalars().all()
    out = JobDetail.model_validate(job)
    out.logs = [LogChunkOut.model_validate(c) for c in chunks]
    return out


@router.get("/{job_id}/output", response_class=PlainTextResponse)
async def job_output(job_id: str, db: DB, _: CurrentUser, stream: str = "both"):
    job = await db.get(m.Job, parse_uuid(job_id))
    if job is None:
        raise HTTPException(404, "Job not found")
    query = select(m.JobLogChunk).where(m.JobLogChunk.job_id == job.id)
    if stream in ("stdout", "stderr"):
        query = query.where(m.JobLogChunk.stream == stream)
    chunks = (await db.execute(query.order_by(m.JobLogChunk.seq))).scalars().all()
    return "".join(c.data for c in chunks)


@router.post("/{job_id}/cancel", response_model=JobOut)
async def cancel_job(job_id: str, db: DB, _: CurrentUser):
    job = await dispatcher.cancel(parse_uuid(job_id))
    if job is None:
        raise HTTPException(404, "Job not found")
    return job
