"""Network routes on this agent: getting a job's traffic (or an HTTP action's request) to its targets
through a chain of hops (route_chain.py).

With a container runtime, a route's chain runs in its own **route box**: a container built from the
`castor-route` tool, one per route while jobs use it, shared by them. Container jobs join its network
namespace (`--network container:<box>`), where every TCP connection and DNS lookup is sent into the
chain and, for strict routes, nothing else gets out. Host jobs and HTTP requests reach the box's
proxy through a unix socket in the route's directory, behind a password-protected proxy on 127.0.0.1.
Without a container runtime the chain runs in the agent's process (`LocalRoute`), for host jobs only.

A route is started when a job needs it, checked (each SSH hop signs in), kept while jobs use it and
stopped after it has been idle for a while. Its secrets reach this machine only in the job.run or
route.test message, are written owner-only into the route's directory for the box to read, and are
deleted as soon as the box has them."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import secrets
import shutil
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import route_chain as rc

log = logging.getLogger("castor.agent.routes")

IDLE_SECONDS = 90
START_TIMEOUT = 120
Send = Callable[[dict[str, Any]], Awaitable[None]]
Sink = Callable[[str, str], Awaitable[None]]


class RouteError(Exception):
    pass


@dataclass
class BoxLink:
    """Connects through the box's proxy (its unix socket): what host jobs' local proxy uses."""

    socket_path: str

    async def connect(self, host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(self.socket_path), rc.CONNECT_TIMEOUT)
            await asyncio.wait_for(rc.socks5_connect(reader, writer, host, port), rc.CONNECT_TIMEOUT * 2)
        except LookupError as exc:
            raise rc.ChainError(f"the route couldn't reach {host}:{port}: {exc}", None, target=True) from exc
        except (OSError, ConnectionError, asyncio.TimeoutError) as exc:
            raise rc.ChainError(f"the route box didn't answer: {exc}") from exc
        return reader, writer


@dataclass
class Active:
    key: str
    route: dict[str, Any]
    mode: str  # box | local
    dir: Path
    holders: set[str] = field(default_factory=set)
    box_name: str = ""
    proc: asyncio.subprocess.Process | None = None
    local: rc.LocalRoute | None = None
    relay: rc.Endpoint | None = None
    relay_auth: tuple[str, str] = field(default_factory=lambda: (secrets.token_hex(8), secrets.token_hex(16)))
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    error: str | None = None
    broken: dict[str, Any] | None = None  # the hop that's down, if the chain is up but broken
    waiters: dict[str, asyncio.Future] = field(default_factory=dict)
    idle: asyncio.Task | None = None
    reader: asyncio.Task | None = None
    started_at: float = field(default_factory=time.time)

    @property
    def alive(self) -> bool:
        if self.mode == "local":
            return self.local is not None and self.error is None
        return self.proc is not None and self.proc.returncode is None and self.error is None

    def container_url(self, scheme: str) -> str:
        """The proxy as a container job in the box's namespace sees it."""
        return f"{_scheme(scheme)}://127.0.0.1:{rc.PROXY_PORT}"

    def host_url(self, scheme: str) -> str:
        assert self.relay or self.local
        port = self.relay.port if self.relay else self.local.port  # type: ignore[union-attr]
        user, pw = self.relay_auth if self.relay else self.local.auth  # type: ignore[union-attr]
        return f"{_scheme(scheme)}://{user}:{pw}@127.0.0.1:{port}"

    def host_auth(self) -> tuple[str, int, str, str]:
        port = self.relay.port if self.relay else self.local.port  # type: ignore[union-attr]
        user, pw = self.relay_auth if self.relay else self.local.auth  # type: ignore[union-attr]
        return "127.0.0.1", port, user, pw


def _scheme(scheme: str) -> str:
    return scheme if scheme in ("socks5h", "socks5", "socks4a", "http") else "socks5h"


class RouteManager:
    def __init__(self, send: Send, container_runtime: str | None, base: Path, tools: Any, network: str) -> None:
        self.send = send
        self.runtime = container_runtime
        self.base = base / "routes"
        self.tools = tools
        self.network = network
        self.active: dict[str, Active] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def capabilities(self) -> dict[str, Any]:
        return {
            "box": bool(self.runtime),  # container jobs (and everything else) through a route box
            "ssh": bool(shutil.which("ssh")),  # SSH hops without a box
            "proxychains": bool(shutil.which("proxychains4") or shutil.which("proxychains")),
        }

    async def cleanup(self) -> None:
        """Boxes left from before this agent started (it crashed, or was killed)."""
        if not self.runtime:
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                self.runtime, "ps", "-aq", "--filter", "label=castor.route", stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL)
            out, _ = await proc.communicate()
            ids = out.decode().split()
            if ids:
                await _quiet([self.runtime, "rm", "-f", *ids])
        except OSError:
            pass
        shutil.rmtree(self.base, ignore_errors=True)

    # ----- using a route -----

    async def acquire(self, route: dict[str, Any], holder: str, sink: Sink | None = None) -> Active:
        key = str(route["key"])
        async with self._locks.setdefault(key, asyncio.Lock()):
            active = self.active.get(key)
            if active and not active.alive:
                await self._stop(active)
                active = None
            if active is None:
                active = await self._start(route, sink)
            active.holders.add(holder)
            if active.idle:
                active.idle.cancel()
                active.idle = None
            return active

    async def release(self, key: str, holder: str) -> None:
        active = self.active.get(key)
        if active is None:
            return
        active.holders.discard(holder)
        if not active.holders and active.idle is None:
            active.idle = asyncio.ensure_future(self._idle_stop(active))

    async def _idle_stop(self, active: Active) -> None:
        await asyncio.sleep(IDLE_SECONDS)
        if not active.holders and self.active.get(active.key) is active:
            log.info("route %s idle: stopping it", active.route.get("name"))
            await self._stop(active)

    async def stop_all(self) -> None:
        for active in list(self.active.values()):
            await self._stop(active)

    async def _start(self, route: dict[str, Any], sink: Sink | None) -> Active:
        key = str(route["key"])
        directory = self.base / key[:16]
        shutil.rmtree(directory, ignore_errors=True)
        directory.mkdir(parents=True, mode=0o700)
        os.chmod(directory, 0o700)
        mode = "box" if self.runtime else "local"
        active = Active(key=key, route=route, mode=mode, dir=directory)
        self.active[key] = active
        await self._status(active, "starting")
        try:
            if mode == "box":
                await self._start_box(active, sink)
            else:
                await self._start_local(active)
        except RouteError as exc:
            active.error = str(exc)
            await self._status(active, "error", error=str(exc))
            await self._stop(active, report=False)
            raise
        await self._status(active, "broken" if active.broken else "up")
        return active

    async def _start_local(self, active: Active) -> None:
        hops = active.route.get("hops") or []
        if any(h.get("kind") == "ssh" for h in hops) and not shutil.which("ssh"):
            raise RouteError("this agent has no OpenSSH client (ssh) and no container runtime for a route box")
        local = rc.LocalRoute(config=active.route, workdir=active.dir / "chain",
                              on_event=lambda e: self._event(active, e))
        try:
            await local.start()
        except (OSError, ValueError) as exc:
            raise RouteError(f"couldn't start the route: {exc}") from exc
        active.local = local
        if local.chain and local.chain.broken:
            active.broken = {"index": local.chain.broken.hop, "error": str(local.chain.broken)}

    async def _start_box(self, active: Active, sink: Sink | None) -> None:
        way = active.route.get("box")
        if not way:
            raise RouteError("the server didn't say how to get the castor-route tool")

        async def quiet_sink(stream: str, data: str) -> None:
            if sink:
                await sink(stream, data)
            else:
                log.info("route box: %s", data.rstrip())

        error = await self.tools.prepare(way, quiet_sink, str(active.dir))
        if error:
            raise RouteError(f"the route box: {error}")
        image = str(way.get("ref"))
        config = {
            "hops": active.route.get("hops") or [],
            "dns": active.route.get("dns") or "remote",
            "strict": bool(active.route.get("strict", True)),
            "socket": "/castor-route/endpoint.sock",
        }
        cfg = active.dir / "config.json"
        fd = os.open(cfg, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(config, fh)
        shutil.copyfile(rc.__file__, active.dir / "route_chain.py")
        directory = active.dir  # read by the box's root (this agent's own user under rootless Podman)
        active.box_name = f"castor-route-{active.key[:12]}-{uuid.uuid4().hex[:4]}"
        argv = [self.runtime, "run", "--rm", "-i", "--name", active.box_name, "--label", f"castor.route={active.key[:16]}",
                "--cap-add", "NET_ADMIN", "--user", "0",
                "-v", f"{directory}:/castor-route:Z", "--entrypoint", "python3"]
        if self.network and self.network not in ("host", "none", "default"):
            argv += ["--network", self.network]
        argv += [image, "/castor-route/route_chain.py", "box", "/castor-route/config.json"]
        try:
            active.proc = await asyncio.create_subprocess_exec(
                *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except OSError as exc:
            raise RouteError(f"couldn't start the route box: {exc}") from exc
        configured = asyncio.Event()
        stderr_tail: list[str] = []

        async def read_stdout() -> None:
            assert active.proc and active.proc.stdout
            while line := await active.proc.stdout.readline():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("event") == "configured":
                    configured.set()
                elif event.get("event") == "ready":
                    active.broken = event.get("broken")
                    active.ready.set()
                elif event.get("event") == "error":
                    active.error = str(event.get("error"))
                    active.ready.set()
                elif event.get("event") in ("tested", "recent", "names"):
                    fut = active.waiters.pop(str(event.get("id")), None)
                    if fut and not fut.done():
                        fut.set_result(event)
                    continue
                await self._event(active, event)
            # The box ended.
            if active.error is None:
                tail = "".join(stderr_tail).strip().splitlines()
                active.error = f"the route box stopped{': ' + tail[-1] if tail else ''}"
            active.ready.set()
            configured.set()
            for fut in active.waiters.values():
                if not fut.done():
                    fut.set_result({"event": "tested", "error": active.error})
            if self.active.get(active.key) is active:
                await self._status(active, "error", error=active.error)

        async def read_stderr() -> None:
            assert active.proc and active.proc.stderr
            while line := await active.proc.stderr.readline():
                stderr_tail.append(line.decode("utf-8", "replace"))
                del stderr_tail[:-20]

        active.reader = asyncio.ensure_future(asyncio.gather(read_stdout(), read_stderr()))
        try:
            await asyncio.wait_for(configured.wait(), START_TIMEOUT)
        except asyncio.TimeoutError:
            pass
        finally:
            # The box has the secrets in memory now (or never will): they leave the disk.
            with contextlib.suppress(OSError):
                cfg.unlink()
        try:
            await asyncio.wait_for(active.ready.wait(), START_TIMEOUT)
        except asyncio.TimeoutError as exc:
            raise RouteError("the route box didn't come up in time") from exc
        if active.error:
            raise RouteError(active.error)
        # Host jobs and HTTP requests: a local proxy with a password, passed on to the box.
        relay = rc.Endpoint(BoxLink(str(directory / "endpoint.sock")), auth=active.relay_auth)  # type: ignore[arg-type]
        await relay.listen_tcp("127.0.0.1", 0)
        active.relay = relay

    async def _stop(self, active: Active, report: bool = True) -> None:
        if self.active.get(active.key) is active:
            del self.active[active.key]
        if active.idle:
            active.idle.cancel()
        if active.relay:
            await active.relay.close()
        if active.local:
            await active.local.close()
        if active.proc and active.proc.returncode is None:
            try:
                assert active.proc.stdin
                active.proc.stdin.write(b'{"op": "stop"}\n')
                await active.proc.stdin.drain()
            except (OSError, AssertionError, ConnectionError):
                pass
            if self.runtime and active.box_name:
                await _quiet([self.runtime, "rm", "-f", active.box_name])
            with contextlib.suppress(asyncio.TimeoutError, ProcessLookupError):
                await asyncio.wait_for(active.proc.wait(), 10)
        if active.reader:
            active.reader.cancel()
        shutil.rmtree(active.dir, ignore_errors=True)
        if report and active.error is None:
            await self._status(active, "down")

    # ----- telling the server -----

    async def _status(self, active: Active, state: str, **extra: Any) -> None:
        await self.send({"type": "route.status", "route_id": active.route.get("id"), "key": active.key,
                         "state": state, "mode": active.mode, "jobs": len(active.holders),
                         "broken": active.broken, **extra})

    async def _event(self, active: Active, event: dict[str, Any]) -> None:
        kind = event.get("event")
        hops = active.route.get("hops") or []
        index = event.get("index")
        hop_id = hops[index].get("id") if isinstance(index, int) and 0 <= index < len(hops) else None
        if kind == "hop":
            if event.get("state") == "error":
                active.broken = {"index": index, "error": event.get("error")}
            elif event.get("state") == "up" and active.broken and active.broken.get("index") == index:
                active.broken = None
            await self.send({"type": "route.hop", "route_id": active.route.get("id"), "key": active.key,
                             "hop_id": hop_id, "index": index, "state": event.get("state"),
                             "error": event.get("error"), "ms": event.get("ms")})
        elif kind == "host_key":
            await self.send({"type": "route.host_key", "route_id": active.route.get("id"), "hop_id": hop_id,
                             "index": index, "key": event.get("key"), "changed": bool(event.get("changed"))})

    # ----- testing -----

    async def test(self, route: dict[str, Any], target: list[Any] | None) -> dict[str, Any]:
        holder = f"test-{uuid.uuid4().hex[:8]}"
        try:
            active = await self.acquire(route, holder)
        except RouteError as exc:
            return {"error": str(exc)}
        try:
            tgt = (str(target[0]), int(target[1])) if target else None
            if active.mode == "local":
                assert active.local and active.local.chain
                return await active.local.chain.test(tgt)
            request_id = uuid.uuid4().hex
            fut: asyncio.Future = asyncio.get_running_loop().create_future()
            active.waiters[request_id] = fut
            assert active.proc and active.proc.stdin
            active.proc.stdin.write((json.dumps({"op": "test", "id": request_id, "target": list(tgt) if tgt else None})
                                     + "\n").encode())
            await active.proc.stdin.drain()
            try:
                result = await asyncio.wait_for(fut, 120)
            except asyncio.TimeoutError:
                return {"error": "the route box didn't answer the test in time"}
            return {k: v for k, v in result.items() if k in ("hops", "target", "error")}
        finally:
            await self.release(active.key, holder)


async def box_names(manager: RouteManager, key: str) -> dict[str, str]:
    """The names a route box answered with stand-in addresses so far ({address: name}), for the
    server to put back into a job's results."""
    active = manager.active.get(key)
    if active is None or active.mode != "box" or not active.proc or not active.proc.stdin or active.proc.returncode is not None:
        return {}
    request_id = uuid.uuid4().hex
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    active.waiters[request_id] = fut
    try:
        active.proc.stdin.write((json.dumps({"op": "names", "id": request_id}) + "\n").encode())
        await active.proc.stdin.drain()
        event = await asyncio.wait_for(fut, 5)
    except (asyncio.TimeoutError, OSError, ConnectionError):
        return {}
    finally:
        active.waiters.pop(request_id, None)
    names = event.get("names")
    return names if isinstance(names, dict) else {}


async def _quiet(argv: list[str]) -> int:
    try:
        proc = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.DEVNULL,
                                                    stderr=asyncio.subprocess.DEVNULL)
        return await proc.wait()
    except OSError:
        return -1


# ----- how a job is told about its route -----

PROXYCHAINS_FIND = 'pc=$(command -v proxychains4 || command -v proxychains) || { echo "castor: proxychains isn\'t in this image" >&2; exit 127; }'


@dataclass
class Applied:
    """What a job's run needs for its route: a network to join (containers), env, a wrapper."""

    method: str
    network: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    image: str | None = None  # a hooked image, built from the tool's
    files: dict[str, str] = field(default_factory=dict)  # path in the job dir → text
    wrap: bool = False  # run the command under proxychains
    note: str = ""


def proxychains_conf(host: str, port: int, user: str = "", pw: str = "", dns: str = "remote") -> str:
    lines = ["strict_chain", "quiet_mode"]
    if dns != "local":
        lines += ["proxy_dns", "remote_dns_subnet 224"]
    lines += ["tcp_read_time_out 15000", "tcp_connect_time_out 10000", "localnet 127.0.0.0/255.0.0.0", "[ProxyList]",
              f"socks5 {host} {port}" + (f" {user} {pw}" if user else "")]
    return "\n".join(lines) + "\n"


def wrap_command(command: str, conf_env: str = "CASTOR_ROUTE_PROXYCHAINS") -> str:
    import shlex

    return f'{PROXYCHAINS_FIND}; exec "$pc" -q -f "${{{conf_env}}}" /bin/sh -c {shlex.quote(command)}'


def fill(template: str, url: str) -> str:
    host_port = url.rsplit("@", 1)[-1].split("://", 1)[-1]
    return template.replace("{url}", url).replace("{hostport}", host_port)


async def apply(active: Active, plan: list[dict[str, Any]], *, in_container: bool, image: str | None, tools: Any,
                sink: Sink, have_proxychains: bool) -> Applied:
    """Pick the first way in the server's plan this run can use, and set it up."""
    reasons: list[str] = []
    dns = str(active.route.get("dns") or "remote")
    for way in plan:
        method = str(way.get("method"))
        scheme = str(way.get("scheme") or "socks5h")
        flags = str(way.get("flags") or "")
        if in_container:
            if active.mode != "box":
                reasons.append("container jobs need a route box")
                break
            url = active.container_url(scheme)
            applied = Applied(method=method, network=f"container:{active.box_name}")
        else:
            if way.get("container_only") or method == "transparent":
                reasons.append(f"{method} needs a container")
                continue
            url = active.host_url(scheme)
            applied = Applied(method=method)
        args = flags
        applied.env["CASTOR_ROUTE_URL"] = active.container_url("socks5h") if in_container else active.host_url("socks5h")
        if method == "native":
            args = " ".join(x for x in (flags, fill(str(way.get("args") or ""), url)) if x)
        elif method == "env":
            for name in ("ALL_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "all_proxy", "https_proxy", "http_proxy"):
                applied.env[name] = url
            applied.env["NO_PROXY"] = applied.env["no_proxy"] = ""
        elif method == "hooked":
            if in_container:
                if not image:
                    reasons.append("hooked needs an image")
                    continue
                hooked, error = await tools.hooked_image(image, sink)
                if error:
                    reasons.append(error)
                    continue
                applied.image = hooked
                conf = proxychains_conf("127.0.0.1", rc.PROXY_PORT, dns=dns)
            else:
                if not have_proxychains:
                    reasons.append("proxychains isn't installed on this agent")
                    continue
                host, port, user, pw = active.host_auth()
                conf = proxychains_conf(host, port, user, pw, dns=dns)
            applied.files[".castor/route/proxychains.conf"] = conf
            applied.wrap = True
        elif method != "transparent":
            continue
        if args:
            applied.env["CASTOR_ROUTE_ARGS"] = args
        return applied
    raise RouteError("this job can't go through the route" + (f": {'; '.join(reasons)}" if reasons else ""))
