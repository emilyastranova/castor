"""Job execution backends: container (podman/docker) and host shell."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
import sys
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("castor.agent.runtime")

LogSink = Callable[[str, str], Awaitable[None]]  # (stream, data)


def detect_container_runtime(preferred: str) -> str | None:
    if preferred in ("podman", "docker"):
        return preferred if shutil.which(preferred) else None
    if preferred == "host":
        return None
    for candidate in ("podman", "docker"):
        if shutil.which(candidate):
            return candidate
    return None


@dataclass
class RunResult:
    exit_code: int | None
    error: str | None = None


class JobRunner:
    """Runs a single job and streams its output. Cancellable."""

    def __init__(
        self,
        job: dict[str, Any],
        *,
        container_runtime: str | None,
        allow_host: bool,
        default_image: str,
        workdir: str | None,
        network: str,
        extra_run_args: list[str],
    ) -> None:
        self.job = job
        self.job_id = str(job["id"])
        self.container_runtime = container_runtime
        self.allow_host = allow_host
        self.default_image = default_image
        self.workdir = workdir
        self.network = network
        self.extra_run_args = extra_run_args
        self.process: asyncio.subprocess.Process | None = None
        self.container_name = f"castor-{self.job_id[:8]}-{uuid.uuid4().hex[:6]}"
        self._cancelled = False

    # ----- command construction -----

    def _wants_container(self) -> bool:
        return self.job.get("runtime") == "container" or bool(self.job.get("image"))

    def build_argv(self) -> tuple[list[str], bool]:
        """Return (argv, is_container)."""
        command = str(self.job.get("command", ""))
        env = self.job.get("env") or {}
        if self._wants_container():
            if not self.container_runtime:
                raise RuntimeError("job requires a container runtime but none is available")
            image = self.job.get("image") or self.default_image
            argv = [
                self.container_runtime,
                "run",
                "--rm",
                "--name",
                self.container_name,
                "--network",
                self.network,
            ]
            if self.workdir:
                # ":Z" relabels the directory for SELinux hosts (Fedora/RHEL); harmless elsewhere.
                argv += ["-v", f"{os.path.abspath(self.workdir)}:/work:Z", "-w", "/work"]
            for key, value in env.items():
                argv += ["-e", f"{key}={value}"]
            argv += self.extra_run_args
            argv += [image, "/bin/sh", "-c", command]
            return argv, True
        if not self.allow_host:
            raise RuntimeError("host execution is disabled on this agent")
        shell = ["/bin/sh", "-c"] if sys.platform != "win32" else ["cmd", "/c"]
        return [*shell, command], False

    # ----- execution -----

    async def run(self, sink: LogSink) -> RunResult:
        try:
            argv, is_container = self.build_argv()
        except RuntimeError as exc:
            await sink("system", f"{exc}\n")
            return RunResult(exit_code=None, error=str(exc))

        env = dict(os.environ)
        if not is_container:
            env.update({k: str(v) for k, v in (self.job.get("env") or {}).items()})
        where = f"{self.container_runtime} ({self.job.get('image') or self.default_image})" if is_container else "host shell"
        await sink("system", f"$ {self.job.get('command', '')}\n[castor] running on {where}\n")

        try:
            self.process = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.workdir if (self.workdir and not is_container) else None,
                env=env,
                start_new_session=True,
            )
        except (FileNotFoundError, PermissionError) as exc:
            await sink("system", f"failed to start: {exc}\n")
            return RunResult(exit_code=None, error=f"failed to start: {exc}")

        async def pipe(stream: asyncio.StreamReader | None, name: str) -> None:
            if stream is None:
                return
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    break
                await sink(name, chunk.decode("utf-8", errors="replace"))

        timeout = self.job.get("timeout_s")
        pump = asyncio.gather(pipe(self.process.stdout, "stdout"), pipe(self.process.stderr, "stderr"))
        try:
            await asyncio.wait_for(pump, timeout=timeout if timeout else None)
            exit_code = await self.process.wait()
        except asyncio.TimeoutError:
            await sink("system", f"[castor] timed out after {timeout}s\n")
            await self.cancel()
            await asyncio.gather(pump, return_exceptions=True)
            return RunResult(exit_code=None, error=f"timed out after {timeout}s")

        if self._cancelled:
            await sink("system", "[castor] cancelled\n")
            return RunResult(exit_code=exit_code, error="cancelled")
        await sink("system", f"[castor] exit code {exit_code}\n")
        return RunResult(exit_code=exit_code)

    async def cancel(self) -> None:
        self._cancelled = True
        if self.container_runtime and self._wants_container():
            # Kill the container; `run --rm` cleans it up.
            try:
                proc = await asyncio.create_subprocess_exec(
                    self.container_runtime, "kill", self.container_name,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()
            except FileNotFoundError:
                pass
        if self.process and self.process.returncode is None:
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, AttributeError):
                try:
                    self.process.terminate()
                except ProcessLookupError:
                    pass
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                try:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
                except Exception:  # noqa: BLE001 - best effort, then hard kill
                    self.process.kill()
