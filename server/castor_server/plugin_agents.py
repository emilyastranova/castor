"""Agent types from plugins: machines a system already manages (Salt minions, Puppet nodes), each a
Castor agent, run through a plugin.

A plugin's manifest declares `agent_types`; its code answers them with `ctx.agents.on(type, …)`:
`list()` the machines the system reaches, `run(job, io)` a job on one, and `io.signal` aborted on
cancel. Admins import the machines they want (Agents → Add agents from a plugin), each becoming an
`agents` row with `plugin_id`, `agent_type` and `machine_ref`; its tags are an admin's own plus the
ones the system gives it (`source_tags`, refreshed).

Each imported machine is a `MachineLink` that registers with the gateway the way an SSH host does
(a `PluginSocket` stands in for the WebSocket) and speaks the agent protocol: the dispatcher,
selectors, project limits, logs, cancel, artifacts, parsers and playbooks work unchanged. A
`job.run` becomes the plugin's `agent.run` request; its `io.log` lines come back as `agent.log`
calls and its files as `agent.artifact`, both checked against the machines the plugin owns. The
job's variables (env, files) are in that request only, never in its command line, and output
passes through the gateway, which masks secrets. A tool is the plugin's to provide: it gets the
whole tool (`kind: plugin`: image, build, host check and install) and decides.

`PluginAgents` keeps them connected: every heartbeat it heartbeats each link, and every type's
`refresh_minutes` (or when links are down) it asks the plugin for its machines, updating hostnames
and tags and connecting the ones that are there. When the plugin stops or crashes its links go
down (running jobs are lost, as when any agent disconnects) until it's back.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from . import models as m
from .config import settings
from .db import SessionLocal

log = logging.getLogger(__name__)

LIST_TIMEOUT = 60
MAX_JOB_SECONDS = 7 * 24 * 3600
ARTIFACT_MAX = 50 * 1024**2
LAST = 2_000_000_000  # Castor's closing line comes after every line the plugin numbered


class PluginAgentError(ValueError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


def capabilities(spec: Any) -> dict[str, Any]:
    """What a machine of this type runs: shell commands, containers if the type says so; tools are
    the plugin's to provide (`plugin`: the dispatcher hands it the whole tool)."""
    return {"runtimes": ["host", *(["docker"] if spec.containers else [])], "plugin": True, "tools": [], "tty": False,
            "services": False}


def clean_machine(raw: Any) -> dict[str, Any] | None:
    """A machine as the plugin listed it: {id, name, hostname, os, tags, facts}; None if unusable."""
    if not isinstance(raw, dict) or not str(raw.get("id") or "").strip():
        return None
    ident = str(raw["id"]).strip()[:255]
    tags = sorted({str(t).strip()[:80] for t in raw.get("tags") or [] if str(t).strip()})[:100]
    facts = raw.get("facts") if isinstance(raw.get("facts"), dict) else {}
    return {"id": ident, "name": str(raw.get("name") or ident).strip()[:128], "hostname": str(raw.get("hostname") or "")[:255],
            "os": str(raw.get("os") or "")[:255], "tags": tags,
            "facts": {str(k)[:80]: v for k, v in list(facts.items())[:60] if isinstance(v, (str, int, float, bool))}}


def relative_patterns(patterns: list[str]) -> list[str]:
    """Files to collect, relative to the job's directory ("artifacts/**", "scan.xml"), as the
    plugin's system sees them; never its .castor/ (variable files)."""
    out = []
    for p in patterns:
        text = str(p).strip()
        for prefix in ("{{ work }}", "{{work}}", "${CASTOR_WORK}", "$CASTOR_WORK", "/work"):
            if text.startswith(prefix):
                text = text[len(prefix):]
                break
        text = text.lstrip("/")
        if text and ".." not in text.split("/") and not text.startswith(".castor"):
            out.append(text)
    return [*dict.fromkeys(out)]


class PluginSocket:
    """What the gateway holds as a machine's WebSocket."""

    def __init__(self, link: MachineLink) -> None:
        self.link = link

    async def send_text(self, text: str) -> None:
        await self.link.from_server(json.loads(text))

    async def close(self, code: int = 1000) -> None:
        self.link.closed()


class MachineLink:
    """One imported machine, connected to the gateway while its plugin runs and lists it."""

    def __init__(self, agent_id: uuid.UUID, plugin_id: str, agent_type: str) -> None:
        self.agent_id = agent_id
        self.plugin_id = plugin_id
        self.agent_type = agent_type
        self.machine: dict[str, Any] = {}
        self.conn: Any = None
        self.jobs: dict[str, asyncio.Task] = {}
        self.seq: dict[str, int] = {}
        self.received: dict[str, int] = {}  # the plugin's log lines in, per job

    @property
    def connected(self) -> bool:
        from .orchestrator.gateway import gateway

        return self.conn is not None and gateway.connections.get(self.agent_id) is self.conn

    async def connect(self, machine: dict[str, Any], spec: Any) -> None:
        from .orchestrator.gateway import gateway

        self.machine = machine
        if self.connected:
            return
        hello = {"name": machine["name"], "hostname": machine["hostname"], "os": machine["os"], "username": "",
                 "version": f"plugin:{self.plugin_id}", "capabilities": capabilities(spec)}
        self.conn = await gateway._register(PluginSocket(self), hello, fixed_agent_id=self.agent_id)
        gateway._notify()

    async def disconnect(self) -> None:
        from .orchestrator.gateway import gateway

        for task in list(self.jobs.values()):
            task.cancel()
        self.jobs.clear()
        conn, self.conn = self.conn, None
        if conn is not None:
            await asyncio.shield(gateway._unregister(conn))

    def closed(self) -> None:
        """The gateway let go of it (forgotten, or replaced)."""
        for task in list(self.jobs.values()):
            task.cancel()
        self.conn = None

    async def heartbeat(self) -> None:
        from .orchestrator.gateway import gateway

        if self.connected:
            await gateway._on_message(self.conn, {"type": "heartbeat", "tools": []})

    async def to_gateway(self, message: dict[str, Any]) -> None:
        from .orchestrator.gateway import gateway

        if self.conn is not None:
            await gateway._on_message(self.conn, message)

    async def from_server(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        if kind == "job.run":
            job = msg.get("job") or {}
            jid = str(job.get("id"))
            if jid in self.jobs:
                return
            task = asyncio.ensure_future(self._run(job))
            self.jobs[jid] = task
            task.add_done_callback(lambda _t, j=jid: self.jobs.pop(j, None))
        elif kind == "job.cancel":
            await self._cancel(str(msg.get("job_id")))
        # hello.ok, tool.prepare, auth.revoked: nothing to do (the plugin prepares tools in run)

    async def log(self, job_id: str, stream: str, data: str, n: int | None = None) -> None:
        """A line of output. The plugin's are numbered (`n`, from 1) since they're handled
        concurrently; Castor's own come before them (0) or after (LAST)."""
        if n is None:
            seq = self.seq.get(job_id, 0)
            self.seq[job_id] = seq + 1
        else:
            seq = n
        await self.to_gateway({"type": "job.log", "job_id": job_id, "seq": seq, "stream": stream, "data": data})
        if n is not None:  # counted once it's stored: the job only finishes after its last line is in
            self.received[job_id] = self.received.get(job_id, 0) + 1

    async def _all_in(self, job_id: str, logged: int) -> None:
        """Wait (a little) for the plugin's last lines, so parsers read the whole output."""
        for _ in range(200):
            if self.received.get(job_id, 0) >= logged:
                return
            await asyncio.sleep(0.05)

    async def _cancel(self, job_id: str) -> None:
        from .plugin_runtime import host

        proc = host.running(self.plugin_id)
        if proc is not None and job_id in self.jobs:
            with contextlib.suppress(Exception):
                await proc.notify("cancel", {"run_id": job_id})

    async def _run(self, job: dict[str, Any]) -> None:
        from . import plugins
        from .plugin_runtime import PluginRuntimeError

        jid = str(job.get("id"))
        manager.jobs[jid] = self
        await self.to_gateway({"type": "job.started", "job_id": jid, "started_at": _now()})
        shown = job.get("display_command") or job.get("command") or ""
        await self.log(jid, "system", f"$ {shown}\n[castor] running on {self.machine.get('name') or self.agent_id} through the "
                                      f"{self.plugin_id} plugin ({self.agent_type})\n")
        payload: dict[str, Any]
        try:
            proc = await plugins.ensure_running(self.plugin_id)
            timeout = int(job.get("timeout_s") or MAX_JOB_SECONDS)
            result = await proc.request("agent.run", {
                "type": self.agent_type, "machine": self.machine, "job": {
                    "id": jid, "command": job.get("command") or "", "display_command": shown,
                    "runtime": job.get("runtime") or "host", "image": job.get("image"), "container_flags": job.get("container_flags") or [],
                    # Variable values: only in this message, for the plugin to hand the machine without
                    # putting them in a command line.
                    "env": job.get("env") or {}, "files": job.get("files") or [],
                    "timeout_s": job.get("timeout_s"), "tool": job.get("tool"),
                    "artifacts": relative_patterns((job.get("artifacts") or {}).get("collect") or []),
                },
            }, timeout=timeout + 60)
            result = result if isinstance(result, dict) else {}
            if isinstance(result.get("logged"), int):
                await self._all_in(jid, result["logged"])
            code = result.get("exit_code")
            payload = {"exit_code": int(code) if isinstance(code, (int, float)) and not isinstance(code, bool) else None}
            if result.get("error"):
                payload["error"] = str(result["error"])[:2000]
            elif payload["exit_code"] is None:
                payload["error"] = "the plugin didn't say how it ended (exit_code)"
            await self.log(jid, "system", f"\n[castor] {payload.get('error') or f'exit code {payload['exit_code']}'}\n", n=LAST)
        except asyncio.CancelledError:
            raise  # the link went down: the gateway marks the job lost
        except TimeoutError:
            await self._cancel(jid)
            payload = {"exit_code": None, "error": f"timed out after {job.get('timeout_s')}s"}
        except (plugins.PluginError, PluginRuntimeError) as exc:
            payload = {"exit_code": None, "error": f"{self.plugin_id}: {exc}"}
        finally:
            asyncio.get_running_loop().call_later(3, manager.jobs.pop, jid, None)
            self.seq.pop(jid, None)
            self.received.pop(jid, None)
        await self.to_gateway({"type": "job.finished", "job_id": jid, "finished_at": _now(), **payload})


class PluginAgents:
    def __init__(self) -> None:
        self.links: dict[uuid.UUID, MachineLink] = {}
        # (plugin, type) → {ok, message, checked_at, machines}: whether its system answers.
        self.status: dict[tuple[str, str], dict[str, Any]] = {}
        self.jobs: dict[str, MachineLink] = {}  # running job id → its machine, for agent.log / agent.artifact
        self._synced: dict[str, float] = {}
        self._task: asyncio.Task | None = None
        self._locks: dict[str, asyncio.Lock] = {}

    # ----- lifecycle -----

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="plugin-agents")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        for link in list(self.links.values()):
            await link.disconnect()
        self.links.clear()

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("plugin agents: tick failed")
            await asyncio.sleep(settings.agent_heartbeat_interval)

    async def tick(self) -> None:
        for link in list(self.links.values()):
            await link.heartbeat()
        async with SessionLocal() as db:
            rows = (await db.execute(select(m.Agent.plugin_id).where(m.Agent.plugin_id.is_not(None)).distinct())).scalars().all()
        for pid in rows:
            if self._due(pid):
                await self.sync(pid)

    def _due(self, plugin_id: str) -> bool:
        down = any(lk.plugin_id == plugin_id and not lk.connected for lk in self.links.values())
        last = self._synced.get(plugin_id)
        minutes = min((s.get("refresh_minutes", 10) for (p, _), s in self.status.items() if p == plugin_id), default=10)
        return last is None or time.monotonic() - last > minutes * 60 or (down and time.monotonic() - last > 60)

    def plugin_stopped(self, plugin_id: str) -> None:
        """Its process stopped or crashed: its machines go offline until it's back."""
        links = [lk for lk in self.links.values() if lk.plugin_id == plugin_id and lk.conn is not None]
        self._synced.pop(plugin_id, None)
        for lk in links:
            asyncio.ensure_future(lk.disconnect())

    # ----- talking to the plugin -----

    async def machines(self, plugin_id: str, agent_type: str) -> list[dict[str, Any]]:
        """The machines a plugin's agent type reaches now (starting the plugin if needed)."""
        from . import plugins
        from .plugin_runtime import PluginRuntimeError

        try:
            proc = await plugins.ensure_running(plugin_id)
            raw = await proc.request("agent.list", {"type": agent_type}, timeout=LIST_TIMEOUT)
        except TimeoutError as exc:
            self._note(plugin_id, agent_type, False, f"didn't list its machines within {LIST_TIMEOUT} s")
            raise PluginAgentError(f"{plugin_id} didn't list its machines within {LIST_TIMEOUT} s") from exc
        except (plugins.PluginError, PluginRuntimeError) as exc:
            self._note(plugin_id, agent_type, False, str(exc))
            raise PluginAgentError(str(exc)) from exc
        out = [x for x in (clean_machine(r) for r in (raw if isinstance(raw, list) else [])) if x]
        self._note(plugin_id, agent_type, True, "", machines=len(out))
        return out

    def _note(self, plugin_id: str, agent_type: str, ok: bool, message: str, **extra: Any) -> None:
        prev = self.status.get((plugin_id, agent_type), {})
        self.status[(plugin_id, agent_type)] = {**prev, "ok": ok, "message": message[:500], "checked_at": _now(), **extra}

    async def sync(self, plugin_id: str) -> None:
        """Ask the plugin for its machines and bring its imported agents in line: details, tags and
        connections. A plugin that's off (or in safe mode) has its machines offline."""
        from . import plugins
        from .plugins import manifest_of

        lock = self._locks.setdefault(plugin_id, asyncio.Lock())
        async with lock:
            self._synced[plugin_id] = time.monotonic()
            async with SessionLocal() as db:
                p = await db.get(m.Plugin, plugin_id)
                agents = list((await db.execute(select(m.Agent).where(m.Agent.plugin_id == plugin_id))).scalars())
                for a in agents:
                    db.expunge(a)
            specs = {t.name: t for t in manifest_of(p).agent_types} if p is not None else {}
            for (pid, name), _s in list(self.status.items()):
                if pid == plugin_id and name not in specs:
                    self.status.pop((pid, name), None)
            for name, spec in specs.items():
                self.status.setdefault((plugin_id, name), {"ok": None, "message": "", "checked_at": None})["refresh_minutes"] = spec.refresh_minutes
            usable = p is not None and p.enabled and not plugins.safe_mode() and bool(agents)
            if not usable:
                for a in agents:
                    if (lk := self.links.get(a.id)) is not None:
                        await lk.disconnect()
                if p is not None and not p.enabled:
                    for name in specs:
                        self._note(plugin_id, name, False, f"{p.name} is turned off")
                return
            for name, spec in specs.items():
                mine = [a for a in agents if a.agent_type == name]
                if not mine:
                    continue
                try:
                    found = {x["id"]: x for x in await self.machines(plugin_id, name)}
                except PluginAgentError:
                    for a in mine:
                        if (lk := self.links.get(a.id)) is not None:
                            await lk.disconnect()
                    continue
                for a in mine:
                    machine = found.get(a.machine_ref or "")
                    lk = self.links.get(a.id)
                    if machine is None:  # gone from the system (or not answering): offline
                        if lk is not None:
                            await lk.disconnect()
                        continue
                    await self._refresh(a.id, machine)
                    if lk is None:
                        lk = self.links[a.id] = MachineLink(a.id, plugin_id, name)
                    try:
                        await lk.connect({**machine, "name": a.name}, spec)
                    except Exception:
                        log.exception("plugin agents: connecting %s failed", a.name)
                    if lk.connected:  # its tags (an admin's and the system's) as they are now
                        async with SessionLocal() as db:
                            row = await db.get(m.Agent, a.id)
                            if row is not None:
                                lk.conn.tags = list(row.tags or [])

    async def _refresh(self, agent_id: uuid.UUID, machine: dict[str, Any]) -> None:
        async with SessionLocal() as db:
            a = await db.get(m.Agent, agent_id)
            if a is None:
                return
            old = set(a.source_tags or [])
            new = set(machine["tags"])
            if old != new:
                a.tags = sorted((set(a.tags or []) - old) | new)
                a.source_tags = sorted(new)
            a.hostname, a.os = machine["hostname"] or a.hostname, machine["os"] or a.os
            await db.commit()

    def forget(self, agent_id: uuid.UUID) -> None:
        lk = self.links.pop(agent_id, None)
        if lk is not None:
            asyncio.ensure_future(lk.disconnect())

    # ----- importing -----

    async def import_machines(self, db: Any, plugin: m.Plugin, agent_type: str, refs: list[str], *, tags: list[str],
                              project_ids: list[str] | None) -> list[m.Agent]:
        """Make agents of the chosen machines (the ones not imported yet). The caller commits, then
        `sync` connects them."""
        spec = next((t for t in plugins_manifest(plugin).agent_types if t.name == agent_type), None)
        if spec is None:
            raise PluginAgentError(f"{plugin.name} has no agent type {agent_type}")
        listed = {x["id"]: x for x in await self.machines(plugin.id, agent_type)}
        have = set((await db.execute(select(m.Agent.machine_ref).where(m.Agent.plugin_id == plugin.id,
                                                                         m.Agent.agent_type == agent_type))).scalars())
        taken = set((await db.execute(select(m.Agent.name))).scalars())
        out = []
        for ref in dict.fromkeys(refs):
            machine = listed.get(ref)
            if machine is None:
                raise PluginAgentError(f"{plugin.name} doesn't list a machine {ref}")
            if ref in have:
                continue
            name = machine["name"]
            if name in taken:
                name = f"{machine['name']} ({spec.label})"[:128]
                n = 2
                while name in taken:
                    name = f"{machine['name']} ({spec.label} {n})"[:128]
                    n += 1
            taken.add(name)
            a = m.Agent(name=name, hostname=machine["hostname"], os=machine["os"], plugin_id=plugin.id, agent_type=agent_type,
                        machine_ref=ref, source_tags=machine["tags"], tags=sorted(set(tags) | set(machine["tags"])),
                        project_ids=project_ids, max_concurrency=spec.max_concurrency, capabilities=capabilities(spec),
                        status=m.AgentStatus.offline)
            db.add(a)
            out.append(a)
        await db.flush()
        return out

    # ----- what the plugin sends -----

    def _owned(self, plugin_id: str, params: dict[str, Any]) -> MachineLink:
        jid = str(params.get("job_id") or "")
        lk = self.jobs.get(jid)
        if lk is None or lk.plugin_id != plugin_id:
            from .plugins import PluginError

            raise PluginError(f"no job {jid} of this plugin is running")
        return lk

    async def on_log(self, plugin_id: str, params: dict[str, Any]) -> None:
        lk = self._owned(plugin_id, params)
        stream = str(params.get("stream") or "stdout")
        n = params.get("n")
        await lk.log(str(params["job_id"]), stream if stream in ("stdout", "stderr") else "stdout",
                     str(params.get("data") or "")[:1024 * 1024], n=n if isinstance(n, int) and 0 < n < LAST else None)

    async def on_artifact(self, plugin_id: str, params: dict[str, Any]) -> dict[str, Any]:
        from . import artifacts
        from .plugins import PluginError

        lk = self._owned(plugin_id, params)
        name = str(params.get("name") or "").strip().lstrip("/")
        if not name or ".." in name.split("/"):
            raise PluginError("artifact: give it a file name")
        try:
            data = base64.b64decode(str(params.get("base64") or ""), validate=True)
        except ValueError as exc:
            raise PluginError("artifact: the file isn't valid base64") from exc
        if len(data) > min(ARTIFACT_MAX, settings.artifact_max_bytes):
            raise PluginError(f"artifact: {name} is too large")

        async def chunks():
            yield data

        async with SessionLocal() as db:
            job = await db.get(m.Job, uuid.UUID(str(params["job_id"])))
            if job is None or job.agent_id != lk.agent_id:
                raise PluginError("artifact: not this machine's job")
            try:
                out = await artifacts.store(db, job, name, str(params.get("content_type") or "") or None, chunks())
            except artifacts.ArtifactRefused as exc:
                raise PluginError(f"artifact: {exc}") from exc
        return {"id": str(out.id), "filename": out.filename, "size": out.size}

    def status_of(self, agent: m.Agent) -> dict[str, Any] | None:
        if not agent.plugin_id:
            return None
        s = self.status.get((agent.plugin_id, agent.agent_type or ""), {})
        return {"ok": s.get("ok"), "message": s.get("message") or "", "checked_at": s.get("checked_at")}


def plugins_manifest(p: m.Plugin) -> Any:
    from .plugins import manifest_of

    return manifest_of(p)


manager = PluginAgents()
