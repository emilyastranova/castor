"""Events for plugins (docs/PLUGINS.md, phase B): things that happened in a project, told to the
plugins that asked for them in their manifest (`events:`) and are enabled in that project.

`emit(db, …)` queues an event on the database session; it's delivered only once the session
commits (a rollback drops it), so a plugin never hears about something it can't then read. Each
plugin has its own queue and workers: a slow plugin holds up only itself. Delivery is retried a
few times, then dropped and logged. Events caused by a plugin aren't told to that same plugin.
Queues live in memory: events during a restart are lost (they're notifications, not a log)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from typing import Any

from sqlalchemy import event as sa_event
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models as m
from .db import SessionLocal
from .models import utcnow

log = logging.getLogger(__name__)

# The events a plugin can ask for, and what each one's `data` holds.
EVENTS: dict[str, str] = {
    "asset.created": "A confirmed asset is new to the project: {asset: {id, type, key, label, fields, in_scope, scope_groups, confidence}}",
    "asset.linked": "A new relationship between confirmed assets: {link: {id, kind, forward, from, to, fields}}",
    "job.finished": "A card's job finished: {job: {id, status, exit_code, command_ref}, task: {id, title}}",
    "run.finished": "A playbook run finished: {run: {id, playbook_id, status}}",
    "task.moved": "A card moved to another column: {task: {id, title, kind}, from: {id, name, role}, to: {…}}",
    "comment.created": "Someone commented on a card: {comment: {id, body, author}, task: {id, title}}",
}
QUEUE = 500
WORKERS = 2
RETRIES = (1.0, 5.0, 15.0)
TIMEOUT = 120.0
KEY = "castor.plugin_events"


def emit(db: Any, name: str, project_id: uuid.UUID | None, data: dict[str, Any], *, by: m.User | None = None) -> None:
    """Queue an event for plugins; it goes out when this session commits."""
    if name not in EVENTS or project_id is None:
        return
    info = db.info if hasattr(db, "info") else db.sync_session.info
    info.setdefault(KEY, []).append((name, project_id, data, getattr(by, "plugin_id", None)))


@sa_event.listens_for(Session, "after_commit")
def _committed(session: Session) -> None:
    pending = session.info.pop(KEY, None)
    if not pending:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    for name, project_id, data, by_plugin in pending:
        loop.create_task(_route(name, project_id, data, by_plugin))


@sa_event.listens_for(Session, "after_rollback")
def _rolled_back(session: Session) -> None:
    session.info.pop(KEY, None)


class _Queues:
    def __init__(self) -> None:
        self.queues: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        self.workers: dict[str, list[asyncio.Task[Any]]] = {}

    def queue(self, plugin_id: str) -> asyncio.Queue[dict[str, Any]]:
        q = self.queues.get(plugin_id)
        if q is None:
            q = self.queues[plugin_id] = asyncio.Queue(maxsize=QUEUE)
            self.workers[plugin_id] = [asyncio.ensure_future(_work(plugin_id, q)) for _ in range(WORKERS)]
        return q

    def drop(self, plugin_id: str) -> None:
        for task in self.workers.pop(plugin_id, []):
            task.cancel()
        self.queues.pop(plugin_id, None)

    def drop_all(self) -> None:
        for pid in list(self.queues):
            self.drop(pid)


queues = _Queues()


async def _route(name: str, project_id: uuid.UUID, data: dict[str, Any], by_plugin: str | None) -> None:
    from .plugins import enabled_in, plugin_log

    try:
        async with SessionLocal() as db:
            rows = (await db.execute(select(m.Plugin).where(m.Plugin.enabled.is_(True)))).scalars().all()
            wanted = [p for p in rows if name in ((p.granted or {}).get("events") or []) and p.id != by_plugin
                      and await enabled_in(db, p, project_id)]
            project = await db.get(m.Project, project_id) if wanted else None
    except Exception:
        log.exception("routing plugin event %s", name)
        return
    if project is None:
        return
    message = {"name": name, "at": utcnow().isoformat(), "data": data,
               "project": {"id": str(project.id), "codename": project.codename, "name": project.name}}
    for p in wanted:
        try:
            queues.queue(p.id).put_nowait(message)
        except asyncio.QueueFull:
            await plugin_log(p.id, "warn", f"Dropped a {name} event: over {QUEUE} waiting (the plugin is slow or stuck)")


async def _work(plugin_id: str, q: asyncio.Queue[dict[str, Any]]) -> None:
    from .plugin_runtime import PluginRuntimeError
    from .plugins import PluginError, ensure_running, plugin_log

    while True:
        message = await q.get()
        try:
            for attempt, wait in enumerate((0.0, *RETRIES)):
                if wait:
                    await asyncio.sleep(wait)
                try:
                    proc = await ensure_running(plugin_id)
                    await proc.request("event", message, timeout=TIMEOUT)
                    break
                except (PluginError, PluginRuntimeError) as exc:
                    if attempt == len(RETRIES):
                        await plugin_log(plugin_id, "error", f"Gave up on a {message['name']} event: {exc}")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("plugin %s event worker", plugin_id)
        finally:
            q.task_done()


async def idle(plugin_id: str, timeout: float = 30) -> None:
    """Wait until a plugin's queue is empty (tests)."""
    q = queues.queues.get(plugin_id)
    if q is not None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(q.join(), timeout)


# ---------- what each event says ----------


def asset_data(a: m.Asset, project: m.Project | None) -> dict[str, Any]:
    from .assets import scope_groups

    groups = scope_groups(project, a) if project is not None else []
    return {"asset": {"id": str(a.id), "type": a.type, "key": a.key, "label": a.label, "fields": a.fields or {},
                      "in_scope": bool(groups), "scope_groups": groups, "confidence": a.confidence}}


def link_data(lk: m.AssetLink, src: m.Asset, dst: m.Asset) -> dict[str, Any]:
    from .assets import LINK_KINDS

    kind = LINK_KINDS.get(lk.kind)
    end = lambda a: {"id": str(a.id), "type": a.type, "label": a.label}
    return {"link": {"id": str(lk.id), "kind": lk.kind, "forward": kind.forward if kind else lk.kind, "from": end(src), "to": end(dst),
                     "fields": lk.fields or {}}}


def job_data(job: m.Job, task: m.Task) -> dict[str, Any]:
    return {"job": {"id": str(job.id), "status": job.status.value, "exit_code": job.exit_code, "command_ref": task.command_ref},
            "task": {"id": str(task.id), "title": task.title}}
