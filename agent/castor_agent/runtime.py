"""Job execution backends: container (podman/docker) and host shell."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import shutil
import signal
import sys
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("castor.agent.runtime")

# Variable-set files are written here inside the job directory and removed after the job,
# before artifacts are collected.
VARS_DIR = ".castor"

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
        job_dir: str,
        network: str,
        extra_run_args: list[str],
    ) -> None:
        self.job = job
        self.job_id = str(job["id"])
        self.container_runtime = container_runtime
        self.allow_host = allow_host
        self.default_image = default_image
        self.job_dir = job_dir  # this job's own directory, mounted at /work
        self.network = network
        self.extra_run_args = extra_run_args
        self.process: asyncio.subprocess.Process | None = None
        self.container_name = f"castor-{self.job_id[:8]}-{uuid.uuid4().hex[:6]}"
        self._cancelled = False

    # ----- variable files -----

    def write_files(self, is_container: bool) -> dict[str, str]:
        """Write the job's variable files (owner-only) and return {env name: path}, the
        path as the command sees it (/work/... in a container, absolute on the host)."""
        root = Path(self.job_dir).resolve()
        env: dict[str, str] = {}
        for item in self.job.get("files") or []:
            target = (root / str(item["path"])).resolve()
            if not target.is_relative_to(root / VARS_DIR):
                raise RuntimeError(f"refusing to write variable file outside the job: {item['path']}")
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, int(item.get("mode", 0o600)))
            with os.fdopen(fd, "wb") as handle:
                handle.write(base64.b64decode(item.get("data", "")))
            relative = target.relative_to(root).as_posix()
            env[str(item["env"])] = f"/work/{relative}" if is_container else str(target)
        return env

    def remove_files(self) -> None:
        shutil.rmtree(Path(self.job_dir) / VARS_DIR, ignore_errors=True)

    def job_env(self) -> dict[str, str]:
        return {str(k): str(v) for k, v in (self.job.get("env") or {}).items()}

    # ----- command construction -----

    def _wants_container(self) -> bool:
        return self.job.get("runtime") == "container" or bool(self.job.get("image"))

    def build_argv(self, env_names: list[str] | None = None) -> tuple[list[str], bool]:
        """Return (argv, is_container). Container variables are passed by name only
        (`-e NAME`): the runtime reads the value from its own environment, so values (and
        secrets) never appear in the process list."""
        command = str(self.job.get("command", ""))
        names = env_names if env_names is not None else list(self.job_env())
        if self._wants_container():
            if not self.container_runtime:
                raise RuntimeError("job requires a container runtime but none is available")
            image = self.job.get("image") or self.default_image
            container_flags = [str(f) for f in (self.job.get("container_flags") or [])]
            # If the user or agent configuration specifies a network flag, don't supply default --network
            all_extra = [*self.extra_run_args, *container_flags]
            has_network_flag = any(
                arg == "--network" or arg == "--net" or arg.startswith(("--network=", "--net="))
                for arg in all_extra
            )
            argv = [
                self.container_runtime,
                "run",
                "--rm",
                "--name",
                self.container_name,
            ]
            if not has_network_flag and self.network:
                argv += ["--network", self.network]
            # ":Z" relabels the directory for SELinux hosts (Fedora/RHEL); harmless elsewhere.
            argv += ["-v", f"{os.path.abspath(self.job_dir)}:/work:Z", "-w", "/work"]
            for key in names:
                argv += ["-e", key]
            argv += self.extra_run_args
            argv += container_flags
            # Override the image's entrypoint: tool images often set it to the tool itself
            # (instrumentisto/nmap uses /usr/bin/nmap), which would turn "/bin/sh -c <cmd>"
            # into arguments for the tool. Images must therefore provide /bin/sh.
            argv += ["--entrypoint", "/bin/sh", image, "-c", command]
            return argv, True
        if not self.allow_host:
            raise RuntimeError("host execution is disabled on this agent")
        shell = ["/bin/sh", "-c"] if sys.platform != "win32" else ["cmd", "/c"]
        return [*shell, command], False

    # ----- execution -----

    async def run(self, sink: LogSink) -> RunResult:
        try:
            return await self._run(sink)
        finally:
            self.remove_files()

    async def _run(self, sink: LogSink) -> RunResult:
        is_container = self._wants_container()
        try:
            job_env = {**self.job_env(), **self.write_files(is_container)}
            argv, is_container = self.build_argv(list(job_env))
        except (RuntimeError, OSError, ValueError) as exc:
            await sink("system", f"{exc}\n")
            return RunResult(exit_code=None, error=str(exc))

        env = {**os.environ, **job_env}
        if not is_container:
            # Host jobs have no /work mount; they run in the job directory and can find it here.
            env["CASTOR_WORK"] = os.path.abspath(self.job_dir)
        where = f"{self.container_runtime} ({self.job.get('image') or self.default_image})" if is_container else "host shell"
        shown = self.job.get("display_command") or self.job.get("command", "")
        await sink("system", f"$ {shown}\n[castor] running on {where}\n")

        try:
            self.process = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=None if is_container else self.job_dir,
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

    @property
    def cancelled(self) -> bool:
        return self._cancelled

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
