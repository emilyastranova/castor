"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from . import alerts
from . import models as m
from .accounts import ensure_active_admin
from .api import api_router
from .command_library import sync_builtin_definitions
from .config import settings
from .db import SessionLocal, run_migrations
from .orchestrator.dispatcher import dispatcher
from .orchestrator.gateway import gateway
from .playbook_library import sync_builtin_playbooks
from .security import hash_password
from .services import ensure_run_cards
from .tool_library import sync_builtin_tools
from .variables import drop_unclaimed, sync_builtin_kinds
from .ws.ui import router as ui_ws_router

log = logging.getLogger("castor")


async def ensure_admin() -> None:
    async with SessionLocal() as db:
        existing = (await db.execute(select(m.User).limit(1))).scalar_one_or_none()
        if existing is None:
            db.add(
                m.User(
                    username=settings.admin_username,
                    display_name=settings.admin_username,
                    password_hash=hash_password(settings.admin_password),
                    role=m.UserRole.admin,
                )
            )
            await db.commit()
            log.info("created initial admin user %r", settings.admin_username)
        await ensure_active_admin(db)
        # Nothing should be marked running after a restart: connections are gone.
        agents = (await db.execute(select(m.Agent))).scalars().all()
        for agent in agents:
            agent.status = m.AgentStatus.offline
        stale = (
            await db.execute(
                select(m.Job).where(
                    m.Job.status.in_([m.JobStatus.dispatched, m.JobStatus.running])
                )
            )
        ).scalars().all()
        for job in stale:
            job.status = m.JobStatus.lost
            job.error = "server restarted"
        await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=settings.log_level.upper())
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    if settings.auto_migrate:
        await run_migrations()
    await ensure_admin()
    async with SessionLocal() as db:
        await sync_builtin_kinds(db)
        await drop_unclaimed(db)
        loaded_tools = await sync_builtin_tools(db)  # before commands, which name tools
        loaded = await sync_builtin_definitions(db)
        loaded_playbooks = await sync_builtin_playbooks(db)
        carded = await ensure_run_cards(db)  # runs from before run cards (3h)
    if carded:
        log.info("gave %d earlier playbook runs their card", carded)
    log.info("tool library: %d built-in tools", loaded_tools)
    log.info("command library: %d built-in definitions", loaded)
    log.info("playbook library: %d built-in playbooks", loaded_playbooks)
    await dispatcher.start()
    sweeper = asyncio.create_task(alerts.sweep_forever())  # alerts for runs going long
    log.info("castor server ready")
    try:
        yield
    finally:
        sweeper.cancel()
        await dispatcher.stop()


def create_app() -> FastAPI:
    app = FastAPI(title="Castor", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router)
    app.include_router(ui_ws_router)

    @app.websocket("/ws/agent")
    async def agent_socket(ws: WebSocket) -> None:
        await gateway.handle(ws)

    @app.get("/api/health")
    async def health():
        return {"ok": True, "agents_connected": len(gateway.connections)}

    return app


app = create_app()
