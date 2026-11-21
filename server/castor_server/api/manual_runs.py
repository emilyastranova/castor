"""Bringing output in to be parsed (manual_runs.py): find the text and try the parsers on it, then
make it a manual run on a card."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import manual_runs
from .. import models as m
from ..access import check_project, job_or_404, task_or_404
from ..deps import DB, CurrentUser, Operator
from ..schemas import JobOut

router = APIRouter(tags=["parsers"])


class Source(BaseModel):
    """The text: given, or a comment's or timeline post's (its code blocks, if it has any), or a run's
    output (lines [from, to) of it)."""

    text: str | None = Field(None, max_length=manual_runs.MAX_TEXT)
    comment_id: uuid.UUID | None = None
    post_id: uuid.UUID | None = None
    job_id: uuid.UUID | None = None
    lines: tuple[int, int] | None = None


class TryOut(BaseModel):
    text: str
    # Where it came from, for the run ({from, id, lines}).
    origin: dict[str, Any]
    # The card it belongs on, when the source says (a comment's card, a run's card).
    task_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    # The parsers that read something from it, best first: {parser, type, description, generic, count, preview}.
    candidates: list[dict[str, Any]]


class ManualRunIn(Source):
    # The parser to read it with; none: it's kept unread (with `ai`, the AI drafts a parser for it).
    parser: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    ai: bool = False
    # The text was edited after it came from the source: it's kept as given, and the source noted.
    edited: bool = False


async def _source(db, user: m.User, body: Source) -> tuple[str, dict[str, Any], uuid.UUID | None, uuid.UUID | None]:
    """(text, origin, task id, project id) for a source; 404 for what the person can't see."""
    if body.comment_id is not None:
        comment = await db.get(m.Comment, body.comment_id)
        task = await db.get(m.Task, comment.task_id) if comment else None
        if task is None:
            raise HTTPException(404, "Comment not found")
        await check_project(db, user, task.project_id)
        return manual_runs.text_of_writing(comment.body), manual_runs.origin_of("comment", comment.id), task.id, task.project_id
    if body.post_id is not None:
        post = await db.get(m.ProjectPost, body.post_id)
        if post is None:
            raise HTTPException(404, "Post not found")
        await check_project(db, user, post.project_id)
        return manual_runs.text_of_writing(post.body), manual_runs.origin_of("post", post.id), None, post.project_id
    if body.job_id is not None:
        job = await job_or_404(db, user, body.job_id)
        task = await db.get(m.Task, job.task_id)
        text = await manual_runs.text_of_job(db, job, body.lines)
        return text, manual_runs.origin_of("job", job.id, body.lines), job.task_id, task.project_id if task else None
    if body.text is None:
        raise HTTPException(422, "Give the text, or a comment, post or run to take it from")
    return body.text, manual_runs.origin_of("paste"), None, None


@router.post("/parsers/try", response_model=TryOut)
async def try_parsers(body: Source, db: DB, user: CurrentUser):
    """Every parser that reads something from the text, best first, and the text itself (from its
    source). Nothing is kept."""
    text, origin, task_id, project_id = await _source(db, user, body)
    if body.text is not None and origin["from"] != "paste":
        text = body.text  # edited after it came from the source
    return TryOut(text=text, origin=origin, task_id=task_id, project_id=project_id, candidates=await manual_runs.rank(text))


@router.post("/tasks/{task_id}/manual-runs", response_model=JobOut, status_code=201)
async def create_manual_run(task_id: uuid.UUID, body: ManualRunIn, db: DB, user: Operator):
    """Bring output into the card as a manual run, read with `parser` (or, unread, for the AI to draft
    a parser with `ai`). Its results become assets like any run's."""
    task = await task_or_404(db, user, task_id, write=True)
    text, origin, _, project_id = await _source(db, user, body)
    if project_id is not None and project_id != task.project_id:
        raise HTTPException(422, "That comes from another project: bring it into a card of its own project")
    if body.text is not None and origin["from"] != "paste":
        text, origin = body.text, {**origin, "edited": True}
    try:
        job = await manual_runs.create(db, task, user, text, parser=body.parser, options=body.options, origin=origin, ai=body.ai)
    except manual_runs.ManualRunError as exc:
        raise HTTPException(422, str(exc)) from exc
    await db.refresh(job)
    return JobOut.model_validate(job)
