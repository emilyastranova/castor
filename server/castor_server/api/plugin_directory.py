"""The plugin directory for admins (plugin_directory.py): browse it, a plugin's page (its newest
release, what its manifest asks for, its README), a publisher's page, and update checks. Installing
goes through the usual preview (`POST /api/plugins/preview` with `directory`)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from sqlalchemy import select

from .. import app_settings
from .. import models as m
from .. import plugin_directory as D
from ..deps import DB, Admin
from ..plugin_manifest import parse_manifest
from ..plugins import CASTOR_VERSION, castor_ok
from .plugins import _manifest_out

router = APIRouter(tags=["plugins"])


async def _directory() -> D.Directory:
    try:
        return await D.directory()
    except D.DirectoryError as exc:
        raise HTTPException(503, str(exc)) from exc


async def _installed(db: DB) -> dict[str, m.Plugin]:
    return {p.id: p for p in (await db.execute(select(m.Plugin))).scalars()}


async def _entry_out(e: D.Entry, d: D.Directory, installed: dict[str, m.Plugin], *, release: bool = True) -> dict[str, Any]:
    pub = d.publisher(e.publisher)
    latest = None
    error = None
    if release:
        try:
            r = await D.latest(e)
            latest = {"version": r.version, "published_at": r.published_at} if r else None
        except D.DirectoryError as exc:
            error = str(exc)
    here = installed.get(e.id)
    # The same id from someone else: installing this would be refused (the id is taken).
    other = here is not None and here.publisher != e.publisher
    return {
        **e.model_dump(mode="json"), "source_url": e.source_url,
        "publisher_name": pub.name if pub else e.publisher,
        "icon_url": f"/api/plugin-directory/plugins/{e.id}/icon",
        "latest": latest, "error": error,
        "installed": here.version if here and not other else None, "taken": other,
        "update": bool(here and not other and latest and D.newer(latest["version"], here.version)),
    }


@router.get("/plugin-directory")
async def browse(db: DB, _: Admin, refresh: bool = False) -> dict[str, Any]:
    """Every plugin in the directory with its newest release and whether it's installed here."""
    settings_now = await app_settings.load(db)
    out: dict[str, Any] = {"enabled": D.enabled(), "update_checks": settings_now.plugin_update_checks,
                           "castor_version": CASTOR_VERSION, "publishers": [], "plugins": [], "error": None}
    if not D.enabled():
        return out
    try:
        d = await D.directory(force=refresh)
    except D.DirectoryError as exc:
        out["error"] = str(exc)
        return out
    if refresh:
        D._releases.clear()
    installed = await _installed(db)
    out["error"] = D.last_error
    out["publishers"] = [p.model_dump(mode="json") for p in d.publishers]
    out["plugins"] = [await _entry_out(e, d, installed) for e in d.plugins]
    return out


@router.get("/plugin-directory/plugins/{plugin_id}")
async def plugin_page(plugin_id: str, db: DB, _: Admin) -> dict[str, Any]:
    """One plugin: its newest release and notes, what its manifest asks for and its README."""
    d = await _directory()
    e = d.entry(plugin_id)
    if e is None:
        raise HTTPException(404, "The plugin directory has no such plugin")
    out = await _entry_out(e, d, await _installed(db))
    out["publisher_info"] = (p.model_dump(mode="json") if (p := d.publisher(e.publisher)) else None)
    out["release"], out["manifest"], out["readme"], out["problems"] = None, None, None, []
    try:
        r = await D.latest(e)
    except D.DirectoryError as exc:
        out["problems"].append(str(exc))
        return out
    if r is None:
        out["problems"].append("No release Castor can install yet")
        return out
    out["release"] = r.model_dump(mode="json", include={"version", "tag", "published_at", "notes", "url"})
    text = await D.text_asset(r.manifest_url)
    if text:
        try:
            man = parse_manifest(text)
            out["manifest"] = _manifest_out(man)
            if not castor_ok(man.castor):
                out["problems"].append(f"It needs Castor {man.castor} and this is {CASTOR_VERSION}")
        except ValueError as exc:
            out["problems"].append(f"Its castor-plugin.yaml: {exc}")
    out["readme"] = (await D.text_asset(r.readme_url) or "")[:100_000] or None
    return out


@router.get("/plugin-directory/plugins/{plugin_id}/icon")
async def plugin_icon(plugin_id: str, _: Admin):
    """The newest release's icon, checked and served like an installed plugin's."""
    d = await _directory()
    e = d.entry(plugin_id)
    found = None
    if e is not None:
        try:
            found = await D.icon(e)
        except D.DirectoryError:
            found = None
    if found is None:
        raise HTTPException(404, "No icon")
    data, kind = found
    return Response(data, media_type=kind, headers={
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox",
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=86400"})


@router.get("/plugin-directory/publishers/{publisher_id}")
async def publisher_page(publisher_id: str, db: DB, _: Admin) -> dict[str, Any]:
    d = await _directory()
    pub = d.publisher(publisher_id)
    if pub is None:
        raise HTTPException(404, "The plugin directory has no such publisher")
    installed = await _installed(db)
    return {**pub.model_dump(mode="json"),
            "plugins": [await _entry_out(e, d, installed) for e in d.plugins if e.publisher == pub.id]}


@router.post("/plugin-directory/check")
async def check_now(_: Admin) -> dict[str, Any]:
    """Look for newer releases of installed plugins now (the daily check does the same)."""
    try:
        updates = await D.check_updates()
    except D.DirectoryError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"updates": updates}
