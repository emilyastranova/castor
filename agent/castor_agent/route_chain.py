"""A network route's chain of hops, and the local ends jobs reach it through. stdlib only.

A route is a list of hops: SSH jumps, SOCKS4/5 proxies, HTTP CONNECT proxies. `Chain.open(k, host,
port)` connects to host:port through the first k hops:
- an SSH hop is an OpenSSH client process with a dynamic forward (`-D`) on loopback, reached through
  the hops before it by its ProxyCommand (this file's `pipe` mode, through a "stage" SOCKS server
  for those hops);
- proxy hops are negotiated in-stream: after reaching a proxy, the stream asks it for the next hop
  (or the target), so a SOCKS proxy behind a jumpbox behind an HTTP proxy is one connection.

Names are passed on as names (SOCKS5 domain, SOCKS4a, CONNECT host:port), so they're resolved at the
far end; with `dns: local` they're resolved here first.

The same file runs in two places:
- in the agent itself (`LocalRoute`), when the agent has no container runtime: host jobs and HTTP
  actions use an authenticated proxy on 127.0.0.1;
- in the route box (`python3 route_chain.py box config.json`), a container per route that container
  jobs share the network namespace of. The box redirects every TCP connection and DNS lookup in that
  namespace into the chain (iptables REDIRECT, SO_ORIGINAL_DST, names answered with fake addresses
  that map back to the name), offers a mixed SOCKS/HTTP proxy on 127.0.0.1:1080 for tools told about
  a proxy, and with `strict` drops everything else (UDP, ICMP, raw sockets). It talks to the agent in
  JSON lines over stdin/stdout, and the agent reaches its proxy through a unix socket in the route's
  directory.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import ipaddress
import json
import os
import secrets
import shlex
import shutil
import socket
import struct
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

Event = Callable[[dict[str, Any]], Awaitable[None] | None]

CONNECT_TIMEOUT = 15.0
SSH_READY_TIMEOUT = 30.0
BOX_UID = 39999  # the box's chain runs as this user; the kill switch lets only it out
PROXY_PORT = 1080  # the box's proxy for tools told about one
TRANSPARENT_PORT = 12345  # where the box redirects other TCP connections
DNS_PORT = 5353  # where the box redirects DNS lookups
FAKE_NET = ipaddress.ip_network("198.18.0.0/15")  # the box's stand-in addresses for names
KINDS = {"ssh": "SSH", "socks5": "SOCKS5", "socks4": "SOCKS4", "http": "HTTP proxy"}


class ChainError(Exception):
    """A connection through the chain failed. `hop` is the hop that said so (0-based), or None for
    the agent's own network; `target` is True when the hop was reached and the next thing wasn't."""

    def __init__(self, message: str, hop: int | None = None, *, target: bool = False) -> None:
        super().__init__(message)
        self.hop = hop
        self.target = target


@dataclass
class Hop:
    index: int
    kind: str  # ssh | socks5 | socks4 | http
    host: str
    port: int
    username: str = ""
    password: str = ""
    private_key: str = ""
    host_key: str = ""  # ssh: the trusted key line ("ssh-ed25519 AAAA..."); empty: learn it
    id: str = ""

    @classmethod
    def from_dict(cls, index: int, d: dict[str, Any]) -> Hop:
        kind = str(d.get("kind") or "")
        if kind not in KINDS:
            raise ValueError(f"hop {index + 1}: unknown kind {kind!r}")
        host = str(d.get("host") or "").strip()
        if not host or host.startswith("-") or any(c.isspace() for c in host):
            raise ValueError(f"hop {index + 1}: not a host name: {host!r}")
        return cls(index=index, kind=kind, host=host, port=int(d.get("port") or _default_port(kind)),
                   username=str(d.get("username") or ""), password=str(d.get("password") or ""),
                   private_key=str(d.get("private_key") or ""), host_key=str(d.get("host_key") or "").strip(),
                   id=str(d.get("id") or index))

    def label(self) -> str:
        user = f"{self.username}@" if self.username and self.kind == "ssh" else ""
        return f"{KINDS[self.kind]} {user}{_hostport(self.host, self.port)}"


def _default_port(kind: str) -> int:
    return {"ssh": 22, "socks5": 1080, "socks4": 1080, "http": 8080}[kind]


def _hostport(host: str, port: int) -> str:
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


# ---------- proxy handshakes (client side) ----------

SOCKS5_ERRORS = {
    1: "general failure", 2: "connection not allowed by its rules", 3: "network unreachable",
    4: "host unreachable", 5: "connection refused", 6: "timed out (TTL expired)",
    7: "command not supported", 8: "address type not supported",
}


async def _exact(reader: asyncio.StreamReader, n: int) -> bytes:
    try:
        return await reader.readexactly(n)
    except asyncio.IncompleteReadError as exc:
        raise ConnectionError("the connection closed") from exc


async def socks5_connect(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, host: str, port: int,
                         username: str = "", password: str = "") -> None:
    """Ask a SOCKS5 proxy for host:port. Raises ConnectionError (couldn't talk to it) or
    LookupError (it answered, and couldn't reach host:port)."""
    methods = b"\x00\x02" if username else b"\x00"
    writer.write(b"\x05" + bytes([len(methods)]) + methods)
    await writer.drain()
    ver, method = await _exact(reader, 2)
    if ver != 5:
        raise ConnectionError("it isn't a SOCKS5 proxy")
    if method == 0x02:
        u, p = username.encode(), password.encode()
        writer.write(b"\x01" + bytes([len(u)]) + u + bytes([len(p)]) + p)
        await writer.drain()
        _, status = await _exact(reader, 2)
        if status != 0:
            raise ConnectionError("it refused the username and password")
    elif method == 0xFF:
        raise ConnectionError("it wants credentials" if not username else "it accepts none of the ways to sign in offered")
    elif method != 0:
        raise ConnectionError(f"it asked for an unsupported way to sign in ({method})")
    writer.write(b"\x05\x01\x00" + _socks5_addr(host) + struct.pack("!H", port))
    await writer.drain()
    head = await _exact(reader, 4)
    if head[1] != 0:
        raise LookupError(SOCKS5_ERRORS.get(head[1], f"error {head[1]}"))
    atyp = head[3]
    skip = 4 if atyp == 1 else 16 if atyp == 4 else (await _exact(reader, 1))[0]
    await _exact(reader, skip + 2)


def _socks5_addr(host: str) -> bytes:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        raw = host.encode("idna")
        if len(raw) > 255:
            raise LookupError("the name is too long") from None
        return b"\x03" + bytes([len(raw)]) + raw
    return (b"\x01" if ip.version == 4 else b"\x04") + ip.packed


async def socks4_connect(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, host: str, port: int,
                         userid: str = "") -> None:
    try:
        ip = ipaddress.ip_address(host)
        if ip.version != 4:
            raise LookupError("SOCKS4 can't reach IPv6 addresses")
        writer.write(b"\x04\x01" + struct.pack("!H", port) + ip.packed + userid.encode() + b"\x00")
    except ValueError:  # SOCKS4a: the proxy resolves the name
        writer.write(b"\x04\x01" + struct.pack("!H", port) + b"\x00\x00\x00\x01" + userid.encode() + b"\x00"
                     + host.encode("idna") + b"\x00")
    await writer.drain()
    head = await _exact(reader, 8)
    if head[1] != 0x5A:
        raise LookupError({0x5B: "rejected or failed", 0x5C: "its identd couldn't be reached",
                           0x5D: "the user id was refused"}.get(head[1], f"error {head[1]}"))


async def http_connect(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, host: str, port: int,
                       username: str = "", password: str = "") -> None:
    target = _hostport(host, port)
    lines = [f"CONNECT {target} HTTP/1.1", f"Host: {target}"]
    if username:
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        lines.append(f"Proxy-Authorization: Basic {token}")
    writer.write(("\r\n".join(lines) + "\r\n\r\n").encode())
    await writer.drain()
    try:
        head = await reader.readuntil(b"\r\n\r\n")
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError) as exc:
        raise ConnectionError("it closed the connection or didn't answer like an HTTP proxy") from exc
    status_line = head.split(b"\r\n", 1)[0].decode("latin-1")
    parts = status_line.split(" ", 2)
    if len(parts) < 2 or not parts[0].startswith("HTTP/"):
        raise ConnectionError("it didn't answer like an HTTP proxy")
    code = parts[1]
    if code == "200":
        return
    if code == "407":
        raise ConnectionError("it wants credentials" if not username else "it refused the username and password")
    reason = parts[2] if len(parts) > 2 else ""
    raise LookupError(f"{code} {reason}".strip())


async def negotiate(hop: Hop, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, host: str, port: int) -> None:
    if hop.kind == "socks5":
        await socks5_connect(reader, writer, host, port, hop.username, hop.password)
    elif hop.kind == "socks4":
        await socks4_connect(reader, writer, host, port, hop.username)
    elif hop.kind == "http":
        await http_connect(reader, writer, host, port, hop.username, hop.password)
    else:  # pragma: no cover - ssh hops aren't negotiated in-stream
        raise ConnectionError(f"can't negotiate through {hop.kind}")


def _close(writer: asyncio.StreamWriter | None) -> None:
    if writer is None:
        return
    with contextlib.suppress(Exception):
        writer.close()


# ---------- SSH hops ----------

def ssh_reason(stderr: str, hop: Hop) -> str:
    """What an OpenSSH client's error output means, for a person."""
    text = stderr.strip()
    low = text.lower()
    for line in text.splitlines():  # a ProxyCommand (this file's pipe) explains itself
        if line.startswith("castor-route: "):
            return line.removeprefix("castor-route: ")
    if "remote host identification has changed" in low or "host key verification failed" in low:
        return "its host key isn't the one Castor trusts"
    if "permission denied" in low or "too many authentication failures" in low:
        what = "password" if hop.password and not hop.private_key else "key"
        return f"it refused the {what} for {hop.username or 'that user'}"
    if "could not resolve hostname" in low or "name or service not known" in low:
        return f"couldn't resolve {hop.host}"
    if "connection refused" in low:
        return "connection refused (nothing listening on that port?)"
    if "timed out" in low:
        return "timed out connecting"
    if "no route to host" in low or "network is unreachable" in low:
        return "no route to it from here"
    if "kex_exchange_identification" in low or "connection closed" in low or "connection reset" in low:
        return "the connection closed during the handshake (is it an SSH server?)"
    if "no user exists" in low:
        return "the route box has no user for SSH (rebuild the castor-route tool)"
    last = [line for line in text.splitlines() if line.strip()]
    return last[-1][:300] if last else "ssh exited"


class SshProcess:
    """One SSH hop: `ssh -N -D 127.0.0.1:<port>` to the hop, through the hops before it."""

    def __init__(self, chain: Chain, hop: Hop) -> None:
        self.chain = chain
        self.hop = hop
        self.dir = chain.dir / f"hop{hop.index}"
        self.port = 0
        self.proc: asyncio.subprocess.Process | None = None
        self.stderr = ""
        self.learned: str = ""  # the host key seen, when none was trusted yet

    def _files(self) -> tuple[Path, Path | None, Path | None]:
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        known = self.dir / "known_hosts"
        alias = self.alias
        known.write_text(f"{alias} {self.hop.host_key}\n" if self.hop.host_key else "")
        key = askpass = None
        if self.hop.private_key:
            key = self.dir / "key"
            fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(self.hop.private_key.rstrip() + "\n")
        if self.hop.password:
            askpass = self.dir / "askpass"
            fd = os.open(askpass, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o700)
            with os.fdopen(fd, "w") as fh:
                fh.write("#!/bin/sh\nprintf '%s\\n' \"$CASTOR_HOP_SECRET\"\n")
        return known, key, askpass

    @property
    def alias(self) -> str:
        return f"castor-hop-{''.join(c for c in self.hop.id if c.isalnum() or c in '-_') or self.hop.index}"

    def argv(self, known: Path, key: Path | None, *, probe: bool = False) -> list[str]:
        hop = self.hop
        pinned = bool(hop.host_key) and not probe
        opts = {
            "ExitOnForwardFailure": "yes", "ServerAliveInterval": "15", "ServerAliveCountMax": "3",
            "ConnectTimeout": str(int(CONNECT_TIMEOUT)), "HostKeyAlias": self.alias,
            "UserKnownHostsFile": str(known), "GlobalKnownHostsFile": "/dev/null",
            "StrictHostKeyChecking": "yes" if pinned else "accept-new", "UpdateHostKeys": "no", "CheckHostIP": "no",
            "IdentitiesOnly": "yes", "IdentityAgent": "none", "ForwardAgent": "no", "ForwardX11": "no",
            "PubkeyAuthentication": "yes" if key else "no",
            "PasswordAuthentication": "yes" if hop.password else "no",
            "KbdInteractiveAuthentication": "yes" if hop.password else "no",
            "NumberOfPasswordPrompts": "1", "LogLevel": "ERROR",
        }
        if probe:  # only the host key: no credentials are offered to a host whose key changed
            opts.update({"PreferredAuthentications": "none", "PubkeyAuthentication": "no",
                         "PasswordAuthentication": "no", "KbdInteractiveAuthentication": "no", "BatchMode": "yes"})
        elif not hop.password:
            opts["BatchMode"] = "yes"
        argv = ["ssh", "-F", "/dev/null", "-N", "-T"]
        if not probe:
            argv += ["-D", f"127.0.0.1:{self.port}"]
        for k, v in opts.items():
            argv += ["-o", f"{k}={v}"]
        if key and not probe:
            argv += ["-i", str(key)]
        if hop.index > 0:
            argv += ["-o", "ProxyCommand=" + self.chain.pipe_command(hop.index)]
        if hop.username:
            argv += ["-l", hop.username]
        argv += ["-p", str(hop.port), "--", hop.host]
        return argv

    def env(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "USER", "LANG", "LC_ALL", "TMPDIR")}
        env.update(self.chain.pipe_env(self.hop.index))
        if self.hop.password:
            env.update({"SSH_ASKPASS": str(self.dir / "askpass"), "SSH_ASKPASS_REQUIRE": "force",
                        "DISPLAY": "castor", "CASTOR_HOP_SECRET": self.hop.password})
        return env

    async def start(self) -> None:
        """Start it and wait until its forward accepts connections (it's signed in by then)."""
        if not shutil.which("ssh"):
            raise ChainError("this machine has no OpenSSH client (ssh) for SSH hops", self.hop.index)
        known, key, _ = self._files()
        for _attempt in range(3):
            self.port = free_port()
            self.stderr = ""
            self.proc = await asyncio.create_subprocess_exec(
                *self.argv(known, key), stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE, env=self.env(), start_new_session=True)
            reading = asyncio.ensure_future(self._read_stderr())
            deadline = time.monotonic() + SSH_READY_TIMEOUT
            while time.monotonic() < deadline:
                if self.proc.returncode is not None:
                    break
                try:
                    _, w = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", self.port), 1)
                    _close(w)
                    if not self.hop.host_key:
                        self.learned = _read_key(known, self.alias)
                    return
                except (OSError, asyncio.TimeoutError):
                    await asyncio.sleep(0.15)
            if self.proc.returncode is None:
                await self.stop()
                raise ChainError("timed out signing in", self.hop.index)
            await asyncio.wait_for(reading, 2)
            if "port forwarding failed" in self.stderr.lower() or "address already in use" in self.stderr.lower():
                continue  # the port was taken in between: another one
            reason = ssh_reason(self.stderr, self.hop)
            if "host key" in reason and self.hop.host_key:
                self.learned = await self._probe_key()
                raise ChainError(reason, self.hop.index)
            raise ChainError(reason, self.hop.index)
        raise ChainError("couldn't find a free local port for the SSH forward", self.hop.index)

    async def _read_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        while line := await self.proc.stderr.readline():
            self.stderr = (self.stderr + line.decode("utf-8", "replace"))[-4000:]

    async def _probe_key(self) -> str:
        """The key the host shows now (after a mismatch), without signing in to it."""
        scratch = self.dir / "probe_known_hosts"
        scratch.write_text("")
        try:
            proc = await asyncio.create_subprocess_exec(
                *self.argv(scratch, None, probe=True), stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, env=self.env(),
                start_new_session=True)
            await asyncio.wait_for(proc.wait(), CONNECT_TIMEOUT + 5)
        except (OSError, asyncio.TimeoutError):
            return ""
        return _read_key(scratch, self.alias)

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def stop(self) -> None:
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.terminate()
                await asyncio.wait_for(self.proc.wait(), 3)
            except (ProcessLookupError, asyncio.TimeoutError):
                try:
                    self.proc.kill()
                except ProcessLookupError:
                    pass
        shutil.rmtree(self.dir, ignore_errors=True)


def _read_key(known: Path, alias: str) -> str:
    try:
        for line in known.read_text().splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[0] == alias:
                return f"{parts[1]} {parts[2]}"
    except OSError:
        pass
    return ""


# ---------- the chain ----------

class Chain:
    def __init__(self, hops: list[Hop], *, dns: str = "remote", workdir: str | Path, python: str = sys.executable,
                 script: str = __file__, on_event: Event | None = None) -> None:
        self.hops = hops
        self.dns = dns
        self.dir = Path(workdir)
        self.python = python
        self.script = script
        self.on_event = on_event
        self.ssh: dict[int, SshProcess] = {}
        self.stages: dict[int, Endpoint] = {}  # SOCKS servers for "the first k hops", for SSH hops' ProxyCommand
        self.stage_auth = (secrets.token_hex(8), secrets.token_hex(16))
        self.state: dict[int, dict[str, Any]] = {h.index: {"state": "down"} for h in hops}
        self.broken: ChainError | None = None
        self._supervisor: asyncio.Task | None = None
        self._closing = False
        self.resolve: Callable[[str], str | None] | None = None  # the box's fake addresses → names

    async def emit(self, event: dict[str, Any]) -> None:
        if self.on_event:
            result = self.on_event(event)
            if asyncio.iscoroutine(result):
                await result

    def pipe_command(self, k: int) -> str:
        stage = self.stages[k]
        return f"{shlex.quote(self.python)} {shlex.quote(self.script)} pipe 127.0.0.1:{stage.port} %h %p"

    def pipe_env(self, k: int) -> dict[str, str]:
        return {"CASTOR_ROUTE_PIPE_AUTH": f"{self.stage_auth[0]}:{self.stage_auth[1]}"} if k > 0 else {}

    async def start(self) -> None:
        """Bring every SSH hop up, in order. A hop that fails leaves the chain broken there (and
        `supervise` keeps trying)."""
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        for hop in self.hops:
            if hop.kind != "ssh":
                continue
            if hop.index > 0 and hop.index not in self.stages:
                stage = Endpoint(self, upto=hop.index, auth=self.stage_auth)
                await stage.listen_tcp("127.0.0.1", 0)
                self.stages[hop.index] = stage
        await self._bring_up()
        self._supervisor = asyncio.ensure_future(self._supervise())

    async def _bring_up(self) -> None:
        self.broken = None
        for hop in self.hops:
            if hop.kind != "ssh":
                continue
            proc = self.ssh.get(hop.index)
            if proc and proc.alive:
                continue
            proc = self.ssh[hop.index] = SshProcess(self, hop)
            started = time.monotonic()
            try:
                await proc.start()
            except ChainError as exc:
                self.broken = exc
                self.state[hop.index] = {"state": "error", "error": str(exc)}
                await self.emit({"event": "hop", "index": hop.index, "state": "error", "error": str(exc)})
                if proc.learned:
                    await self.emit({"event": "host_key", "index": hop.index, "key": proc.learned, "changed": True})
                for later in self.hops[hop.index + 1:]:
                    self.state[later.index] = {"state": "down"}
                return
            ms = round((time.monotonic() - started) * 1000)
            self.state[hop.index] = {"state": "up", "ms": ms}
            await self.emit({"event": "hop", "index": hop.index, "state": "up", "ms": ms})
            if proc.learned:
                hop.host_key = proc.learned
                await self.emit({"event": "host_key", "index": hop.index, "key": proc.learned, "changed": False})

    async def _supervise(self) -> None:
        """Restart SSH hops that drop (a jumpbox rebooted, a network blip), backing off."""
        delay = 2.0
        while not self._closing:
            await asyncio.sleep(delay)
            down = [h for h in self.hops if h.kind == "ssh" and not (self.ssh.get(h.index) and self.ssh[h.index].alive)]
            if not down:
                delay = 2.0
                continue
            for h in down:
                if self.state[h.index].get("state") == "up":
                    self.state[h.index] = {"state": "down"}
                    await self.emit({"event": "hop", "index": h.index, "state": "down", "error": "the SSH connection dropped"})
            await self._bring_up()
            delay = 2.0 if self.broken is None else min(delay * 2, 30.0)

    def _last_ssh(self, k: int) -> int:
        for i in range(k - 1, -1, -1):
            if self.hops[i].kind == "ssh":
                return i
        return -1

    def _addr(self, i: int, k: int, host: str, port: int) -> tuple[str, int]:
        """Where the stream at hop i goes next: hop i+1, or the target after hop k-1."""
        if i + 1 < k:
            nxt = self.hops[i + 1]
            return nxt.host, nxt.port
        return host, port

    async def open(self, k: int, host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Connect to host:port through the first k hops."""
        if self.dns == "local" and not _is_ip(host) and k == len(self.hops):
            host = await self._resolve_here(host)
        j = self._last_ssh(k)
        writer = None
        try:
            if j >= 0:
                proc = self.ssh.get(j)
                if not proc or not proc.alive:
                    err = self.state.get(j, {}).get("error") or "not connected"
                    raise ChainError(err, j)
                nh, np_ = self._addr(j, k, host, port)
                try:
                    reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", proc.port), CONNECT_TIMEOUT)
                    await asyncio.wait_for(socks5_connect(reader, writer, nh, np_), CONNECT_TIMEOUT)
                except LookupError as exc:
                    raise ChainError(f"couldn't reach {_hostport(nh, np_)} from there: {exc}", j, target=True) from exc
                except (OSError, ConnectionError, asyncio.TimeoutError) as exc:
                    raise ChainError(f"its forward failed: {_why(exc)}", j) from exc
                start = j + 1
            else:
                first = (self.hops[0].host, self.hops[0].port) if k > 0 else (host, port)
                try:
                    reader, writer = await asyncio.wait_for(asyncio.open_connection(*first), CONNECT_TIMEOUT)
                except (OSError, asyncio.TimeoutError) as exc:
                    if k == 0:
                        raise ChainError(f"couldn't reach {_hostport(*first)}: {_why(exc)}", None, target=True) from exc
                    raise ChainError(f"couldn't reach it: {_why(exc)}", 0) from exc
                start = 0
            for i in range(start, k):
                hop = self.hops[i]
                nh, np_ = self._addr(i, k, host, port)
                try:
                    await asyncio.wait_for(negotiate(hop, reader, writer, nh, np_), CONNECT_TIMEOUT)
                except LookupError as exc:
                    raise ChainError(f"couldn't reach {_hostport(nh, np_)} from there: {exc}", i, target=True) from exc
                except (OSError, ConnectionError, asyncio.TimeoutError) as exc:
                    raise ChainError(_why(exc), i) from exc
            return reader, writer
        except ChainError:
            _close(writer)
            raise

    async def connect(self, host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        if self.resolve and (name := self.resolve(host)):
            host = name
        return await self.open(len(self.hops), host, port)

    async def _resolve_here(self, host: str) -> str:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise ChainError(f"couldn't resolve {host} here (the route resolves names on the agent)", None, target=True) from exc
        return str(infos[0][4][0])

    async def test(self, target: tuple[str, int] | None = None) -> dict[str, Any]:
        """Hop by hop: whether it's reached and how long reaching it took, which hop broke and why;
        then the target, if one is given."""
        if self.broken is not None or any(h.kind == "ssh" and not (self.ssh.get(h.index) and self.ssh[h.index].alive)
                                          for h in self.hops):
            await self._bring_up()
        results: dict[int, dict[str, Any]] = {}
        failed = False
        for hop in self.hops:
            if failed:
                results.setdefault(hop.index, {"index": hop.index, "ok": None, "error": "not reached"})
                continue
            started = time.monotonic()
            try:
                if hop.kind == "ssh" and not (self.ssh.get(hop.index) and self.ssh[hop.index].alive):
                    raise ChainError(self.state.get(hop.index, {}).get("error") or "not connected", hop.index)
                _, w = await self.open(hop.index, hop.host, hop.port)
                _close(w)
                results[hop.index] = {"index": hop.index, "ok": True, "ms": round((time.monotonic() - started) * 1000)}
            except ChainError as exc:
                failed = True
                if exc.hop is not None and exc.hop < hop.index and not exc.target:
                    # The hop before wasn't what it should be (credentials, not a proxy...).
                    results[exc.hop] = {"index": exc.hop, "ok": False, "error": str(exc)}
                    results[hop.index] = {"index": hop.index, "ok": None, "error": "not reached"}
                elif exc.hop is not None and exc.hop < hop.index:
                    results[hop.index] = {"index": hop.index, "ok": False, "error": f"not reachable from hop {exc.hop + 1}: {exc}"}
                else:
                    results[hop.index] = {"index": hop.index, "ok": False, "error": str(exc)}
        if not failed and self.hops and self.hops[-1].kind != "ssh":
            # The last proxy: does it take requests (and its credentials)? Ask it for somewhere that
            # doesn't answer; a refusal from it means it works.
            last = self.hops[-1]
            try:
                _, w = await self.open(len(self.hops), "127.0.0.1", 9)
                _close(w)
            except ChainError as exc:
                if not exc.target and exc.hop == last.index:
                    failed = True
                    results[last.index] = {"index": last.index, "ok": False, "error": str(exc)}
        out: dict[str, Any] = {"hops": [results[h.index] for h in self.hops]}
        if target and not failed:
            started = time.monotonic()
            try:
                _, w = await self.connect(*target)
                _close(w)
                out["target"] = {"ok": True, "ms": round((time.monotonic() - started) * 1000)}
            except ChainError as exc:
                out["target"] = {"ok": False, "error": str(exc), "hop": exc.hop}
        return out

    async def close(self) -> None:
        self._closing = True
        if self._supervisor:
            self._supervisor.cancel()
        for proc in self.ssh.values():
            await proc.stop()
        for stage in self.stages.values():
            await stage.close()


def _why(exc: BaseException) -> str:
    if isinstance(exc, asyncio.TimeoutError):
        return "timed out"
    if isinstance(exc, ConnectionRefusedError):
        return "connection refused"
    if isinstance(exc, socket.gaierror):
        return "couldn't resolve the name"
    text = str(exc) or exc.__class__.__name__
    return text[:300]


# ---------- local ends: a mixed SOCKS5 / SOCKS4 / HTTP proxy ----------

async def pipe_both(r1: asyncio.StreamReader, w1: asyncio.StreamWriter, r2: asyncio.StreamReader,
                    w2: asyncio.StreamWriter) -> None:
    async def copy(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        try:
            while data := await r.read(65536):
                w.write(data)
                await w.drain()
        except (OSError, ConnectionError):
            pass
        finally:
            try:
                if w.can_write_eof():
                    w.write_eof()
            except (OSError, RuntimeError):
                pass

    await asyncio.gather(copy(r1, w2), copy(r2, w1))
    _close(w1)
    _close(w2)


class Endpoint:
    """A proxy that sends each connection through the chain: SOCKS5 (CONNECT, names or addresses),
    SOCKS4/4a, and HTTP (CONNECT, and plain requests with an absolute URL). With `auth`, SOCKS5 and
    HTTP clients must sign in (SOCKS4 is refused): the agent's own loopback ends use it."""

    def __init__(self, chain: Chain, *, upto: int | None = None, auth: tuple[str, str] | None = None,
                 on_connection: Callable[[str, int, str | None], None] | None = None) -> None:
        self.chain = chain
        self.upto = upto  # None: the whole chain
        self.auth = auth
        self.port = 0
        self.servers: list[asyncio.base_events.Server] = []
        self.on_connection = on_connection

    async def listen_tcp(self, host: str, port: int) -> int:
        server = await asyncio.start_server(self._client, host, port)
        self.servers.append(server)
        self.port = int(server.sockets[0].getsockname()[1])
        return self.port

    async def listen_unix(self, path: str, sock: socket.socket | None = None) -> None:
        server = await asyncio.start_unix_server(self._client, path=None if sock else path, sock=sock)
        self.servers.append(server)

    async def close(self) -> None:
        for server in self.servers:
            server.close()

    async def _open(self, host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        if self.upto is None:
            return await self.chain.connect(host, port)
        return await self.chain.open(self.upto, host, port)

    def _note(self, host: str, port: int, error: str | None) -> None:
        if self.on_connection:
            self.on_connection(host, port, error)

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            first = await asyncio.wait_for(reader.readexactly(1), CONNECT_TIMEOUT)
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, OSError):
            _close(writer)
            return
        try:
            if first == b"\x05":
                await self._socks5(reader, writer)
            elif first == b"\x04":
                await self._socks4(reader, writer)
            else:
                await self._http(first, reader, writer)
        except (OSError, ConnectionError, asyncio.IncompleteReadError, asyncio.TimeoutError, ValueError):
            _close(writer)

    async def _socks5(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        n = (await reader.readexactly(1))[0]
        methods = await reader.readexactly(n)
        if self.auth:
            if 2 not in methods:
                writer.write(b"\x05\xff")
                await writer.drain()
                _close(writer)
                return
            writer.write(b"\x05\x02")
            await writer.drain()
            await reader.readexactly(1)
            user = (await reader.readexactly((await reader.readexactly(1))[0])).decode(errors="replace")
            pw = (await reader.readexactly((await reader.readexactly(1))[0])).decode(errors="replace")
            ok = secrets.compare_digest(user, self.auth[0]) and secrets.compare_digest(pw, self.auth[1])
            writer.write(b"\x01" + (b"\x00" if ok else b"\x01"))
            await writer.drain()
            if not ok:
                _close(writer)
                return
        else:
            writer.write(b"\x05\x00")
            await writer.drain()
        _ver, cmd, _, atyp = await reader.readexactly(4)
        if atyp == 1:
            host = str(ipaddress.IPv4Address(await reader.readexactly(4)))
        elif atyp == 4:
            host = str(ipaddress.IPv6Address(await reader.readexactly(16)))
        elif atyp == 3:
            host = (await reader.readexactly((await reader.readexactly(1))[0])).decode("idna")
        else:
            writer.write(b"\x05\x08\x00\x01" + b"\x00" * 6)
            await writer.drain()
            _close(writer)
            return
        (port,) = struct.unpack("!H", await reader.readexactly(2))
        if cmd != 1:  # BIND and UDP ASSOCIATE don't go through a route
            writer.write(b"\x05\x07\x00\x01" + b"\x00" * 6)
            await writer.drain()
            _close(writer)
            return
        try:
            r2, w2 = await self._open(host, port)
        except ChainError as exc:
            self._note(host, port, str(exc))
            code = 5 if "refused" in str(exc) else 4 if exc.target else 1
            writer.write(b"\x05" + bytes([code]) + b"\x00\x01" + b"\x00" * 6)
            await writer.drain()
            _close(writer)
            return
        self._note(host, port, None)
        writer.write(b"\x05\x00\x00\x01" + b"\x00" * 6)
        await writer.drain()
        await pipe_both(reader, writer, r2, w2)

    async def _socks4(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        cmd = (await reader.readexactly(1))[0]
        (port,) = struct.unpack("!H", await reader.readexactly(2))
        ip = await reader.readexactly(4)
        await reader.readuntil(b"\x00")  # user id
        host = str(ipaddress.IPv4Address(ip))
        if ip[:3] == b"\x00\x00\x00" and ip[3] != 0:
            host = (await reader.readuntil(b"\x00"))[:-1].decode("idna")
        if cmd != 1 or self.auth:  # SOCKS4 has no password: not on an end that needs one
            writer.write(b"\x00\x5b" + b"\x00" * 6)
            await writer.drain()
            _close(writer)
            return
        try:
            r2, w2 = await self._open(host, port)
        except ChainError as exc:
            self._note(host, port, str(exc))
            writer.write(b"\x00\x5b" + b"\x00" * 6)
            await writer.drain()
            _close(writer)
            return
        self._note(host, port, None)
        writer.write(b"\x00\x5a" + b"\x00" * 6)
        await writer.drain()
        await pipe_both(reader, writer, r2, w2)

    async def _http(self, first: bytes, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = first + await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), CONNECT_TIMEOUT)
        lines = head.decode("latin-1").split("\r\n")
        try:
            method, target, version = lines[0].split(" ", 2)
        except ValueError:
            _close(writer)
            return
        headers = [ln for ln in lines[1:] if ln]
        if self.auth and not self._http_auth_ok(headers):
            writer.write(b"HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Authenticate: Basic realm=\"castor\"\r\n"
                         b"Content-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
            _close(writer)
            return
        if method.upper() == "CONNECT":
            host, port = _split_hostport(target, 443)
            try:
                r2, w2 = await self._open(host, port)
            except ChainError as exc:
                self._note(host, port, str(exc))
                writer.write(f"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nX-Castor-Route: {_header_safe(exc)}\r\n\r\n".encode())
                await writer.drain()
                _close(writer)
                return
            self._note(host, port, None)
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()
            await pipe_both(reader, writer, r2, w2)
            return
        # A plain request through a proxy: GET http://host[:port]/path HTTP/1.1
        if not target.lower().startswith("http://"):
            writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
            _close(writer)
            return
        rest = target[7:]
        authority, _, path = rest.partition("/")
        host, port = _split_hostport(authority, 80)
        try:
            r2, w2 = await self._open(host, port)
        except ChainError as exc:
            self._note(host, port, str(exc))
            writer.write(f"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nX-Castor-Route: {_header_safe(exc)}\r\n\r\n".encode())
            await writer.drain()
            _close(writer)
            return
        self._note(host, port, None)
        kept = [h for h in headers if h.split(":", 1)[0].strip().lower() not in
                ("proxy-authorization", "proxy-connection", "connection", "keep-alive")]
        out = [f"{method} /{path} {version}", *kept, "Connection: close", "", ""]
        w2.write("\r\n".join(out).encode("latin-1"))
        await w2.drain()
        await pipe_both(reader, writer, r2, w2)

    def _http_auth_ok(self, headers: list[str]) -> bool:
        assert self.auth
        for h in headers:
            name, _, value = h.partition(":")
            if name.strip().lower() == "proxy-authorization":
                scheme, _, token = value.strip().partition(" ")
                if scheme.lower() != "basic":
                    return False
                try:
                    user, _, pw = base64.b64decode(token).decode().partition(":")
                except (ValueError, UnicodeDecodeError):
                    return False
                return secrets.compare_digest(user, self.auth[0]) and secrets.compare_digest(pw, self.auth[1])
        return False


def _split_hostport(text: str, default: int) -> tuple[str, int]:
    if text.startswith("["):
        host, _, rest = text[1:].partition("]")
        return host, int(rest.lstrip(":") or default)
    host, sep, port = text.rpartition(":")
    if sep and port.isdigit():
        return host, int(port)
    return text, default


def _header_safe(exc: BaseException) -> str:
    return "".join(c for c in str(exc) if 32 <= ord(c) < 127)[:200]


# ---------- the box: transparent redirection, fake DNS, kill switch ----------

class FakeDns:
    """Answers A lookups with addresses from 198.18.0.0/15 that stand for the name, so the
    connection that follows can ask the far end for the name itself (names never resolve here).
    AAAA gets no answer (IPv4 through the route); PTR for a stand-in gives the name back."""

    def __init__(self) -> None:
        self.by_name: dict[str, str] = {}
        self.by_ip: dict[str, str] = {}
        self._next = int(FAKE_NET.network_address) + 1

    def address_for(self, name: str) -> str:
        name = name.lower().rstrip(".")
        if name in self.by_name:
            return self.by_name[name]
        if self._next >= int(FAKE_NET.broadcast_address):
            self._next = int(FAKE_NET.network_address) + 1  # wrap; old mappings are overwritten
        ip = str(ipaddress.IPv4Address(self._next))
        self._next += 1
        old = self.by_ip.pop(ip, None)
        if old:
            self.by_name.pop(old, None)
        self.by_name[name] = ip
        self.by_ip[ip] = name
        return ip

    def name_for(self, ip: str) -> str | None:
        return self.by_ip.get(ip)

    def answer(self, query: bytes) -> bytes | None:
        if len(query) < 12:
            return None
        qid, flags, qd = struct.unpack("!HHH", query[:6])
        if flags & 0x8000 or qd != 1:
            return None
        pos, labels = 12, []
        try:
            while query[pos]:
                ln = query[pos]
                labels.append(query[pos + 1:pos + 1 + ln].decode("idna"))
                pos += 1 + ln
            pos += 1
            qtype, qclass = struct.unpack("!HH", query[pos:pos + 4])
        except (IndexError, struct.error, UnicodeError):
            return None
        question = query[12:pos + 4]
        name = ".".join(labels)
        answers = b""
        count = 0
        rcode = 0
        if qtype == 1 and qclass == 1 and name:  # A
            ip = self.address_for(name)
            answers = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 30, 4) + ipaddress.IPv4Address(ip).packed
            count = 1
        elif qtype == 12 and name.endswith(".in-addr.arpa"):  # PTR of a stand-in
            parts = name.removesuffix(".in-addr.arpa").split(".")
            target = self.name_for(".".join(reversed(parts)))
            if target:
                rdata = b"".join(bytes([len(p)]) + p.encode("idna") for p in target.split(".")) + b"\x00"
                answers = b"\xc0\x0c" + struct.pack("!HHIH", 12, 1, 30, len(rdata)) + rdata
                count = 1
            else:
                rcode = 3
        resp_flags = 0x8180 | rcode  # response, recursion desired + available
        return struct.pack("!HHHHHH", qid, resp_flags, 1, count, 0, 0) + question + answers


class _DnsUdp(asyncio.DatagramProtocol):
    def __init__(self, dns: FakeDns) -> None:
        self.dns = dns
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: Any) -> None:
        reply = self.dns.answer(data)
        if reply and self.transport:
            self.transport.sendto(reply, addr)


async def _dns_tcp(dns: FakeDns, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            (n,) = struct.unpack("!H", await reader.readexactly(2))
            reply = dns.answer(await reader.readexactly(n))
            if reply is None:
                break
            writer.write(struct.pack("!H", len(reply)) + reply)
            await writer.drain()
    except (asyncio.IncompleteReadError, OSError):
        pass
    _close(writer)


SO_ORIGINAL_DST = 80


def original_dst(sock: socket.socket) -> tuple[str, int]:
    raw = sock.getsockopt(socket.SOL_IP, SO_ORIGINAL_DST, 16)
    port = struct.unpack("!H", raw[2:4])[0]
    return str(ipaddress.IPv4Address(raw[4:8])), port


async def _transparent(chain: Chain, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                       note: Callable[[str, int, str | None], None]) -> None:
    sock = writer.get_extra_info("socket")
    try:
        host, port = original_dst(sock)
    except OSError:
        _close(writer)
        return
    try:
        r2, w2 = await chain.connect(host, port)
    except ChainError as exc:
        note(chain.resolve(host) if chain.resolve else host, port, str(exc))
        _close(writer)  # a reset, as a refused connection would be
        return
    note(chain.resolve(host) if chain.resolve and chain.resolve(host) else host, port, None)
    await pipe_both(reader, writer, r2, w2)


def _run(*argv: str) -> None:
    import subprocess

    done = subprocess.run(list(argv), check=False, capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError(f"{' '.join(argv)}: {(done.stderr or done.stdout).strip()[:300]}")


def box_network(strict: bool) -> None:
    """In the box's own network namespace (which the route's container jobs join): every TCP
    connection and DNS lookup that isn't the chain's own goes into the chain; with `strict`,
    nothing else leaves at all (IPv6 neither)."""
    uid = str(BOX_UID)
    nat = ["iptables", "-t", "nat", "-I", "OUTPUT", "1"]
    # Inserted at the top, last first: DNS (also to Docker's 127.0.0.11), then other TCP.
    _run(*nat, "-p", "tcp", "-m", "owner", "!", "--uid-owner", uid, "!", "-d", "127.0.0.0/8",
         "-j", "REDIRECT", "--to-ports", str(TRANSPARENT_PORT))
    _run(*nat, "-p", "tcp", "--dport", "53", "-m", "owner", "!", "--uid-owner", uid, "-j", "REDIRECT",
         "--to-ports", str(DNS_PORT))
    _run(*nat, "-p", "udp", "--dport", "53", "-m", "owner", "!", "--uid-owner", uid, "-j", "REDIRECT",
         "--to-ports", str(DNS_PORT))
    if strict:
        _run("iptables", "-A", "OUTPUT", "-o", "lo", "-j", "ACCEPT")
        # What was redirected above: still "out eth0" to this hook (it was routed before the nat
        # hook changed its address), but bound for 127.0.0.1 now.
        _run("iptables", "-A", "OUTPUT", "-d", "127.0.0.0/8", "-j", "ACCEPT")
        _run("iptables", "-A", "OUTPUT", "-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT")
        _run("iptables", "-A", "OUTPUT", "-m", "owner", "--uid-owner", uid, "-j", "ACCEPT")
        _run("iptables", "-P", "OUTPUT", "DROP")
        try:
            _run("ip6tables", "-A", "OUTPUT", "-o", "lo", "-j", "ACCEPT")
            _run("ip6tables", "-P", "OUTPUT", "DROP")
        except (RuntimeError, FileNotFoundError):
            # No IPv6 tables: fine only if there's no IPv6 to leak through.
            if Path("/proc/net/if_inet6").exists() and Path("/proc/net/if_inet6").read_text().strip():
                raise


class Box:
    """The route box's main program (`route_chain.py box <config>`)."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.hops = [Hop.from_dict(i, h) for i, h in enumerate(config.get("hops") or [])]
        self.dns = FakeDns()
        self.chain: Chain | None = None
        self.out_lock = asyncio.Lock()
        self.recent: list[dict[str, Any]] = []  # connections, for the agent's status

    async def emit(self, event: dict[str, Any]) -> None:
        async with self.out_lock:
            sys.stdout.write(json.dumps(event) + "\n")
            sys.stdout.flush()

    def note(self, host: str, port: int, error: str | None) -> None:
        self.recent.append({"host": host, "port": port, "error": error, "at": time.time()})
        del self.recent[:-50]

    async def run(self) -> None:
        cfg = self.config
        strict = bool(cfg.get("strict", True))
        sock_path = str(cfg["socket"])
        work = Path("/tmp/castor-route")
        work.mkdir(mode=0o700, exist_ok=True)
        script = work / "route_chain.py"
        shutil.copyfile(__file__, script)
        unix = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            os.unlink(sock_path)
        except FileNotFoundError:
            pass
        unix.bind(sock_path)
        os.chmod(sock_path, 0o666)  # inside the agent's own 0700 directory: only it (and root) reach it
        unix.listen(64)
        unix.setblocking(False)
        if os.geteuid() == 0:
            try:
                box_network(strict)
            except (RuntimeError, FileNotFoundError) as exc:
                await self.emit({"event": "error", "error": f"couldn't set up the route's network: {exc}"})
                return
            os.chown(work, BOX_UID, BOX_UID)
            os.chown(script, BOX_UID, BOX_UID)
            os.setgroups([])
            os.setgid(BOX_UID)
            os.setuid(BOX_UID)
            os.environ["HOME"] = str(work)
        elif strict:
            await self.emit({"event": "error", "error": "the route box isn't running as root, so it can't hold jobs to the route"})
            return
        await self.emit({"event": "configured"})
        self.chain = chain = Chain(self.hops, dns=str(cfg.get("dns") or "remote"), workdir=work / "chain",
                                   python=sys.executable, script=str(script), on_event=self.emit)
        chain.resolve = self.dns.name_for
        await chain.start()
        endpoint = Endpoint(chain, on_connection=self.note)
        await endpoint.listen_tcp("127.0.0.1", PROXY_PORT)
        await endpoint.listen_unix(sock_path, sock=unix)
        await asyncio.start_server(lambda r, w: _transparent(chain, r, w, self.note), "127.0.0.1", TRANSPARENT_PORT)
        loop = asyncio.get_running_loop()
        await loop.create_datagram_endpoint(lambda: _DnsUdp(self.dns), local_addr=("127.0.0.1", DNS_PORT))
        await asyncio.start_server(lambda r, w: _dns_tcp(self.dns, r, w), "127.0.0.1", DNS_PORT)
        await self.emit({"event": "ready", "broken": None if chain.broken is None else
                         {"index": chain.broken.hop, "error": str(chain.broken)}})
        await self._commands()

    async def _commands(self) -> None:
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
        while line := await reader.readline():
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("op") == "test" and self.chain:
                target = msg.get("target")
                result = await self.chain.test((str(target[0]), int(target[1])) if target else None)
                await self.emit({"event": "tested", "id": msg.get("id"), **result})
            elif msg.get("op") == "names":
                await self.emit({"event": "names", "id": msg.get("id"), "names": dict(self.dns.by_ip)})
            elif msg.get("op") == "recent":
                await self.emit({"event": "recent", "id": msg.get("id"), "connections": self.recent[-20:]})
            elif msg.get("op") == "stop":
                break
        if self.chain:
            await self.chain.close()


# ---------- the pipe (an SSH hop's ProxyCommand) ----------

def pipe(address: str, host: str, port: int) -> int:
    """Connect stdin/stdout to host:port through a stage server (SOCKS5 on 127.0.0.1)."""
    import threading

    stage_host, _, stage_port = address.rpartition(":")
    user, _, pw = os.environ.get("CASTOR_ROUTE_PIPE_AUTH", "").partition(":")
    try:
        sock = socket.create_connection((stage_host, int(stage_port)), timeout=CONNECT_TIMEOUT + 5)
        sock.sendall(b"\x05\x01\x02" if user else b"\x05\x01\x00")
        _ver, method = sock.recv(2)
        if method == 2:
            u, p = user.encode(), pw.encode()
            sock.sendall(b"\x01" + bytes([len(u)]) + u + bytes([len(p)]) + p)
            if sock.recv(2)[1:2] != b"\x00":
                raise ConnectionError("the stage refused its credentials")
        sock.sendall(b"\x05\x01\x00" + _socks5_addr(host) + struct.pack("!H", port))
        head = sock.recv(4)
        if len(head) < 2 or head[1] != 0:
            code = head[1] if len(head) > 1 else 1
            raise ConnectionError(f"the hop before couldn't reach {host}:{port} ({SOCKS5_ERRORS.get(code, code)})")
        atyp = head[3]
        skip = 4 if atyp == 1 else 16 if atyp == 4 else sock.recv(1)[0]
        _recv_exact(sock, skip + 2)
        sock.settimeout(None)
    except (OSError, ValueError, ConnectionError) as exc:
        sys.stderr.write(f"castor-route: {exc}\n")
        return 1

    def up() -> None:
        try:
            while data := os.read(0, 65536):
                sock.sendall(data)
        except OSError:
            pass
        try:
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass

    threading.Thread(target=up, daemon=True).start()
    try:
        while data := sock.recv(65536):
            os.write(1, data)
    except OSError:
        pass
    return 0


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("the stage closed the connection")
        buf += chunk
    return buf


# ---------- in the agent: a route without a box ----------

@dataclass
class LocalRoute:
    """The chain in the agent's own process (an agent without a container runtime): a proxy on
    127.0.0.1 that needs a password, for host jobs and HTTP actions."""

    config: dict[str, Any]
    workdir: Path
    on_event: Event | None = None
    chain: Chain | None = None
    endpoint: Endpoint | None = None
    auth: tuple[str, str] = field(default_factory=lambda: (secrets.token_hex(8), secrets.token_hex(16)))

    async def start(self) -> None:
        hops = [Hop.from_dict(i, h) for i, h in enumerate(self.config.get("hops") or [])]
        self.chain = Chain(hops, dns=str(self.config.get("dns") or "remote"), workdir=self.workdir,
                           on_event=self.on_event)
        await self.chain.start()
        self.endpoint = Endpoint(self.chain, auth=self.auth)
        await self.endpoint.listen_tcp("127.0.0.1", 0)

    @property
    def port(self) -> int:
        return self.endpoint.port if self.endpoint else 0

    async def close(self) -> None:
        if self.endpoint:
            await self.endpoint.close()
        if self.chain:
            await self.chain.close()
        shutil.rmtree(self.workdir, ignore_errors=True)


def main(argv: list[str]) -> int:
    if len(argv) >= 4 and argv[0] == "pipe":
        return pipe(argv[1], argv[2], int(argv[3]))
    if len(argv) >= 2 and argv[0] == "box":
        path = Path(argv[1])
        config = json.loads(path.read_text())
        box = Box(config)
        asyncio.run(box.run())
        return 0
    sys.stderr.write("usage: route_chain.py pipe <stage> <host> <port> | box <config.json>\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
