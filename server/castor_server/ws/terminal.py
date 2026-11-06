"""An interactive job's terminal: keystrokes and window sizes from a browser to the job's
agent (Phase 3j). Output doesn't come back here; it's the job's log, on /ws/ui like any job.

Messages from the browser: {"t": "in", "d": "<text>"}, {"t": "resize", "cols": n, "rows": n},
{"t": "signal", "name": "INT" | "TERM"}. Only people who can change the project may type;
the first keystroke makes the job theirs (interactive.took_control)."""

from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import interactive
from .. import models as m
from ..access import check_project
from ..db import SessionLocal
from ..deps import websocket_user
from ..events import bus
from ..orchestrator.gateway import gateway

log = logging.getLogger(__name__)
router = APIRouter()
MAX_INPUT = 64 * 1024  # a paste


@router.websocket("/ws/jobs/{job_id}/terminal")
async def terminal_socket(ws: WebSocket, job_id: str) -> None:
    try:
        jid = uuid.UUID(job_id)
    except ValueError:
        await ws.close(code=4404)
        return
    async with SessionLocal() as db:
        user = await websocket_user(ws, db)
        job = await db.get(m.Job, jid) if user else None
        task = await db.get(m.Task, job.task_id) if job else None
        allowed = False
        if user is not None and task is not None and job.tty:
            try:
                await check_project(db, user, task.project_id, write=True)
                allowed = True
            except Exception:  # noqa: BLE001 - HTTPException: not theirs to type into
                allowed = False
    if user is None:
        await ws.close(code=4401)
        return
    if not allowed:
        await ws.close(code=4403)
        return
    await ws.accept()
    typed = False
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            kind = msg.get("t")
            async with SessionLocal() as db:
                job = await db.get(m.Job, jid)
                if job is None or job.status in (m.JobStatus.succeeded, m.JobStatus.failed, m.JobStatus.cancelled, m.JobStatus.lost):
                    await ws.send_text(json.dumps({"t": "ended"}))
                    continue
                if kind == "in" and isinstance(msg.get("d"), str) and msg["d"]:
                    sent = await gateway.to_job(job, {"type": "job.stdin", "data": msg["d"][:MAX_INPUT]})
                    if sent and not typed:
                        typed = True
                        await interactive.took_control(db, job, user)
                elif kind == "resize" and (size := interactive.size_of(msg)):
                    if job.tty_size != size:
                        job.tty_size = size
                        await db.commit()
                        bus.publish("job.resize", task.project_id, {"job_id": str(job.id), **size})
                    await gateway.to_job(job, {"type": "job.resize", **size})
                elif kind == "signal" and msg.get("name") in ("INT", "TERM", "QUIT"):
                    if await gateway.to_job(job, {"type": "job.signal", "name": msg["name"]}) and not typed:
                        typed = True
                        await interactive.took_control(db, job, user)
    except WebSocketDisconnect:
        pass
