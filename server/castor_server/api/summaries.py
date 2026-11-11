"""AI summaries of a finished job or playbook run (summaries.py): read one, or have it written
(again). Reading needs only sight of the project; writing is for people who can change it."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import llm, summaries
from .. import models as m
from ..access import job_or_404
from ..deps import DB, CurrentUser, parse_uuid
from .playbooks import _run_or_404

router = APIRouter(tags=["ai"])


class SummaryOut(BaseModel):
    id: str
    job_id: str | None
    run_id: str | None
    task_id: str | None
    status: str  # writing | done | failed
    text: str
    model: str
    error: str
    automatic: bool  # written by itself (the project has summaries on), not asked for
    updated_at: str | None


def _out(row: m.AiSummary | None) -> dict[str, Any] | None:
    return summaries.summary_out(row)


async def _needs_ai(db: DB) -> None:
    if not await llm.enabled(db, summaries.PURPOSE):
        raise HTTPException(409, "No AI model is set up (Settings → AI)")


@router.get("/jobs/{job_id}/summary", response_model=SummaryOut | None)
async def job_summary(job_id: str, db: DB, user: CurrentUser):
    """The AI summary of a job, or null."""
    job = await job_or_404(db, user, parse_uuid(job_id))
    return _out(await summaries.for_job(db, job.id))


@router.post("/jobs/{job_id}/summary", response_model=SummaryOut)
async def write_job_summary(job_id: str, db: DB, user: CurrentUser):
    """Have the AI summarise a finished job (again): what it found, what failed, what next."""
    job = await job_or_404(db, user, parse_uuid(job_id), write=True)
    if job.status not in (m.JobStatus.succeeded, m.JobStatus.failed, m.JobStatus.lost):
        raise HTTPException(409, "The job hasn't finished")
    await _needs_ai(db)
    return _out(await summaries.write(db, job=job, user=user))


@router.get("/playbook-runs/{run_id}/summary", response_model=SummaryOut | None)
async def run_summary(run_id: str, db: DB, user: CurrentUser):
    """The AI summary of a playbook run, or null."""
    run = await _run_or_404(db, user, run_id)
    return _out(await summaries.for_run(db, run.id))


@router.post("/playbook-runs/{run_id}/summary", response_model=SummaryOut)
async def write_run_summary(run_id: str, db: DB, user: CurrentUser):
    """Have the AI summarise a finished playbook run (again)."""
    run = await _run_or_404(db, user, run_id, write=True)
    if run.status == m.PlaybookRunStatus.running:
        raise HTTPException(409, "The run hasn't finished")
    await _needs_ai(db)
    return _out(await summaries.write(db, run=run, user=user))
