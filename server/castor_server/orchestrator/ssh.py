"""SSH hosts: the server connects to a host over SSH and runs jobs there, as an agent.

A connected agent dials in over a WebSocket; an SSH host is the other way around. Each host
has a HostLink that keeps one SSH connection open (asyncssh), registers it with the gateway
as an ordinary AgentConnection and speaks the agent protocol to it in-process: the gateway
"sends" job.run / job.cancel / tool.prepare / job.stdin… to an SshSocket, and the link
answers with job.started / job.log / job.finished as if they came over the wire. Dispatch,
log masking, alerts and lost-job handling are therefore the gateway's, unchanged.

On the host, each job gets <workdir>/<job id>/ (like an agent's job directory):
- variable files go to .castor/ (owner-only) over SFTP, with the env vars in .castor/env,
  which the job's script sources and deletes before it runs the command (values never
  appear in a command line, so not in `ps` either);
- .castor/run.sh cd's there, records its pid (sshd makes each session its own process
  group, so cancel is `kill -- -<pid>`) and execs the command in /bin/sh, or in
  `podman|docker run` with the job dir at /work when the host has a container runtime;
- afterwards .castor/ is removed and declared outputs and artifacts/** are fetched over
  SFTP into the card's attachments (artifacts.store), skipping symlinks and anything that
  resolves outside the job dir.

Every command sent is `/bin/sh -c '<quoted>'`, so the login shell (bash, zsh, fish…) only
has to run one quoted word. Host keys are trusted on first use: the key seen first is kept,
and a different one later stops the link (host_key_pending) until an admin accepts it."""

from __future__ import annotations

import asyncio
import base64
import codecs
import glob as globlib
import json
import logging
import posixpath
import re
import shlex
import stat
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import asyncssh

from .. import artifacts, ssh_config, vault
from .. import models as m
from ..config import settings
from ..db import SessionLocal
from ..events import bus
from .gateway import AgentConnection, RegisterError, gateway

log = logging.getLogger(__name__)

Sink = Callable[[str, str], Awaitable[None]]
DEFAULT_IMAGE = "docker.io/library/alpine:latest"
TERM_ENV = {"TERM": "xterm-256color", "COLORTERM": "truecolor"}
DEFAULT_SIZE = {"cols": 120, "rows": 32}
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
CONNECT_TIMEOUT = 15


class HostKeyChanged(Exception):
    """The host (or a jump host) presented a different key than the one Castor trusted."""


class JumpFailed(Exception):
    """Couldn't get through a jump host."""


# ---------- keys ----------


def generate_key(comment: str) -> tuple[str, str]:
    """A new ed25519 key pair: (OpenSSH private key, public key line)."""
    key = asyncssh.generate_private_key("ssh-ed25519", comment=comment)
    return key.export_private_key().decode(), key.export_public_key().decode().strip()


def import_key(text: str, passphrase: str | None = None) -> tuple[str, str]:
    """A pasted private key, decrypted if it has a passphrase (Castor keeps it in the vault
    instead): (OpenSSH private key, public key line). Raises ValueError if unreadable."""
    try:
        key = asyncssh.import_private_key(text.strip() + "\n", passphrase or None)
    except (asyncssh.KeyImportError, asyncssh.KeyEncryptionError) as exc:
        raise ValueError(f"can't read that private key: {exc}") from exc
    return key.export_private_key().decode(), key.export_public_key().decode().strip()


def fingerprint(public_key: str | None) -> str | None:
    if not public_key:
        return None
    try:
        return asyncssh.import_public_key(public_key).get_fingerprint()
    except (asyncssh.KeyImportError, ValueError):
        return None


def _sh(script: str) -> str:
    """One command for whatever login shell the account has."""
    return "/bin/sh -c " + shlex.quote(script)


def _q(path: str) -> str:
    return shlex.quote(path)


# ---------- interactive prompts (as the agent's runtime.PromptWatch) ----------

ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])")
STRONG_PROMPT = re.compile(
    r"(pass(word|phrase|code)|\bpin\b|token|user ?name|login|\[y/n\]|\(y/n\)|\(yes/no[^)]*\)|"
    r"continue\?|proceed\?|are you sure|overwrite\?|press (enter|return|any key))",
    re.IGNORECASE,
)
WEAK_PROMPT = re.compile(r"[:?>$#\]]\s*$")
STRONG_QUIET, WEAK_QUIET = 3.0, 10.0


class PromptWatch:
    """An interactive job sitting at a prompt: reported once, and cleared when output resumes.
    Keep in step with castor_agent.runtime.PromptWatch."""

    def __init__(self, on_prompt: Callable[[str | None], Awaitable[None]]) -> None:
        self.on_prompt = on_prompt
        self.tail = ""
        self.last = time.monotonic()
        self.reported: str | None = None

    async def output(self, text: str) -> None:
        self.tail = (self.tail + text)[-2000:]
        self.last = time.monotonic()
        if self.reported is not None:
            await self.clear()

    def waiting(self) -> str | None:
        plain = ANSI.sub("", self.tail)
        line = plain.replace("\r\n", "\n").split("\n")[-1].rsplit("\r", 1)[-1].strip()
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
            if self.reported is None and (line := self.waiting()):
                self.reported = line
                await self.on_prompt(line)

    async def clear(self) -> None:
        if self.reported is not None:
            self.reported = None
            await self.on_prompt(None)


# ---------- the gateway's side ----------


class SshSocket:
    """What the gateway holds as this agent's WebSocket."""

    def __init__(self, link: HostLink) -> None:
        self.link = link

    async def send_text(self, text: str) -> None:
        await self.link.from_server(json.loads(text))

    async def close(self, code: int = 1000) -> None:
        self.link.drop(f"the server closed the connection ({code})")


@dataclass
class Probe:
    workdir: str  # absolute
    hostname: str
    os: str
    username: str
    runtime: str | None


class JobRun:
    """One job on the host."""

    def __init__(self, link: HostLink, job: dict[str, Any]) -> None:
        self.link = link
        self.job = job
        self.id = str(job["id"])
        self.dir = posixpath.join(link.probe.workdir, self.id)
        self.container_name = f"castor-{self.id[:8]}-{uuid.uuid4().hex[:6]}"
        self.tty = bool(job.get("tty"))
        self.process: asyncssh.SSHClientProcess | None = None
        self.cancelled = False
        self.seq = 0
        self._log_lock = asyncio.Lock()

    @property
    def in_container(self) -> bool:
        return self.job.get("runtime") == "container" or bool(self.job.get("image"))

    async def send(self, message: dict[str, Any]) -> None:
        await self.link.to_gateway({**message, "job_id": self.id})

    async def sink(self, stream: str, data: str) -> None:
        async with self._log_lock:  # stdout and stderr arrive at once; keep them in order
            seq, self.seq = self.seq, self.seq + 1
            await self.send({"type": "job.log", "seq": seq, "stream": stream, "data": data})

    async def run(self) -> None:
        await self.send({"type": "job.started", "started_at": _now()})
        payload: dict[str, Any]
        try:
            tool = self.job.get("tool")
            error = await self.link.prepare(tool, self.sink) if tool else None
            if error:
                await self.sink("system", f"[castor] {error}\n")
                payload = {"exit_code": None, "error": error}
            elif self.cancelled:
                payload = {"exit_code": None, "error": "cancelled"}
            else:
                payload = await self.execute()
                await self.collect()
        except asyncio.CancelledError:
            raise  # the connection dropped: the gateway marks the job lost
        except Exception as exc:
            log.exception("ssh job %s on %s failed", self.id, self.link.name)
            payload = {"exit_code": None, "error": f"ssh: {exc}"}
        finally:
            try:
                await self.link.exec(f"rm -rf -- {_q(self.dir + '/.castor')}")
            except Exception:  # the connection may be gone
                log.debug("cleaning up job %s on %s", self.id, self.link.name, exc_info=True)
        await self.send({"type": "job.finished", "finished_at": _now(), **payload})

    # ----- setting up -----

    async def _write_files(self, sftp: asyncssh.SFTPClient) -> dict[str, str]:
        env: dict[str, str] = {}
        for item in self.job.get("files") or []:
            relative = posixpath.normpath(str(item["path"]))
            if not relative.startswith(".castor/") or ".." in relative.split("/"):
                raise RuntimeError(f"refusing to write variable file outside the job: {item['path']}")
            target = posixpath.join(self.dir, relative)
            await sftp.makedirs(posixpath.dirname(target), exist_ok=True)
            mode = int(item.get("mode", 0o600))
            async with sftp.open(target, "wb", asyncssh.SFTPAttrs(permissions=mode)) as handle:
                await handle.write(base64.b64decode(item.get("data", "")))
            await sftp.chmod(target, mode)
            env[str(item["env"])] = f"/work/{relative}" if self.in_container else target
        return env

    def _command(self, names: list[str]) -> str:
        command = str(self.job.get("command", ""))
        if not self.in_container:
            return f"exec /bin/sh -c {_q(command)}"
        link = self.link
        image = self.job.get("image") or DEFAULT_IMAGE
        flags = [str(f) for f in (self.job.get("container_flags") or [])]
        argv = [link.probe.runtime or "podman", "run", "--rm", "--name", self.container_name]
        if self.tty:
            argv += ["-t", "-i", "-e", "TERM", "-e", "COLORTERM"]
        if not any(a in ("--network", "--net") or a.startswith(("--network=", "--net=")) for a in flags) and link.network:
            argv += ["--network", link.network]
        argv += ["-v", f"{self.dir}:/work:Z", "-w", "/work"]
        for name in names:
            argv += ["-e", name]
        argv += [*flags, "--entrypoint", "/bin/sh", image, "-c", command]
        return "exec " + " ".join(_q(a) for a in argv)

    async def execute(self) -> dict[str, Any]:
        link = self.link
        if self.in_container and not link.probe.runtime:
            raise RuntimeError("job requires a container runtime but this host has none")
        if not self.in_container and not link.allow_host:
            raise RuntimeError("host execution is disabled on this SSH host")
        await link.exec(f"mkdir -p -- {_q(self.dir)} && mkdir -p -m 700 -- {_q(self.dir + '/.castor')}", check=True)
        env = {str(k): str(v) for k, v in (self.job.get("env") or {}).items()}
        async with link.conn.start_sftp_client() as sftp:
            env.update(await self._write_files(sftp))
            if not self.in_container:
                env["CASTOR_WORK"] = self.dir
            if self.tty:
                env.update(TERM_ENV)
            bad = [n for n in env if not ENV_NAME.fullmatch(n)]
            if bad:
                raise RuntimeError(f"invalid variable name: {bad[0]}")
            lines = "".join(f"export {n}={_q(v)}\n" for n, v in env.items())
            async with sftp.open(f"{self.dir}/.castor/env", "w", asyncssh.SFTPAttrs(permissions=0o600)) as handle:
                await handle.write(lines)
            script = (
                f"cd -- {_q(self.dir)} || exit 127\n"
                "echo $$ > .castor/pid\n"
                ". ./.castor/env\nrm -f .castor/env\n"
                + self._command([n for n in env if n != "CASTOR_WORK"])
                + ("" if self.tty else " </dev/null")
                + "\n"
            )
            async with sftp.open(f"{self.dir}/.castor/run.sh", "w", asyncssh.SFTPAttrs(permissions=0o700)) as handle:
                await handle.write(script)

        where = f"{link.probe.runtime} ({self.job.get('image') or DEFAULT_IMAGE}) on {link.name}" if self.in_container else f"{link.name} over SSH"
        shown = self.job.get("display_command") or self.job.get("command", "")
        nl = "\r\n" if self.tty else "\n"
        await self.sink("system", f"$ {shown}{nl}[castor] running on {where}{nl}")

        size = self.job.get("tty_size") or DEFAULT_SIZE
        self.process = await link.conn.create_process(
            f"/bin/sh {_q(self.dir + '/.castor/run.sh')}",
            encoding=None,
            term_type=TERM_ENV["TERM"] if self.tty else None,
            term_size=(int(size.get("cols", 120)), int(size.get("rows", 32))) if self.tty else None,
            stderr=asyncssh.STDOUT if self.tty else asyncssh.PIPE,
        )
        if self.cancelled:
            await self.cancel()
        watch = PromptWatch(lambda text: self.send({"type": "job.prompt", "text": text})) if self.tty else None
        watcher = asyncio.create_task(watch.run()) if watch else None

        async def pipe(reader: asyncssh.SSHReader, name: str) -> None:
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            while chunk := await reader.read(65536):
                text = decoder.decode(chunk)
                if text:
                    if watch:
                        await watch.output(text)
                    await self.sink(name, text)
            if tail := decoder.decode(b"", final=True):
                await self.sink(name, tail)

        streams = [pipe(self.process.stdout, "stdout")]
        if not self.tty:
            streams.append(pipe(self.process.stderr, "stderr"))
        timeout = self.job.get("timeout_s")
        try:
            await asyncio.wait_for(asyncio.gather(*streams), timeout=timeout or None)
            await self.process.wait()
        except TimeoutError:
            await self.sink("system", f"{nl}[castor] timed out after {timeout}s{nl}")
            await self.cancel()
            return {"exit_code": None, "error": f"timed out after {timeout}s"}
        finally:
            if watcher:
                watcher.cancel()
            if watch:
                await watch.clear()
        code = self.process.returncode
        if self.cancelled:
            await self.sink("system", f"{nl}[castor] cancelled{nl}")
            return {"exit_code": code, "error": "cancelled"}
        await self.sink("system", f"{nl}[castor] exit code {code}{nl}")
        return {"exit_code": code}

    # ----- while it runs -----

    async def kill(self, sig: str) -> None:
        pidfile = _q(self.dir + "/.castor/pid")
        await self.link.exec(f'p=$(cat {pidfile} 2>/dev/null) && kill -{sig} -- "-$p" 2>/dev/null; true')

    async def cancel(self) -> None:
        self.cancelled = True
        if self.process is None:
            return  # it's cancelled as soon as it starts
        if self.in_container and self.link.probe.runtime:
            await self.link.exec(f"{_q(self.link.probe.runtime)} kill {_q(self.container_name)} >/dev/null 2>&1; true")
        await self.kill("TERM")
        try:
            await asyncio.wait_for(self.process.wait(), timeout=5)
        except TimeoutError:
            await self.kill("KILL")

    async def stdin(self, data: str) -> None:
        if self.process is not None and self.tty:
            self.process.stdin.write(data.encode())

    def resize(self, cols: int, rows: int) -> None:
        if self.process is not None and self.tty:
            self.process.change_terminal_size(cols, rows)

    async def signal(self, name: str) -> None:
        if name == "INT" and self.tty:
            await self.stdin("\x03")
        elif name in ("INT", "TERM", "QUIT"):
            await self.kill(name)

    # ----- afterwards -----

    async def collect(self) -> None:
        spec = self.job.get("artifacts") or {}
        patterns = spec.get("collect") or []
        if not spec.get("token") or not patterns:
            return
        async with self.link.conn.start_sftp_client() as sftp:
            root = await sftp.realpath(self.dir)
            files = await _collect(sftp, root, patterns)
            if not files:
                return
            sent = 0
            for path, relative, size in files:
                if size > settings.artifact_max_bytes:
                    await self.sink("system", f"[castor] skipped {relative}: larger than the artifact size limit\n")
                    continue
                try:
                    async with sftp.open(path, "rb") as handle:
                        async def chunks(h=handle):
                            while chunk := await h.read(1024 * 1024):
                                yield chunk

                        async with SessionLocal() as db:
                            job = await db.get(m.Job, uuid.UUID(self.id))
                            if job is None:
                                return
                            await artifacts.store(db, job, relative, None, chunks())
                    sent += 1
                except (artifacts.ArtifactRefused, asyncssh.SFTPError, OSError) as exc:
                    await self.sink("system", f"[castor] could not keep {relative}: {exc}\n")
            await self.sink("system", f"[castor] kept {sent} of {len(files)} file(s) on the card\n")


async def _collect(sftp: asyncssh.SFTPClient, root: str, patterns: list[str]) -> list[tuple[str, str, int]]:
    """(path, path relative to the job dir, size) of regular files matching the patterns
    (/work/... paths or globs, dir/** for everything under dir), as the agent's collect()."""
    found: dict[str, tuple[str, int]] = {}

    async def walk(base: str) -> list[str]:
        out: list[str] = []
        try:
            entries = await sftp.readdir(base)
        except asyncssh.SFTPError:
            return out
        for entry in entries:
            name = entry.filename if isinstance(entry.filename, str) else entry.filename.decode()
            if name in (".", ".."):
                continue
            path = posixpath.join(base, name)
            mode = entry.attrs.permissions or 0
            if stat.S_ISDIR(mode):
                out += await walk(path)
            elif stat.S_ISREG(mode):
                out.append(path)
        return out

    for pattern in patterns:
        relative = pattern.removeprefix("/work").lstrip("/")
        if not relative:
            continue
        if relative.endswith("/**"):
            candidates = await walk(posixpath.join(root, relative[:-3]))
        else:
            try:
                candidates = await sftp.glob(posixpath.join(globlib.escape(root), relative))
            except asyncssh.SFTPError:
                candidates = []
        for path in candidates:
            path = path if isinstance(path, str) else path.decode()
            try:
                attrs = await sftp.lstat(path)
                if not stat.S_ISREG(attrs.permissions or 0):
                    continue  # symlinks and directories
                real = await sftp.realpath(path)
            except asyncssh.SFTPError:
                continue
            if not real.startswith(root + "/"):
                continue
            rel = real[len(root) + 1:]
            if rel.split("/", 1)[0] == ".castor":
                continue  # variable files (credentials) never leave the host
            found.setdefault(rel, (real, attrs.size or 0))
    return [(path, rel, size) for rel, (path, size) in sorted(found.items())]


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


# ---------- one host ----------


class HostLink:
    """Keeps one SSH host connected and registered as an agent."""

    def __init__(self, host_id: uuid.UUID, agent_id: uuid.UUID) -> None:
        self.host_id = host_id
        self.agent_id = agent_id
        self.name = ""
        self.state = "connecting"  # connecting | connected | error | host_key_changed | disabled
        self.error: str | None = None
        self.conn: asyncssh.SSHClientConnection = None  # type: ignore[assignment]
        self.connected: Connected | None = None
        self.agent: AgentConnection | None = None
        self.probe: Probe = None  # type: ignore[assignment]
        self.network = "host"
        self.allow_host = True
        self.allow_host_install = False
        self.jobs: dict[str, JobRun] = {}
        self.tasks: set[asyncio.Task] = set()
        self.tools: list[str] = []
        self._locks: dict[str, asyncio.Lock] = {}
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._writes: set[asyncio.Future] = set()

    # ----- lifecycle -----

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                log.debug("stopping ssh host %s", self.name, exc_info=True)
        # Writes that outlive the cancel (marking the agent offline, the last state) finish
        # before the caller touches the same rows (SQLite has one writer at a time).
        while self._writes:
            await asyncio.gather(*list(self._writes), return_exceptions=True)

    def drop(self, reason: str) -> None:
        log.info("ssh host %s: dropping the connection: %s", self.name, reason)
        if self.connected is not None:
            self.connected.close()

    def retry_now(self) -> None:
        self._wake.set()

    async def _write(self, coro: Awaitable[Any]) -> Any:
        """A DB write that a cancel doesn't cut off halfway (stop() waits for it)."""
        task = asyncio.ensure_future(coro)
        self._writes.add(task)
        task.add_done_callback(self._writes.discard)
        return await asyncio.shield(task)

    async def _set(self, state: str, error: str | None = None) -> None:
        self.state, self.error = state, error

        async def store() -> None:
            async with SessionLocal() as db:
                row = await db.get(m.SshHost, self.host_id)
                if row is not None and row.last_error != error:
                    row.last_error = error
                    await db.commit()

        await self._write(store())
        bus.publish("ssh_hosts.changed", None, {"id": self.host_id})

    async def _loop(self) -> None:
        backoff = 2
        while True:
            try:
                await self._session()
                backoff = 2
            except asyncio.CancelledError:
                raise
            except HostKeyChanged as exc:
                await self._set("host_key_changed", str(exc))
                backoff = 300  # nothing changes until an admin accepts the key (retry_now)
            except (RegisterError, JumpFailed, ssh_config.ConfigError) as exc:
                await self._set("error", str(exc))
                backoff = 60 if not isinstance(exc, JumpFailed) else min(backoff * 2, 60)
            except (OSError, asyncssh.Error, TimeoutError, vault.VaultError) as exc:
                await self._set("error", _describe(exc))
                backoff = min(backoff * 2, 60)
            except Exception as exc:
                log.exception("ssh host %s", self.name)
                await self._set("error", f"{type(exc).__name__}: {exc}")
                backoff = 60
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=backoff)
            except TimeoutError:
                pass

    async def _session(self) -> None:
        async with SessionLocal() as db:
            row = await db.get(m.SshHost, self.host_id)
            agent = await db.get(m.Agent, self.agent_id)
            if row is None or agent is None:
                raise asyncio.CancelledError
            db.expunge(row)
            self.name = agent.name
            config_text = await load_config(db)
        if not row.enabled:
            await self._set("disabled")
            await asyncio.Event().wait()  # until reload() replaces this link
        self.network, self.allow_host, self.allow_host_install = row.network, row.allow_host, row.allow_host_install
        await self._set("connecting", None)
        connected = await connect(row, config_text)
        self.connected, self.conn = connected, connected.conn
        new_jumps = {k: v for k, v in connected.jump_keys.items() if k not in (row.jump_host_keys or {})}
        if row.host_key is None or new_jumps:
            async with SessionLocal() as db:
                current = await db.get(m.SshHost, self.host_id)
                if current is not None:
                    if current.host_key is None:
                        current.host_key, current.host_key_pending = connected.host_key, None
                    if new_jumps:
                        current.jump_host_keys = {**(current.jump_host_keys or {}), **new_jumps}
                        current.jump_host_keys_pending = {k: v for k, v in (current.jump_host_keys_pending or {}).items()
                                                          if k not in new_jumps}
                    await db.commit()
            for label, key in ([("", connected.host_key)] if row.host_key is None else []) + list(new_jumps.items()):
                log.info("ssh host %s: trusting %s key %s on first use", self.name, label or "its", fingerprint(key))
        workdir = row.workdir
        try:
            self.probe = await probe(self.conn, workdir)
            self.tools = await self._cached_tools()
            hello = {
                "name": self.name, "hostname": self.probe.hostname, "os": self.probe.os,
                "username": self.probe.username, "version": "ssh", "capabilities": self.capabilities(),
            }
            self.agent = await gateway._register(SshSocket(self), hello, ssh_agent_id=self.agent_id)
            await self._set("connected", None)
            gateway._notify()
            closed = asyncio.create_task(self.conn.wait_closed())
            try:
                while not closed.done():
                    await self.to_gateway({"type": "heartbeat", "tools": self.tools})
                    await asyncio.wait({closed}, timeout=settings.agent_heartbeat_interval)
            finally:
                closed.cancel()
        finally:
            for task in list(self.tasks):
                task.cancel()
            connected.close()
            if self.agent is not None:
                await self._write(gateway._unregister(self.agent))
                self.agent = None
            self.jobs.clear()
        raise ConnectionError("the SSH connection closed")

    def capabilities(self) -> dict[str, Any]:
        runtimes = [self.probe.runtime] if self.probe.runtime else []
        if self.allow_host:
            runtimes.append("host")
        return {"runtimes": runtimes, "default_image": DEFAULT_IMAGE, "tools": self.tools, "tty": True, "ssh": True,
                "host_install": self.allow_host_install and self.allow_host}

    # ----- the protocol -----

    async def to_gateway(self, message: dict[str, Any]) -> None:
        if self.agent is not None:
            await gateway._on_message(self.agent, message)

    def _spawn(self, coro: Awaitable[Any]) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def from_server(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        if kind == "job.run":
            job = msg["job"]
            run = JobRun(self, job)
            if run.id in self.jobs:
                return
            self.jobs[run.id] = run

            async def go() -> None:
                try:
                    await run.run()
                finally:
                    self.jobs.pop(run.id, None)

            self._spawn(go())
        elif kind == "job.cancel":
            if run := self.jobs.get(str(msg.get("job_id"))):
                self._spawn(run.cancel())
        elif kind in ("job.stdin", "job.resize", "job.signal"):
            run = self.jobs.get(str(msg.get("job_id")))
            if run is None:
                return
            if kind == "job.stdin":
                await run.stdin(str(msg.get("data") or ""))
            elif kind == "job.resize":
                try:
                    run.resize(int(msg["cols"]), int(msg["rows"]))
                except (KeyError, TypeError, ValueError):
                    pass
            else:
                self._spawn(run.signal(str(msg.get("name") or "")))
        elif kind == "tool.prepare":
            self._spawn(self._prepare_ahead(msg.get("tool") or {}))
        # hello.ok, auth.revoked: nothing to do

    # ----- remote commands -----

    async def exec(self, script: str, check: bool = False) -> asyncssh.SSHCompletedProcess:
        result = await self.conn.run(_sh(script), check=False, stdin=asyncssh.DEVNULL)
        if check and result.exit_status != 0:
            raise RuntimeError(f"{script.split()[0]} failed on the host: {str(result.stderr or '').strip()[:300]}")
        return result

    async def stream(self, script: str, sink: Sink) -> int:
        proc = await self.conn.create_process(_sh(script), stderr=asyncssh.STDOUT, stdin=asyncssh.DEVNULL)
        async for line in proc.stdout:
            await sink("system", line)
        await proc.wait()
        return proc.returncode if proc.returncode is not None else 1

    # ----- tools (as the agent's tools.ToolPreparer) -----

    async def _cached_tools(self) -> list[str]:
        if not self.probe.runtime:
            return []
        out = await self.exec(f"{_q(self.probe.runtime)} image ls --filter label=castor.tool --format '{{{{.Repository}}}}:{{{{.Tag}}}}'")
        tags = {t.removeprefix("localhost/") for t in str(out.stdout or "").split() if ":" in t and "<none>" not in t}
        return sorted(tags)

    async def prepare(self, tool: dict[str, Any], sink: Sink) -> str | None:
        kind, name = tool.get("kind"), tool.get("name") or tool.get("id")
        rt = self.probe.runtime
        try:
            if kind in ("image", "build") and not rt:
                return f"{name} needs a container runtime, and this host has none"
            if kind == "image":
                ref = str(tool["ref"])
                if (await self.exec(f"{_q(rt)} image inspect {_q(ref)} >/dev/null 2>&1")).exit_status == 0:
                    return None
                await sink("system", f"[castor] preparing {name}: pulling {ref}\n")
                return None if await self.stream(f"{_q(rt)} pull {_q(ref)}", sink) == 0 else f"couldn't pull {ref} for {name}"
            if kind == "build":
                return await self._build(tool, name, sink)
            if kind == "host":
                check, install = str(tool.get("check") or ""), str(tool.get("install") or "")
                if check and (await self.exec(check)).exit_status == 0:
                    return None
                if not install or not self.allow_host_install:
                    hint = tool.get("install_hint") or install
                    how = f" Install it with: {hint}" if hint else ""
                    return f"{name} isn't installed on {self.name}.{how} (or allow host installs on the SSH host)"
                await sink("system", f"[castor] preparing {name}: installing on the host\n$ {install}\n")
                if await self.stream(install, sink) != 0:
                    return f"installing {name} on the host failed"
                if check and (await self.exec(check)).exit_status != 0:
                    return f"{name} still isn't available after installing (check: {check})"
                return None
        except (KeyError, asyncssh.Error, OSError) as exc:
            return f"preparing {name} failed: {exc}"
        return f"unknown way to get {name}: {kind}"

    async def _build(self, tool: dict[str, Any], name: str, sink: Sink) -> str | None:
        rt, ref = str(self.probe.runtime), str(tool["ref"])
        files = tool.get("files") or {}
        if "Dockerfile" not in files:
            return f"the recipe for {name} has no Dockerfile"
        async with self._locks.setdefault(ref, asyncio.Lock()):
            if (await self.exec(f"{_q(rt)} image inspect {_q(ref)} >/dev/null 2>&1")).exit_status == 0:
                return None
            await sink("system", f"[castor] preparing {name}: building {ref} (first time on this host; later jobs reuse it)\n")
            context = posixpath.join(self.probe.workdir, f".tool-{uuid.uuid4().hex[:10]}")
            try:
                async with self.conn.start_sftp_client() as sftp:
                    await sftp.makedirs(context, exist_ok=True)
                    for filename, text in files.items():
                        if not re.fullmatch(r"[A-Za-z0-9._-]+", filename):
                            return f"refusing a recipe file named {filename!r}"
                        async with sftp.open(posixpath.join(context, filename), "w") as handle:
                            await handle.write(str(text))
                code = await self.stream(f"BUILDKIT_PROGRESS=plain {_q(rt)} build -t {_q(ref)} {_q(context)}", sink)
            finally:
                await self.exec(f"rm -rf -- {_q(context)}")
            if code != 0:
                return f"building {name} failed (exit {code}); see the output above, then fix the tool's recipe"
            self.tools = await self._cached_tools()
            await sink("system", f"[castor] {name} is ready\n")
        return None

    async def _prepare_ahead(self, tool: dict[str, Any]) -> None:
        name = tool.get("name") or tool.get("id")

        async def sink(_stream: str, data: str) -> None:
            log.info("prepare %s on %s: %s", name, self.name, data.rstrip())

        error = await self.prepare(tool, sink)
        self.tools = await self._cached_tools()
        await self.to_gateway({"type": "tool.prepared", "tool_id": tool.get("id"), "ref": tool.get("ref"),
                               "ok": error is None, "error": error, "tools": self.tools})


def _describe(exc: BaseException) -> str:
    if isinstance(exc, asyncssh.PermissionDenied):
        return "permission denied: add Castor's public key to the account's ~/.ssh/authorized_keys"
    if isinstance(exc, TimeoutError):
        return "timed out connecting"
    if isinstance(exc, ConnectionRefusedError):
        return "connection refused"
    text = str(exc) or type(exc).__name__
    return text if len(text) < 300 else text[:300] + "…"


class _SeenKey(asyncssh.SSHClient):
    """Notes an untrusted host key the server presented (and still refuses it)."""

    def __init__(self) -> None:
        self.key: asyncssh.SSHKey | None = None

    def validate_host_public_key(self, host: str, addr: str, port: int, key: asyncssh.SSHKey) -> bool:
        self.key = key
        return False


@dataclass
class Connected:
    conn: asyncssh.SSHClientConnection
    tunnels: list[asyncssh.SSHClientConnection]  # the jump hosts' connections, in order
    host_key: str  # the key the host presented
    jump_keys: dict[str, str]  # every jump host's presented key, by hop label

    def close(self) -> None:
        self.conn.close()
        for tunnel in reversed(self.tunnels):
            tunnel.close()


async def load_config(db: Any) -> str:
    row = await db.get(m.AppSetting, ssh_config.SETTING)
    return str(row.value or "") if row is not None else ""


async def connect(row: m.SshHost, config_text: str = "") -> Connected:
    """Reach the host, through its jump hosts if the SSH config gives it some, checking each
    hop's host key against the trusted one (trust on first use)."""
    hops = ssh_config.route(ssh_config.parse(config_text), row.host, row.username or None, row.port)
    key = asyncssh.import_private_key(vault.decrypt(row.private_key).decode())
    trusted_jumps = row.jump_host_keys or {}
    opened: list[asyncssh.SSHClientConnection] = []
    keys: dict[str, str] = {}
    try:
        for i, hop in enumerate(hops):
            last = i == len(hops) - 1
            trusted = row.host_key if last else trusted_jumps.get(hop.label)
            seen = _SeenKey()
            options: dict[str, Any] = {
                "port": hop.port, "username": hop.user, "client_keys": [key], "agent_path": None, "config": [],
                "preferred_auth": "publickey", "client_factory": lambda seen=seen: seen,
                "keepalive_interval": hop.keepalive_interval or 15, "keepalive_count_max": hop.keepalive_count or 3,
                "connect_timeout": hop.connect_timeout or CONNECT_TIMEOUT, "login_timeout": CONNECT_TIMEOUT,
                "known_hosts": ([asyncssh.import_public_key(trusted)], [], []) if trusted else None,
                "tunnel": opened[-1] if opened else (),
            }
            try:
                conn = await asyncssh.connect(hop.hostname, **options)
            except asyncssh.HostKeyNotVerifiable as exc:
                # The key it presented instead, from this same connection (a second one just
                # to read the key would be turned away by sshd's per-source penalties).
                new = seen.key.export_public_key().decode().strip() if seen.key else None
                async with SessionLocal() as db:
                    current = await db.get(m.SshHost, row.id)
                    if current is not None:
                        if last:
                            current.host_key_pending = new
                        else:
                            current.jump_host_keys_pending = {**(current.jump_host_keys_pending or {}), hop.label: new}
                        await db.commit()
                what = "the host key" if last else f"the host key of jump host {hop.label}"
                raise HostKeyChanged(
                    f"{what} changed (now {fingerprint(new) or 'unknown'}, trusted {fingerprint(trusted)}): "
                    "make sure it's the right machine, then accept the new key"
                ) from exc
            except (OSError, asyncssh.Error, TimeoutError) as exc:
                if last:
                    raise
                raise JumpFailed(f"jump host {hop.label}: {_describe(exc)}") from exc
            opened.append(conn)
            presented = conn.get_server_host_key()
            keys[hop.label] = presented.export_public_key().decode().strip() if presented else ""
    except BaseException:
        for conn in reversed(opened):
            conn.close()
        raise
    target = keys.pop(hops[-1].label)
    return Connected(conn=opened[-1], tunnels=opened[:-1], host_key=target, jump_keys=keys)


async def probe(conn: asyncssh.SSHClientConnection, workdir: str) -> Probe:
    """Make the work directory and learn about the host."""
    rel = workdir.removeprefix("~/")
    script = (
        f"mkdir -p -- {_q(rel)} && cd -- {_q(rel)} && pwd && (uname -srm 2>/dev/null || echo) && "
        "(hostname 2>/dev/null || uname -n) && (id -un 2>/dev/null || whoami) && "
        "for rt in podman docker; do if command -v $rt >/dev/null 2>&1 && $rt version >/dev/null 2>&1; then echo $rt; break; fi; done; true"
    )
    result = await conn.run(_sh(script), check=False, stdin=asyncssh.DEVNULL)
    lines = str(result.stdout or "").splitlines()
    if result.exit_status != 0 or len(lines) < 4:
        raise RuntimeError(f"couldn't set up the work directory {workdir!r}: {str(result.stderr or '').strip()[:300]}")
    return Probe(workdir=lines[0].strip(), os=lines[1].strip(), hostname=lines[2].strip(), username=lines[3].strip(),
                 runtime=lines[4].strip() if len(lines) > 4 and lines[4].strip() in ("podman", "docker") else None)


# ---------- all hosts ----------


class SshHosts:
    def __init__(self) -> None:
        self.links: dict[uuid.UUID, HostLink] = {}

    def by_agent(self, agent_id: uuid.UUID) -> HostLink | None:
        return next((link for link in self.links.values() if link.agent_id == agent_id), None)

    async def start(self) -> None:
        async with SessionLocal() as db:
            from sqlalchemy import select

            rows = (await db.execute(select(m.SshHost))).scalars().all()
            pairs = [(r.id, r.agent_id) for r in rows]
        for host_id, agent_id in pairs:
            self._start(host_id, agent_id)

    def _start(self, host_id: uuid.UUID, agent_id: uuid.UUID) -> HostLink:
        link = HostLink(host_id, agent_id)
        self.links[host_id] = link
        link.start()
        return link

    async def reload(self, host_id: uuid.UUID, agent_id: uuid.UUID) -> None:
        """Start (again) with the host's current settings."""
        await self.remove(host_id)
        self._start(host_id, agent_id)

    async def remove(self, host_id: uuid.UUID) -> None:
        link = self.links.pop(host_id, None)
        if link is not None:
            await link.stop()

    async def stop(self) -> None:
        for host_id in list(self.links):
            await self.remove(host_id)


ssh_hosts = SshHosts()
