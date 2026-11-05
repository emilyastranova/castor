"""Getting a job's tool ready before it runs (docs/PHASE3.md, 3a).

The server sends how this agent should get the tool:
- image: pull it if it isn't here;
- build: build the recipe once as castor-tools/<id>:<hash> (cached by the container
  runtime, so the next job reuses it);
- host: check it's installed; install it only if the agent allows host installs.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

log = logging.getLogger("castor.agent.tools")
Sink = Callable[[str, str], Awaitable[None]]


async def _quiet(argv: list[str], cwd: str | None = None) -> int:
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=cwd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    return await proc.wait()


async def _stream(argv: list[str], sink: Sink, cwd: str | None = None, env: dict[str, str] | None = None) -> int:
    """Run a command, sending its output (stdout and stderr) to the job's console."""
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=cwd, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    assert proc.stdout is not None
    while line := await proc.stdout.readline():
        await sink("system", line.decode("utf-8", errors="replace"))
    return await proc.wait()


class ToolPreparer:
    def __init__(self, container_runtime: str | None, allow_host_install: bool, workdir: str | None) -> None:
        self.runtime = container_runtime
        self.allow_host_install = allow_host_install
        self.workdir = workdir
        self._locks: dict[str, asyncio.Lock] = {}
        self.cached: list[str] = []

    async def refresh_cached(self) -> list[str]:
        """The tool builds this agent has (images labelled castor.tool)."""
        if not self.runtime:
            return []
        proc = await asyncio.create_subprocess_exec(
            self.runtime, "image", "ls", "--filter", "label=castor.tool",
            "--format", "{{.Repository}}:{{.Tag}}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        # podman prefixes local images with localhost/; the server knows them without it.
        tags = {t.removeprefix("localhost/") for t in out.decode().split() if ":" in t and "<none>" not in t}
        self.cached = sorted(tags)
        return self.cached

    async def _have_image(self, ref: str) -> bool:
        assert self.runtime
        return await _quiet([self.runtime, "image", "inspect", ref]) == 0

    async def prepare(self, tool: dict[str, Any], sink: Sink, cwd: str) -> str | None:
        """Make the tool available. Returns an error message, or None when it's ready."""
        kind = tool.get("kind")
        name = tool.get("name") or tool.get("id")
        try:
            if kind == "image":
                return await self._image(str(tool["ref"]), name, sink)
            if kind == "build":
                return await self._build(tool, name, sink)
            if kind == "host":
                return await self._host(tool, name, sink, cwd)
        except (OSError, KeyError) as exc:
            return f"preparing {name} failed: {exc}"
        return f"unknown way to get {name}: {kind}"

    async def _image(self, ref: str, name: str, sink: Sink) -> str | None:
        if not self.runtime:
            return f"{name} needs a container runtime, and this agent has none"
        if await self._have_image(ref):
            return None
        await sink("system", f"[castor] preparing {name}: pulling {ref}\n")
        if await _stream([self.runtime, "pull", ref], sink) != 0:
            return f"couldn't pull {ref} for {name}"
        return None

    async def _build(self, tool: dict[str, Any], name: str, sink: Sink) -> str | None:
        if not self.runtime:
            return f"{name} needs a container runtime, and this agent has none"
        ref = str(tool["ref"])
        files = tool.get("files") or {}
        if "Dockerfile" not in files:
            return f"the recipe for {name} has no Dockerfile"
        # One build per tag at a time; jobs waiting on the same tool reuse the result.
        async with self._locks.setdefault(ref, asyncio.Lock()):
            if await self._have_image(ref):
                return None
            await sink("system", f"[castor] preparing {name}: building {ref} (first time on this agent; later jobs reuse it)\n")
            base = Path(self.workdir) if self.workdir else Path(tempfile.gettempdir()) / "castor-agent"
            base.mkdir(parents=True, exist_ok=True)
            context = Path(tempfile.mkdtemp(prefix="tool-", dir=base))
            try:
                for filename, text in files.items():
                    if not re.fullmatch(r"[A-Za-z0-9._-]+", filename):
                        return f"refusing a recipe file named {filename!r}"
                    (context / filename).write_text(str(text))
                env = {**os.environ, "BUILDKIT_PROGRESS": "plain"}
                code = await _stream([self.runtime, "build", "-t", ref, str(context)], sink, env=env)
            finally:
                shutil.rmtree(context, ignore_errors=True)
            if code != 0:
                return f"building {name} failed (exit {code}); see the output above, then fix the tool's recipe"
            await self.refresh_cached()
            await sink("system", f"[castor] {name} is ready\n")
        return None

    async def _host(self, tool: dict[str, Any], name: str, sink: Sink, cwd: str) -> str | None:
        check = str(tool.get("check") or "")
        if check and await _quiet(["/bin/sh", "-c", check], cwd=cwd) == 0:
            return None
        install = str(tool.get("install") or "")
        if not install or not self.allow_host_install:
            hint = tool.get("install_hint") or install
            how = f" Install it with: {hint}" if hint else ""
            return f"{name} isn't installed on this agent's host.{how} (or start the agent with --allow-host-install)"
        await sink("system", f"[castor] preparing {name}: installing on the host\n$ {install}\n")
        if await _stream(["/bin/sh", "-c", install], sink, cwd=cwd) != 0:
            return f"installing {name} on the host failed"
        if check and await _quiet(["/bin/sh", "-c", check], cwd=cwd) != 0:
            return f"{name} still isn't available after installing (check: {check})"
        return None
