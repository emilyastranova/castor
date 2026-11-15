"""Castor as an MCP client (docs/ACTIONS.md phase 3): calling tools on other MCP servers.

Two ways to reach a server, behind one small `Session` interface (JSON-RPC requests and the
server's notifications):

- `HttpSession`: Streamable HTTP from the Castor server. Every POST goes through the outbound
  policy (`outbound.client`), answers come back as JSON or as an event stream (server
  notifications such as log messages and progress arrive on it before the result).
- `SseSession`: the older HTTP+SSE transport (protocol 2024-11-05): a long GET event stream that
  first names an endpoint (same origin only) to POST messages to; answers and notifications
  arrive on the stream.
- `StdioSession`: a program that speaks MCP on stdin/stdout, run by an agent (never on the
  server): `mcp.start` starts it (in a container from a tool or image, or on the agent's host),
  `mcp.send`/`mcp.message` carry JSON-RPC lines, `mcp.exited` says it stopped. One warm program
  per connection, agent and project is shared by that project's calls; the agent stops it after
  `idle_s` without use.

Secrets (a token in a header or an environment variable) come from the connection's variable
set and exist only in the request or the `mcp.start` message.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from . import outbound
from .version import VERSION

log = logging.getLogger(__name__)

PROTOCOL = "2025-06-18"
MAX_TOOL_PAGES = 20
MAX_BYTES = 20 * 1024 * 1024

Notify = Callable[[dict[str, Any]], Awaitable[None]]
_stopping: set[asyncio.Task] = set()


class McpError(Exception):
    """The server couldn't be reached or answered something that isn't MCP."""


class McpRpcError(McpError):
    """The server answered a request with a JSON-RPC error."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"{message} (error {code})")
        self.code = code


class McpGone(McpError):
    """The program (or the agent running it) stopped while a request was waiting."""


async def _ignore(_msg: dict[str, Any]) -> None:
    return None


def _answer(msg: dict[str, Any]) -> Any:
    if "error" in msg and msg["error"] is not None:
        err = msg["error"] if isinstance(msg["error"], dict) else {"message": str(msg["error"])}
        raise McpRpcError(int(err.get("code") or 0), str(err.get("message") or "error"))
    return msg.get("result")


class Session:
    """What a tool call needs from a connection."""

    server_info: dict[str, Any] | None = None

    async def request(self, method: str, params: dict[str, Any] | None = None, on_note: Notify = _ignore) -> Any:
        raise NotImplementedError

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        return None


async def initialize(session: Session) -> dict[str, Any]:
    result = await session.request("initialize", {
        "protocolVersion": PROTOCOL,
        "capabilities": {},
        "clientInfo": {"name": "castor", "title": "Castor", "version": VERSION},
    })
    if not isinstance(result, dict):
        raise McpError("the server's answer to initialize isn't an object")
    await session.notify("notifications/initialized")
    session.server_info = {
        "name": (result.get("serverInfo") or {}).get("name", ""),
        "title": (result.get("serverInfo") or {}).get("title", ""),
        "version": (result.get("serverInfo") or {}).get("version", ""),
        "protocol": result.get("protocolVersion", ""),
        "instructions": str(result.get("instructions") or "")[:2000],
    }
    return session.server_info


async def list_tools(session: Session) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    cursor = None
    for _ in range(MAX_TOOL_PAGES):
        result = await session.request("tools/list", {"cursor": cursor} if cursor else {})
        for tool in (result or {}).get("tools") or []:
            if isinstance(tool, dict) and tool.get("name"):
                tools.append({k: tool.get(k) for k in ("name", "title", "description", "inputSchema", "outputSchema", "annotations")
                              if tool.get(k) is not None})
        cursor = (result or {}).get("nextCursor")
        if not cursor:
            break
    return tools


async def call_tool(session: Session, name: str, arguments: dict[str, Any], on_note: Notify = _ignore) -> dict[str, Any]:
    result = await session.request("tools/call", {"name": name, "arguments": arguments}, on_note)
    if not isinstance(result, dict):
        raise McpError("the tool's result isn't an object")
    return result


# ---------- Streamable HTTP ----------


class HttpSession(Session):
    def __init__(self, url: str, headers: dict[str, str], rules: outbound.Policy, timeout: float = 120) -> None:
        self.url = url
        self.headers = headers
        self.client = outbound.client(rules, timeout)
        self.rules = rules
        self.session_id: str | None = None
        self.protocol: str | None = None
        self._ids = 0

    def _headers(self) -> dict[str, str]:
        out = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", **self.headers}
        if self.session_id:
            out["Mcp-Session-Id"] = self.session_id
        if self.protocol:
            out["MCP-Protocol-Version"] = self.protocol
        return out

    async def _post(self, body: dict[str, Any], want: int | None, on_note: Notify) -> Any:
        self.rules.check_host(outbound.check_url(self.url))
        try:
            async with self.client.stream("POST", self.url, headers=self._headers(), content=json.dumps(body)) as resp:
                if resp.headers.get("mcp-session-id"):
                    self.session_id = resp.headers["mcp-session-id"]
                if resp.status_code == 404 and self.session_id:
                    raise McpError("the server ended the session")
                if resp.status_code >= 400:
                    text = (await resp.aread())[:300].decode("utf-8", "replace").strip()
                    raise McpError(f"HTTP {resp.status_code}" + (f": {text}" if text else ""))
                if want is None:
                    return None
                kind = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                if kind == "application/json":
                    data = await resp.aread()
                    if len(data) > MAX_BYTES:
                        raise McpError("the answer is too large")
                    return self._pick(json.loads(data), want)
                if kind == "text/event-stream":
                    return await self._events(resp, want, on_note)
                raise McpError(f"the server answered {kind or 'nothing'}, not JSON or an event stream")
        except httpx.HTTPError as exc:
            raise McpError(str(exc) or type(exc).__name__) from exc
        except ValueError as exc:
            raise McpError(f"the answer isn't JSON: {exc}") from exc

    @staticmethod
    def _pick(data: Any, want: int) -> Any:
        for msg in data if isinstance(data, list) else [data]:
            if isinstance(msg, dict) and msg.get("id") == want:
                return _answer(msg)
        raise McpError("the answer had no response to the request")

    async def _events(self, resp: httpx.Response, want: int, on_note: Notify) -> Any:
        data: list[str] = []
        size = 0
        async for line in resp.aiter_lines():
            size += len(line)
            if size > MAX_BYTES:
                raise McpError("the answer is too large")
            if line.startswith("data:"):
                data.append(line[5:].lstrip())
                continue
            if line.strip() or not data:
                continue
            msg = json.loads("\n".join(data))
            data = []
            if isinstance(msg, dict) and msg.get("id") == want and ("result" in msg or "error" in msg):
                return _answer(msg)
            if isinstance(msg, dict) and msg.get("method"):
                await on_note(msg)
        if data:  # a last event without the blank line after it
            msg = json.loads("\n".join(data))
            if isinstance(msg, dict) and msg.get("id") == want and ("result" in msg or "error" in msg):
                return _answer(msg)
        raise McpError("the event stream ended without an answer")

    async def request(self, method: str, params: dict[str, Any] | None = None, on_note: Notify = _ignore) -> Any:
        self._ids += 1
        result = await self._post({"jsonrpc": "2.0", "id": self._ids, "method": method, "params": params or {}}, self._ids, on_note)
        if method == "initialize" and isinstance(result, dict):
            self.protocol = str(result.get("protocolVersion") or PROTOCOL)
        return result

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        await self._post({"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})}, None, _ignore)

    async def close(self) -> None:
        if self.session_id:
            try:
                await self.client.delete(self.url, headers=self._headers())
            except httpx.HTTPError:
                pass
        await self.client.aclose()


# ---------- the older HTTP+SSE transport ----------


class SseSession(Session):
    """HTTP+SSE (MCP 2024-11-05): GET `url` opens an event stream whose first `endpoint` event
    says where to POST messages; responses and notifications come back on the stream. The
    endpoint must be on the same origin as `url` (a server can't send our requests elsewhere)."""

    def __init__(self, url: str, headers: dict[str, str], rules: outbound.Policy, timeout: float = 120) -> None:
        self.url = url
        self.headers = headers
        self.rules = rules
        self.timeout = timeout
        # No read timeout on the stream (it's quiet between answers); each request has its own.
        self.client = outbound.client(rules, timeout)
        self.endpoint: asyncio.Future[str] | None = None
        self.pending: dict[int, asyncio.Future] = {}
        self.notes: dict[int, Notify] = {}
        self.reader: asyncio.Task | None = None
        self.failure: McpError | None = None
        self._ids = 0

    async def _open(self) -> str:
        if self.endpoint is None:
            self.rules.check_host(outbound.check_url(self.url))
            self.endpoint = asyncio.get_running_loop().create_future()
            self.endpoint.add_done_callback(lambda f: f.cancelled() or f.exception())  # seen: no "never retrieved"
            self.reader = asyncio.create_task(self._read())
        try:
            return await asyncio.wait_for(asyncio.shield(self.endpoint), self.timeout)
        except TimeoutError as exc:
            raise McpError("the server didn't say where to send messages (no endpoint event)") from exc

    def _fail(self, error: McpError) -> None:
        self.failure = error
        if self.endpoint is not None and not self.endpoint.done():
            self.endpoint.set_exception(error)
            self.endpoint.exception()
        for future in self.pending.values():
            if not future.done():
                future.set_exception(error)

    def _endpoint(self, data: str) -> str:
        from urllib.parse import urljoin, urlsplit

        target = urljoin(self.url, data.strip())
        a, b = urlsplit(self.url), urlsplit(target)
        if (a.scheme, a.hostname, a.port) != (b.scheme, b.hostname, b.port):
            raise McpError("the server named an endpoint on another origin; refused")
        return target

    async def _read(self) -> None:
        headers = {"Accept": "text/event-stream", **self.headers}
        try:
            async with self.client.stream("GET", self.url, headers=headers,
                                          timeout=httpx.Timeout(self.timeout, read=None)) as resp:
                if resp.status_code >= 400:
                    text = (await resp.aread())[:300].decode("utf-8", "replace").strip()
                    raise McpError(f"HTTP {resp.status_code}" + (f": {text}" if text else ""))
                kind = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                if kind != "text/event-stream":
                    raise McpError(f"the server answered {kind or 'nothing'}, not an event stream")
                event, data = "message", []
                async for line in resp.aiter_lines():
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data.append(line[5:].lstrip())
                    elif not line.strip() and data:
                        await self._event(event, "\n".join(data))
                        event, data = "message", []
            raise McpError("the server closed the event stream")
        except asyncio.CancelledError:
            raise
        except McpError as exc:
            self._fail(exc)
        except (httpx.HTTPError, outbound.OutboundRefused) as exc:
            self._fail(McpError(str(exc) or type(exc).__name__))
        except Exception as exc:  # noqa: BLE001 - a broken stream fails the calls waiting on it
            self._fail(McpError(f"the event stream broke: {exc}"))

    async def _event(self, event: str, data: str) -> None:
        if len(data) > MAX_BYTES:
            raise McpError("the answer is too large")
        if event == "endpoint":
            if self.endpoint is not None and not self.endpoint.done():
                self.endpoint.set_result(self._endpoint(data))
            return
        if event != "message":
            return
        try:
            msg = json.loads(data)
        except ValueError:
            return
        for one in msg if isinstance(msg, list) else [msg]:
            if not isinstance(one, dict):
                continue
            rid = one.get("id")
            if rid in self.pending and ("result" in one or "error" in one):
                if not self.pending[rid].done():
                    self.pending[rid].set_result(one)
            elif one.get("method"):
                token = ((one.get("params") or {}).get("_meta") or {}).get("progressToken")
                targets = [self.notes[token]] if token in self.notes else list(self.notes.values())
                for note in targets:
                    try:
                        await note(one)
                    except Exception:
                        log.exception("MCP notification handler failed")

    async def _post(self, body: dict[str, Any]) -> None:
        endpoint = await self._open()
        self.rules.check_host(outbound.check_url(endpoint))
        try:
            resp = await self.client.post(endpoint, headers={"Content-Type": "application/json", **self.headers}, content=json.dumps(body))
        except httpx.HTTPError as exc:
            raise McpError(str(exc) or type(exc).__name__) from exc
        if resp.status_code >= 400:
            raise McpError(f"HTTP {resp.status_code}" + (f": {resp.text[:300].strip()}" if resp.text.strip() else ""))

    async def request(self, method: str, params: dict[str, Any] | None = None, on_note: Notify = _ignore) -> Any:
        if self.failure is not None:
            raise self.failure
        self._ids += 1
        rid = self._ids
        future = asyncio.get_running_loop().create_future()
        self.pending[rid] = future
        self.notes[rid] = on_note
        try:
            await self._post({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
            return _answer(await future)
        finally:
            self.pending.pop(rid, None)
            self.notes.pop(rid, None)

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        await self._post({"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})})

    async def close(self) -> None:
        if self.reader is not None:
            self.reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):  # it failed or was stopped: either way it's done
                await self.reader
        await self.client.aclose()


# ---------- stdio programs on agents ----------


@dataclass
class _Program:
    """One warm stdio program on one agent, for one project: two projects never share a program
    (or whatever it keeps between calls)."""

    key: tuple[str, uuid.UUID, str]
    session_id: str
    started: asyncio.Future
    pending: dict[Any, asyncio.Future] = field(default_factory=dict)
    notes: dict[Any, Notify] = field(default_factory=dict)
    initialized: bool = False
    server_info: dict[str, Any] | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    ids: int = 0


class StdioSession(Session):
    """A call's view of a shared program: requests get ids of their own."""

    def __init__(self, hub: StdioHub, program: _Program) -> None:
        self.hub = hub
        self.program = program
        self.server_info = program.server_info

    async def request(self, method: str, params: dict[str, Any] | None = None, on_note: Notify = _ignore) -> Any:
        return await self.hub.request(self.program, method, params, on_note)

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        await self.hub.send(self.program, {"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})})


class StdioHub:
    """The stdio programs agents run for the server, by (connection id, agent id, project id or
    "" for a check from Settings)."""

    def __init__(self) -> None:
        self.programs: dict[tuple[str, uuid.UUID, str], _Program] = {}
        self.by_session: dict[str, _Program] = {}

    async def session(self, conn: Any, connection_id: str, launch: dict[str, Any], timeout: float = 300,
                      project_id: uuid.UUID | None = None) -> StdioSession:
        """The connection's program on this agent (`conn`, a gateway AgentConnection) for this
        project, started and initialized if it isn't already."""
        key = (connection_id, conn.agent_id, str(project_id or ""))
        program = self.programs.get(key)
        if program is None or program.started.done() and program.started.exception() is not None:
            program = _Program(key=key, session_id=uuid.uuid4().hex, started=asyncio.get_running_loop().create_future())
            self.programs[key] = program
            self.by_session[program.session_id] = program
            program.conn = conn  # type: ignore[attr-defined]
            await conn.send({"type": "mcp.start", "session_id": program.session_id, **launch})
        try:
            await asyncio.wait_for(asyncio.shield(program.started), timeout)
        except TimeoutError as exc:
            self._drop(program, McpGone("the program didn't start in time"))
            raise McpError(f"{conn.name} didn't start the MCP server in {int(timeout)} s") from exc
        session = StdioSession(self, program)
        async with program.lock:
            if not program.initialized:
                program.server_info = await initialize(session)
                program.initialized = True
        session.server_info = program.server_info
        return session

    async def send(self, program: _Program, message: dict[str, Any]) -> None:
        await program.conn.send({"type": "mcp.send", "session_id": program.session_id, "message": message})  # type: ignore[attr-defined]

    async def request(self, program: _Program, method: str, params: dict[str, Any] | None, on_note: Notify) -> Any:
        program.ids += 1
        rid = f"c{program.ids}"
        future = asyncio.get_running_loop().create_future()
        program.pending[rid] = future
        program.notes[rid] = on_note
        try:
            await self.send(program, {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
            return _answer(await future)
        except asyncio.CancelledError:
            try:
                await self.send(program, {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": rid}})
            except Exception as exc:  # noqa: BLE001 - the agent may be gone already
                log.debug("couldn't tell the program to cancel %s: %s", rid, exc)
            raise
        finally:
            program.pending.pop(rid, None)
            program.notes.pop(rid, None)

    async def on_message(self, agent_id: uuid.UUID, msg: dict[str, Any]) -> None:
        """mcp.started / mcp.message / mcp.exited from an agent."""
        program = self.by_session.get(str(msg.get("session_id")))
        if program is None or program.key[1] != agent_id:
            return
        kind = msg.get("type")
        if kind == "mcp.started":
            if msg.get("error"):
                self._drop(program, McpGone(str(msg["error"])))
            elif not program.started.done():
                program.started.set_result(True)
        elif kind == "mcp.exited":
            detail = str(msg.get("error") or "").strip()
            self._drop(program, McpGone("the MCP server stopped" + (f": {detail}" if detail else "")))
        elif kind == "mcp.message":
            message = msg.get("message")
            if not isinstance(message, dict):
                return
            rid = message.get("id")
            if rid in program.pending and ("result" in message or "error" in message):
                future = program.pending[rid]
                if not future.done():
                    future.set_result(message)
            elif message.get("method"):
                # A notification (log, progress) goes to the call it's about, or to every one.
                token = ((message.get("params") or {}).get("_meta") or {}).get("progressToken")
                targets = [program.notes[token]] if token in program.notes else list(program.notes.values())
                for note in targets:
                    try:
                        await note(message)
                    except Exception:
                        log.exception("MCP notification handler failed")

    def agent_gone(self, agent_id: uuid.UUID, name: str) -> None:
        for program in [p for p in self.programs.values() if p.key[1] == agent_id]:
            self._drop(program, McpGone(f"{name} disconnected"))

    def forget(self, connection_id: str) -> None:
        """A connection changed or went away: its programs are started again next time
        (the agents stop the old ones when they're idle)."""
        for program in [p for p in self.programs.values() if p.key[0] == connection_id]:
            self._drop(program, McpGone("the connection changed"))
            try:
                task = asyncio.get_running_loop().create_task(program.conn.send({"type": "mcp.stop", "session_id": program.session_id}))  # type: ignore[attr-defined]
                _stopping.add(task)
                task.add_done_callback(_stopping.discard)
            except RuntimeError as exc:  # no running loop (called from a thread)
                log.debug("couldn't stop the old program: %s", exc)

    def _drop(self, program: _Program, error: Exception) -> None:
        self.programs.pop(program.key, None)
        self.by_session.pop(program.session_id, None)
        if not program.started.done():
            program.started.set_exception(error)
            program.started.exception()  # retrieved: no "never retrieved" warning
        for future in program.pending.values():
            if not future.done():
                future.set_exception(error)


hub = StdioHub()
