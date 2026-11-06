"""Job execution backends: container (podman/docker) and host shell."""

from __future__ import annotations

import asyncio
import base64
import codecs
import logging
import os
import re
import shutil
import signal
import struct
import sys
import time
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
PromptSink = Callable[[str | None], Awaitable[None]]  # the prompt it waits at, or None

# Interactive jobs: what a terminal says it is, and its size until someone sets one.
TERM_ENV = {"TERM": "xterm-256color", "COLORTERM": "truecolor"}
DEFAULT_SIZE = {"cols": 120, "rows": 32}
ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])")
# A last line that asks for something: answered quickly. Anything ending like a prompt
# (":", "?", ">", "$", "#") counts too, after a longer quiet spell.
STRONG_PROMPT = re.compile(
    r"(pass(word|phrase|code)|\bpin\b|token|user ?name|login|\[y/n\]|\(y/n\)|\(yes/no[^)]*\)|"
    r"continue\?|proceed\?|are you sure|overwrite\?|press (enter|return|any key))",
    re.IGNORECASE,
)
WEAK_PROMPT = re.compile(r"[:?>$#\]]\s*$")
STRONG_QUIET, WEAK_QUIET = 3.0, 10.0


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
        self.tty = bool(job.get("tty")) and sys.platform != "win32"
        self._master: int | None = None  # the pty's controlling side, while it runs

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
            if self.tty:
                # A terminal inside the container too; the runtime passes keys and window
                # size through from the pty this command runs under.
                argv += ["-t", "-i"]
            for key in TERM_ENV if self.tty else ():
                argv += ["-e", key]
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

    async def run(self, sink: LogSink, on_prompt: PromptSink | None = None) -> RunResult:
        try:
            return await self._run(sink, on_prompt)
        finally:
            self.remove_files()

    async def _run(self, sink: LogSink, on_prompt: PromptSink | None = None) -> RunResult:
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

        if self.tty:
            return await self._run_tty(argv, env, is_container, sink, on_prompt)
        try:
            self.process = await asyncio.create_subprocess_exec(
                *argv,
                # Nothing to read: a tool that reads stdin when it isn't a terminal gets EOF at
                # once instead of waiting forever. Interactive jobs get a terminal instead.
                stdin=asyncio.subprocess.DEVNULL,
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

    # ----- interactive (pty) -----

    async def _run_tty(self, argv: list[str], env: dict[str, str], is_container: bool, sink: LogSink,
                       on_prompt: PromptSink | None) -> RunResult:
        """Run under a pseudo-terminal: one combined output stream (raw terminal output),
        keystrokes in (write_input), window size changes (resize), prompt detection."""
        import fcntl
        import pty
        import termios

        master, slave = pty.openpty()
        self._master = master
        self._set_size(self.job.get("tty_size") or DEFAULT_SIZE)

        def controlling_tty() -> None:  # in the child, after setsid: make the pty its terminal
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

        try:
            self.process = await asyncio.create_subprocess_exec(
                *argv, stdin=slave, stdout=slave, stderr=slave,
                cwd=None if is_container else self.job_dir,
                env={**env, **TERM_ENV}, start_new_session=True, preexec_fn=controlling_tty,
            )
        except (FileNotFoundError, PermissionError) as exc:
            os.close(slave)
            os.close(master)
            self._master = None
            await sink("system", f"failed to start: {exc}\n")
            return RunResult(exit_code=None, error=f"failed to start: {exc}")
        os.close(slave)
        os.set_blocking(master, False)

        loop = asyncio.get_running_loop()
        chunks: asyncio.Queue[bytes | None] = asyncio.Queue()

        def readable() -> None:
            try:
                data = os.read(master, 65536)
            except BlockingIOError:
                return
            except OSError:  # EIO: every process holding the terminal has exited
                data = b""
            if not data:
                loop.remove_reader(master)
                chunks.put_nowait(None)
            else:
                chunks.put_nowait(data)

        loop.add_reader(master, readable)
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        watch = PromptWatch(on_prompt)

        async def pump() -> None:
            while True:
                data = await chunks.get()
                if data is None:
                    break
                # Coalesce what's already arrived (full-screen redraws come in bursts).
                while not chunks.empty():
                    more = chunks.get_nowait()
                    if more is None:
                        chunks.put_nowait(None)
                        break
                    data += more
                text = decoder.decode(data)
                if text:
                    await watch.output(text)
                    await sink("stdout", text)
            tail = decoder.decode(b"", final=True)
            if tail:
                await sink("stdout", tail)

        timeout = self.job.get("timeout_s")
        watcher = asyncio.create_task(watch.run())
        try:
            await asyncio.wait_for(pump(), timeout=timeout if timeout else None)
            exit_code = await self.process.wait()
        except asyncio.TimeoutError:
            await sink("system", f"\r\n[castor] timed out after {timeout}s\r\n")
            await self.cancel()
            return RunResult(exit_code=None, error=f"timed out after {timeout}s")
        finally:
            watcher.cancel()
            loop.remove_reader(master)
            os.close(master)
            self._master = None
            await watch.clear()
        if self._cancelled:
            await sink("system", "\r\n[castor] cancelled\r\n")
            return RunResult(exit_code=exit_code, error="cancelled")
        await sink("system", f"\r\n[castor] exit code {exit_code}\r\n")
        return RunResult(exit_code=exit_code)

    def _set_size(self, size: dict[str, Any]) -> None:
        if self._master is None:
            return
        import fcntl
        import termios

        try:
            cols, rows = int(size["cols"]), int(size["rows"])
        except (KeyError, TypeError, ValueError):
            return
        # The kernel tells the terminal's foreground processes (SIGWINCH); a container
        # runtime's CLI passes the new size on to the container.
        fcntl.ioctl(self._master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    async def write_input(self, data: str) -> None:
        """Keystrokes (or a paste) from the person at the terminal."""
        if self._master is None:
            return
        raw = data.encode()
        while raw:
            try:
                written = os.write(self._master, raw)
                raw = raw[written:]
            except BlockingIOError:
                await asyncio.sleep(0.01)
            except OSError:
                return

    def resize(self, cols: int, rows: int) -> None:
        self._set_size({"cols": cols, "rows": rows})

    def send_signal(self, name: str) -> None:
        if name == "INT" and self._master is not None:
            # Like pressing Ctrl+C: the terminal delivers it to whatever is in front.
            asyncio.ensure_future(self.write_input("\x03"))
            return
        sig = {"TERM": signal.SIGTERM, "QUIT": signal.SIGQUIT, "INT": signal.SIGINT}.get(name)
        if sig and self.process and self.process.returncode is None:
            try:
                os.killpg(os.getpgid(self.process.pid), sig)
            except (ProcessLookupError, PermissionError):
                pass

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


class PromptWatch:
    """Notices an interactive job sitting at a prompt: its output stopped mid-line, and that
    line asks for something (quickly) or merely ends like a prompt (after longer). Reports it
    once, and reports again (None) when output resumes."""

    def __init__(self, on_prompt: PromptSink | None) -> None:
        self.on_prompt = on_prompt
        self.tail = ""
        self.last = time.monotonic()
        self.reported: str | None = None

    async def output(self, text: str) -> None:
        self.tail = (self.tail + text)[-2000:]
        self.last = time.monotonic()
        if self.reported is not None:
            await self.clear()

    def current_line(self) -> str:
        plain = ANSI.sub("", self.tail)
        if plain.endswith(("\n", "\r")):
            return ""  # the cursor is at the start of a line: nothing is being asked
        line = plain.replace("\r\n", "\n").split("\n")[-1]
        return line.rsplit("\r", 1)[-1].strip()

    def waiting(self) -> str | None:
        line = self.current_line()
        if not line:
            return None
        quiet = time.monotonic() - self.last
        if STRONG_PROMPT.search(line) and quiet >= STRONG_QUIET:
            return line
        if WEAK_PROMPT.search(line) and quiet >= WEAK_QUIET:
            return line
        return None

    async def run(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            if self.reported is None and self.on_prompt and (line := self.waiting()):
                self.reported = line
                await self.on_prompt(line)

    async def clear(self) -> None:
        if self.reported is not None and self.on_prompt:
            self.reported = None
            await self.on_prompt(None)
