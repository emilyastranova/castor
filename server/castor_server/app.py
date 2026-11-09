"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from sqlalchemy import select

from . import alerts, api_tokens
from . import models as m
from .accounts import ensure_active_admin
from .api import api_router
from .command_library import sync_builtin_definitions
from .config import settings
from .db import SessionLocal, run_migrations
from .orchestrator.dispatcher import dispatcher
from .orchestrator.gateway import gateway
from .orchestrator.ssh import ssh_hosts
from .parser_library import load_all as load_library_parsers
from .playbook_library import sync_builtin_playbooks
from .security import hash_password
from .services import ensure_run_cards
from .tool_library import sync_builtin_tools
from .variables import drop_unclaimed, sync_builtin_kinds
from .ws.terminal import router as terminal_ws_router
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
        library_parsers = await load_library_parsers(db)  # before commands, which name parsers
        loaded_tools = await sync_builtin_tools(db)  # before commands, which name tools
        loaded = await sync_builtin_definitions(db)
        loaded_playbooks = await sync_builtin_playbooks(db)
        carded = await ensure_run_cards(db)  # runs from before run cards (3h)
    if carded:
        log.info("gave %d earlier playbook runs their card", carded)
    log.info("parser library: %d parsers", library_parsers)
    log.info("tool library: %d built-in tools", loaded_tools)
    log.info("command library: %d built-in definitions", loaded)
    log.info("playbook library: %d built-in playbooks", loaded_playbooks)
    await dispatcher.start()
    await ssh_hosts.start()  # hosts Castor connects to over SSH
    sweeper = asyncio.create_task(alerts.sweep_forever())  # alerts for runs going long
    log.info("castor server ready")
    try:
        yield
    finally:
        sweeper.cancel()
        await ssh_hosts.stop()
        await dispatcher.stop()


API_DESCRIPTION = """Everything the Castor UI does goes through this API.

**Authentication.** Make a personal API token (your account menu → API tokens) and send it as
`Authorization: Bearer castor_pat_…`. A token acts as you, so your role and projects apply,
narrowed to its scopes and, if you chose some, its projects. Each operation below says the
scope it needs; *session only* ones (tokens, your password and profile) refuse tokens.

**Realtime.** `GET /ws/ui` (WebSocket, `projects:read`) streams the events the UI uses
(`task.moved`, `job.log`, …) for the projects the token can see.
"""


def _openapi(app: FastAPI):
    """The schema, with a bearer scheme and each operation's token scope."""

    def build() -> dict:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version, description=API_DESCRIPTION, routes=app.routes)
        schema.setdefault("components", {})["securitySchemes"] = {
            "token": {"type": "http", "scheme": "bearer", "description": "A personal API token (castor_pat_…)"},
        }
        labels = {api_tokens.ANY: "any token", api_tokens.SESSION: "session only (API tokens can't use this)",
                  api_tokens.PUBLIC: "none"}
        for path, ops in schema.get("paths", {}).items():
            for method, op in ops.items():
                scope = api_tokens.required_scope(method, path) or api_tokens.SESSION
                op["x-castor-scope"] = scope
                if scope != api_tokens.PUBLIC:
                    op["security"] = [{"token": []}]
                note = f"**Token scope:** `{scope}`" if ":" in scope or scope in api_tokens.SCOPES else f"**Token scope:** {labels[scope]}"
                op["description"] = f"{note}\n\n{op.get('description', '')}".strip()
        app.openapi_schema = schema
        return schema

    return build


def create_app() -> FastAPI:
    app = FastAPI(title="Castor", version="0.1.0", lifespan=lifespan, docs_url="/api/docs", redoc_url="/api/redoc",
                  openapi_url="/api/openapi.json", swagger_ui_parameters={"persistAuthorization": True})
    app.openapi = _openapi(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router)
    app.include_router(ui_ws_router)
    app.include_router(terminal_ws_router)

    @app.websocket("/ws/agent")
    async def agent_socket(ws: WebSocket) -> None:
        await gateway.handle(ws)

    @app.get("/api/health")
    async def health():
        return {"ok": True, "agents_connected": len(gateway.connections)}

    return app


app = create_app()
