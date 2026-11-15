"""MCP servers that speak stdio, run here for the Castor server (docs/ACTIONS.md phase 3).

`mcp.start` starts the program: in a container (a tool's image or build, or an image) or on
this host when host commands are allowed. Each JSON-RPC message from the server (`mcp.send`)
is written to its stdin as one line; each line it prints is sent back (`mcp.message`). The
program keeps running between calls and is stopped after `idle_s` without a message, or by
`mcp.stop`; when it ends the server hears `mcp.exited` (with the end of its stderr). Its
environment (the connection's secrets) is passed by name to a container, never in the argv.
stdlib only."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger("castor.agent.mcp")
Send = Callable[[dict[str, Any]], Awaitable[None]]
MAX_LINE = 16 * 1024 * 1024


class Program:
    def __init__(self, session_id: str, proc: asyncio.subprocess.Process, idle_s: float) -> None:
        self.session_id = session_id
        self.proc = proc
        self.idle_s = idle_s
        self.last_used = time.monotonic()
        self.stderr_tail = ""
        self.tasks: list[asyncio.Task] = []


class McpBridge:
    def __init__(self, send: Send, runtime: str | None, allow_host: bool, network: str, tools: Any, workdir: str) -> None:
        self.send = send
        self.runtime = runtime
        self.allow_host = allow_host
        self.network = network
        self.tools = tools  # the agent's ToolPreparer
        self.workdir = workdir
        self.programs: dict[str, Program] = {}

    async def handle(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        session_id = str(msg.get("session_id") or "")
        if kind == "mcp.start":
            await self._start(session_id, msg)
        elif kind == "mcp.send":
            program = self.programs.get(session_id)
            if program is None:
                await self.send({"type": "mcp.exited", "session_id": session_id, "error": "it isn't running"})
                return
            program.last_used = time.monotonic()
            line = json.dumps(msg.get("message"), separators=(",", ":")) + "\n"
            try:
                assert program.proc.stdin is not None
                program.proc.stdin.write(line.encode())
                await program.proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError, AssertionError):
                await self._stop(program)
        elif kind == "mcp.stop":
            program = self.programs.get(session_id)
            if program is not None:
                await self._stop(program)

    def argv(self, msg: dict[str, Any], image: str | None) -> list[str]:
        """The program's argv: in a container with the image's entrypoint (the command as its
        arguments), or the command itself on this host."""
        command = [str(c) for c in msg.get("command") or []]
        env = msg.get("env") or {}
        if image:
            if not self.runtime:
                raise RuntimeError("this agent has no container runtime")
            argv = [self.runtime, "run", "-i", "--rm", "--name", f"castor-mcp-{msg['session_id'][:12]}"]
            if self.network:
                argv += ["--network", self.network]
            for name in env:
                argv += ["-e", str(name)]
            return [*argv, image, *command]
        if not self.allow_host:
            raise RuntimeError("host commands aren't allowed on this agent (--allow-host)")
        if not command:
            raise RuntimeError("the connection names no command to run")
        return command

    async def _start(self, session_id: str, msg: dict[str, Any]) -> None:
        if session_id in self.programs:
            await self.send({"type": "mcp.started", "session_id": session_id})
            return
        try:
            image = msg.get("image")
            tool = msg.get("tool")
            if tool:
                async def sink(_stream: str, data: str) -> None:
                    log.info("preparing %s: %s", tool.get("id"), data.rstrip())

                error = await self.tools.prepare(tool, sink, self.workdir)
                if error:
                    raise RuntimeError(error)
                image = tool.get("ref") if tool.get("kind") in ("image", "build") else None
            argv = self.argv(msg, image)
            env = {**os.environ, **{str(k): str(v) for k, v in (msg.get("env") or {}).items()}}
            os.makedirs(self.workdir, exist_ok=True)
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=self.workdir, env=env, limit=MAX_LINE,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except (OSError, RuntimeError, KeyError) as exc:
            await self.send({"type": "mcp.started", "session_id": session_id, "error": str(exc)})
            return
        program = Program(session_id, proc, float(msg.get("idle_s") or 300))
        self.programs[session_id] = program
        program.tasks = [asyncio.create_task(self._read(program)), asyncio.create_task(self._errors(program)),
                         asyncio.create_task(self._idle(program))]
        log.info("started MCP server for %s (%s)", msg.get("connection"), argv[0])
        await self.send({"type": "mcp.started", "session_id": session_id})

    async def _read(self, program: Program) -> None:
        assert program.proc.stdout is not None
        try:
            while line := await program.proc.stdout.readline():
                text = line.decode("utf-8", "replace").strip()
                if not text:
                    continue
                try:
                    message = json.loads(text)
                except ValueError:
                    log.debug("not JSON from the MCP server: %s", text[:200])
                    continue
                program.last_used = time.monotonic()
                await self.send({"type": "mcp.message", "session_id": program.session_id, "message": message})
        except (ValueError, asyncio.LimitOverrunError) as exc:
            program.stderr_tail += f"\n(a line too long: {exc})"
        code = await program.proc.wait()
        if self.programs.pop(program.session_id, None) is not None:
            tail = program.stderr_tail.strip()[-1000:]
            await self.send({"type": "mcp.exited", "session_id": program.session_id,
                             "error": f"exit {code}" + (f": {tail}" if tail else "")})
        for task in program.tasks:
            if task is not asyncio.current_task():
                task.cancel()

    async def _errors(self, program: Program) -> None:
        assert program.proc.stderr is not None
        while line := await program.proc.stderr.readline():
            program.stderr_tail = (program.stderr_tail + line.decode("utf-8", "replace"))[-4000:]

    async def _idle(self, program: Program) -> None:
        while True:
            await asyncio.sleep(min(10.0, program.idle_s))
            if time.monotonic() - program.last_used > program.idle_s:
                log.info("stopping idle MCP server %s", program.session_id[:8])
                await self._stop(program)
                return

    async def _stop(self, program: Program) -> None:
        with contextlib.suppress(ProcessLookupError):
            program.proc.terminate()
        try:
            await asyncio.wait_for(program.proc.wait(), 5)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                program.proc.kill()

    async def stop_all(self) -> None:
        for program in list(self.programs.values()):
            await self._stop(program)
