"""Plugin panels (docs/PLUGINS.md, phase C): a plugin's HTML pages shown inside Castor, in sandboxed
iframes. Castor serves their files at an address only signed-in people are given, with a Content
Security Policy that keeps them sandboxed and off the network even if one is opened on its own;
everything they do comes back through `…/panels/{ui}/call`, which acts as the plugin."""

from __future__ import annotations

import hashlib
import hmac
import mimetypes
import re
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import models as m
from .. import plugins as P
from ..access import can_see, project_or_404, visible_project_ids
from ..config import settings
from ..deps import DB, CurrentUser, parse_uuid
from ..plugin_runtime import SDK_DIR, PluginRuntimeError
from .plugins import icon_url

router = APIRouter(tags=["plugins"])

# What a plugin's pages may load from its own folder.
SERVED = {".html", ".htm", ".css", ".js", ".mjs", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".json", ".woff", ".woff2", ".txt"}
# Sandboxed (scripts only: an opaque origin, no cookies, no top navigation, no popups), no network
# at all (connect-src 'none'), only its own files, framed only by Castor. A sandboxed page's origin
# is opaque, so 'self' matches nothing there: the server it came from is named instead ({here}).
CSP = ("sandbox allow-scripts; default-src 'none'; script-src {here} 'unsafe-inline'; style-src {here} 'unsafe-inline'; "
       "img-src {here} data: blob:; font-src {here} data:; media-src {here} data: blob:; connect-src 'none'; form-action 'none'; "
       "base-uri 'none'; frame-ancestors 'self'")
_HOST = re.compile(r"^(?:[A-Za-z0-9.-]+|\[[0-9A-Fa-f:.]+\])(?::\d{1,5})?$")
BASE_CSS = """<style>
:root { color-scheme: light dark; }
html { font: 14px/1.45 var(--font, system-ui, sans-serif); color: var(--on-surface, #1b1b21); background: transparent; }
body { margin: 0; }
a { color: var(--primary, #545a92); }
code, pre { font-family: var(--mono, ui-monospace, monospace); }
</style>"""


def _sig(p: m.Plugin) -> str:
    """The signature in a plugin's page addresses: per plugin and release, unguessable."""
    return hmac.new(settings.secret_key.encode(), f"plugin-ui:{p.id}:{p.checksum}".encode(), hashlib.sha256).hexdigest()[:32]


def page_url(p: m.Plugin, path: str) -> str:
    return f"/api/plugin-ui/{p.id}/{_sig(p)}/{path}"


def csp_for(request: Request) -> str:
    """The CSP, naming the host the browser asked (a proxy in front may pass it as X-Forwarded-Host).
    Only this response's own sources; anything odd falls back to 'self'."""
    hosts = [h.strip() for h in request.headers.get("x-forwarded-host", "").split(",")[:1] + [request.headers.get("host", "")]]
    sources = ["'self'"] + [f"{scheme}://{h}" for h in dict.fromkeys(hosts) if h and _HOST.match(h) for scheme in ("https", "http")]
    return CSP.format(here=" ".join(sources))


async def _may_run(db: DB, p: m.Plugin) -> bool:
    """Not in safe mode, nor an unrestricted plugin while restricted mode is on."""
    return not P.safe_mode() and not (p.runtime == "unrestricted" and await P.restricted(db))


def _sdk() -> str:
    return (SDK_DIR / "ui.js").read_text()


@router.get("/plugin-ui/{plugin_id}/{sig}/{path:path}", include_in_schema=False)
async def plugin_file(plugin_id: str, sig: str, path: str, request: Request, db: DB):
    """A plugin page's file. Public on purpose: a sandboxed iframe sends no cookies; the address
    itself (signed per plugin and release) is what only signed-in people are given."""
    p = await db.get(m.Plugin, plugin_id)
    if p is None or not p.enabled or not hmac.compare_digest(sig, _sig(p)) or not await _may_run(db, p):
        raise HTTPException(404, "Not found")
    folder = P.folder_of(p).resolve()
    target = (folder / path).resolve()
    if folder not in target.parents or not target.is_file() or target.suffix.lower() not in SERVED or ".." in Path(path).parts:
        raise HTTPException(404, "Not found")
    data = target.read_bytes()
    kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    if target.suffix.lower() in (".html", ".htm"):
        # The SDK and a base style go in first, so the page can use them straight away.
        head = f"<script>{_sdk()}</script>{BASE_CSS}".encode()
        lower = data.lower()
        at = lower.find(b"<head>")
        data = data[: at + 6] + head + data[at + 6:] if at >= 0 else head + data
        kind = "text/html; charset=utf-8"
    return Response(data, media_type=kind, headers={
        "Content-Security-Policy": csp_for(request), "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
        "Cache-Control": "private, max-age=300"})


@router.get("/projects/{project_id}/plugin-panels")
async def project_panels(project_id: str, db: DB, user: CurrentUser) -> list[dict[str, Any]]:
    """The panels of the plugins enabled in this project: where each goes (an asset's dialog, a
    card's tabs, a page of the project) and the address of its page."""
    project = await project_or_404(db, user, parse_uuid(project_id))
    rows = (await db.execute(select(m.Plugin).where(m.Plugin.enabled.is_(True)).order_by(m.Plugin.name))).scalars().all()
    out = []
    for p in rows:
        if not await P.enabled_in(db, p, project.id):
            continue
        for ui in P.manifest_of(p).ui:
            if ui.slot == "page.script":
                continue
            out.append({"plugin": {"id": p.id, "name": p.name, "icon_url": icon_url(p)}, **ui.model_dump(mode="json"),
                        "url": page_url(p, ui.path)})
    return out


@router.get("/plugin-scripts")
async def plugin_scripts(db: DB, user: CurrentUser) -> list[dict[str, Any]]:
    """The scripts unrestricted plugins run in Castor's own page (`page.script`), for the plugins
    enabled in a project this person can see. None in safe mode or restricted mode."""
    if P.safe_mode() or await P.restricted(db):
        return []
    visible = await visible_project_ids(db, user)
    rows = (await db.execute(select(m.Plugin).where(m.Plugin.enabled.is_(True), m.Plugin.runtime == "unrestricted")
                             .order_by(m.Plugin.name))).scalars().all()
    out = []
    for p in rows:
        scripts = [u for u in P.manifest_of(p).ui if u.slot == "page.script"]
        if not scripts:
            continue
        if not p.all_projects and visible is not None and not set(await P.project_ids(db, p.id)) & visible:
            continue
        out += [{"plugin": {"id": p.id, "name": p.name}, "id": u.id, "url": page_url(p, u.path)} for u in scripts]
    return out


class PanelCall(BaseModel):
    project_id: uuid.UUID
    task_id: uuid.UUID | None = None
    asset_id: uuid.UUID | None = None
    # api: a REST call as the plugin; call: a handler in the plugin's code (ctx.ui.on).
    kind: Literal["api", "call"]
    method: str = "GET"
    path: str = ""
    query: dict[str, Any] | None = None
    body: Any = None
    name: str = Field("", max_length=64)
    args: Any = None


@router.post("/plugins/{plugin_id}/panels/{ui_id}/call")
async def panel_call(plugin_id: str, ui_id: str, body: PanelCall, db: DB, user: CurrentUser) -> dict[str, Any]:
    """What a plugin's panel asks for, on behalf of the person looking at it. Anyone who can see the
    project can read through it; changing anything (an API call other than GET, or a handler the
    plugin didn't declare read-only) needs someone who can change the project."""
    p = await db.get(m.Plugin, plugin_id)
    if p is None or not p.enabled or not await can_see(db, user, body.project_id) or not await P.enabled_in(db, p, body.project_id):
        raise HTTPException(404, "No such plugin in this project")
    man = P.manifest_of(p)
    panel = man.panel(ui_id)
    if panel is None:
        raise HTTPException(404, "No such panel")
    task = await db.get(m.Task, body.task_id) if body.task_id else None
    asset = await db.get(m.Asset, body.asset_id) if body.asset_id else None
    if (body.task_id and (task is None or task.project_id != body.project_id)) or \
            (body.asset_id and (asset is None or asset.project_id != body.project_id)):
        raise HTTPException(404, "Card or asset not found")
    project = await db.get(m.Project, body.project_id)
    can_write = user.can(m.UserRole.operator) and getattr(user, "api_token", None) is None  # a token reads only
    try:
        proc = await P.ensure_running(p.id)
    except (ValueError, PluginRuntimeError) as exc:
        raise HTTPException(409, str(exc)) from exc
    if body.kind == "api":
        method = body.method.upper()
        if method != "GET" and not can_write:
            raise HTTPException(403, "Only people who can change the project can do that")
        try:
            r = await P.call_api(proc, {"method": method, "path": body.path, "query": body.query, "body": body.body})
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"status": r["status"], "result": r["body"]}
    if body.name not in proc.ui_handlers:
        raise HTTPException(404, f"The plugin has no handler “{body.name}” (ctx.ui.on)")
    if not can_write and body.name not in proc.ui_read_only:
        raise HTTPException(403, "Only people who can change the project can do that")
    params = {
        "name": body.name, "args": body.args, "panel": panel.id,
        "user": {"id": str(user.id), "username": user.username, "display_name": user.display_name, "can_write": can_write},
        "project": {"id": str(project.id), "codename": project.codename, "name": project.name},
        "task": {"id": str(task.id), "title": task.title} if task else None,
        "asset": {"id": str(asset.id), "type": asset.type, "label": asset.label} if asset else None,
        "settings": await P.settings_of(db, p, user.id),
    }
    try:
        result = await proc.request("ui", params, timeout=60)
    except PluginRuntimeError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"status": 200, "result": result}
