"""What changed in a run's results since an earlier one (compare.py): a card's job against the
run before it, a playbook run step by step against the playbook's run before it."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_, select

from .. import compare
from .. import models as m
from ..access import job_or_404
from ..deps import DB, CurrentUser, parse_uuid
from ..services import _item_tasks, _run_spec, _run_task_ids, _step_outputs, latest_jobs_for
from .playbooks import _run_or_404

router = APIRouter(tags=["results"])

FINISHED = (m.JobStatus.succeeded, m.JobStatus.failed)
RUN_FINISHED = (m.PlaybookRunStatus.succeeded, m.PlaybookRunStatus.failed)
CANDIDATES = 40


class OutputChange(BaseModel):
    name: str
    type: str | None
    status: str  # same | changed | new (only in this run) | gone (only in the earlier one) | error
    error: str | None = None
    key: list[str] | None  # the fields records were matched by
    added: list[Any]
    removed: list[Any]
    changed: list[dict[str, Any]]  # {item, before, fields: [{field, before, after}]}
    unchanged: int
    counts: dict[str, int]  # added, removed, changed (whole, even when the lists are cut short)
    before_count: int | None
    after_count: int | None
    before: Any = None  # values that aren't lists
    after: Any = None


class JobBase(BaseModel):
    job_id: uuid.UUID
    task_id: uuid.UUID
    card_title: str
    how: str  # card | step | command | same command line: why it's comparable
    status: str
    queued_at: datetime


class JobChanges(BaseModel):
    job_id: uuid.UUID
    base: JobBase | None  # what it's compared with; None: nothing earlier to compare with
    candidates: list[JobBase]  # other runs to compare with instead, newest first
    outputs: list[OutputChange]
    totals: dict[str, int]


class RunBase(BaseModel):
    run_id: uuid.UUID
    card_id: uuid.UUID | None
    status: str
    created_at: datetime
    inputs: dict[str, Any]


class StepChange(BaseModel):
    step_id: str
    name: str
    status: str  # same | changed | new | gone
    outputs: list[OutputChange]
    totals: dict[str, int]


class RunChanges(BaseModel):
    run_id: uuid.UUID
    base: RunBase | None
    candidates: list[RunBase]
    steps: list[StepChange]
    totals: dict[str, int]


# ---------- a card's job ----------


async def _job_outputs(db: DB, job_id: uuid.UUID) -> list[m.JobOutput]:
    return list((await db.execute(select(m.JobOutput).where(m.JobOutput.job_id == job_id))).scalars())


def _compare_rows(before: list[m.JobOutput], after: list[m.JobOutput]) -> list[dict[str, Any]]:
    errors = {r.name: r.error for r in [*before, *after] if r.error}
    ok_before = {r.name: (r.type, r.value) for r in before if not r.error}
    ok_after = {r.name: (r.type, r.value) for r in after if not r.error}
    result = compare.compare({k: v for k, v in ok_before.items() if k not in errors},
                             {k: v for k, v in ok_after.items() if k not in errors})
    for name, error in errors.items():
        row = next((r for r in [*after, *before] if r.name == name), None)
        empty = compare.diff(name, row.type if row else None, None, None)
        result.append({**empty, "status": "error", "error": error})
    return result


@router.get("/jobs/{job_id}/changes", response_model=JobChanges)
async def job_changes(job_id: str, db: DB, user: CurrentUser, base: str | None = None):
    """What changed in this job's parsed results since an earlier run: by default the run before
    it on the same card, else the same playbook step or library command in the project; `base`
    picks another job to compare with."""
    job = await job_or_404(db, user, parse_uuid(job_id))
    task = await db.get(m.Task, job.task_id)
    assert task is not None
    same = [m.Task.id == task.id, m.Job.command == job.command]
    if task.command_ref:
        same.append(m.Task.command_ref == task.command_ref)
    rows = (await db.execute(
        select(m.Job, m.Task).join(m.Task, m.Task.id == m.Job.task_id)
        .where(m.Task.project_id == task.project_id, m.Job.id != job.id, m.Job.status.in_(FINISHED),
               m.Job.id.in_(select(m.JobOutput.job_id)), or_(*same))
        .order_by(m.Job.queued_at.desc()).limit(CANDIDATES * 3)
    )).all()

    def how(j: m.Job, t: m.Task) -> tuple[int, str]:
        if t.id == task.id:
            return 0, "card"
        if task.playbook_step_id and t.playbook_step_id == task.playbook_step_id and t.command_ref == task.command_ref:
            return 1, "step"
        if task.command_ref and t.command_ref == task.command_ref:
            return 2, "command"
        return 3, "same command line"

    def out(h: tuple[int, str], j: m.Job, t: m.Task) -> JobBase:
        return JobBase(job_id=j.id, task_id=t.id, card_title=t.title, how=h[1], status=j.status.value, queued_at=j.queued_at)

    ranked = [(how(j, t), j, t) for j, t in rows]
    candidates = [out(*r) for r in ranked][:CANDIDATES]
    chosen: JobBase | None = None
    if base:
        base_id = parse_uuid(base)
        chosen = next((c for c in candidates if c.job_id == base_id), None)
        if chosen is None:
            other = await job_or_404(db, user, base_id)
            other_task = await db.get(m.Task, other.task_id)
            if other_task is None or other_task.project_id != task.project_id:
                raise HTTPException(422, "base: a run in the same project")
            chosen = JobBase(job_id=other.id, task_id=other_task.id, card_title=other_task.title, how="chosen",
                             status=other.status.value, queued_at=other.queued_at)
    else:
        earlier = [r for r in ranked if r[1].queued_at < job.queued_at]
        if earlier:  # the closest kind of run first (this card, then the same step, command…), then the latest
            chosen = out(*min(earlier, key=lambda r: (r[0][0], -r[1].queued_at.timestamp())))
    after = await _job_outputs(db, job.id)
    outputs = _compare_rows(await _job_outputs(db, chosen.job_id), after) if chosen else []
    return JobChanges(job_id=job.id, base=chosen, candidates=candidates, outputs=outputs, totals=compare.totals(outputs))


# ---------- a playbook run ----------


async def _run_values(db: DB, run: m.PlaybookRun) -> dict[str, dict[str, Any]]:
    """Each step's results as the run can use them: parsed outputs of its succeeded jobs (for_each
    items merged), transform results."""
    spec = _run_spec(run)
    task_ids = _run_task_ids(run)
    items = await _item_tasks(db, run)
    latest = await latest_jobs_for(db, [*task_ids.values(), *(t.id for ts in items.values() for t in ts)])
    return await _step_outputs(db, spec, task_ids, latest, dict(run.step_results or {}), items)


async def _types(db: DB, run: m.PlaybookRun) -> dict[tuple[str, str], str]:
    """(step id, output name) → the output's type, from the run's parsed outputs."""
    rows = (await db.execute(
        select(m.Task.playbook_step_id, m.JobOutput.name, m.JobOutput.type)
        .join(m.Job, m.Job.id == m.JobOutput.job_id).join(m.Task, m.Task.id == m.Job.task_id)
        .where(m.Task.playbook_run_id == run.id)
    )).all()
    return {(str(s), n): t for s, n, t in rows if s}


def _run_base(r: m.PlaybookRun) -> RunBase:
    return RunBase(run_id=r.id, card_id=r.card_id, status=r.status.value, created_at=r.created_at, inputs=r.inputs or {})


@router.get("/playbook-runs/{run_id}/changes", response_model=RunChanges)
async def run_changes(run_id: str, db: DB, user: CurrentUser, base: str | None = None):
    """What changed in a playbook run's results, step by step, since the playbook's run before
    it in the same project (`base` picks another run of it)."""
    run = await _run_or_404(db, user, run_id)
    others = list((await db.execute(
        select(m.PlaybookRun).where(m.PlaybookRun.project_id == run.project_id, m.PlaybookRun.playbook_id == run.playbook_id,
                                    m.PlaybookRun.id != run.id, m.PlaybookRun.status.in_(RUN_FINISHED))
        .order_by(m.PlaybookRun.created_at.desc()).limit(CANDIDATES)
    )).scalars())
    candidates = [_run_base(r) for r in others]
    chosen: m.PlaybookRun | None = None
    if base:
        chosen = await _run_or_404(db, user, base)
        if chosen.project_id != run.project_id or chosen.id == run.id:
            raise HTTPException(422, "base: another run in the same project")
    else:
        chosen = next((r for r in others if r.created_at < run.created_at), None)
    if chosen is None:
        return RunChanges(run_id=run.id, base=None, candidates=candidates, steps=[], totals=compare.totals([]))

    now_values, base_values = await _run_values(db, run), await _run_values(db, chosen)
    types = {**await _types(db, chosen), **await _types(db, run)}
    names = {s.id: s.name for s in _run_spec(chosen).steps} | {s.id: s.name for s in _run_spec(run).steps}
    order = list(dict.fromkeys([*(s.id for s in _run_spec(run).steps), *(s.id for s in _run_spec(chosen).steps)]))
    steps: list[StepChange] = []
    for step_id in order:
        before, after = base_values.get(step_id), now_values.get(step_id)
        if before is None and after is None:
            continue
        outputs = compare.compare({k: (types.get((step_id, k)), v) for k, v in (before or {}).items()},
                                  {k: (types.get((step_id, k)), v) for k, v in (after or {}).items()})
        t = compare.totals(outputs)
        status = "new" if before is None else "gone" if after is None else "changed" if any(t.values()) else "same"
        steps.append(StepChange(step_id=step_id, name=names.get(step_id, step_id), status=status,
                                outputs=[OutputChange(**o) for o in outputs], totals=t))
    total = {k: sum(s.totals[k] for s in steps) for k in ("added", "removed", "changed")}
    return RunChanges(run_id=run.id, base=_run_base(chosen), candidates=candidates, steps=steps, totals=total)

