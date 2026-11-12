"""Running plugins (docs/PLUGINS.md): one Deno process per enabled plugin, talking JSON-RPC 2.0
over its stdin/stdout, one message per line.

A sandboxed plugin starts with none of Deno's permissions (no files, network, environment,
subprocesses or FFI); it reaches Castor only through the SDK (plugin_sdk/mod.ts), whose calls
come back here and are answered by plugins.handle_call as the plugin's own service account.
Static imports are the one thing Deno loads without a permission check, so before every start
`deno info` lists the plugin's module graph and Castor refuses one that reaches outside the
plugin's folder and the SDK. Plugin files are never changed after they're unpacked.

A plugin that crashes is started again the next time it's needed; three crashes in a minute
stop it until an admin starts it again."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from .config import settings

log = logging.getLogger(__name__)

SDK_DIR = Path(__file__).resolve().parent / "plugin_sdk"
SDK = SDK_DIR / "mod.ts"
BASE_FLAGS = ["--no-remote", "--no-npm", "--no-config", "--no-lock", "--quiet"]
MEMORY_MB = 512
START_TIMEOUT = 20.0
LINE_LIMIT = 32 * 1024**2
CRASHES = 3
CRASH_WINDOW = 60.0

# (plugin id, method, params, process) → result; raises to answer with an error.
CallHandler = Callable[[str, str, dict[str, Any], "PluginProcess"], Awaitable[Any]]


class PluginRuntimeError(RuntimeError):
    pass


def deno_bin() -> str:
    """The Deno binary: the `deno` Python package's (a server dependency), else one on PATH."""
    try:
        import deno

        found = deno.find_deno_bin()
        if found:
            return str(found)
    except (ImportError, FileNotFoundError):
        pass
    found = shutil.which("deno")
    if not found:
        raise PluginRuntimeError("Deno isn't installed, so plugins can't run (pip install deno)")
    return found


def cache_dir(plugin_id: str) -> Path:
    d = settings.data_dir / "plugins" / ".cache" / plugin_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def import_map(plugin_id: str) -> Path:
    """Where `@castor/plugin` comes from: the SDK this Castor ships."""
    path = cache_dir(plugin_id) / "import_map.json"
    uri = SDK.as_uri()
    path.write_text(json.dumps({"imports": {"@castor/plugin": uri, "jsr:@castor/plugin": uri}}))
    return path


def _env(plugin_id: str) -> dict[str, str]:
    """No secrets in the environment, even though a sandboxed plugin can't read it."""
    cache = cache_dir(plugin_id)
    return {"DENO_DIR": str(cache / "deno"), "HOME": str(cache), "NO_COLOR": "1", "DENO_NO_UPDATE_CHECK": "1",
            "PATH": os.defpath}


def _inside(path: Path, roots: list[Path]) -> bool:
    return any(path == r or r in path.parents for r in roots)


async def check_graph(plugin_id: str, folder: Path, main: str) -> None:
    """Refuse a plugin whose imports reach outside its folder and the SDK (or don't resolve)."""
    proc = await asyncio.create_subprocess_exec(
        deno_bin(), "info", "--json", *BASE_FLAGS, f"--import-map={import_map(plugin_id)}", main,
        cwd=folder, env=_env(plugin_id), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=60)
    except TimeoutError:
        proc.kill()
        raise PluginRuntimeError("Reading the plugin's modules took too long") from None
    if proc.returncode:
        raise PluginRuntimeError(f"The plugin's modules don't load: {err.decode(errors='replace').strip()[-800:]}")
    graph = json.loads(out or b"{}")
    roots = [folder.resolve(), SDK_DIR.resolve()]
    bad, broken = [], []
    for module in graph.get("modules", []):
        spec = str(module.get("specifier", ""))
        if module.get("error"):
            broken.append(f"{spec}: {module['error']}")
            continue
        if not spec.startswith("file://"):
            bad.append(spec)
            continue
        if not _inside(Path(spec.removeprefix("file://")).resolve(), roots):
            bad.append(spec)
    if broken:
        raise PluginRuntimeError("Some of the plugin's imports don't resolve: " + "; ".join(broken[:5]))
    if bad:
        raise PluginRuntimeError("The plugin imports files from outside its own folder, which a sandboxed plugin may not: "
                                 + ", ".join(bad[:5]))


class PluginProcess:
    """One running plugin."""

    def __init__(self, plugin_id: str, folder: Path, main: str, on_call: CallHandler, on_exit: Callable[[PluginProcess, int], None],
                 network: list[str] | None = None) -> None:
        self.plugin_id = plugin_id
        self.folder = folder
        self.main = main
        self.on_call = on_call
        self.on_exit = on_exit
        self.network = network or []
        self.proc: asyncio.subprocess.Process | None = None
        self.pending: dict[int, asyncio.Future[Any]] = {}
        self.next_id = 1
        self.write_lock = asyncio.Lock()
        self.tasks: set[asyncio.Task[Any]] = set()
        self.stopping = False
        self.token = ""  # its API token, for this run of the process only
        self.files: dict[str, Path] = {}  # run id → the file the action was given

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    def command(self) -> list[str]:
        cmd = [deno_bin(), "run", "--no-prompt", *BASE_FLAGS, f"--import-map={import_map(self.plugin_id)}",
               f"--v8-flags=--max-old-space-size={MEMORY_MB}"]
        if self.network:
            cmd.append("--allow-net=" + ",".join(self.network))
        return [*cmd, self.main]

    async def start(self) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            *self.command(), cwd=self.folder, env=_env(self.plugin_id), limit=LINE_LIMIT,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        self._spawn(self._read_stdout())
        self._spawn(self._read_stderr())
        self._spawn(self._wait())

    def _spawn(self, coro: Awaitable[Any]) -> None:
        task = asyncio.ensure_future(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _send(self, message: dict[str, Any]) -> None:
        if not self.alive or self.proc is None or self.proc.stdin is None:
            raise PluginRuntimeError("The plugin isn't running")
        data = (json.dumps({"jsonrpc": "2.0", **message}, default=str) + "\n").encode()
        async with self.write_lock:
            self.proc.stdin.write(data)
            await self.proc.stdin.drain()

    async def request(self, method: str, params: dict[str, Any], timeout: float) -> Any:
        mid = self.next_id
        self.next_id += 1
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self.pending[mid] = fut
        try:
            await self._send({"id": mid, "method": method, "params": params})
            return await asyncio.wait_for(fut, timeout=timeout)
        except TimeoutError:
            raise PluginRuntimeError(f"The plugin didn't answer {method} within {int(timeout)} s") from None
        finally:
            self.pending.pop(mid, None)

    async def _read_stdout(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        from .plugins import plugin_log

        while True:
            try:
                line = await self.proc.stdout.readline()
            except ValueError:  # a line over the limit
                await plugin_log(self.plugin_id, "error", "The plugin wrote a message over 32 MB; it was dropped")
                continue
            if not line:
                return
            text = line.decode("utf-8", "replace").strip()
            if not text:
                continue
            try:
                msg = json.loads(text)
            except json.JSONDecodeError:
                await plugin_log(self.plugin_id, "info", text[:20000])  # printed by something other than the SDK
                continue
            if not isinstance(msg, dict):
                continue
            if "method" in msg:
                self._spawn(self._answer(msg))
            elif (fut := self.pending.get(msg.get("id"))) is not None and not fut.done():
                if "error" in msg:
                    fut.set_exception(PluginRuntimeError(str((msg.get("error") or {}).get("message") or "The plugin failed")))
                else:
                    fut.set_result(msg.get("result"))

    async def _answer(self, msg: dict[str, Any]) -> None:
        mid = msg.get("id")
        try:
            result = await self.on_call(self.plugin_id, str(msg["method"]), msg.get("params") or {}, self)
            if mid is not None:
                await self._send({"id": mid, "result": result})
        except Exception as exc:  # noqa: BLE001 - every failure goes back to the plugin
            if mid is not None:
                with contextlib.suppress(PluginRuntimeError):
                    await self._send({"id": mid, "error": {"code": -32000, "message": str(exc) or exc.__class__.__name__}})

    async def _read_stderr(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        from .plugins import plugin_log

        while line := await self.proc.stderr.readline():
            await plugin_log(self.plugin_id, "error", line.decode("utf-8", "replace").rstrip()[:20000])

    async def _wait(self) -> None:
        assert self.proc is not None
        code = await self.proc.wait()
        for fut in self.pending.values():
            if not fut.done():
                fut.set_exception(PluginRuntimeError(f"The plugin stopped (exit code {code})"))
        self.on_exit(self, code)

    async def stop(self) -> None:
        self.stopping = True
        if not self.alive or self.proc is None:
            return
        with contextlib.suppress(Exception):
            await self.request("deactivate", {}, timeout=3)
        with contextlib.suppress(Exception):
            if self.proc.stdin:
                self.proc.stdin.close()
            await asyncio.wait_for(self.proc.wait(), timeout=3)
        if self.alive:
            self.proc.kill()
            with contextlib.suppress(Exception):
                await self.proc.wait()


class PluginHost:
    """The running plugins. `ensure` starts one when it's needed; crashes are counted."""

    def __init__(self) -> None:
        self.procs: dict[str, PluginProcess] = {}
        self.locks: dict[str, asyncio.Lock] = {}
        self.crashes: dict[str, list[float]] = {}

    def lock(self, plugin_id: str) -> asyncio.Lock:
        return self.locks.setdefault(plugin_id, asyncio.Lock())

    def running(self, plugin_id: str) -> PluginProcess | None:
        p = self.procs.get(plugin_id)
        return p if p is not None and p.alive else None

    def crashed_too_often(self, plugin_id: str) -> bool:
        recent = [t for t in self.crashes.get(plugin_id, []) if time.monotonic() - t < CRASH_WINDOW]
        self.crashes[plugin_id] = recent
        return len(recent) >= CRASHES

    def forget_crashes(self, plugin_id: str) -> None:
        self.crashes.pop(plugin_id, None)

    def exited(self, proc: PluginProcess, code: int) -> None:
        if self.procs.get(proc.plugin_id) is proc:
            self.procs.pop(proc.plugin_id, None)
        if proc.stopping:
            return
        self.crashes.setdefault(proc.plugin_id, []).append(time.monotonic())
        from .plugins import on_crash

        asyncio.ensure_future(on_crash(proc.plugin_id, code, self.crashed_too_often(proc.plugin_id)))

    async def stop(self, plugin_id: str) -> None:
        proc = self.procs.pop(plugin_id, None)
        if proc is not None:
            await proc.stop()

    async def stop_all(self) -> None:
        await asyncio.gather(*(self.stop(pid) for pid in list(self.procs)), return_exceptions=True)


host = PluginHost()
