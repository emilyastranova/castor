"""Agent WebSocket gateway: registry of connected agents and the wire protocol."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import select

from .. import agent_tokens, alerts, plugin_events, variables
from .. import models as m
from ..command_library import load_spec
from ..config import settings
from ..db import SessionLocal
from ..events import bus, dumps
from ..schemas import AgentOut
from ..services import publish_job, publish_task, record_activity, utcnow

log = logging.getLogger(__name__)


@dataclass
class AgentConnection:
    agent_id: uuid.UUID
    name: str
    ws: Any  # a WebSocket, or an SSH host's SshSocket
    capabilities: dict[str, Any]
    tags: list[str]
    max_concurrency: int
    # Projects whose jobs it may run (ids as strings); None: any (Agent.project_ids).
    project_ids: list[str] | None = None
    # Who may run work on it ({users, groups}); None: everyone (Agent.people, agent_people.py).
    people: dict[str, Any] | None = None
    # The token it authenticated with (None: the shared secret).
    token_id: uuid.UUID | None = None
    running: set[uuid.UUID] = field(default_factory=set)
    # Of those, the services (run until stopped): they count against max_services, not max_concurrency.
    services: set[uuid.UUID] = field(default_factory=set)
    last_seen: datetime = field(default_factory=utcnow)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def runtimes(self) -> set[str]:
        return set(self.capabilities.get("runtimes", []))

    @property
    def free_slots(self) -> int:
        return max(self.max_concurrency - len(self.running - self.services), 0)

    @property
    def max_services(self) -> int:
        return int(self.capabilities.get("max_services") or 0)

    @property
    def free_service_slots(self) -> int:
        return max(self.max_services - len(self.services), 0)

    async def send(self, message: dict[str, Any]) -> None:
        async with self.send_lock:
            await self.ws.send_text(dumps(message))


class RegisterError(Exception):
    """A valid credential that can't be used as this agent."""


class AgentGone(Exception):
    """The agent an HTTP request went through disconnected before it answered."""


@dataclass
class _HttpWait:
    """An HTTP action's request an agent is making for the server (http.request)."""

    agent_id: uuid.UUID
    future: asyncio.Future
    chunks: list[str] = field(default_factory=list)
    size: int = 0


class AgentGateway:
    def __init__(self) -> None:
        self.connections: dict[uuid.UUID, AgentConnection] = {}
        # Secret values each running job was sent, masked out of its logs. Rebuilt from the
        # job's bindings if the server restarted while the job ran.
        self._redactions: dict[uuid.UUID, variables.Resolved] = {}
        self.on_change: list = []  # callbacks invoked when capacity may have changed
        # Last "prepare ahead" result per (agent, tool): preparing / ready / failed.
        self.tool_status: dict[tuple[uuid.UUID, str], dict[str, Any]] = {}
        # HTTP requests agents are making for the server runner, by request id.
        self._http: dict[str, _HttpWait] = {}
        # Route tests agents are running (route.test → route.tested), by request id.
        self._route_tests: dict[str, tuple[uuid.UUID, asyncio.Future]] = {}
        # The route payload (secrets included) of each running HTTP action that goes through one.
        self.route_payloads: dict[uuid.UUID, dict[str, Any]] = {}

    # ----- registry -----

    def get(self, agent_id: uuid.UUID) -> AgentConnection | None:
        return self.connections.get(agent_id)

    def _notify(self) -> None:
        for callback in self.on_change:
            try:
                callback()
            except Exception:  # pragma: no cover
                log.exception("gateway change callback failed")

    async def agent_out(self, agent: m.Agent) -> AgentOut:
        out = AgentOut.model_validate(agent)
        conn = self.connections.get(agent.id)
        out.connected = conn is not None
        out.running_jobs = len(conn.running) if conn else 0
        from .ssh import ssh_hosts

        link = ssh_hosts.by_agent(agent.id)
        out.ssh_host_id = link.host_id if link else None
        if agent.plugin_id:
            from ..plugin_agents import manager

            out.plugin = {"id": agent.plugin_id, "type": agent.agent_type, "machine": agent.machine_ref}
            out.plugin_status = manager.status_of(agent)
        return out

    async def _publish_agent(self, agent: m.Agent) -> None:
        bus.publish("agent.updated", None, await self.agent_out(agent))

    # ----- connection lifecycle -----

    async def handle(self, ws: WebSocket) -> None:
        await ws.accept()
        conn: AgentConnection | None = None
        try:
            raw = await asyncio.wait_for(ws.receive_text(), timeout=10)
            hello = json.loads(raw)
            if hello.get("type") != "hello":
                await ws.send_text(dumps({"type": "hello.error", "reason": "expected hello"}))
                await ws.close(code=4400)
                return
            async with SessionLocal() as db:
                auth = await agent_tokens.authenticate(db, hello)
            if not auth.ok:
                log.warning("agent %r refused: %s", hello.get("name"), auth.reason)
                await ws.send_text(dumps({"type": "hello.error", "reason": auth.reason}))
                await ws.close(code=4401)
                return
            try:
                conn = await self._register(ws, hello, auth.token.id if auth.token else None)
            except RegisterError as exc:
                log.warning("agent %r refused: %s", hello.get("name"), exc)
                await ws.send_text(dumps({"type": "hello.error", "reason": str(exc)}))
                await ws.close(code=4401)
                return
            await conn.send(
                {
                    "type": "hello.ok",
                    "agent_id": conn.agent_id,
                    "heartbeat_interval": settings.agent_heartbeat_interval,
                }
            )
            self._notify()
            while True:
                raw = await ws.receive_text()
                # Each message is handled to the end even if this handler is cancelled (the
                # socket closing right after job.finished): a cancel landing inside a DB
                # session's cleanup can leave it never finishing.
                await asyncio.shield(self._on_message(conn, json.loads(raw)))
        except WebSocketDisconnect:
            pass
        except TimeoutError:
            await ws.close(code=4408)
        except Exception:
            log.exception("agent connection error")
        finally:
            if conn is not None:
                # Cleanup must finish even if this handler is cancelled (server shutdown,
                # test harness teardown); otherwise a half-done DB transaction is left behind.
                await asyncio.shield(self._unregister(conn))

    async def _register(
        self, ws: Any, hello: dict[str, Any], token_id: uuid.UUID | None = None,
        ssh_agent_id: uuid.UUID | None = None, fixed_agent_id: uuid.UUID | None = None,
    ) -> AgentConnection:
        """Find or create the agent record. With a token, the token decides which agent
        this is (binding it on first use); with the shared secret, the name does, unless
        that agent has a token of its own. An SSH host (orchestrator/ssh.py) is its own
        agent record, which no connecting agent can claim."""
        name = str(hello.get("name") or hello.get("hostname") or uuid.uuid4().hex[:8])
        # An SSH host's or a plugin machine's own agent record (Castor connects it; no agent dials in as it).
        ssh_agent_id = ssh_agent_id or fixed_agent_id
        async with SessionLocal() as db:
            by_name = (
                await db.execute(select(m.Agent).where(m.Agent.name == name))
            ).scalar_one_or_none()
            token = await db.get(m.AgentToken, token_id) if token_id else None
            enrolled = False
            if ssh_agent_id is not None:
                agent = await db.get(m.Agent, ssh_agent_id)
                if agent is None:
                    raise RegisterError("the SSH host's agent no longer exists")
            elif token is not None and token.agent_id is not None:
                agent = await db.get(m.Agent, token.agent_id)
                if agent is None:  # pragma: no cover - the FK cascades
                    raise RegisterError("the token's agent no longer exists")
                if agent.name != name and by_name is None:
                    agent.name = name  # renamed with --name; the token says who it is
            else:
                agent = by_name
                if agent is not None and await agent_tokens.has_token(db, agent.id):
                    raise RegisterError(
                        f"agent {name} has its own token (or had one that was revoked); connect with "
                        "its token, or forget the agent first"
                    )
                if agent is None:
                    agent = m.Agent(name=name)
                    db.add(agent)
                    await db.flush()
                if token is not None:
                    token.agent_id = agent.id
                    agent.tags = sorted(set(agent.tags or []) | set(token.tags or []))
                    if token.project_ids is not None:  # the token's limit, set when it was made
                        agent.project_ids = list(token.project_ids)
                    enrolled = True
            if ssh_agent_id is None and await db.scalar(select(m.SshHost.id).where(m.SshHost.agent_id == agent.id)):
                raise RegisterError(f"{agent.name} is an SSH host that Castor connects to; give this agent another --name")
            if ssh_agent_id is None and agent.plugin_id:
                raise RegisterError(f"{agent.name} is a machine the {agent.plugin_id} plugin reaches; give this agent another --name")
            if token is not None:
                agent_tokens.touch(token)
            agent.hostname = str(hello.get("hostname", ""))
            agent.os = str(hello.get("os", ""))
            agent.username = str(hello.get("username", ""))
            agent.version = str(hello.get("version", ""))
            agent.capabilities = hello.get("capabilities") or {}
            if ssh_agent_id is None:  # an SSH host's tags and slots are set in Castor only
                incoming_tags = hello.get("tags") or []
                agent.tags = sorted(set(agent.tags or []) | set(incoming_tags))
                agent.max_concurrency = int(hello.get("max_concurrency") or 1)
            agent.status = m.AgentStatus.idle
            agent.last_seen_at = utcnow()
            await db.flush()
            # If this name reconnects while we still hold an old socket, drop the old one.
            old = self.connections.get(agent.id)
            if old is not None:
                try:
                    await old.ws.close(code=4409)
                except Exception:  # noqa: BLE001, S110 - stale socket may already be gone
                    pass
            conn = AgentConnection(
                agent_id=agent.id,
                name=agent.name,
                ws=ws,
                capabilities=agent.capabilities,
                tags=agent.tags,
                max_concurrency=agent.max_concurrency,
                project_ids=agent.project_ids,
                people=agent.people,
                token_id=token.id if token else None,
            )
            self.connections[agent.id] = conn
            if enrolled:
                await record_activity(
                    db,
                    type="agent.enrolled",
                    message=f"Agent {agent.name} enrolled with token {token.prefix}…",
                    project_id=None,
                    actor_type="agent",
                    actor_id=str(agent.id),
                    actor_name=agent.name,
                    payload={"token_id": str(token.id)},
                )
            await record_activity(
                db,
                type="agent.connected",
                message=f"Agent {agent.name} connected ({agent.hostname})",
                project_id=None,
                actor_type="agent",
                actor_id=str(agent.id),
                actor_name=agent.name,
            )
            await db.commit()
            await self._publish_agent(agent)
            if enrolled:
                bus.publish("agent_tokens.changed", None, {"id": token.id})
        log.info("agent %s connected (%s)", conn.name, conn.agent_id)
        return conn

    async def disconnect(self, agent_id: uuid.UUID, reason: str) -> bool:
        """Drop an agent's connection now (its token was revoked or rotated)."""
        conn = self.connections.get(agent_id)
        if conn is None:
            return False
        try:
            await conn.send({"type": "auth.revoked", "reason": reason})
            await conn.ws.close(code=4401)
        except Exception:  # the socket may already be gone
            log.debug("closing agent %s", conn.name, exc_info=True)
        return True

    async def sweep_stale(self) -> list[str]:
        """Drop agents that have gone quiet: a hung agent or a half-open connection keeps its
        socket but stops sending heartbeats. Each is handled like a disconnect (offline, its
        jobs lost) and its socket is closed so the agent, if it's alive, reconnects."""
        limit = timedelta(seconds=settings.agent_offline_after)
        now = utcnow()
        stale = [c for c in list(self.connections.values()) if now - c.last_seen > limit]
        for conn in stale:
            log.warning("agent %s silent for %ss, marking it offline", conn.name, int((now - conn.last_seen).total_seconds()))
            await asyncio.shield(self._unregister(conn))
            try:
                await asyncio.wait_for(conn.ws.close(code=4408), timeout=2)
            except Exception:  # a dead connection may not take a close frame
                log.debug("closing stale agent %s", conn.name, exc_info=True)
        return [c.name for c in stale]

    async def _unregister(self, conn: AgentConnection) -> None:
        if self.connections.get(conn.agent_id) is not conn:
            return  # replaced by a newer connection
        del self.connections[conn.agent_id]
        from ..mcp_client import hub

        hub.agent_gone(conn.agent_id, conn.name)
        for request_id, wait in list(self._http.items()):
            if wait.agent_id == conn.agent_id and not wait.future.done():
                wait.future.set_exception(AgentGone(f"{conn.name} disconnected"))
                self._http.pop(request_id, None)
        for request_id, (agent_id, fut) in list(self._route_tests.items()):
            if agent_id == conn.agent_id and not fut.done():
                fut.set_result({"error": f"{conn.name} disconnected"})
                self._route_tests.pop(request_id, None)
        from .. import network_routes

        for route_id in network_routes.forget_agent(conn.agent_id):
            bus.publish("route.live", None, {"route_id": route_id, "agent_id": str(conn.agent_id), "state": "down"})
        async with SessionLocal() as db:
            agent = await db.get(m.Agent, conn.agent_id)
            if agent is not None:
                agent.status = m.AgentStatus.offline
                agent.last_seen_at = utcnow()
            # Any job still in flight on this agent is lost.
            jobs = (
                await db.execute(
                    select(m.Job).where(
                        m.Job.agent_id == conn.agent_id,
                        m.Job.status.in_([m.JobStatus.dispatched, m.JobStatus.running]),
                    )
                )
            ).scalars().all()
            for job in jobs:
                job.status = m.JobStatus.lost
                job.finished_at = utcnow()
                job.error = "agent disconnected"
                if job.waiting_input is not None:
                    from ..interactive import finished as interactive_finished

                    await interactive_finished(db, job)
                task = await db.get(m.Task, job.task_id)
                if task is not None:
                    await record_activity(
                        db,
                        type="job.lost",
                        message=f"Lost contact with agent while running “{task.title}”",
                        project_id=task.project_id,
                        task_id=task.id,
                        actor_type="agent",
                        actor_id=str(conn.agent_id),
                        actor_name=conn.name,
                        payload={"job_id": str(job.id)},
                    )
                    await alerts.job_finished(db, task, job, conn.name)
                    plugin_events.emit(db, "job.finished", task.project_id, plugin_events.job_data(job, task),
                                    by=await db.get(m.User, job.created_by) if job.created_by else None)
                    publish_job(job, task.project_id)
                    await publish_task(db, task)
            if agent is not None:
                await record_activity(
                    db,
                    type="agent.disconnected",
                    message=f"Agent {agent.name} disconnected",
                    project_id=None,
                    actor_type="agent",
                    actor_id=str(agent.id),
                    actor_name=agent.name,
                )
            await db.commit()
            if agent is not None:
                await self._publish_agent(agent)
        log.info("agent %s disconnected", conn.name)
        self._notify()

    # ----- inbound messages -----

    async def _on_message(self, conn: AgentConnection, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        conn.last_seen = utcnow()
        if kind == "heartbeat":
            await self._on_heartbeat(conn, msg)
        elif kind == "job.started":
            await self._on_job_started(conn, msg)
        elif kind == "job.log":
            await self._on_job_log(conn, msg)
        elif kind == "job.finished":
            await self._on_job_finished(conn, msg)
        elif kind == "tool.prepared":
            await self._on_tool_prepared(conn, msg)
        elif kind == "job.prompt":
            await self._on_job_prompt(conn, msg)
        elif kind == "http.chunk":
            wait = self._http.get(str(msg.get("request_id")))
            if wait is not None and wait.agent_id == conn.agent_id:
                data = str(msg.get("data") or "")
                wait.chunks.append(data)
                wait.size += len(data) * 3 // 4
                if wait.size > settings.http_max_bytes + 4:
                    self._http.pop(str(msg.get("request_id")), None)
                    if not wait.future.done():
                        wait.future.set_result({"error": "the response is too large", "error_kind": "too_big"})
        elif kind in ("mcp.started", "mcp.message", "mcp.exited"):
            from ..mcp_client import hub

            await hub.on_message(conn.agent_id, msg)
        elif kind == "http.response":
            wait = self._http.pop(str(msg.get("request_id")), None)
            if wait is not None and wait.agent_id == conn.agent_id and not wait.future.done():
                wait.future.set_result({**msg, "body": "".join(wait.chunks)})
        elif kind in ("route.status", "route.hop", "route.host_key", "route.tested", "job.route"):
            try:
                await self._on_route(conn, kind, msg)
            except Exception:  # a bad report mustn't drop the agent's connection
                log.exception("route message %s from %s", kind, conn.name)
        else:
            log.warning("agent %s sent unknown message type %r", conn.name, kind)

    # ----- tools -----

    async def _set_tools(self, agent: m.Agent, conn: AgentConnection, tags: list[Any]) -> None:
        """The tool builds an agent has cached (castor-tools/<id>:<hash> tags)."""
        tags = sorted({str(t) for t in tags})
        if conn.capabilities.get("tools") == tags:
            return
        conn.capabilities = {**conn.capabilities, "tools": tags}
        agent.capabilities = conn.capabilities  # reassign: JSON columns don't track mutation
        await self._publish_agent(agent)

    async def prepare_tool(self, conn: AgentConnection, way: dict[str, Any]) -> None:
        """Ask an agent to pull/build/check a tool now, ahead of any job."""
        self.tool_status[(conn.agent_id, way["id"])] = {"state": "preparing", "ref": way.get("ref"), "error": None, "at": utcnow()}
        bus.publish("tool.status", None, {"agent_id": conn.agent_id, "tool_id": way["id"], **self.tool_status[(conn.agent_id, way["id"])]})
        await conn.send({"type": "tool.prepare", "tool": way})

    async def _on_tool_prepared(self, conn: AgentConnection, msg: dict[str, Any]) -> None:
        tool_id = str(msg.get("tool_id") or "")
        ok = bool(msg.get("ok"))
        status = {"state": "ready" if ok else "failed", "ref": msg.get("ref"), "error": msg.get("error"), "at": utcnow()}
        self.tool_status[(conn.agent_id, tool_id)] = status
        if isinstance(msg.get("tools"), list):
            async with SessionLocal() as db:
                agent = await db.get(m.Agent, conn.agent_id)
                if agent is not None:
                    await self._set_tools(agent, conn, msg["tools"])
                    await db.commit()
        bus.publish("tool.status", None, {"agent_id": conn.agent_id, "tool_id": tool_id, **status})

    async def _on_heartbeat(self, conn: AgentConnection, msg: dict[str, Any]) -> None:
        async with SessionLocal() as db:
            agent = await db.get(m.Agent, conn.agent_id)
            if agent is not None:
                agent.last_seen_at = conn.last_seen
                agent.status = m.AgentStatus.busy if conn.running else m.AgentStatus.idle
                if isinstance(msg.get("tools"), list):
                    await self._set_tools(agent, conn, msg["tools"])
                await db.commit()

    async def _job_id(self, msg: dict[str, Any]) -> uuid.UUID | None:
        try:
            return uuid.UUID(str(msg.get("job_id")))
        except ValueError:
            return None

    async def _on_job_started(self, conn: AgentConnection, msg: dict[str, Any]) -> None:
        job_id = await self._job_id(msg)
        if job_id is None:
            return
        async with SessionLocal() as db:
            job = await db.get(m.Job, job_id)
            if job is None or job.agent_id != conn.agent_id:
                return
            job.status = m.JobStatus.running
            job.started_at = utcnow()
            conn.running.add(job.id)
            task = await db.get(m.Task, job.task_id)
            agent = await db.get(m.Agent, conn.agent_id) if conn.agent_id else None
            if agent is not None:
                agent.status = m.AgentStatus.busy
            await db.commit()
            if task is not None:
                publish_job(job, task.project_id)
                await publish_task(db, task)
            if agent is not None:
                await self._publish_agent(agent)

    async def _on_job_log(self, conn: AgentConnection, msg: dict[str, Any]) -> None:
        job_id = await self._job_id(msg)
        if job_id is None:
            return
        stream = msg.get("stream", "stdout")
        data = str(msg.get("data", ""))
        seq = int(msg.get("seq", 0))
        async with SessionLocal() as db:
            job = await db.get(m.Job, job_id)
            if job is None or job.agent_id != conn.agent_id:
                return
            data = (await self._redaction(db, job)).redact(data)
            db.add(m.JobLogChunk(job_id=job.id, seq=seq, stream=stream, data=data))
            ready_before = bool((job.progress or {}).get("ready_at"))
            matches_before = len((job.progress or {}).get("matches") or [])
            if job.service and stream in ("stdout", "stderr"):
                from ..service_jobs import on_output

                await on_output(db, job, data)  # ready yet? a watched line?
            service_changed = job.service is not None and (bool((job.progress or {}).get("ready_at")) != ready_before
                                                           or len((job.progress or {}).get("matches") or []) != matches_before)
            if stream == "stdout":
                job.stdout_bytes += len(data.encode())
            elif stream == "stderr":
                job.stderr_bytes += len(data.encode())
            task_project = (
                await db.execute(select(m.Task.project_id).where(m.Task.id == job.task_id))
            ).scalar()
            await db.commit()
            if service_changed:
                from ..services import publish_job

                publish_job(job, task_project)
        bus.publish(
            "job.log",
            task_project,
            {"job_id": job_id, "task_id": job.task_id, "seq": seq, "stream": stream, "data": data},
        )

    async def _on_job_finished(self, conn: AgentConnection, msg: dict[str, Any]) -> None:
        job_id = await self._job_id(msg)
        if job_id is None:
            return
        conn.running.discard(job_id)
        conn.services.discard(job_id)
        from ..service_jobs import forget

        forget(job_id)
        async with SessionLocal() as db:
            job = await db.get(m.Job, job_id)
            if job is None or job.agent_id != conn.agent_id:
                return
            redaction = await self._redaction(db, job)
            self._redactions.pop(job_id, None)
            if job.status == m.JobStatus.cancelled:
                job.finished_at = utcnow()
            else:
                exit_code = msg.get("exit_code")
                job.exit_code = int(exit_code) if exit_code is not None else None
                if job.route_info and msg.get("route_names"):
                    from ..routing import stand_ins

                    # Names its route box answered with stand-in addresses: parsing puts them back.
                    job.route_info = {**job.route_info, "names": stand_ins(msg["route_names"])}
                error = msg.get("error")
                job.error = redaction.redact(error) if error else None
                job.finished_at = utcnow()
                job.status = (
                    m.JobStatus.succeeded
                    if job.exit_code == 0 and not job.error
                    else m.JobStatus.failed
                )
                stopping = (job.progress or {}).get("stopping") if job.service else None
                if stopping and not msg.get("start_failed"):
                    # A service someone stopped: done, whatever it exits with on its stop signal.
                    job.status, job.error = m.JobStatus.succeeded, None
                shell = await db.get(m.Task, job.task_id)
                if shell is not None and shell.shell and job.exit_code is not None and not job.error:
                    # A shell someone left: its exit code is only its last command's (kept on the job).
                    job.status = m.JobStatus.succeeded
            if job.waiting_input is not None:
                from ..interactive import finished as interactive_finished

                await interactive_finished(db, job)
            task = await db.get(m.Task, job.task_id)
            agent = await db.get(m.Agent, conn.agent_id) if conn.agent_id else None
            if agent is not None:
                agent.status = m.AgentStatus.busy if conn.running else m.AgentStatus.idle
            if task is not None:
                stopped = bool(job.service and (job.progress or {}).get("stopping"))
                verb = "was stopped" if stopped else {
                    m.JobStatus.succeeded: "finished",
                    m.JobStatus.failed: "failed",
                    m.JobStatus.cancelled: "was cancelled",
                }.get(job.status, "ended")
                await record_activity(
                    db,
                    type=f"job.{job.status.value}",
                    message=f"“{task.title}” {verb} on {conn.name}"
                    + (f" (exit {job.exit_code})" if job.exit_code not in (None, 0) else ""),
                    project_id=task.project_id,
                    task_id=task.id,
                    actor_type="agent" if conn.agent_id else "system",
                    actor_id=str(conn.agent_id) if conn.agent_id else None,
                    actor_name=conn.name,
                    payload={"job_id": str(job.id), "exit_code": job.exit_code},
                )
                await self._maybe_advance(db, task, job)
                await alerts.job_finished(db, task, job, conn.name)
                plugin_events.emit(db, "job.finished", task.project_id, plugin_events.job_data(job, task),
                                    by=await db.get(m.User, job.created_by) if job.created_by else None)
            await db.commit()
            if task is not None and job.batch_id is not None and job.status == m.JobStatus.succeeded:
                await self._advance_batch(job.batch_id, task.id)
                await db.refresh(task)  # it may have moved
            if task is not None:
                publish_job(job, task.project_id)
                await publish_task(db, task)
                if task.parent_id is not None:
                    await self.next_subtasks(task.parent_id)
                if job.service and ((job.progress or {}).get("stopping") or {}).get("restart"):
                    await self._restart_service(task.id, ((job.progress or {}).get("stopping") or {}).get("by"))
            if agent is not None:
                await self._publish_agent(agent)
            if task is not None and not job.output_specs and job.status != m.JobStatus.cancelled:
                from ..assets_text import after_job_output

                after_job_output(job.id)  # no parsers: assets its output mentions, suggested
            if job.output_specs and job.status != m.JobStatus.cancelled:
                from ..outputs import schedule_parse

                schedule_parse(job.id)  # and then its AI summary, if the project wants one
            elif task is not None and task.playbook_run_id is None and job.status != m.JobStatus.cancelled:
                from ..summaries import after_job

                after_job(job.id)
            elif task is not None and task.playbook_run_id is not None:
                from ..orchestrator.dispatcher import dispatcher
                from ..services import advance_playbook_run

                _, enqueued = await advance_playbook_run(db, task.playbook_run_id)
                if enqueued:
                    dispatcher.wake()
        self._notify()

    async def _restart_service(self, task_id: uuid.UUID, by: str | None) -> None:
        """A service stopped to be restarted (POST /api/jobs/{id}/restart): run its card again."""
        from ..services import enqueue_job, publish_task

        async with SessionLocal() as db:
            task = await db.get(m.Task, task_id)
            if task is None or task.archived_at is not None:
                return
            user = await db.get(m.User, uuid.UUID(by)) if by else None
            await enqueue_job(db, task, user, reason="restarted")
            await db.commit()
            await publish_task(db, task)
        from .dispatcher import dispatcher

        dispatcher.wake()

    async def next_subtasks(self, parent_id: uuid.UUID) -> None:
        """A subtask's run ended: its activity's Run all starts the next ones waiting their turn."""
        from ..subtasks import pump

        async with SessionLocal() as db:
            started = await pump(db, parent_id)
            if not started:
                return
            await db.commit()
            for kid in started:
                await publish_task(db, kid)
            parent = await db.get(m.Task, parent_id)
            if parent is not None:
                await publish_task(db, parent)
        from .dispatcher import dispatcher

        dispatcher.wake()

    async def _advance_batch(self, batch_id: uuid.UUID, task_id: uuid.UUID) -> None:
        """A batch on several agents moves its card on once every job of it has succeeded.
        Checked after each job's result is committed, in a session of its own, so the last
        job to finish sees all the others (they finish at the same time on other agents)."""
        async with SessionLocal() as db:
            statuses = (await db.execute(select(m.Job.status).where(m.Job.batch_id == batch_id))).scalars().all()
            if not statuses or any(s != m.JobStatus.succeeded for s in statuses):
                return
            task = await db.get(m.Task, task_id)
            if task is None:
                return
            await self._maybe_advance(db, task, None)
            await db.commit()
            await publish_task(db, task)

    async def _maybe_advance(self, db, task: m.Task, job: m.Job | None) -> None:
        """Auto-move the card to the Review column on success if the project asks for it.
        `job` None: a batch on several agents that all succeeded (_advance_batch)."""
        if job is not None and (job.status != m.JobStatus.succeeded or job.batch_id is not None):
            return
        if task.archived_at is not None:  # off the board (a run tried from the Library): it stays where it is
            return
        project = await db.get(m.Project, task.project_id)
        if project is None or not (project.settings or {}).get("auto_advance", True):
            return
        from ..services import column_with_role, next_position

        review = await column_with_role(db, project.id, m.ColumnRole.review)
        if review is None or task.column_id == review.id:
            return
        task.column_id = review.id
        if task.parent_id is None:  # a subtask keeps its place among its activity's subtasks
            task.position = await next_position(db, review.id)
        await record_activity(
            db,
            type="task.moved",
            message=f"Moved “{task.title}” to {review.name}",
            project_id=task.project_id,
            task_id=task.id,
            payload={"to": str(review.id), "auto": True},
        )
        if task.parent_id is not None:
            from ..subtasks import follow

            await follow(db, task.parent_id, None)  # its activity follows; published with the subtask

    async def _redaction(self, db, job: m.Job) -> variables.Resolved:
        cached = self._redactions.get(job.id)
        if cached is not None:
            return cached
        resolved = variables.Resolved()
        if job.var_bindings:
            task = await db.get(m.Task, job.task_id)
            spec = await load_spec(db, task.command_ref) if task and task.command_ref else None
            if spec is not None:
                try:
                    resolved = await variables.resolve(db, spec.vars, job.var_bindings, task.project_id)
                except variables.VariableError:
                    pass
        if job.route_info:  # its route's passwords too
            from .. import network_routes

            ids = [*(job.route_info.get("via") or []), job.route_info.get("id")]
            rows = [r for r in [await network_routes.get(db, i) for i in ids] if r is not None]
            resolved.secrets = [*resolved.secrets, *network_routes.secrets_of(rows)]
        self._redactions[job.id] = resolved
        return resolved

    # ----- outbound -----

    async def send_job(
        self,
        conn: AgentConnection,
        job: m.Job,
        artifacts: dict[str, Any] | None = None,
        resolved: variables.Resolved | None = None,
        tool: dict[str, Any] | None = None,
        route: dict[str, Any] | None = None,
    ) -> None:
        resolved = resolved or variables.Resolved()
        self._redactions[job.id] = resolved
        conn.running.add(job.id)
        if job.service:
            conn.services.add(job.id)
        # A job without an image runs in the host shell, or (on an agent that doesn't allow
        # host commands) in the agent's default image; the dispatcher only sends it to an
        # agent that can do one of the two (agent_matches).
        in_container = bool(job.image) or "host" not in conn.runtimes
        await conn.send(
            {
                "type": "job.run",
                "job": {
                    "id": job.id,
                    "command": job.command,
                    # Shown as the first line of the output; values filled in, secrets masked.
                    "display_command": resolved.display_command(job.command, in_container),
                    "image": job.image,
                    "container_flags": job.container_flags or [],
                    "runtime": "container" if in_container else "host",
                    # Variable values exist only in this message: never on the job row.
                    # {{ work }} is "${CASTOR_WORK}": /work in a container (agents set it to the
                    # job directory themselves for host jobs).
                    "env": {**(job.env or {}), **resolved.env, **({"CASTOR_WORK": "/work"} if in_container else {})},
                    "files": resolved.files,
                    "timeout_s": job.timeout_s,
                    # An interactive terminal (pty), and its size if someone already set one.
                    "tty": job.tty,
                    "tty_size": job.tty_size,
                    # Runs until stopped: how to stop it gracefully (signal, then a wait, then a kill).
                    "service": {"stop_signal": job.service.get("stop_signal", "TERM"),
                                "stop_timeout": job.service.get("stop_timeout", 10)} if job.service else None,
                    # One-off upload token + files to collect from the job's /work afterwards.
                    "artifacts": artifacts or {},
                    # How to get the tool first: pull an image, build a recipe, or check
                    # (and maybe install) it on the host. None: nothing to prepare.
                    "tool": tool,
                    # Its network route, secrets included (only ever in this message), with the
                    # ways to go through it best first. None: it goes direct.
                    "route": route,
                },
            }
        )

    async def _on_job_prompt(self, conn: AgentConnection, msg: dict[str, Any]) -> None:
        """An interactive job is (or is no longer) waiting at a prompt."""
        from ..interactive import on_prompt

        job_id = await self._job_id(msg)
        if job_id is None:
            return
        async with SessionLocal() as db:
            job = await db.get(m.Job, job_id)
            if job is None or job.agent_id != conn.agent_id or not job.tty:
                return
            await on_prompt(db, job, msg.get("text"))

    async def to_job(self, job: m.Job, message: dict[str, Any]) -> bool:
        """Send a message about a running job to its agent (keystrokes, resize, signals)."""
        if job.agent_id is None or job.status not in (m.JobStatus.dispatched, m.JobStatus.running):
            return False
        conn = self.connections.get(job.agent_id)
        if conn is None:
            return False
        await conn.send({**message, "job_id": job.id})
        return True

    async def http_request(self, conn: AgentConnection, request: dict[str, Any], timeout: float) -> dict[str, Any]:
        """Have an agent make one HTTP request (an HTTP action sent through it). Returns its
        answer: {status, reason, headers, url, body (base64)} or {error, error_kind}. Raises
        AgentGone if the agent disconnects, TimeoutError if it doesn't answer in time."""
        request_id = uuid.uuid4().hex
        wait = _HttpWait(agent_id=conn.agent_id, future=asyncio.get_running_loop().create_future())
        self._http[request_id] = wait
        try:
            await conn.send({"type": "http.request", "request_id": request_id, **request})
            return await asyncio.wait_for(wait.future, timeout)
        finally:
            self._http.pop(request_id, None)

    async def route_test(self, conn: AgentConnection, route: dict[str, Any], target: list[Any] | None,
                         timeout: float) -> dict[str, Any]:
        """Have an agent bring a route up and try it hop by hop (route.test → route.tested)."""
        if not (conn.capabilities.get("routes")):
            return {"error": f"{conn.name} is too old for network routes (update the agent)"}
        request_id = uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        self._route_tests[request_id] = (conn.agent_id, fut)
        try:
            await conn.send({"type": "route.test", "request_id": request_id, "route": route, "target": target})
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._route_tests.pop(request_id, None)

    async def _on_route(self, conn: AgentConnection, kind: str, msg: dict[str, Any]) -> None:
        """What an agent says about the routes it holds: up/down/broken, each hop, host keys seen,
        test results, and how a job went through its route."""
        from .. import network_routes

        if kind == "route.tested":
            entry = self._route_tests.pop(str(msg.get("request_id")), None)
            if entry and entry[0] == conn.agent_id and not entry[1].done():
                entry[1].set_result({k: v for k, v in msg.items() if k in ("hops", "target", "error")})
            return
        route_id = str(msg.get("route_id") or "") or None
        if kind == "route.status":
            state = network_routes.set_state(route_id, conn.agent_id, {
                "state": msg.get("state"), "mode": msg.get("mode"), "jobs": msg.get("jobs"),
                "broken": msg.get("broken"), "error": msg.get("error")})
            if state:
                bus.publish("route.live", None, {"route_id": route_id, **state})
        elif kind == "route.hop":
            hop_id = str(msg.get("hop_id") or "")
            current = network_routes.STATES.get((str(route_id), str(conn.agent_id)), {})
            hops = {**(current.get("hops") or {}), hop_id: {"state": msg.get("state"), "error": msg.get("error"), "ms": msg.get("ms")}}
            state = network_routes.set_state(route_id, conn.agent_id, {"hops": hops})
            if state:
                bus.publish("route.live", None, {"route_id": route_id, **state})
        elif kind == "route.host_key":
            async with SessionLocal() as db:
                await network_routes.learn_host_key(db, route_id, str(msg.get("hop_id") or ""), str(msg.get("key") or ""),
                                                    bool(msg.get("changed")))
        elif kind == "job.route":
            job_id = await self._job_id(msg)
            if job_id is None:
                return
            async with SessionLocal() as db:
                job = await db.get(m.Job, job_id)
                if job is None or job.agent_id != conn.agent_id or not job.route_info:
                    return
                job.route_info = {**job.route_info, "method": str(msg.get("method") or "")[:20]}
                await db.commit()
                task = await db.get(m.Task, job.task_id)
                if task is not None:
                    publish_job(job, task.project_id)

    async def cancel_job(self, job: m.Job) -> bool:
        if job.runner == "server":
            from .server_runner import server_runner

            return server_runner.cancel(job.id)
        if job.agent_id is None:
            return False
        conn = self.connections.get(job.agent_id)
        if conn is None:
            return False
        await conn.send({"type": "job.cancel", "job_id": job.id})
        return True


gateway = AgentGateway()
