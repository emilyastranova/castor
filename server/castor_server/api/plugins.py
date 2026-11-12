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
from ..access import check_project, project_or_404
from ..deps import DB, Admin, CurrentUser, Operator, parse_uuid
from ..events import bus
from ..plugin_manifest import Manifest
from ..plugin_runtime import PluginRuntimeError, host

router = APIRouter(tags=["plugins"])
MAX_URL_DOWNLOAD = plugin_package.MAX_ZIP


def _scopes(names: list[str]) -> list[dict[str, str]]:
    return [{"name": n, "label": api_tokens.SCOPES[n].label, "description": api_tokens.SCOPES[n].description}
            for n in names if n in api_tokens.SCOPES]


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
        "homepage": man.homepage, "license": man.license, "runtime": p.runtime, "source": p.source, "checksum": p.checksum,
        "enabled": p.enabled, "all_projects": p.all_projects, "project_ids": [str(x) for x in await P.project_ids(db, p.id)],
        "status": status, "status_message": p.status_message, "running": running,
        "granted": p.granted or {}, "permission_details": _scopes(list((p.granted or {}).get("permissions") or [])),
        "actions": [a.model_dump(mode="json") for a in man.actions],
        "settings_spec": [s.model_dump(mode="json") for s in man.settings],
        "settings": p.settings or {}, "secrets_set": sorted((p.secrets or {}).keys()),
        "missing_settings": P.missing_settings(man, p),
        "account": {"id": str(account.id), "username": account.username} if account else None,
        "installed_by": by.display_name if by else None, "installed_at": p.created_at.isoformat(), "updated_at": p.updated_at.isoformat(),
    }


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
    out: dict[str, Any] = {"token": token, "source": source, "checksum": pkg.checksum, "size": pkg.size, "files": len(pkg.files),
                           "manifest": _manifest_out(man), "problems": man.not_yet(), "existing": None, "new_permissions": {}}
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


@router.post("/plugins", status_code=201)
async def install(body: InstallIn, db: DB, user: Admin) -> dict[str, Any]:
    """Install the previewed plugin, or update the one with its id. Needs `approve`."""
    if not body.approve:
        raise HTTPException(422, "approve: confirm what the plugin asks for first")
    try:
        _, pkg = plugin_package.staged(body.token)
        p = await P.install(db, user, pkg, source=body.source or "upload", project_ids_in=body.project_ids,
                            all_projects=body.all_projects, values=body.settings, secrets_in=body.secrets)
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


@router.patch("/plugins/{plugin_id}")
async def update_plugin(plugin_id: str, body: PluginPatch, db: DB, user: Admin) -> dict[str, Any]:
    """Turn a plugin on or off, choose its projects, change its settings. It restarts to pick them up."""
    from ..services import actor_of, record_activity

    p = await _or_404(db, plugin_id)
    man = P.manifest_of(p)
    said = []
    try:
        if body.settings is not None or body.secrets is not None:
            p.settings, p.secrets = P.check_settings(man, body.settings or {}, body.secrets, p.settings, p.secrets)
            said.append("its settings")
        if body.project_ids is not None or body.all_projects is not None:
            ids = body.project_ids if body.project_ids is not None else await P.project_ids(db, p.id)
            await P.set_projects(db, p, ids, p.all_projects if body.all_projects is None else body.all_projects)
            said.append("its projects")
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(422, str(exc)) from exc
    if body.enabled is not None and body.enabled != p.enabled:
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
            out.append({"plugin": {"id": p.id, "name": p.name, "status": p.status}, **a.model_dump(mode="json")})
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

