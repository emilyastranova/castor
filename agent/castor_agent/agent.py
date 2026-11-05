"""The agent: connects to the server, receives jobs, runs them, streams logs."""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import websockets
from websockets.asyncio.client import ClientConnection

from . import artifacts
from .config import AgentConfig, host_info
from .runtime import JobRunner, detect_container_runtime

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

    # ----- capabilities -----

    def capabilities(self) -> dict[str, Any]:
        runtimes = []
        if self.container_runtime:
            runtimes.append(self.container_runtime)
        if self.config.allow_host:
            runtimes.append("host")
        return {"runtimes": runtimes, "default_image": self.config.default_image}

    # ----- wire helpers -----

    async def send(self, message: dict[str, Any]) -> None:
        if self.ws is None:
            return
        async with self.send_lock:
            await self.ws.send(json.dumps(message))

    # ----- main loop -----

    async def run_forever(self) -> None:
        backoff = 2
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
                # Running jobs keep going; their logs are dropped until a future
                # milestone adds buffering. The server marks them lost.

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_interval)
            await self.send({"type": "heartbeat", "running": list(self.runners)})

    # ----- messages -----

    async def _handle(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        if kind == "job.run":
            job = msg["job"]
            job_id = str(job["id"])
            if job_id in self.runners:
                return
            self.tasks[job_id] = asyncio.create_task(self._run_job(job))
        elif kind == "auth.revoked":
            log.error("the server revoked this agent's credentials: %s", msg.get("reason", ""))
        elif kind == "job.cancel":
            runner = self.runners.get(str(msg.get("job_id")))
            if runner:
                log.info("cancelling job %s", runner.job_id)
                await runner.cancel()
        else:
            log.debug("ignoring message %r", kind)

    def _job_dir(self, job_id: str) -> Path:
        base = Path(self.config.workdir) if self.config.workdir else Path(tempfile.gettempdir()) / "castor-agent"
        job_dir = base / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        return job_dir

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
        try:
            result = await runner.run(sink)
        except Exception as exc:
            log.exception("job %s crashed", job_id)
            result_payload = {"exit_code": None, "error": f"agent error: {exc}"}
        else:
            result_payload = {"exit_code": result.exit_code, "error": result.error}
            # Files go up before job.finished so they are on the card when the job completes.
            await self._upload_artifacts(job, job_dir, sink)
        finally:
            self.runners.pop(job_id, None)
            self.tasks.pop(job_id, None)
        log.info("job %s finished: %s", job_id, result_payload)
        await self.send(
            {"type": "job.finished", "job_id": job_id, "finished_at": now_iso(), **result_payload}
        )
