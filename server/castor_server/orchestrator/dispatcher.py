"""Matches queued jobs to connected agents."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .. import models as m
from .. import variables
from ..artifacts import collect_patterns, new_token
from ..command_library import load_spec
from ..config import settings
from ..db import SessionLocal
from ..outputs import output_specs_for
from ..services import publish_job, publish_task, record_activity, utcnow
from ..tool_library import tools_by_id
from ..tool_spec import ToolSpec
from .gateway import AgentConnection, gateway

log = logging.getLogger(__name__)


def tool_way(conn: AgentConnection, tool: ToolSpec) -> dict[str, Any] | None:
    """How this agent would run the tool (image, build or host), or None if it can't."""
    return tool.way_for(conn.runtimes, bool(conn.capabilities.get("host_install")))


def tool_ready(conn: AgentConnection, tool: ToolSpec | None) -> bool:
    """Whether the agent has the tool already (a recipe it has built); used to prefer it."""
    way = tool_way(conn, tool) if tool else None
    return way is None or way["kind"] != "build" or way["ref"] in (conn.capabilities.get("tools") or [])


def agent_matches(conn: AgentConnection, job: m.Job, selector: dict[str, Any], tool: ToolSpec | None = None) -> bool:
    if conn.free_slots <= 0:
        return False
    if getattr(job, "tty", False) and not conn.capabilities.get("tty"):
        return False  # an agent from before interactive jobs would run it without a terminal
    if tool is not None:
        if tool_way(conn, tool) is None:
            return False
    elif job.image:
        if not (conn.runtimes & {"podman", "docker"}):
            return False
    elif "host" not in conn.runtimes and not (conn.runtimes & {"podman", "docker"}):
        return False
    wanted_agent = selector.get("agent_id")
    if wanted_agent and str(conn.agent_id) != str(wanted_agent):
        return False
    wanted_tags = set(selector.get("tags") or [])
    return not wanted_tags or wanted_tags.issubset(set(conn.tags))


class Dispatcher:
    def __init__(self) -> None:
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def wake(self) -> None:
        self._wake.set()

    async def start(self) -> None:
        gateway.on_change.append(self.wake)
        self._task = asyncio.create_task(self._loop(), name="dispatcher")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=settings.dispatch_interval)
            except TimeoutError:
                pass
            self._wake.clear()
            try:
                await gateway.sweep_stale()  # before handing out work, so a silent agent gets none
            except Exception:
                log.exception("agent sweep failed")
            try:
                await self.pump()
            except Exception:
                log.exception("dispatcher pump failed")

    async def pump(self) -> None:
        async with self._lock, SessionLocal() as db:
            queued = (
                await db.execute(
                    select(m.Job)
                    .options(selectinload(m.Job.task))
                    .where(m.Job.status == m.JobStatus.queued)
                    .order_by(m.Job.queued_at)
                )
            ).scalars().all()
            if not queued:
                return
            tools = await tools_by_id(db) if any(j.tool for j in queued) else {}
            for job in queued:
                task = job.task
                selector = task.agent_selector or {}
                tool = tools.get(job.tool) if job.tool else None
                if job.tool and tool is None:
                    await self._fail_unsent(db, job, task, f"the tool {job.tool} isn't in the library")
                    continue
                candidates = [
                    c for c in gateway.connections.values() if agent_matches(c, job, selector, tool)
                ]
                if not candidates:
                    continue
                # Prefer an agent with the tool ready and, for a plain shell command, one that
                # runs it on the host over one that would use its default image.
                conn = min(
                    candidates,
                    key=lambda c: (
                        not tool_ready(c, tool),
                        not job.image and tool is None and "host" not in c.runtimes,
                        len(c.running),
                    ),
                )
                way = tool_way(conn, tool) if tool else None
                if way is not None:
                    # The image the tool resolves to on this agent, kept on the job.
                    job.image = way.get("ref") if way["kind"] in ("image", "build") else None
                job.agent_id = conn.agent_id
                job.status = m.JobStatus.dispatched
                job.dispatched_at = utcnow()
                upload_token, job.artifact_token_hash = new_token()
                spec = await load_spec(db, task.command_ref) if task.command_ref else None
                artifacts = {"token": upload_token, "collect": collect_patterns(spec)}
                job.output_specs = output_specs_for(spec.outputs) if spec else None
                try:
                    resolved = await variables.resolve(
                        db, spec.vars if spec else [], job.var_bindings or {}, task.project_id
                    )
                except variables.VariableError as exc:
                    await self._fail_unsent(db, job, task, f"variables: {exc}")
                    continue
                await record_activity(
                    db,
                    type="job.dispatched",
                    message=f"Sent “{task.title}” to {conn.name}",
                    project_id=task.project_id,
                    task_id=task.id,
                    payload={"job_id": str(job.id), "agent_id": str(conn.agent_id)},
                )
                await db.commit()
                try:
                    await gateway.send_job(conn, job, artifacts, resolved, way)
                except Exception as exc:  # noqa: BLE001 - any send failure re-queues the job
                    log.warning("failed to send job %s to %s: %s", job.id, conn.name, exc)
                    conn.running.discard(job.id)
                    job.status = m.JobStatus.queued
                    job.agent_id = None
                    job.dispatched_at = None
                    await db.commit()
                    continue
                publish_job(job, task.project_id)
                await publish_task(db, task)

    async def _fail_unsent(self, db, job: m.Job, task: m.Task, error: str) -> None:
        """A job that can't be sent (e.g. its variable set is gone) fails on the card."""
        job.status = m.JobStatus.failed
        job.agent_id = None
        job.error = error
        job.finished_at = utcnow()
        await record_activity(
            db,
            type="job.failed",
            message=f"“{task.title}” could not start: {error}",
            project_id=task.project_id,
            task_id=task.id,
            payload={"job_id": str(job.id)},
        )
        await db.commit()
        publish_job(job, task.project_id)
        await publish_task(db, task)
        if task.playbook_run_id is not None:
            from ..services import advance_playbook_run

            await advance_playbook_run(db, task.playbook_run_id)

    async def cancel(self, job_id: uuid.UUID) -> m.Job | None:
        async with SessionLocal() as db:
            job = await db.get(m.Job, job_id)
            if job is None or job.status.is_terminal:
                return job
            task = await db.get(m.Task, job.task_id)
            if job.status == m.JobStatus.queued:
                job.status = m.JobStatus.cancelled
                job.finished_at = utcnow()
            else:
                job.status = m.JobStatus.cancelled
                sent = await gateway.cancel_job(job)
                if not sent:
                    job.finished_at = utcnow()
                    job.error = "agent unreachable"
            if task is not None:
                await record_activity(
                    db,
                    type="job.cancelled",
                    message=f"Cancelled “{task.title}”",
                    project_id=task.project_id,
                    task_id=task.id,
                    payload={"job_id": str(job.id)},
                )
            await db.commit()
            if task is not None:
                publish_job(job, task.project_id)
                await publish_task(db, task)
            return job


dispatcher = Dispatcher()
