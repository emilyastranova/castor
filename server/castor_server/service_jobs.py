"""Services: jobs that run until someone stops them (command_spec.ServiceSpec): a listener, a relay,
a capture, a log tail. What's different from an ordinary job:

- **Stopping is graceful.** `stop` asks the agent to send the service its `stop_signal` (to the
  process group, or `<runtime> kill --signal` for a container started with `--init`), wait up to
  `stop_timeout` seconds for it to exit and free its ports, and only then kill it. Stopped that
  way the job counts as done (`progress.stopping`), whatever it exits with; `restart` stops it and
  runs its card again once it has.
- **Its output is read as it comes.** `ready` (a regex) marks it up and listening; `watch`
  patterns tell the card's people about lines as they arrive (each distinct line once, at most
  MAX_NOTES per run); and its outputs are parsed every `parse_every` seconds while there's new
  output (`parse_running`), so assets and asset triggers follow it live.
- **It has its own slots** on an agent (`--max-services`), so a listener doesn't hold up scans.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from typing import Any

import regex
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m
from .db import SessionLocal
from .models import utcnow

log = logging.getLogger(__name__)

MAX_NOTES = 50  # watch notifications per run
MAX_MATCHES = 100  # recent matches kept on the job (progress.matches)
_TIMEOUT = 0.2  # seconds a pattern may take on one line
_tails: dict[uuid.UUID, str] = {}  # an unfinished last line per running service


class ServiceError(ValueError):
    """It can't be stopped or restarted (not a running service); the message says why."""


async def stop(db: AsyncSession, job: m.Job, user: m.User | None, *, restart: bool = False) -> m.Job:
    """Stop a running service gracefully (and run it again once it has, with `restart`). The caller commits."""
    from .orchestrator.gateway import gateway
    from .services import actor_of, publish_job, record_activity

    if not job.service:
        raise ServiceError("it isn't a service (one that runs until stopped): cancel it instead")
    if job.status not in (m.JobStatus.dispatched, m.JobStatus.running):
        raise ServiceError(f"it isn't running (it's {job.status.value})")
    spec = job.service
    already = (job.progress or {}).get("stopping")
    job.progress = {**(job.progress or {}), "stopping": {
        "by": str(user.id) if user else None, "at": utcnow().isoformat(), "restart": restart or bool((already or {}).get("restart")),
    }}
    task = await db.get(m.Task, job.task_id)
    if not already:
        sent = await gateway.to_job(job, {"type": "job.stop", "signal": spec.get("stop_signal", "TERM"),
                                          "timeout": spec.get("stop_timeout", 10)})
        if not sent:
            raise ServiceError("its agent isn't connected; cancel it to mark it ended")
    if task is not None:
        await record_activity(db, type="job.stopping", project_id=task.project_id, task_id=task.id,
                              message=f"{'Restarting' if restart else 'Stopping'} “{task.title}” "
                                      f"(SIG{spec.get('stop_signal', 'TERM')}, up to {spec.get('stop_timeout', 10)} s)",
                              payload={"job_id": str(job.id)}, **actor_of(user))
        publish_job(job, task.project_id)
    return job


async def on_output(db: AsyncSession, job: m.Job, data: str) -> None:
    """New output of a running service: is it ready, and does a line match a watch pattern? Updates
    `job.progress` (the caller commits and publishes the job when it changed)."""
    spec = job.service or {}
    if not (spec.get("ready") or spec.get("watch")):
        return
    text = _tails.pop(job.id, "") + data
    lines = text.split("\n")
    _tails[job.id] = lines.pop()[-4000:]  # the unfinished last line waits for the rest
    progress = dict(job.progress or {})
    changed = False
    if spec.get("ready") and not progress.get("ready_at"):
        for line in lines + [_tails[job.id]]:
            if _search(spec["ready"], line):
                progress["ready_at"] = utcnow().isoformat()
                changed = True
                await _activity(db, job, "job.ready", "“{title}” is ready")
                break
    watches = spec.get("watch") or []
    if watches:
        seen = set(progress.get("seen") or [])
        matches = list(progress.get("matches") or [])
        notes = int(progress.get("notes") or 0)
        for line in lines:
            line = line.strip()
            if not line:
                continue
            for w in watches:
                if not _search(w["match"], line):
                    continue
                key = hashlib.sha256(line.encode()).hexdigest()[:16]
                if key in seen:
                    break
                seen.add(key)
                matches.append({"line": line[:500], "say": w.get("say") or "", "at": utcnow().isoformat()})
                changed = True
                if notes < MAX_NOTES:
                    notes += 1
                    await _notify(db, job, w.get("say") or "", line, last=notes == MAX_NOTES)
                break
        progress.update(seen=sorted(seen)[-1000:], matches=matches[-MAX_MATCHES:], notes=notes)
    if changed:
        job.progress = progress


def forget(job_id: uuid.UUID) -> None:
    _tails.pop(job_id, None)


def _search(pattern: str, line: str) -> bool:
    try:
        return regex.search(pattern, line, timeout=_TIMEOUT) is not None
    except (regex.error, TimeoutError):
        return False


async def _activity(db: AsyncSession, job: m.Job, kind: str, message: str) -> None:
    from .services import record_activity

    task = await db.get(m.Task, job.task_id)
    if task is not None:
        await record_activity(db, type=kind, project_id=task.project_id, task_id=task.id,
                              message=message.format(title=task.title), payload={"job_id": str(job.id)},
                              actor_type="system", actor_name="castor")


async def _notify(db: AsyncSession, job: m.Job, say: str, line: str, *, last: bool) -> None:
    from .notifications import notify
    from .services import card_people

    task = await db.get(m.Task, job.task_id)
    if task is None:
        return
    people = [u.id if isinstance(u, m.User) else u for u in await card_people(db, task)]
    if job.created_by and job.created_by not in people:
        people.append(job.created_by)
    title = f"“{task.title}”: {say}" if say else f"“{task.title}” matched"
    body = line[:300] + ("\n(More matches show on the card; no more notifications for this run.)" if last else "")
    await notify(db, kind="job.watch", title=title, body=body, link=f"/projects/{task.project_id}?task={task.id}",
                 severity="info", project_id=task.project_id, subject=f"watch:{job.id}", payload={"job_id": str(job.id)},
                 user_ids=people, webhooks=False)


# ---------- parsing while it runs ----------


async def parse_running() -> None:
    """Parse every running service's outputs that have new output and are due (parse_every)."""
    from .outputs import parse_job

    async with SessionLocal() as db:
        jobs = (await db.execute(select(m.Job).where(m.Job.service.is_not(None), m.Job.output_specs.is_not(None),
                                                      m.Job.status == m.JobStatus.running))).scalars().all()
        now = utcnow()
        for job in jobs:
            progress = dict(job.progress or {})
            size = (job.stdout_bytes or 0) + (job.stderr_bytes or 0)
            last = progress.get("parsed")
            if last and last.get("bytes") == size:
                continue
            if last and (now - _when(last["at"])).total_seconds() < int((job.service or {}).get("parse_every") or 15):
                continue
            try:
                await parse_job(db, job, partial=True)
            except Exception:
                log.exception("parsing running service %s failed", job.id)
                await db.rollback()
            # Read it again from the database (this session keeps what it loaded before parsing): its
            # output went on meanwhile and may have set ready_at or matches, which must not be lost.
            job = await db.get(m.Job, job.id, populate_existing=True)
            if job is not None:
                job.progress = {**(job.progress or {}), "parsed": {"at": utcnow().isoformat(), "bytes": size}}
                await db.commit()


def _when(text: str):
    from datetime import datetime

    return datetime.fromisoformat(text)


async def parse_forever(interval: float = 5.0) -> None:
    while True:
        try:
            await parse_running()
        except Exception:
            log.exception("parsing running services failed")
        await asyncio.sleep(interval)


def describe(spec: dict[str, Any] | None) -> str:
    """"stops with SIGINT (up to 10 s)" for the card and the log."""
    if not spec:
        return ""
    return f"stops with SIG{spec.get('stop_signal', 'TERM')} (up to {spec.get('stop_timeout', 10)} s)"
