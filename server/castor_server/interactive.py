"""Interactive jobs (Phase 3j): jobs that run in a terminal (pty) people can type into.

The agent runs the job under a pty and streams raw terminal output; the browser renders it
with xterm.js. Keystrokes and window sizes come in over /ws/jobs/{id}/terminal and go to the
agent as `job.stdin` / `job.resize` / `job.signal`. When an automated run stops at what looks
like a prompt, the agent sends `job.prompt`, and the people who'd care are told, unless
someone already took the terminal over: then it's theirs, and prompts are expected.

Keystrokes are never logged (a password typed at a prompt doesn't echo, and must not end up
in the job's output or the activity feed); only who took control, and when.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from . import alerts, notifications
from . import models as m
from .services import actor_of, publish_job, record_activity

# CSI/OSC/other escape sequences, for reading terminal output as text (AI, expect, downloads).
ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[PX^_][^\x1b]*\x1b\\|[@-Z\\-_])")


def plain(text: str) -> str:
    """Terminal output as text: escape sequences removed, carriage-return overwrites applied
    line by line (progress bars keep their last state)."""
    text = ANSI.sub("", text)
    lines = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if "\r" in line:
            line = line.rsplit("\r", 1)[-1] or line.replace("\r", "")
        lines.append(line)
    return "\n".join(lines)


def _subject(job: m.Job) -> str:
    return f"job:{job.id}:input"


async def on_prompt(db: AsyncSession, job: m.Job, text: str | None) -> None:
    """The agent says the job is (text) or is no longer (None) waiting at a prompt."""
    text = (text or "").strip()[:300] or None
    if text == job.waiting_input:
        return
    task = await db.get(m.Task, job.task_id)
    if text is None or job.input_by is not None or job.status != m.JobStatus.running:
        if job.waiting_input is not None:
            job.waiting_input = None
            await notifications.clear(db, _subject(job))
            await db.commit()
            if task is not None:
                publish_job(job, task.project_id)
        return
    job.waiting_input = text
    if task is not None:
        people = {uid for uid in [job.created_by, task.created_by] if uid}
        people |= {uuid.UUID(a) for a in (task.assignees or [])}
        from .people import group_people

        people |= {u.id for u in await group_people(db, task.project_id, task.group_assignees or [])}
        people |= {uuid.UUID(str(p)) for p in ((task.alerts or {}).get("people") or [])}
        link = f"/projects/{task.project_id}?task={task.id}"
        if task.playbook_run_id is not None:
            run = await db.get(m.PlaybookRun, task.playbook_run_id)
            if run is not None:
                people.add(run.created_by) if run.created_by else None
                link = f"/projects/{task.project_id}?task={run.card_id}&step={task.id}" if run.card_id else link
        await record_activity(db, type="job.waiting_input", message=f"“{task.title}” is waiting for input: {text}",
                              project_id=task.project_id, task_id=task.id, payload={"job_id": str(job.id), "prompt": text})
        await notifications.notify(
            db, kind="job.input", severity="warning", title=f"“{task.title}” is waiting for input",
            body=text, link=link, project_id=task.project_id, subject=_subject(job),
            payload={"job_id": str(job.id)}, user_ids=people, webhooks=False,
        )
        project = await db.get(m.Project, task.project_id)
        values = alerts.values_for("input", title=f"“{task.title}” is waiting for input", body=text, link=link,
                                   severity="warning", status="waiting", project=project, card=task.title, started=job.started_at)
        await alerts.emit(db, "input", values, card=task, subject=f"job:{job.id}:input")
    await db.commit()
    if task is not None:
        publish_job(job, task.project_id)


async def took_control(db: AsyncSession, job: m.Job, user: m.User) -> None:
    """Someone typed into the job: it's theirs now (once per job, recorded)."""
    changed = False
    if job.input_by is None:
        job.input_by = user.id
        changed = True
        task = await db.get(m.Task, job.task_id)
        if task is not None:
            await record_activity(db, type="job.taken_over", message=f"{user.display_name} took over the terminal of “{task.title}”",
                                  project_id=task.project_id, task_id=task.id, payload={"job_id": str(job.id)}, **actor_of(user))
    if job.waiting_input is not None:
        job.waiting_input = None
        await notifications.clear(db, _subject(job))
        changed = True
    if changed:
        await db.commit()
        task = await db.get(m.Task, job.task_id)
        if task is not None:
            publish_job(job, task.project_id)


async def finished(db: AsyncSession, job: m.Job) -> None:
    """The job ended: nothing to wait for any more (the caller commits)."""
    if job.waiting_input is not None:
        job.waiting_input = None
        await notifications.clear(db, _subject(job))


def size_of(message: dict[str, Any]) -> dict[str, int] | None:
    try:
        cols, rows = int(message["cols"]), int(message["rows"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (10 <= cols <= 1000 and 2 <= rows <= 500):
        return None
    return {"cols": cols, "rows": rows}
