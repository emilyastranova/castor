"""The agent: connects to the server, receives jobs, runs them, streams logs."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import websockets
from websockets.asyncio.client import ClientConnection

from . import artifacts, http_proxy
from . import routes as routing
from .config import AgentConfig, host_info
from .mcp_bridge import McpBridge
from .runtime import JobRunner, RunResult, detect_container_runtime
from .tools import ToolPreparer

log = logging.getLogger("castor.agent")


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


class AuthRejected(Exception):
    """The server refused this agent's token or secret; retrying won't help."""


def credentials(config: AgentConfig) -> dict[str, str]:
    """What the hello authenticates with: the token if there is one, else the secret."""
    return {"token": config.token} if config.token else {"secret": config.secret}


class Agent:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.container_runtime = detect_container_runtime(config.runtime)
        self.runners: dict[str, JobRunner] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.ws: ClientConnection | None = None
        self.send_lock = asyncio.Lock()
        self.heartbeat_interval = 15
        self._background: set[asyncio.Task] = set()
        self.tools = ToolPreparer(self.container_runtime, config.allow_host_install, config.workdir)
        base = Path(config.workdir) if config.workdir else Path(tempfile.gettempdir()) / "castor-agent"
        self.mcp = McpBridge(self.send, self.container_runtime, config.allow_host, config.network, self.tools, str(base / "mcp"))
        self.routes = routing.RouteManager(self.send, self.container_runtime, base, self.tools, config.network)

    # ----- capabilities -----

    def capabilities(self) -> dict[str, Any]:
        runtimes = []
        if self.container_runtime:
            runtimes.append(self.container_runtime)
        if self.config.allow_host:
            runtimes.append("host")
        return {
            "runtimes": runtimes,
            "default_image": self.config.default_image,
            "host_install": self.config.allow_host_install and self.config.allow_host,
            "tools": self.tools.cached,
            # Interactive jobs run under a pty (POSIX only); older agents don't say this.
            "tty": sys.platform != "win32",
            # Sends HTTP actions' requests from this network (http.request); older agents don't.
            "http": self.config.http,
            # Runs MCP servers that speak stdio for MCP actions (mcp.start); older agents don't.
            "mcp_stdio": True,
            # Runs services (jobs until stopped) and stops them gracefully (job.stop); how many at once.
            "services": True,
            "max_services": self.config.max_services,
            # Network routes (route.test, job.run's route, http.request's route); older agents don't.
            "routes": self.routes.capabilities(),
        }

    # ----- wire helpers -----

    async def send(self, message: dict[str, Any]) -> None:
        if self.ws is None:
            return
        async with self.send_lock:
            await self.ws.send(json.dumps(message))

    # ----- main loop -----

    async def run_forever(self) -> None:
        backoff = 2
        await self.routes.cleanup()
        while True:
            try:
                await self._session()
                backoff = 2
            except AuthRejected as exc:
                log.error("the server refused this agent: %s", exc)
                if self.config.token:
                    log.error("get a new token from Agents in the Castor UI and restart with --token")
                raise SystemExit(2) from exc
            except (OSError, websockets.exceptions.WebSocketException) as exc:
                log.warning("connection to %s failed: %s", self.config.server_url, exc)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("agent session crashed")
            log.info("reconnecting in %ss", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    async def _session(self) -> None:
        log.info("connecting to %s as %r", self.config.server_url, self.config.name)
        host = urlparse(self.config.server_url).hostname or ""
        if self.config.server_url.startswith("ws://") and host not in ("localhost", "127.0.0.1", "::1", "server"):
            log.warning("connecting without TLS: the token or secret crosses the network unencrypted (use wss://)")
        async with websockets.connect(
            self.config.server_url, ping_interval=20, ping_timeout=20, max_size=8 * 1024 * 1024
        ) as ws:
            self.ws = ws
            await self.tools.refresh_cached()
            info = host_info()
            await self.send(
                {
                    "type": "hello",
                    **credentials(self.config),
                    "name": self.config.name,
                    "capabilities": self.capabilities(),
                    "tags": self.config.tags,
                    "max_concurrency": self.config.max_concurrency,
                    **info,
                }
            )
            reply = json.loads(await ws.recv())
            if reply.get("type") == "hello.error":
                raise AuthRejected(reply.get("reason", "rejected"))
            if reply.get("type") != "hello.ok":
                raise RuntimeError(f"unexpected reply to hello: {reply}")
            self.heartbeat_interval = int(reply.get("heartbeat_interval", 15))
            log.info(
                "connected (agent id %s); runtimes=%s",
                reply.get("agent_id"),
                ",".join(self.capabilities()["runtimes"]) or "none",
            )
            heartbeat = asyncio.create_task(self._heartbeat())
            try:
                async for raw in ws:
                    await self._handle(json.loads(raw))
            finally:
                heartbeat.cancel()
                self.ws = None
                await self.mcp.stop_all()  # the server forgets them when the connection drops
                # Running jobs keep going; their logs are dropped until a future
                # milestone adds buffering. The server marks them lost.

    async def _prepare_tool(self, tool: dict[str, Any]) -> None:
        """Get a tool ready ahead of any job (Tools → Prepare on agents)."""
        name = tool.get("name") or tool.get("id")

        async def sink(_stream: str, data: str) -> None:
            log.info("prepare %s: %s", name, data.rstrip())

        error = await self.tools.prepare(tool, sink, tempfile.gettempdir())
        await self.send({
            "type": "tool.prepared", "tool_id": tool.get("id"), "ref": tool.get("ref"),
            "ok": error is None, "error": error, "tools": await self.tools.refresh_cached(),
        })

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_interval)
            await self.send({"type": "heartbeat", "running": list(self.runners), "tools": self.tools.cached})

    # ----- messages -----

    async def _handle(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        if kind == "job.run":
            job = msg["job"]
            job_id = str(job["id"])
            if job_id in self.runners:
                return
            self.tasks[job_id] = asyncio.create_task(self._run_job(job))
        elif kind == "tool.prepare":
            task = asyncio.create_task(self._prepare_tool(msg.get("tool") or {}))
            self._background.add(task)  # keep a reference until it's done
            task.add_done_callback(self._background.discard)
        elif kind == "http.request":
            if not self.config.http:
                await self.send({"type": "http.response", "request_id": msg.get("request_id"),
                                 "error": "this agent doesn't send HTTP requests (--no-http)", "error_kind": "refused"})
                return
            task = asyncio.create_task(self._http(msg))
            self._background.add(task)
            task.add_done_callback(self._background.discard)
        elif kind == "route.test":
            task = asyncio.create_task(self._route_test(msg))
            self._background.add(task)
            task.add_done_callback(self._background.discard)
        elif kind == "mcp.start":
            task = asyncio.create_task(self.mcp.handle(msg))  # it may pull or build a tool first
            self._background.add(task)
            task.add_done_callback(self._background.discard)
        elif kind in ("mcp.send", "mcp.stop"):
            await self.mcp.handle(msg)
        elif kind == "auth.revoked":
            log.error("the server revoked this agent's credentials: %s", msg.get("reason", ""))
        elif kind == "job.cancel":
            runner = self.runners.get(str(msg.get("job_id")))
            if runner:
                log.info("cancelling job %s", runner.job_id)
                await runner.cancel()
        elif kind == "job.stop":
            # A service: its stop signal, a wait for it to exit (and free its ports), then a kill.
            runner = self.runners.get(str(msg.get("job_id")))
            if runner:
                log.info("stopping service %s with SIG%s", runner.job_id, msg.get("signal") or "TERM")
                asyncio.ensure_future(runner.stop(str(msg.get("signal") or "TERM"), float(msg.get("timeout") or 10)))
        elif kind in ("job.stdin", "job.resize", "job.signal"):
            runner = self.runners.get(str(msg.get("job_id")))
            if runner is None or not runner.tty:
                return
            if kind == "job.stdin":
                await runner.write_input(str(msg.get("data") or ""))
            elif kind == "job.resize":
                try:
                    runner.resize(int(msg["cols"]), int(msg["rows"]))
                except (KeyError, TypeError, ValueError):
                    pass
            else:
                runner.send_signal(str(msg.get("name") or ""))
        else:
            log.debug("ignoring message %r", kind)

    async def _route_test(self, msg: dict[str, Any]) -> None:
        result = await self.routes.test(msg.get("route") or {}, msg.get("target"))
        await self.send({"type": "route.tested", "request_id": msg.get("request_id"), **result})

    async def _http(self, msg: dict[str, Any]) -> None:
        """One request of an HTTP action, made from here; the body goes back in chunks."""
        request_id = msg.get("request_id")
        route = msg.get("route")
        if route:
            # Through a network route: its local proxy, never this machine's own way out.
            try:
                active = await self.routes.acquire(route, f"http-{request_id}")
            except routing.RouteError as exc:
                await self.send({"type": "http.response", "request_id": request_id,
                                 "error": f"route: {exc}", "error_kind": "connect"})
                return
            try:
                head, body = await asyncio.to_thread(http_proxy.fetch, msg, active.host_url("http"))
            finally:
                await self.routes.release(active.key, f"http-{request_id}")
        else:
            head, body = await asyncio.to_thread(http_proxy.fetch, msg)
        for piece in http_proxy.chunks(body):
            await self.send({"type": "http.chunk", "request_id": request_id, "data": piece})
        await self.send({"type": "http.response", "request_id": request_id, **head})

    def _job_dir(self, job_id: str) -> Path:
        base = Path(self.config.workdir) if self.config.workdir else Path(tempfile.gettempdir()) / "castor-agent"
        job_dir = base / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        return job_dir

    async def _route_for(self, job: dict[str, Any], runner: JobRunner, sink) -> tuple[str | None, str | None]:
        """Bring the job's route up and decide how this run goes through it. Returns (error, route
        key): a job whose route isn't up fails with the reason, never runs unrouted."""
        route = job["route"]
        name = route.get("name") or "the route"
        await sink("system", f"[castor] route “{name}”: {route.get('label') or ''}\n")
        try:
            active = await self.routes.acquire(route, str(job["id"]), sink)
        except routing.RouteError as exc:
            return f"route “{name}” isn't up: {exc}", None
        if active.broken and route.get("strict", True):
            hop = active.broken.get("index")
            where = f"hop {hop + 1}" if isinstance(hop, int) else "the route"
            await self.routes.release(active.key, str(job["id"]))
            return f"route “{name}” is broken at {where}: {active.broken.get('error')}", None
        in_container = runner._wants_container()
        try:
            applied = await routing.apply(active, route.get("plan") or [], in_container=in_container,
                                          image=job.get("image") or (self.config.default_image if in_container else None),
                                          tools=self.tools, sink=sink, have_proxychains=self.routes.capabilities()["proxychains"])
        except routing.RouteError as exc:
            await self.routes.release(active.key, str(job["id"]))
            return f"route “{name}”: {exc}", None
        runner.route = applied
        await self.send({"type": "job.route", "job_id": job["id"], "method": applied.method})
        how = {"native": "the tool's own proxy setting", "env": "proxy environment variables",
               "hooked": "proxychains", "transparent": "transparently (every connection in its network goes through it)"}
        dns = "names resolved at the far end" if route.get("dns", "remote") != "local" else "names resolved here"
        rest = "nothing else leaves" if route.get("strict", True) and in_container else "other traffic isn't held back"
        await sink("system", f"[castor] going through it by {how.get(applied.method, applied.method)}; {dns}; {rest}\n")
        return None, active.key

    async def _upload_artifacts(self, job: dict[str, Any], job_dir: Path, sink) -> None:
        spec = job.get("artifacts") or {}
        token = spec.get("token")
        if not token:
            return
        files = artifacts.collect(job_dir, spec.get("collect") or [])
        if not files:
            return
        url = artifacts.upload_url(self.config.server_url, str(job["id"]))
        sent = 0
        for path, relative in files:
            try:
                await asyncio.to_thread(artifacts.upload, url, token, path, relative)
                sent += 1
            except Exception as exc:  # noqa: BLE001 - report each failed file, keep going
                log.warning("artifact %s upload failed: %s", relative, exc)
                await sink("system", f"[castor] could not upload {relative}: {exc}\n")
        await sink("system", f"[castor] uploaded {sent} of {len(files)} file(s) to the card\n")

    async def _run_job(self, job: dict[str, Any]) -> None:
        job_id = str(job["id"])
        job_dir = self._job_dir(job_id)
        runner = JobRunner(
            job,
            container_runtime=self.container_runtime,
            allow_host=self.config.allow_host,
            default_image=self.config.default_image,
            job_dir=str(job_dir),
            network=self.config.network,
            extra_run_args=self.config.extra_run_args,
        )
        self.runners[job_id] = runner
        seq = 0

        async def sink(stream: str, data: str) -> None:
            nonlocal seq
            await self.send(
                {"type": "job.log", "job_id": job_id, "seq": seq, "stream": stream, "data": data}
            )
            seq += 1

        log.info("job %s started", job_id)
        await self.send({"type": "job.started", "job_id": job_id, "started_at": now_iso()})
        route = job.get("route")
        route_key: str | None = None
        try:
            tool = job.get("tool")
            error = await self.tools.prepare(tool, sink, str(job_dir)) if tool else None
            if not error and route:
                error, route_key = await self._route_for(job, runner, sink)
            if error:
                await sink("system", f"[castor] {error}\n")
                result = RunResult(exit_code=None, error=error)
            elif runner.cancelled:
                result = RunResult(exit_code=None, error="cancelled")
            else:
                async def on_prompt(text: str | None) -> None:
                    await self.send({"type": "job.prompt", "job_id": job_id, "text": text})

                result = await runner.run(sink, on_prompt)
        except Exception as exc:
            log.exception("job %s crashed", job_id)
            result_payload = {"exit_code": None, "error": f"agent error: {exc}"}
        else:
            result_payload = {"exit_code": result.exit_code, "error": result.error}
            if route_key and runner.route and runner.route.network:
                names = await routing.box_names(self.routes, route_key)
                if names:
                    result_payload["route_names"] = names
            # Files go up before job.finished so they are on the card when the job completes.
            await self._upload_artifacts(job, job_dir, sink)
        finally:
            self.runners.pop(job_id, None)
            self.tasks.pop(job_id, None)
            if route_key:
                await self.routes.release(route_key, job_id)
        log.info("job %s finished: %s", job_id, result_payload)
        await self.send(
            {"type": "job.finished", "job_id": job_id, "finished_at": now_iso(), **result_payload}
        )
