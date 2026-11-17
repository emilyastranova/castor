"""Matches queued jobs to connected agents."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import selectinload

from .. import models as m
from .. import network_routes, routing, variables
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
    """How this agent would run the tool (image, build or host), or None if it can't. A plugin's
    machine gets the whole tool (`kind: plugin`): how it gets there is the plugin's to decide."""
    if conn.capabilities.get("plugin"):
        return {"id": tool.id, "name": tool.name, "kind": "plugin", "image": tool.image,
                "build": tool.build_files() if tool.build else None, "build_ref": tool.build_tag() if tool.build else None,
                "check": tool.host.check if tool.host else "", "install": tool.host.install if tool.host else ""}
    return tool.way_for(conn.runtimes, bool(conn.capabilities.get("host_install")))


def tool_ready(conn: AgentConnection, tool: ToolSpec | None) -> bool:
    """Whether the agent has the tool already (a recipe it has built); used to prefer it."""
    way = tool_way(conn, tool) if tool else None
    return way is None or way["kind"] != "build" or way["ref"] in (conn.capabilities.get("tools") or [])


def agent_can_run(conn: AgentConnection, job: m.Job, tool: ToolSpec | None = None) -> bool:
    """Whether the agent could run the job at all (runtime, tool, terminal), busy or not."""
    if getattr(job, "tty", False) and not conn.capabilities.get("tty"):
        return False  # an agent from before interactive jobs would run it without a terminal
    if getattr(job, "service", None) and not conn.capabilities.get("services"):
        return False  # an agent from before services can't stop one gracefully
    if tool is not None:
        return tool_way(conn, tool) is not None
    if job.image:
        return bool(conn.runtimes & {"podman", "docker"})
    return "host" in conn.runtimes or bool(conn.runtimes & {"podman", "docker"})


def selector_matches(conn: AgentConnection, selector: dict[str, Any]) -> bool:
    """{agent_id}, or {tags}: the agent has every tag (`all` decides how many agents run it,
    not which)."""
    wanted_agent = selector.get("agent_id")
    if wanted_agent and str(conn.agent_id) != str(wanted_agent):
        return False
    wanted_tags = set(selector.get("tags") or [])
    return not wanted_tags or wanted_tags.issubset(set(conn.tags))


def route_capable(conn: AgentConnection, job: m.Job, tool: ToolSpec | None) -> bool:
    """Whether the agent can take this job through a route: it knows routes, and has a route box
    for a job that runs in a container."""
    caps = conn.capabilities.get("routes")
    if not caps:
        return False
    way = tool_way(conn, tool) if tool else None
    in_container = (way["kind"] in ("image", "build")) if way else (bool(job.image) or "host" not in conn.runtimes)
    return bool(caps.get("box")) or not in_container


def project_allowed(conn: AgentConnection, project_id: uuid.UUID | str | None) -> bool:
    """Whether the agent may run this project's jobs (Agent.project_ids; None: any project)."""
    return conn.project_ids is None or (project_id is not None and str(project_id) in conn.project_ids)


def agent_matches(conn: AgentConnection, job: m.Job, selector: dict[str, Any], tool: ToolSpec | None = None) -> bool:
    # A service (runs until stopped) takes one of the agent's service slots, not a job slot, so a
    # listener doesn't hold up the scans.
    if (conn.free_service_slots if getattr(job, "service", None) else conn.free_slots) <= 0:
        return False
    if getattr(job, "target_agent_id", None) is not None:  # one of a batch: that agent only
        return conn.agent_id == job.target_agent_id and agent_can_run(conn, job, tool)
    return agent_can_run(conn, job, tool) and selector_matches(conn, selector)


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
                    .where(or_(
                        m.Job.status == m.JobStatus.queued,
                        and_(m.Job.status == m.JobStatus.waiting, m.Job.resume_at <= utcnow()),
                    ))
                    .order_by(m.Job.queued_at)
                )
            ).scalars().all()
            if not queued:
                return
            tools = await tools_by_id(db) if any(j.tool for j in queued) else {}
            # Agents with a route of their own (everything they run goes that way).
            agent_routes = {a: r for a, r in (await db.execute(
                select(m.Agent.id, m.Agent.route).where(m.Agent.route.is_not(None), m.Agent.route != ""))).all()}
            for job in queued:
                task = job.task
                if job.kind in ("http", "mcp", "plugin"):
                    await self._to_server(db, job, task)
                    continue
                if job.status != m.JobStatus.queued:
                    continue
                selector = task.agent_selector or {}
                tool = tools.get(job.tool) if job.tool else None
                if job.tool and tool is None:
                    await self._fail_unsent(db, job, task, f"the tool {job.tool} isn't in the library")
                    continue
                job_route = await network_routes.chosen_for(db, task)
                candidates = [
                    c for c in gateway.connections.values()
                    if project_allowed(c, task.project_id) and agent_matches(c, job, selector, tool)
                    and (not (job_route or agent_routes.get(c.agent_id)) or route_capable(c, job, tool))
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
                spec = await load_spec(db, task.command_ref) if task.command_ref else None
                route_payload = None
                try:
                    rows = await network_routes.rows_for(db, task, agent_routes.get(conn.agent_id))
                    if rows:
                        route_payload = await self._route_plan(db, job, rows, conn, tool, spec, tools)
                except routing.RouteError as exc:
                    await self._fail_unsent(db, job, task, f"route: {exc}")
                    continue
                job.agent_id = conn.agent_id
                job.status = m.JobStatus.dispatched
                job.dispatched_at = utcnow()
                upload_token, job.artifact_token_hash = new_token()
                artifacts = {"token": upload_token, "collect": collect_patterns(spec)}
                job.output_specs = output_specs_for(spec.outputs) if spec else None
                try:
                    resolved = await variables.resolve(
                        db, spec.vars if spec else [], job.var_bindings or {}, task.project_id
                    )
                except variables.VariableError as exc:
                    await self._fail_unsent(db, job, task, f"variables: {exc}")
                    continue
                if route_payload is not None:
                    resolved.secrets = [*resolved.secrets, *network_routes.secrets_of(rows)]
                    job.route_info = network_routes.info(rows)
                via = f" through route “{job.route_info['name']}”" if route_payload is not None and job.route_info else ""
                await record_activity(
                    db,
                    type="job.dispatched",
                    message=f"Sent “{task.title}” to {conn.name}{via}",
                    project_id=task.project_id,
                    task_id=task.id,
                    payload={"job_id": str(job.id), "agent_id": str(conn.agent_id),
                             **({"route_id": job.route_info["id"]} if route_payload is not None and job.route_info else {})},
                )
                await db.commit()
                try:
                    await gateway.send_job(conn, job, artifacts, resolved, way, route_payload)
                except Exception as exc:  # noqa: BLE001 - any send failure re-queues the job
                    log.warning("failed to send job %s to %s: %s", job.id, conn.name, exc)
                    conn.running.discard(job.id)
                    conn.services.discard(job.id)
                    job.status = m.JobStatus.queued
                    job.agent_id = None
                    job.dispatched_at = None
                    await db.commit()
                    continue
                publish_job(job, task.project_id)
                await publish_task(db, task)

    async def _route_plan(self, db, job: m.Job, rows: list[m.Route], conn: AgentConnection, tool: ToolSpec | None,
                          spec: Any, tools: dict[str, ToolSpec]) -> dict[str, Any]:
        """The route as this agent gets it in job.run: hops (with secrets), how to go through it
        (best way first, for this tool and whether it runs in a container) and the route box."""
        in_container = bool(job.image) or "host" not in conn.runtimes
        caps = routing.merge_caps(tool.route if tool else None, spec.route if spec else None, shell=tool is None and spec is None)
        strict = any(r.strict for r in rows)
        names = " + ".join(r.name for r in rows)
        try:
            ways = routing.plan(caps, job.command, in_container=in_container, strict=strict, agent=conn.capabilities)
        except routing.RouteError as exc:
            raise routing.RouteError(f"“{names}”: {exc}") from exc
        data = network_routes.payload(rows)
        data["plan"] = ways
        box = (tools or await tools_by_id(db)).get("castor-route") or (await tools_by_id(db)).get("castor-route")
        if (conn.capabilities.get("routes") or {}).get("box"):
            if box is None:
                raise routing.RouteError("the castor-route tool (the route box) isn't in the library")
            data["box"] = tool_way(conn, box)
        return data

    async def _to_server(self, db, job: m.Job, task: m.Task) -> None:
        """An HTTP action: the Castor server's runner makes it (docs/ACTIONS.md). The request
        is fixed when it first starts (arguments and target lists as they are then); a job
        that waited carries on with the same one."""
        from ..command_render import ArgumentError, resolve_args
        from ..command_spec import CommandSpec
        from .server_runner import server_runner

        if server_runner.free_slots <= 0:
            return
        first = job.request is None
        if first:
            spec = await load_spec(db, task.command_ref) if task.command_ref else None
            if spec is None or spec.kind != job.kind:
                await self._fail_unsent(db, job, task, f"its {({'http': 'HTTP', 'mcp': 'MCP'}).get(job.kind, 'plugin')} action is no longer in the library")
                return
            try:
                expanded = await variables.expand_list_refs(db, spec, task.command_args or {}, task.project_id)
                args = resolve_args(spec, expanded)
            except (ArgumentError, variables.VariableError) as exc:
                await self._fail_unsent(db, job, task, f"arguments: {exc}")
                return
            job.request = {
                "args": args,
                "spec": spec.model_dump(mode="json", include={"args", "vars", "request", "paginate", "batch", "expect", "retry", "rate_limit", "mcp", "plugin"}),
            }
            job.output_specs = output_specs_for(spec.outputs)
        else:
            spec = CommandSpec.model_validate({"id": "job", "name": task.title, "kind": job.kind, **job.request["spec"]})
        try:
            resolved = await variables.resolve(db, spec.vars, job.var_bindings or {}, task.project_id)
        except variables.VariableError as exc:
            await self._fail_unsent(db, job, task, f"variables: {exc}")
            return
        # Sent through an agent (the card names one, or tags): one that's connected, may work for
        # the project and sends HTTP requests. None now: it waits for one, like a command would.
        # (An MCP action's connection decides where it runs, not the card.)
        selector = {k: v for k, v in (task.agent_selector or {}).items() if k in ("agent_id", "tags") and v} if job.kind == "http" else {}
        # A route: an HTTP action goes through an agent that holds it (routes only run on agents).
        try:
            rows = await network_routes.rows_for(db, task, None) if job.kind == "http" else []
        except routing.RouteError as exc:
            await self._fail_unsent(db, job, task, f"route: {exc}")
            return
        via: AgentConnection | None = None
        if selector or rows:
            able = [c for c in gateway.connections.values()
                    if c.capabilities.get("http") and project_allowed(c, task.project_id) and selector_matches(c, selector)
                    and (not rows or c.capabilities.get("routes"))]
            if not able:
                return
            via = min(able, key=lambda c: len(c.running))
        if rows and via is not None:
            data = network_routes.payload(rows)
            box = (await tools_by_id(db)).get("castor-route")
            if (via.capabilities.get("routes") or {}).get("box") and box is not None:
                data["box"] = tool_way(via, box)
            gateway.route_payloads[job.id] = data
            job.route_info = {**network_routes.info(rows), "method": "proxy"}
        job.runner = "server"
        job.agent_id = None
        job.via_agent_id = via.agent_id if via else None
        if first:
            job.status = m.JobStatus.dispatched
            job.dispatched_at = utcnow()
            await record_activity(
                db,
                type="job.dispatched",
                message=f"Started “{task.title}” on the Castor server" + (f", sending through {via.name}" if via else "")
                + (f" by route “{job.route_info['name']}”" if rows and job.route_info else ""),
                project_id=task.project_id,
                task_id=task.id,
                payload={"job_id": str(job.id), "runner": "server", **({"agent_id": str(via.agent_id)} if via else {})},
            )
        else:
            job.status = m.JobStatus.running
        await db.commit()
        server_runner.start(job.id, resolved)
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
            parked = job.status == m.JobStatus.waiting and not (job.runner == "server" and gateway_running(job.id))
            if job.status == m.JobStatus.queued or parked:
                job.status = m.JobStatus.cancelled
                job.finished_at = utcnow()
                if parked:
                    from .server_runner import records_file

                    records_file(job.id).unlink(missing_ok=True)  # the pages it had kept
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


def gateway_running(job_id: uuid.UUID) -> bool:
    """Whether the server runner is working on this job right now."""
    from .server_runner import server_runner

    return job_id in server_runner.conn.running


dispatcher = Dispatcher()
