"""Plugins (docs/PLUGINS.md), phase A: installing, updating and removing them, their settings and
projects, the service account each acts as, starting them (plugin_runtime.py), answering what
they ask Castor for, and running their actions.

A plugin acts only as its own service account (a user with `plugin_id` set: never signs in, isn't
listed among people, sees only the projects the plugin is enabled in), through the REST API, with
an API token limited to the scopes an admin approved. The token is made fresh each time the
plugin starts and lives only in memory."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import mimetypes
import re
import secrets
import time
import uuid
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import api_tokens, assets, plugin_events, plugin_package, vault
from . import models as m
from .config import settings as config
from .db import SessionLocal
from .events import bus
from .models import utcnow
from .plugin_manifest import Manifest, version_key
from .plugin_runtime import START_TIMEOUT, PluginProcess, PluginRuntimeError, check_graph, host

log = logging.getLogger(__name__)

APP: Any = None  # the ASGI app, set at startup (app.py): plugins call the API in-process
CASTOR_VERSION = "0.1.0"
LOG_KEEP = 2000
STORAGE_VALUE_MAX = 1024**2
# Calls to Castor's API per plugin per minute: enough for any import, not enough to swamp Castor.
API_PER_MINUTE = 1200
UPLOAD_MAX = 20 * 1024**2  # a file a plugin uploads (ctx.api.upload)
_calls: dict[str, deque[float]] = {}


class PluginError(ValueError):
    pass


class PluginBlocked(PluginError):
    """Not allowed to run now: safe mode, or an unrestricted plugin in restricted mode."""


# ---------- records ----------


def manifest_of(p: m.Plugin) -> Manifest:
    return Manifest.model_validate(p.manifest)


def folder_of(p: m.Plugin) -> Path:
    return plugin_package.root() / p.path


def grants(man: Manifest) -> dict[str, Any]:
    # "runtime" lists "unrestricted" when it is, so an update that becomes unrestricted asks again.
    return {"permissions": sorted(man.permissions), "network": sorted(man.network), "events": sorted(man.events),
            "runtime": ["unrestricted"] if man.runtime == "unrestricted" else []}


# ---------- restricted and safe mode ----------

def safe_mode() -> bool:
    """CASTOR_PLUGINS_SAFE_MODE: Castor started with every plugin stopped, to recover from a bad one."""
    return config.plugins_safe_mode


def restricted_locked() -> bool:
    return config.plugins_restricted


async def restricted(db: AsyncSession) -> bool:
    """While on, unrestricted plugins can't be installed, turned on or run."""
    from . import app_settings

    return restricted_locked() or (await app_settings.load(db)).plugins_restricted


def check_runtime(man: Manifest, is_restricted: bool) -> None:
    if man.runtime == "unrestricted" and is_restricted:
        raise PluginBlocked(f"{man.name} is an unrestricted plugin, and restricted mode is on"
                          + (" (CASTOR_PLUGINS_RESTRICTED)" if restricted_locked() else "; an admin can turn it off in Settings → Plugins"))


async def stop_unrestricted() -> None:
    """Restricted mode was turned on: stop the unrestricted plugins that are running."""
    async with SessionLocal() as db:
        ids = list((await db.execute(select(m.Plugin.id).where(m.Plugin.runtime == "unrestricted"))).scalars())
    for pid in ids:
        if host.running(pid) is not None:
            await host.stop(pid)
        await _status(pid, "stopped", "Restricted mode is on")


def new_grants(old: dict[str, Any], man: Manifest) -> dict[str, list[str]]:
    """What an update asks for that the admin hasn't approved before."""
    want = grants(man)
    return {k: sorted(set(v) - set(old.get(k) or [])) for k, v in want.items() if set(v) - set(old.get(k) or [])}


async def project_ids(db: AsyncSession, plugin_id: str) -> list[uuid.UUID]:
    return list((await db.execute(select(m.PluginProject.project_id).where(m.PluginProject.plugin_id == plugin_id))).scalars())


async def enabled_in(db: AsyncSession, p: m.Plugin, project_id: uuid.UUID) -> bool:
    if p.all_projects:
        return True
    return (await db.get(m.PluginProject, (p.id, project_id))) is not None


async def _service_account(db: AsyncSession, p: m.Plugin, man: Manifest) -> m.User:
    """The user a plugin acts as: made once, kept (disabled) after it's removed so what it did
    keeps its name, and woken again if it comes back."""
    from .transfer import _unusable_password

    user = (await db.execute(select(m.User).where(m.User.plugin_id == p.id))).scalars().first()
    role = m.UserRole.operator if any(api_tokens.SCOPES[s].role != m.UserRole.viewer for s in man.permissions) else m.UserRole.viewer
    if user is None:
        name = f"plugin-{p.id}"[:64]
        n = 2
        while (await db.execute(select(m.User.id).where(func.lower(m.User.username) == name.lower()))).first():
            name = f"plugin-{p.id}"[:60] + f"-{n}"
            n += 1
        user = m.User(username=name, display_name=man.name[:128], password_hash=_unusable_password(), role=role, plugin_id=p.id)
        db.add(user)
        await db.flush()
    user.display_name = man.name[:128]
    user.role = role
    user.disabled_at = None
    return user


async def _fresh_token(db: AsyncSession, p: m.Plugin) -> str:
    """Its token for this start: the approved scopes, nothing else; the last one goes."""
    await db.execute(delete(m.ApiToken).where(m.ApiToken.user_id == p.user_id))
    token, digest, prefix = api_tokens.new_token()
    db.add(m.ApiToken(user_id=p.user_id, name=f"Plugin {p.id}", token_hash=digest, prefix=prefix,
                      scopes=list((p.granted or {}).get("permissions") or [])))
    await db.flush()
    return token


def _open(plain: dict[str, Any] | None, sealed: dict[str, str] | None) -> dict[str, Any]:
    out = dict(plain or {})
    for name, value in (sealed or {}).items():
        with contextlib.suppress(Exception):
            out[name] = vault.decrypt(value).decode()
    return out


def per_user_names(p: m.Plugin, man: Manifest | None = None) -> set[str]:
    """The settings each person sets for themselves: those the admin chose, and those the
    manifest says always are."""
    man = man or manifest_of(p)
    chosen = set(p.per_user or [])
    return {s.name for s in man.settings if s.per_user == "required" or (s.name in chosen and s.per_user != "never")}


def settings_for(p: m.Plugin) -> dict[str, Any]:
    """What the plugin gets at activate (`ctx.settings`): the shared settings, secrets decrypted.
    Per-person ones come with each action and event instead."""
    mine = per_user_names(p)
    return {k: v for k, v in _open(p.settings, p.secrets).items() if k not in mine}


async def settings_of(db: AsyncSession, p: m.Plugin, user_id: uuid.UUID | None) -> dict[str, Any]:
    """The settings an action or event runs with: the shared ones plus that person's own."""
    out = settings_for(p)
    if user_id is not None and (mine := per_user_names(p)):
        row = await db.get(m.PluginUserSetting, (p.id, user_id))
        if row is not None:
            out.update({k: v for k, v in _open(row.settings, row.secrets).items() if k in mine})
    return out


async def missing_for(db: AsyncSession, p: m.Plugin, user_id: uuid.UUID | None) -> list[str]:
    """Per-person settings someone hasn't set but must (labels)."""
    man = manifest_of(p)
    mine = per_user_names(p, man)
    required = [s for s in man.settings if s.name in mine and s.required]
    if not required:
        return []
    row = await db.get(m.PluginUserSetting, (p.id, user_id)) if user_id else None
    have = {**((row.settings if row else None) or {}), **{k: True for k in ((row.secrets if row else None) or {})}}
    return [s.label for s in required if have.get(s.name) in (None, "")]


def set_per_user(man: Manifest, p: m.Plugin, names: list[str]) -> None:
    """Which settings are each person's own; their shared values go."""
    specs = {s.name: s for s in man.settings}
    for name in names:
        if name not in specs:
            raise PluginError(f"per_user.{name}: this plugin has no such setting")
        if specs[name].per_user == "never":
            raise PluginError(f"per_user.{name}: {specs[name].label} is one value for everyone")
    p.per_user = sorted(set(names) | {s.name for s in man.settings if s.per_user == "required"})
    p.settings = {k: v for k, v in (p.settings or {}).items() if k not in p.per_user}
    p.secrets = {k: v for k, v in (p.secrets or {}).items() if k not in p.per_user}


def check_settings(man: Manifest, values: dict[str, Any], secrets_in: dict[str, Any] | None,
                   current: dict[str, Any] | None = None, sealed: dict[str, str] | None = None,
                   names: set[str] | None = None) -> tuple[dict[str, Any], dict[str, str]]:
    """Plain settings and vault-sealed secrets, checked against the manifest (and limited to
    `names`, when given). A secret left as None keeps its value; "" clears it."""
    plain = dict(current or {})
    kept = dict(sealed or {})
    specs = {s.name: s for s in man.settings if names is None or s.name in names}
    for name, value in (values or {}).items():
        spec = specs.get(name)
        if spec is None:
            raise PluginError(f"settings.{name}: not a setting you can set here")
        if spec.type == "secret":
            raise PluginError(f"settings.{name}: a secret; send it in secrets")
        if spec.type == "bool":
            value = bool(value)
        elif spec.type == "number":
            try:
                value = float(value) if "." in str(value) else int(value)
            except (TypeError, ValueError):
                raise PluginError(f"settings.{name}: a number") from None
        elif spec.type == "select":
            if value not in spec.options:
                raise PluginError(f"settings.{name}: one of {', '.join(spec.options)}")
        else:
            value = str(value)[:5000]
        plain[name] = value
    for name, value in (secrets_in or {}).items():
        spec = specs.get(name)
        if spec is None or spec.type != "secret":
            raise PluginError(f"secrets.{name}: not a secret you can set here")
        if value is None:
            continue
        if value == "":
            kept.pop(name, None)
        else:
            kept[name] = vault.encrypt(str(value)[:10000].encode())
    for spec in specs.values():
        if spec.type != "secret" and spec.name not in plain and spec.default is not None:
            plain[spec.name] = spec.default
    plain = {k: v for k, v in plain.items() if k in specs}
    kept = {k: v for k, v in kept.items() if k in specs}
    return plain, kept


def missing_settings(man: Manifest, p: m.Plugin) -> list[str]:
    """Shared settings it needs and doesn't have (per-person ones are each person's to set)."""
    mine = per_user_names(p, man)
    return [s.label for s in man.settings if s.required and s.name not in mine
            and not (p.secrets or {}).get(s.name) and (p.settings or {}).get(s.name) in (None, "")]


# ---------- installing ----------


async def install(db: AsyncSession, user: m.User, pkg: plugin_package.Package, *, source: str,
                  project_ids_in: list[uuid.UUID], all_projects: bool, values: dict[str, Any] | None = None,
                  secrets_in: dict[str, Any] | None = None, per_user: list[str] | None = None) -> m.Plugin:
    """A new plugin, or a newer (or different) release of one already here. The caller has the
    admin's approval of what the manifest asks for. Commits; the plugin starts in the background."""
    from .services import actor_of, record_activity

    man = pkg.manifest
    if problems := man.not_yet() + type_conflicts(man):
        raise PluginError("; ".join(problems))
    check_runtime(man, await restricted(db))
    existing = await db.get(m.Plugin, man.id)
    folder = plugin_package.unpack(pkg)
    rel = str(folder.relative_to(plugin_package.root()))
    if existing is not None:
        if existing.checksum == pkg.checksum:
            raise PluginError(f"{man.name} {man.version} is already installed")
        await host.stop(existing.id)
        old = manifest_of(existing)
        existing.name, existing.version, existing.publisher = man.name, man.version, man.publisher
        existing.description, existing.manifest, existing.runtime = man.description, man.model_dump(mode="json"), man.runtime
        existing.source, existing.checksum, existing.path, existing.granted = source, pkg.checksum, rel, grants(man)
        set_per_user(man, existing, [n for n in (existing.per_user or []) if n in {s.name for s in man.settings if s.per_user != "never"}])
        shared = {s.name for s in man.settings} - per_user_names(existing, man)
        existing.settings, existing.secrets = check_settings(man, values or {}, secrets_in, existing.settings, existing.secrets, shared)
        existing.status, existing.status_message = "stopped", ""
        await _service_account(db, existing, man)
        verb = "Updated" if version_key(man.version) >= version_key(old.version) else "Changed"
        await record_activity(db, type="plugin.updated", message=f"{verb} the plugin {man.name} to {man.version}", project_id=None,
                              payload={"id": man.id, "from": old.version, "to": man.version}, **actor_of(user))
        p = existing
    else:
        p = m.Plugin(id=man.id, name=man.name, version=man.version, publisher=man.publisher, description=man.description,
                     manifest=man.model_dump(mode="json"), runtime=man.runtime, source=source, checksum=pkg.checksum,
                     path=rel, enabled=True, all_projects=all_projects, granted=grants(man), installed_by=user.id)
        set_per_user(man, p, per_user or [])
        p.settings, p.secrets = check_settings(man, values or {}, secrets_in, names={s.name for s in man.settings} - set(p.per_user))
        db.add(p)
        await db.flush()
        account = await _service_account(db, p, man)
        p.user_id = account.id
        await record_activity(db, type="plugin.installed", message=f"Installed the plugin {man.name} {man.version}", project_id=None,
                              payload={"id": man.id, "permissions": man.permissions, "runtime": man.runtime}, **actor_of(user))
    if existing is None or project_ids_in or all_projects != p.all_projects:
        await set_projects(db, p, project_ids_in, all_projects)
    await db.commit()
    register_types(p.id, man)
    host.forget_crashes(p.id)
    bus.publish("plugins.changed", None, {"id": p.id})
    _soon(start(p.id))
    with contextlib.suppress(Exception):
        plugin_package.prune(p.id, folder)
    return p


def type_conflicts(man: Manifest) -> list[str]:
    """Asset types and relationship kinds another installed plugin already adds."""
    return assets.conflicts(man.id, [t.name for t in man.asset_types], [k.name for k in man.link_kinds if k.forward])


def register_types(plugin_id: str, man: Manifest) -> None:
    assets.register_plugin(plugin_id, [t.model_dump(mode="json") for t in man.asset_types],
                           [k.model_dump(mode="json") for k in man.link_kinds])


async def load_types() -> None:
    """At startup: every installed plugin's asset types and relationship kinds (on or off, and in
    safe mode too: they describe data that's there, and run nothing)."""
    async with SessionLocal() as db:
        for p in (await db.execute(select(m.Plugin))).scalars():
            try:
                register_types(p.id, manifest_of(p))
            except Exception:  # a manifest this version can't read: the plugin shows its own error
                log.exception("Plugin %s: couldn't read its asset types", p.id)


async def set_projects(db: AsyncSession, p: m.Plugin, ids: list[uuid.UUID], all_projects: bool) -> None:
    p.all_projects = all_projects
    await db.execute(delete(m.PluginProject).where(m.PluginProject.plugin_id == p.id))
    if not all_projects:
        for pid in dict.fromkeys(ids):
            if await db.get(m.Project, pid) is None:
                raise PluginError(f"No project {pid}")
            db.add(m.PluginProject(plugin_id=p.id, project_id=pid))
    await db.flush()


async def uninstall(db: AsyncSession, user: m.User, p: m.Plugin) -> None:
    """Stop it and remove it; its service account is disabled (what it did keeps its name), and
    what it recorded in projects stays."""
    from .services import actor_of, record_activity

    await host.stop(p.id)
    plugin_events.queues.drop(p.id)
    if p.user_id:
        account = await db.get(m.User, p.user_id)
        if account is not None:
            account.disabled_at = datetime.now(UTC)
        await db.execute(delete(m.ApiToken).where(m.ApiToken.user_id == p.user_id))
    name = p.name
    await db.delete(p)
    await record_activity(db, type="plugin.removed", message=f"Removed the plugin {name}", project_id=None,
                          payload={"id": p.id}, **actor_of(user))
    await db.commit()
    assets.unregister_plugin(p.id)
    plugin_package.remove(p.id)
    bus.publish("plugins.changed", None, {"id": p.id, "removed": True})


# ---------- running ----------

_background: set[asyncio.Task[Any]] = set()


def _soon(coro: Any) -> None:
    task = asyncio.ensure_future(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


async def _status(plugin_id: str, status: str, message: str = "") -> None:
    async with SessionLocal() as db:
        p = await db.get(m.Plugin, plugin_id)
        if p is None:
            return
        p.status, p.status_message = status, message[:4000]
        await db.commit()
    bus.publish("plugins.changed", None, {"id": plugin_id, "status": status})


async def start(plugin_id: str, *, force: bool = False) -> PluginProcess | None:
    """Start a plugin if it isn't running (quietly: problems go to its status and log)."""
    try:
        return await ensure_running(plugin_id, force=force)
    except (PluginError, PluginRuntimeError) as exc:
        log.info("plugin %s didn't start: %s", plugin_id, exc)
        if isinstance(exc, PluginBlocked):
            await _status(plugin_id, "stopped", str(exc))
        return None


async def ensure_running(plugin_id: str, *, force: bool = False) -> PluginProcess:
    """The plugin's process, started if needed. Raises PluginError/PluginRuntimeError with why not."""
    async with host.lock(plugin_id):
        if (running := host.running(plugin_id)) is not None:
            return running
        async with SessionLocal() as db:
            p = await db.get(m.Plugin, plugin_id)
            if p is None:
                raise PluginError("No such plugin")
            if safe_mode():
                raise PluginBlocked("Castor is in safe mode (CASTOR_PLUGINS_SAFE_MODE): no plugin runs")
            if not p.enabled:
                raise PluginError(f"{p.name} is turned off")
            if host.crashed_too_often(plugin_id) and not force:
                raise PluginError(f"{p.name} crashed {3} times in a minute; an admin can start it again from Settings → Plugins")
            man = manifest_of(p)
            if problems := man.not_yet():
                raise PluginError("; ".join(problems))
            check_runtime(man, await restricted(db))
            if missing := missing_settings(man, p):
                raise PluginError(f"{p.name} needs its settings first: {', '.join(missing)}")
            folder = folder_of(p)
            token = await _fresh_token(db, p)
            await db.commit()
            payload = {"plugin": {"id": p.id, "version": p.version}, "settings": settings_for(p), "castor_version": CASTOR_VERSION}
            network = list((p.granted or {}).get("network") or [])
        if force:
            host.forget_crashes(plugin_id)
        await _status(plugin_id, "starting")
        try:
            unrestricted = man.runtime == "unrestricted"
            if not unrestricted:  # an unrestricted plugin may import anything; the sandbox's check doesn't apply
                await check_graph(plugin_id, folder, man.main)
            proc = PluginProcess(plugin_id, folder, man.main, handle_call, host.exited, network=network, unrestricted=unrestricted)
            proc.token = token
            await proc.start()
            host.procs[plugin_id] = proc
            answer = await proc.request("activate", payload, timeout=START_TIMEOUT)
            for h in (answer.get("ui") if isinstance(answer, dict) else None) or []:
                if isinstance(h, dict) and isinstance(h.get("name"), str):
                    proc.ui_handlers.add(h["name"])
                    if h.get("read_only"):
                        proc.ui_read_only.add(h["name"])
        except (PluginRuntimeError, OSError) as exc:
            await host.stop(plugin_id)
            await _status(plugin_id, "error", str(exc))
            await plugin_log(plugin_id, "error", f"Didn't start: {exc}")
            raise PluginRuntimeError(str(exc)) from exc
        await _status(plugin_id, "running")
        if unrestricted:
            await _audit_start(plugin_id, man)
        return proc


async def _audit_start(plugin_id: str, man: Manifest) -> None:
    """Each start of an unrestricted plugin goes in the activity log: it can do anything on this server."""
    from .services import record_activity

    async with SessionLocal() as db:
        await record_activity(db, type="plugin.started", message=f"Started the unrestricted plugin {man.name} {man.version}",
                              project_id=None, payload={"id": plugin_id, "runtime": "unrestricted"})
        await db.commit()


async def restart(plugin_id: str) -> PluginProcess:
    await host.stop(plugin_id)
    return await ensure_running(plugin_id, force=True)


async def on_crash(plugin_id: str, code: int, too_often: bool) -> None:
    why = f"It stopped by itself (exit code {code})"
    if too_often:
        why += " three times in a minute, so Castor won't start it again until an admin does"
    await _status(plugin_id, "error" if too_often else "crashed", why)
    await plugin_log(plugin_id, "error", why)


async def start_enabled() -> None:
    """At startup: every enabled plugin, in the background (none in safe mode)."""
    if safe_mode():
        log.warning("Plugins safe mode (CASTOR_PLUGINS_SAFE_MODE): no plugin is started")
        return
    async with SessionLocal() as db:
        ids = list((await db.execute(select(m.Plugin.id).where(m.Plugin.enabled.is_(True)))).scalars())
    for pid in ids:
        await start(pid)


async def stop_all() -> None:
    plugin_events.queues.drop_all()
    await host.stop_all()


_log_count = 0


async def plugin_log(plugin_id: str, level: str, message: str, run_id: str | None = None) -> None:
    global _log_count
    level = level if level in ("debug", "info", "warn", "error") else "info"
    try:
        rid = uuid.UUID(run_id) if run_id else None
    except ValueError:
        rid = None
    async with SessionLocal() as db:
        if await db.get(m.Plugin, plugin_id) is None:
            return
        if rid is not None and await db.get(m.PluginRun, rid) is None:
            rid = None
        db.add(m.PluginLog(plugin_id=plugin_id, run_id=rid, level=level, message=message[:20000]))
        _log_count += 1
        if _log_count % 200 == 0:
            cutoff = (await db.execute(select(m.PluginLog.id).where(m.PluginLog.plugin_id == plugin_id)
                                       .order_by(m.PluginLog.id.desc()).offset(LOG_KEEP).limit(1))).scalar_one_or_none()
            if cutoff is not None:
                await db.execute(delete(m.PluginLog).where(m.PluginLog.plugin_id == plugin_id, m.PluginLog.id <= cutoff))
        await db.commit()


# ---------- what a plugin asks for ----------

API_PATH = re.compile(r"^/api/[A-Za-z0-9._~%/-]*$")
# Never through a plugin, whatever its token says: these are people's own.
API_REFUSED = ("/api/mcp", "/api/auth", "/api/tokens", "/api/plugins")


async def handle_call(plugin_id: str, method: str, params: dict[str, Any], proc: PluginProcess) -> Any:
    if method == "api":
        return await _api(proc, params)
    if method.startswith("storage."):
        return await _storage(plugin_id, method.removeprefix("storage."), params)
    if method == "file.read":
        return _file_read(proc, params)
    if method == "progress":
        await _progress(plugin_id, params)
        return None
    if method == "log":
        await plugin_log(plugin_id, str(params.get("level") or "info"), str(params.get("message") or ""), params.get("run_id"))
        return None
    raise PluginError(f"Castor has no method {method}")


def _rate_limited(plugin_id: str) -> bool:
    now = time.monotonic()
    calls = _calls.setdefault(plugin_id, deque())
    while calls and now - calls[0] > 60:
        calls.popleft()
    if len(calls) >= API_PER_MINUTE:
        return True
    calls.append(now)
    return False


async def call_api(proc: PluginProcess, params: dict[str, Any]) -> dict[str, Any]:
    """A REST call as the plugin (for its panels): the same rules as the plugin's own calls."""
    return await _api(proc, params)


async def _api(proc: PluginProcess, params: dict[str, Any]) -> dict[str, Any]:
    if _rate_limited(proc.plugin_id):
        raise PluginError(f"Over {API_PER_MINUTE} calls to Castor in a minute: slow down and try again")
    method = str(params.get("method") or "GET").upper()
    path = str(params.get("path") or "")
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE") or not API_PATH.match(path) or ".." in path:
        raise PluginError(f"Not an API path: {method} {path}")
    if any(path == p or path.startswith(p + "/") for p in API_REFUSED):
        raise PluginError(f"Plugins can't call {path}")
    headers = {"Authorization": f"Bearer {proc.token}", api_tokens.VIA_HEADER: api_tokens.PLUGIN_KEY}
    query = {k: v for k, v in (params.get("query") or {}).items() if v is not None}
    upload = params.get("file")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=APP), base_url="http://castor", timeout=300) as client:
        body = params.get("body")
        if isinstance(upload, dict) and method == "POST":  # ctx.api.upload: multipart, a file and form fields
            name = str(upload.get("name") or "file")[:255]
            try:
                content = base64.b64decode(str(upload.get("base64") or ""), validate=True)
            except ValueError as exc:
                raise PluginError("upload: the file isn't valid base64") from exc
            if len(content) > UPLOAD_MAX:
                raise PluginError(f"upload: {name} is over {UPLOAD_MAX // 1024**2} MB")
            kind = str(upload.get("content_type") or "") or mimetypes.guess_type(name)[0] or "application/octet-stream"
            form = {str(k): str(v) for k, v in (params.get("form") or {}).items()}
            r = await client.post(path, headers=headers, params=query or None, data=form, files={"file": (name, content, kind)})
        else:
            r = await client.request(method, path, headers=headers, params=query or None,
                                     json=body if method != "GET" and body is not None else None)
    try:
        out = r.json() if r.content else None
    except ValueError:
        out = r.text
    return {"status": r.status_code, "body": out}


async def _storage(plugin_id: str, op: str, params: dict[str, Any]) -> Any:
    scope = ""
    if params.get("project"):
        try:
            pid = uuid.UUID(str(params["project"]))
        except ValueError:
            raise PluginError("project: an id") from None
        async with SessionLocal() as db:
            p = await db.get(m.Plugin, plugin_id)
            if p is None or not await enabled_in(db, p, pid):
                raise PluginError("The plugin isn't enabled in that project")
        scope = str(pid)
    key = str(params.get("key") or "")
    if op != "list" and not (0 < len(key) <= 200):
        raise PluginError("key: 1–200 characters")
    async with SessionLocal() as db:
        where = (m.PluginStorage.plugin_id == plugin_id, m.PluginStorage.scope == scope)
        if op == "get":
            row = (await db.execute(select(m.PluginStorage).where(*where, m.PluginStorage.key == key))).scalar_one_or_none()
            return row.value if row else None
        if op == "set":
            import json

            if len(json.dumps(params.get("value"))) > STORAGE_VALUE_MAX:
                raise PluginError("value: at most 1 MB")
            row = (await db.execute(select(m.PluginStorage).where(*where, m.PluginStorage.key == key))).scalar_one_or_none()
            if row is None:
                db.add(m.PluginStorage(plugin_id=plugin_id, scope=scope, key=key, value=params.get("value")))
            else:
                row.value = params.get("value")
                row.updated_at = utcnow()
            await db.commit()
            return None
        if op == "delete":
            await db.execute(delete(m.PluginStorage).where(*where, m.PluginStorage.key == key))
            await db.commit()
            return None
        if op == "list":
            prefix = str(params.get("prefix") or "")
            rows = (await db.execute(select(m.PluginStorage.key).where(*where).order_by(m.PluginStorage.key))).scalars()
            return [k for k in rows if k.startswith(prefix)][:10000]
    raise PluginError(f"No storage.{op}")


def _file_read(proc: PluginProcess, params: dict[str, Any]) -> dict[str, Any]:
    path = proc.files.get(str(params.get("run_id")))
    if path is None:
        raise PluginError("No file for that run")
    offset = max(0, int(params.get("offset") or 0))
    length = max(1, min(int(params.get("length") or 1024**2), 4 * 1024**2))
    with path.open("rb") as f:
        f.seek(offset)
        data = f.read(length)
    return {"data": base64.b64encode(data).decode(), "eof": offset + len(data) >= path.stat().st_size}


async def _progress(plugin_id: str, params: dict[str, Any]) -> None:
    try:
        rid = uuid.UUID(str(params.get("run_id")))
    except ValueError:
        return
    async with SessionLocal() as db:
        run = await db.get(m.PluginRun, rid)
        if run is None or run.plugin_id != plugin_id or run.status != "running":
            return
        run.progress = {k: params.get(k) for k in ("done", "total", "text") if params.get(k) is not None}
        await db.commit()
        bus.publish("plugin_run.updated", run.project_id, run_out(run, None))


# ---------- actions ----------


def run_out(run: m.PluginRun, plugin_name: str | None, label: str | None = None) -> dict[str, Any]:
    return {"id": str(run.id), "plugin_id": run.plugin_id, "plugin_name": plugin_name, "action": run.action, "label": label,
            "project_id": str(run.project_id) if run.project_id else None, "task_id": str(run.task_id) if run.task_id else None,
            "asset_id": str(run.asset_id) if run.asset_id else None, "status": run.status, "message": run.message,
            "progress": run.progress or {}, "file_name": run.file_name, "file_size": run.file_size,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None}


async def run_action(run_id: uuid.UUID, file_path: Path | None) -> None:
    """In the background: give the plugin the action, record how it went."""
    from .services import record_activity

    async with SessionLocal() as db:
        run = await db.get(m.PluginRun, run_id)
        p = await db.get(m.Plugin, run.plugin_id) if run else None
        if run is None or p is None:
            return
        man = manifest_of(p)
        spec = man.action(run.action)
        project = await db.get(m.Project, run.project_id) if run.project_id else None
        task = await db.get(m.Task, run.task_id) if run.task_id else None
        asset = await db.get(m.Asset, run.asset_id) if run.asset_id else None
        person = await db.get(m.User, run.user_id) if run.user_id else None
        params = {
            "run_id": str(run.id), "action": run.action,
            "user": {"id": str(person.id), "username": person.username, "display_name": person.display_name} if person else None,
            "project": {"id": str(project.id), "codename": project.codename, "name": project.name} if project else None,
            "task": {"id": str(task.id), "title": task.title} if task else None,
            "asset": {"id": str(asset.id), "type": asset.type, "label": asset.label} if asset else None,
            "file": {"name": run.file_name, "size": run.file_size} if file_path else None,
            "settings": await settings_of(db, p, run.user_id),
        }
        timeout = (spec.timeout_minutes if spec else 30) * 60
        label, name, plugin_id = (spec.label if spec else run.action), p.name, p.id
    status, message = "succeeded", ""
    proc: PluginProcess | None = None
    try:
        proc = await ensure_running(plugin_id)
        if file_path is not None:
            proc.files[str(run.id)] = file_path
        result = await proc.request("action", params, timeout=timeout)
        message = str((result or {}).get("message") or "")
    except (PluginError, PluginRuntimeError) as exc:
        status, message = "failed", str(exc)
    except Exception as exc:
        log.exception("plugin action %s failed", run_id)
        status, message = "failed", f"Castor failed running it: {exc}"
    finally:
        if proc is not None:
            proc.files.pop(str(run.id), None)
        if file_path is not None:
            file_path.unlink(missing_ok=True)
    async with SessionLocal() as db:
        run = await db.get(m.PluginRun, run_id)
        if run is None:
            return
        run.status, run.message, run.finished_at = status, message[:4000], utcnow()
        person = await db.get(m.User, run.user_id) if run.user_id else None
        said = f"{label}{' ' + run.file_name if run.file_name else ''} ({name})"
        await record_activity(
            db, type=f"plugin.action.{status}", project_id=run.project_id, task_id=run.task_id,
            message=(f"Ran {said}: {message}" if status == "succeeded" else f"{said} failed: {message}")[:1000],
            payload={"plugin": run.plugin_id, "action": run.action, "run": str(run.id), "via": "plugin"},
            actor_type="user" if person else "system", actor_id=str(person.id) if person else None,
            actor_name=person.display_name if person else "castor",
        )
        await db.commit()
        bus.publish("plugin_run.updated", run.project_id, run_out(run, name, label))


async def start_action(db: AsyncSession, user: m.User, p: m.Plugin, action_id: str, *, project: m.Project,
                       task: m.Task | None, asset: m.Asset | None, upload: tuple[str, Any] | None) -> m.PluginRun:
    """Check and queue a run; the file (name, readable) is saved first. Commits."""
    man = manifest_of(p)
    spec = man.action(action_id)
    if spec is None:
        raise PluginError(f"{p.name} has no action {action_id}")
    if not p.enabled:
        raise PluginError(f"{p.name} is turned off")
    if not await enabled_in(db, p, project.id):
        raise PluginError(f"{p.name} isn't enabled in this project")
    where = "asset" if asset else "card" if task else "project"
    if where not in spec.on:
        raise PluginError(f"“{spec.label}” isn't offered on a {where}")
    if asset is not None and spec.asset_types and asset.type not in spec.asset_types:
        raise PluginError(f"“{spec.label}” is for {', '.join(spec.asset_types)}")
    if spec.file and spec.file.required and upload is None:
        raise PluginError(f"“{spec.label}” needs a file")
    if missing := await missing_for(db, p, user.id):
        raise PluginError(f"{p.name} needs your own {', '.join(missing)} first: set {'it' if len(missing) == 1 else 'them'} "
                          "in Your plugin settings (your account menu)")
    run = m.PluginRun(plugin_id=p.id, action=action_id, project_id=project.id, task_id=task.id if task else None,
                      asset_id=asset.id if asset else None, user_id=user.id, status="running")
    db.add(run)
    await db.flush()
    path: Path | None = None
    if upload is not None and spec.file is not None:
        name, stream = upload
        if spec.file.accept and not any(name.lower().endswith(a.lower()) for a in spec.file.accept if a.startswith(".")) \
                and not any(not a.startswith(".") for a in spec.file.accept):
            raise PluginError(f"“{spec.label}” takes {', '.join(spec.file.accept)} files")
        path = plugin_package.uploads() / f"{run.id}-{secrets.token_hex(4)}"
        limit, size = spec.file.max_mb * 1024**2, 0
        with path.open("wb") as out:
            while chunk := await stream.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    out.close()
                    path.unlink(missing_ok=True)
                    raise PluginError(f"The file is over {spec.file.max_mb} MB")
                out.write(chunk)
        run.file_name, run.file_size = name[:255], size
    await db.commit()
    bus.publish("plugin_run.updated", project.id, run_out(run, p.name, spec.label))
    _soon(run_action(run.id, path))
    return run
