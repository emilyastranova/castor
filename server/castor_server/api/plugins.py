"""Plugins (plugins.py, docs/PLUGINS.md): admins preview, install, update, configure and remove
them (Settings → Plugins); anyone who can change a project runs the actions of the plugins
enabled there."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import api_tokens, plugin_package
from .. import models as m
from .. import plugins as P
from ..access import check_project, member_project_ids, project_or_404
from ..deps import DB, Admin, CurrentUser, Operator, parse_uuid
from ..events import bus
from ..plugin_manifest import Manifest
from ..plugin_runtime import PluginRuntimeError, host

router = APIRouter(tags=["plugins"])
MAX_URL_DOWNLOAD = plugin_package.MAX_ZIP


def _scopes(names: list[str]) -> list[dict[str, str]]:
    return [{"name": n, "label": api_tokens.SCOPES[n].label, "description": api_tokens.SCOPES[n].description}
            for n in names if n in api_tokens.SCOPES]


def icon_url(p: m.Plugin) -> str | None:
    """Where the browser gets a plugin's icon (the checksum keeps a cached one fresh after an update)."""
    return f"/api/plugins/{p.id}/icon?v={p.checksum[:10]}" if (p.manifest or {}).get("icon") else None


def _manifest_out(man: Manifest) -> dict[str, Any]:
    return {**man.model_dump(mode="json"), "permission_details": _scopes(man.permissions)}


async def _plugin_out(db: DB, p: m.Plugin) -> dict[str, Any]:
    man = P.manifest_of(p)
    account = await db.get(m.User, p.user_id) if p.user_id else None
    by = await db.get(m.User, p.installed_by) if p.installed_by else None
    running = host.running(p.id) is not None
    status = p.status if running or p.status in ("error", "crashed") else ("stopped" if p.status == "running" else p.status)
    return {
        "id": p.id, "name": p.name, "version": p.version, "publisher": p.publisher, "description": p.description,
        "icon_url": icon_url(p),
        "homepage": man.homepage, "license": man.license, "runtime": p.runtime, "source": p.source, "checksum": p.checksum,
        "enabled": p.enabled, "all_projects": p.all_projects, "project_ids": [str(x) for x in await P.project_ids(db, p.id)],
        "status": status, "status_message": p.status_message, "running": running,
        "granted": p.granted or {}, "permission_details": _scopes(list((p.granted or {}).get("permissions") or [])),
        "actions": [a.model_dump(mode="json") for a in man.actions],
        "asset_types": [{"name": t.name, "label": t.label, "plural": t.plural or t.label + "s", "icon": t.icon} for t in man.asset_types],
        "link_kinds": [{"name": k.name, "forward": k.forward, "reverse": k.reverse} for k in man.link_kinds if k.forward],
        "settings_spec": [s.model_dump(mode="json") for s in man.settings],
        "settings": p.settings or {}, "secrets_set": sorted((p.secrets or {}).keys()),
        "per_user": sorted(P.per_user_names(p, man)),
        "people_set": await _people_set(db, p),
        "missing_settings": P.missing_settings(man, p),
        "account": {"id": str(account.id), "username": account.username} if account else None,
        "installed_by": by.display_name if by else None, "installed_at": p.created_at.isoformat(), "updated_at": p.updated_at.isoformat(),
    }


async def _people_set(db: DB, p: m.Plugin) -> int:
    """How many people have set their own values."""
    from sqlalchemy import func

    return (await db.execute(select(func.count()).select_from(m.PluginUserSetting).where(m.PluginUserSetting.plugin_id == p.id))).scalar_one()


async def _or_404(db: DB, plugin_id: str) -> m.Plugin:
    p = await db.get(m.Plugin, plugin_id)
    if p is None:
        raise HTTPException(404, "Plugin not found")
    return p


@router.get("/plugins")
async def list_plugins(db: DB, _: Admin) -> list[dict[str, Any]]:
    """Installed plugins with their status, what they were allowed, settings and projects."""
    rows = (await db.execute(select(m.Plugin).order_by(m.Plugin.name))).scalars().all()
    return [await _plugin_out(db, p) for p in rows]


@router.get("/plugin-mode")
async def plugin_mode(db: DB, _: Admin) -> dict[str, Any]:
    """Restricted mode (unrestricted plugins can't be installed or run) and safe mode (none runs)."""
    return {"restricted": await P.restricted(db), "restricted_locked": P.restricted_locked(), "safe_mode": P.safe_mode()}


class ModeIn(BaseModel):
    restricted: bool


@router.put("/plugin-mode")
async def set_plugin_mode(body: ModeIn, db: DB, user: Admin) -> dict[str, Any]:
    """Turn restricted mode on or off. Turning it on stops the unrestricted plugins that run."""
    from .. import app_settings
    from ..services import actor_of, record_activity

    if not body.restricted and P.restricted_locked():
        raise HTTPException(422, "Restricted mode is locked on by CASTOR_PLUGINS_RESTRICTED on this server")
    current = await app_settings.load(db)
    if current.plugins_restricted != body.restricted:
        await app_settings.save(db, {"plugins_restricted": body.restricted}, user.id)
        await record_activity(db, type="plugin.mode", project_id=None, payload={"restricted": body.restricted},
                              message="Turned plugins' restricted mode " + ("on" if body.restricted else
                                                                             "off: unrestricted plugins can be installed"),
                              **actor_of(user))
        await db.commit()
        if body.restricted:
            await P.stop_unrestricted()
        else:
            for pid in (await db.execute(select(m.Plugin.id).where(m.Plugin.runtime == "unrestricted",
                                                                   m.Plugin.enabled.is_(True)))).scalars():
                P._soon(P.start(pid))
        bus.publish("plugins.changed", None, {"mode": True})
    return await plugin_mode(db, user)


@router.get("/plugins/{plugin_id}")
async def get_plugin(plugin_id: str, db: DB, _: Admin) -> dict[str, Any]:
    return await _plugin_out(db, await _or_404(db, plugin_id))


async def _download(url: str) -> bytes:
    if not url.startswith("https://"):
        raise HTTPException(422, "url: an https:// link to the plugin's .zip")
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client, client.stream("GET", url) as r:
            if r.status_code >= 400:
                raise HTTPException(422, f"Downloading it failed: HTTP {r.status_code}")
            chunks, size = [], 0
            async for chunk in r.aiter_bytes():
                size += len(chunk)
                if size > MAX_URL_DOWNLOAD:
                    raise HTTPException(422, f"The file is over {MAX_URL_DOWNLOAD // 1024**2} MB")
                chunks.append(chunk)
            return b"".join(chunks)
    except httpx.HTTPError as exc:
        raise HTTPException(422, f"Downloading it failed: {exc}") from exc


@router.get("/plugins/{plugin_id}/icon")
async def plugin_icon(plugin_id: str, db: DB, _: CurrentUser):
    """A plugin's icon, for anyone signed in. Served so that even an SVG opened on its own can't
    run anything (it was checked at install too)."""
    from fastapi.responses import Response

    p = await db.get(m.Plugin, plugin_id)
    name = (p.manifest or {}).get("icon") if p else None
    path = (P.folder_of(p) / name) if p and name else None
    if path is None or not path.is_file():
        raise HTTPException(404, "No icon")
    return Response(path.read_bytes(), media_type=plugin_package.icon_type(name), headers={
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox",
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=86400"})


@router.post("/plugins/preview")
async def preview(db: DB, _: Admin, file: Annotated[UploadFile | None, File()] = None,
                  url: Annotated[str | None, Form()] = None) -> dict[str, Any]:
    """Upload a plugin's .zip (or give an https URL to one) and see what it is and asks for,
    before anything of it runs. Send the token back to install it."""
    if file is not None:
        data, source = await file.read(MAX_URL_DOWNLOAD + 1), f"upload:{file.filename or 'plugin.zip'}"
    elif url:
        data, source = await _download(url.strip()), url.strip()
    else:
        raise HTTPException(422, "Choose a plugin .zip or give its URL")
    try:
        token, pkg = plugin_package.stage(data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    man = pkg.manifest
    existing = await db.get(m.Plugin, man.id)
    icon = None
    if man.icon:  # not installed yet: the preview carries the icon itself
        import base64

        icon = f"data:{plugin_package.icon_type(man.icon)};base64,{base64.b64encode(pkg.files[man.icon]).decode()}"
    out: dict[str, Any] = {"token": token, "source": source, "checksum": pkg.checksum, "size": pkg.size, "files": len(pkg.files),
                           "icon": icon,
                           "manifest": _manifest_out(man), "problems": man.not_yet() + P.type_conflicts(man), "existing": None,
                           "new_permissions": {}}
    try:
        P.check_runtime(man, await P.restricted(db))
    except P.PluginError as exc:
        out["problems"].append(str(exc))
    if existing is not None:
        out["existing"] = {"version": existing.version, "same": existing.checksum == pkg.checksum}
        out["new_permissions"] = P.new_grants(existing.granted or {}, man)
    return out


class InstallIn(BaseModel):
    token: str = Field(max_length=100)
    source: str = Field("", max_length=500)
    # The admin saw and approved what the manifest asks for (and, for an update, anything new).
    approve: bool = False
    project_ids: list[uuid.UUID] = Field(default_factory=list)
    all_projects: bool = False
    settings: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, str | None] = Field(default_factory=dict)
    # Settings each person sets for themselves (an API key everyone brings) instead of one shared value.
    per_user: list[str] = Field(default_factory=list)
    # An unrestricted plugin: the admin types its id, to show they know it can do anything here.
    confirm: str = Field("", max_length=80)


@router.post("/plugins", status_code=201)
async def install(body: InstallIn, db: DB, user: Admin) -> dict[str, Any]:
    """Install the previewed plugin, or update the one with its id. Needs `approve`."""
    if not body.approve:
        raise HTTPException(422, "approve: confirm what the plugin asks for first")
    try:
        _, pkg = plugin_package.staged(body.token)
        if pkg.manifest.runtime == "unrestricted" and body.confirm.strip() != pkg.manifest.id:
            raise ValueError(f"confirm: {pkg.manifest.name} is unrestricted (it can do anything on this server); "
                             f"type its id, {pkg.manifest.id}, to install it")
        p = await P.install(db, user, pkg, source=body.source or "upload", project_ids_in=body.project_ids,
                            all_projects=body.all_projects, values=body.settings, secrets_in=body.secrets, per_user=body.per_user)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(422, str(exc)) from exc
    plugin_package.unstage(body.token)
    return await _plugin_out(db, p)


class PluginPatch(BaseModel):
    enabled: bool | None = None
    all_projects: bool | None = None
    project_ids: list[uuid.UUID] | None = None
    settings: dict[str, Any] | None = None
    # Secret settings: a value sets it, "" clears it, null (or leaving it out) keeps it.
    secrets: dict[str, str | None] | None = None
    # The settings each person sets for themselves; their shared values go.
    per_user: list[str] | None = None


@router.patch("/plugins/{plugin_id}")
async def update_plugin(plugin_id: str, body: PluginPatch, db: DB, user: Admin) -> dict[str, Any]:
    """Turn a plugin on or off, choose its projects, change its settings. It restarts to pick them up."""
    from ..services import actor_of, record_activity

    p = await _or_404(db, plugin_id)
    man = P.manifest_of(p)
    said = []
    try:
        if body.per_user is not None and sorted(body.per_user) != sorted(p.per_user or []):
            P.set_per_user(man, p, body.per_user)
            said.append("whose settings are each person's")
        if body.settings is not None or body.secrets is not None:
            shared = {s.name for s in man.settings} - P.per_user_names(p, man)
            p.settings, p.secrets = P.check_settings(man, body.settings or {}, body.secrets, p.settings, p.secrets, shared)
            said.append("its settings")
        if body.project_ids is not None or body.all_projects is not None:
            ids = body.project_ids if body.project_ids is not None else await P.project_ids(db, p.id)
            await P.set_projects(db, p, ids, p.all_projects if body.all_projects is None else body.all_projects)
            said.append("its projects")
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(422, str(exc)) from exc
    if body.enabled is not None and body.enabled != p.enabled:
        if body.enabled:
            try:
                P.check_runtime(man, await P.restricted(db))
            except P.PluginError as exc:
                await db.rollback()
                raise HTTPException(422, str(exc)) from exc
        p.enabled = body.enabled
        said.append("turned it on" if body.enabled else "turned it off")
    if said:
        await record_activity(db, type="plugin.changed", message=f"Changed the plugin {p.name}: {', '.join(said)}", project_id=None,
                              payload={"id": p.id}, **actor_of(user))
    await db.commit()
    await host.stop(p.id)
    if p.enabled:
        P._soon(P.start(p.id, force=True))
    else:
        await P._status(p.id, "stopped")
    bus.publish("plugins.changed", None, {"id": p.id})
    return await _plugin_out(db, p)


@router.post("/plugins/{plugin_id}/restart")
async def restart_plugin(plugin_id: str, db: DB, _: Admin) -> dict[str, Any]:
    """Start it again (also after it crashed too often)."""
    p = await _or_404(db, plugin_id)
    try:
        await P.restart(p.id)
    except (ValueError, PluginRuntimeError) as exc:
        raise HTTPException(409, str(exc)) from exc
    await db.refresh(p)
    return await _plugin_out(db, p)


@router.delete("/plugins/{plugin_id}", status_code=204)
async def remove_plugin(plugin_id: str, db: DB, user: Admin):
    """Remove a plugin and its files, storage, runs and logs. What it recorded in projects stays."""
    await P.uninstall(db, user, await _or_404(db, plugin_id))


@router.get("/plugins/{plugin_id}/logs")
async def plugin_logs(plugin_id: str, db: DB, _: Admin, run_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    await _or_404(db, plugin_id)
    q = select(m.PluginLog).where(m.PluginLog.plugin_id == plugin_id)
    if run_id:
        q = q.where(m.PluginLog.run_id == parse_uuid(run_id))
    rows = (await db.execute(q.order_by(m.PluginLog.id.desc()).limit(max(1, min(limit, 2000))))).scalars().all()
    return [{"id": r.id, "level": r.level, "message": r.message, "at": r.at.isoformat(), "run_id": str(r.run_id) if r.run_id else None}
            for r in rows]


@router.get("/plugins/{plugin_id}/runs")
async def plugin_runs(plugin_id: str, db: DB, _: Admin, limit: int = 50) -> list[dict[str, Any]]:
    p = await _or_404(db, plugin_id)
    rows = (await db.execute(select(m.PluginRun).where(m.PluginRun.plugin_id == plugin_id)
                             .order_by(m.PluginRun.started_at.desc()).limit(max(1, min(limit, 500))))).scalars().all()
    labels = {a.id: a.label for a in P.manifest_of(p).actions}
    return [P.run_out(r, p.name, labels.get(r.action)) for r in rows]


# ---------- your own settings ----------


async def _usable(db: DB, user: m.User, p: m.Plugin) -> bool:
    """A plugin someone can use: on, and in a project they can see (admins: any)."""
    if not p.enabled:
        return False
    if p.all_projects or user.is_admin:
        return True
    ids = set(await P.project_ids(db, p.id))
    return bool(ids & set((await db.execute(member_project_ids(user.id))).scalars()))


async def _mine_out(db: DB, user: m.User, p: m.Plugin) -> dict[str, Any]:
    man = P.manifest_of(p)
    mine = P.per_user_names(p, man)
    row = await db.get(m.PluginUserSetting, (p.id, user.id))
    return {"id": p.id, "name": p.name, "description": p.description, "icon_url": icon_url(p),
            "settings_spec": [s.model_dump(mode="json") for s in man.settings if s.name in mine],
            "settings": {k: v for k, v in ((row.settings if row else None) or {}).items() if k in mine},
            "secrets_set": sorted(k for k in ((row.secrets if row else None) or {}) if k in mine),
            "missing": await P.missing_for(db, p, user.id)}


@router.get("/auth/me/plugins")
async def my_plugin_settings(db: DB, user: CurrentUser) -> list[dict[str, Any]]:
    """The plugins you use that want some settings from you (your own API key…), with yours."""
    rows = (await db.execute(select(m.Plugin).order_by(m.Plugin.name))).scalars().all()
    return [await _mine_out(db, user, p) for p in rows if P.per_user_names(p) and await _usable(db, user, p)]


class MySettingsIn(BaseModel):
    settings: dict[str, Any] = Field(default_factory=dict)
    # A value sets it, "" clears it, null keeps it.
    secrets: dict[str, str | None] = Field(default_factory=dict)


@router.put("/auth/me/plugins/{plugin_id}")
async def set_my_plugin_settings(plugin_id: str, body: MySettingsIn, db: DB, user: CurrentUser) -> dict[str, Any]:
    """Set your own values of a plugin's per-person settings."""
    p = await db.get(m.Plugin, plugin_id)
    if p is None or not await _usable(db, user, p) or not P.per_user_names(p):
        raise HTTPException(404, "Plugin not found")
    man = P.manifest_of(p)
    row = await db.get(m.PluginUserSetting, (p.id, user.id))
    try:
        plain, sealed = P.check_settings(man, body.settings, body.secrets, row.settings if row else None,
                                         row.secrets if row else None, P.per_user_names(p, man))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if row is None:
        row = m.PluginUserSetting(plugin_id=p.id, user_id=user.id)
        db.add(row)
    row.settings, row.secrets = plain, sealed
    await db.commit()
    return await _mine_out(db, user, p)


# ---------- in a project ----------


@router.get("/projects/{project_id}/plugin-actions")
async def project_actions(project_id: str, db: DB, user: CurrentUser) -> list[dict[str, Any]]:
    """The actions of the plugins enabled in this project (where each is offered: project, card,
    asset; and whether it takes a file)."""
    project = await project_or_404(db, user, parse_uuid(project_id))
    rows = (await db.execute(select(m.Plugin).where(m.Plugin.enabled.is_(True)).order_by(m.Plugin.name))).scalars().all()
    out = []
    for p in rows:
        if not await P.enabled_in(db, p, project.id):
            continue
        for a in P.manifest_of(p).actions:
            out.append({"plugin": {"id": p.id, "name": p.name, "status": p.status, "icon_url": icon_url(p), "runtime": p.runtime},
                        **a.model_dump(mode="json")})
    return out


@router.post("/plugins/{plugin_id}/actions/{action}", status_code=202)
async def run_action(plugin_id: str, action: str, db: DB, user: Operator,
                     project_id: Annotated[str, Form()], task_id: Annotated[str | None, Form()] = None,
                     asset_id: Annotated[str | None, Form()] = None, file: Annotated[UploadFile | None, File()] = None) -> dict[str, Any]:
    """Run a plugin's action on a project (or one of its cards or assets), with a file if it takes
    one. It runs in the background: follow it with GET /api/plugin-runs/{id}."""
    project = await project_or_404(db, user, parse_uuid(project_id), write=True)
    p = await db.get(m.Plugin, plugin_id)
    if p is None or not await P.enabled_in(db, p, project.id):
        raise HTTPException(404, "No such plugin in this project")
    task = asset = None
    if task_id:
        task = await db.get(m.Task, parse_uuid(task_id))
        if task is None or task.project_id != project.id:
            raise HTTPException(404, "Card not found")
    if asset_id:
        asset = await db.get(m.Asset, parse_uuid(asset_id))
        if asset is None or asset.project_id != project.id:
            raise HTTPException(404, "Asset not found")
    try:
        run = await P.start_action(db, user, p, action, project=project, task=task, asset=asset,
                                   upload=(file.filename or "upload", file) if file is not None else None)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(422, str(exc)) from exc
    spec = P.manifest_of(p).action(action)
    return P.run_out(run, p.name, spec.label if spec else None)


class _Bytes:
    """Bytes read like an upload (start_action reads in chunks)."""

    def __init__(self, data: bytes) -> None:
        self.data, self.at = data, 0

    async def read(self, n: int) -> bytes:
        chunk = self.data[self.at: self.at + n]
        self.at += len(chunk)
        return chunk


class RunIn(BaseModel):
    plugin_id: str = Field(max_length=64)
    action: str = Field(max_length=64)
    project_id: uuid.UUID
    task_id: uuid.UUID | None = None
    asset_id: uuid.UUID | None = None
    # A file for actions that take one, base64-encoded (up to 50 MB here; larger ones through the form upload).
    filename: str | None = Field(None, max_length=255)
    content_base64: str | None = Field(None, max_length=70 * 1024**2)


@router.post("/plugin-actions/run", status_code=202)
async def run_action_json(body: RunIn, db: DB, user: Operator) -> dict[str, Any]:
    """The same as POST /api/plugins/{id}/actions/{action}, as JSON (for scripts and MCP)."""
    import base64
    import binascii

    upload = None
    if body.content_base64 is not None:
        try:
            upload = (body.filename or "upload", _Bytes(base64.b64decode(body.content_base64, validate=True)))
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(422, f"content_base64 isn't valid base64: {exc}") from exc
    project = await project_or_404(db, user, body.project_id, write=True)
    p = await db.get(m.Plugin, body.plugin_id)
    if p is None or not await P.enabled_in(db, p, project.id):
        raise HTTPException(404, "No such plugin in this project")
    task = await db.get(m.Task, body.task_id) if body.task_id else None
    asset = await db.get(m.Asset, body.asset_id) if body.asset_id else None
    if (body.task_id and (task is None or task.project_id != project.id)) or (body.asset_id and (asset is None or asset.project_id != project.id)):
        raise HTTPException(404, "Card or asset not found")
    try:
        run = await P.start_action(db, user, p, body.action, project=project, task=task, asset=asset, upload=upload)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(422, str(exc)) from exc
    spec = P.manifest_of(p).action(body.action)
    return P.run_out(run, p.name, spec.label if spec else None)


async def _run_or_404(db: DB, user: m.User, run_id: str) -> m.PluginRun:
    run = await db.get(m.PluginRun, parse_uuid(run_id))
    if run is None or run.project_id is None:
        raise HTTPException(404, "Run not found")
    try:
        await check_project(db, user, run.project_id)
    except HTTPException:
        raise HTTPException(404, "Run not found") from None
    return run


@router.get("/plugin-runs/{run_id}")
async def get_run(run_id: str, db: DB, user: CurrentUser) -> dict[str, Any]:
    """A plugin action's run: running, succeeded or failed, its progress and message."""
    run = await _run_or_404(db, user, run_id)
    p = await db.get(m.Plugin, run.plugin_id)
    spec = P.manifest_of(p).action(run.action) if p else None
    return P.run_out(run, p.name if p else run.plugin_id, spec.label if spec else None)


@router.get("/projects/{project_id}/plugin-runs")
async def project_runs(project_id: str, db: DB, user: CurrentUser, limit: int = 20) -> list[dict[str, Any]]:
    project = await project_or_404(db, user, parse_uuid(project_id))
    rows = (await db.execute(select(m.PluginRun).where(m.PluginRun.project_id == project.id)
                             .order_by(m.PluginRun.started_at.desc()).limit(max(1, min(limit, 200))))).scalars().all()
    names = {p.id: p for p in (await db.execute(select(m.Plugin))).scalars()}
    out = []
    for r in rows:
        p = names.get(r.plugin_id)
        spec = P.manifest_of(p).action(r.action) if p else None
        out.append(P.run_out(r, p.name if p else r.plugin_id, spec.label if spec else None))
    return out

