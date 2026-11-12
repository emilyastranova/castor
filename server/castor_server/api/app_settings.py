"""Instance-wide settings: everyone can read them (the UI follows them), admins change them."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from .. import app_settings
from ..app_settings import AppSettings
from ..deps import DB, Admin, CurrentUser
from ..events import bus
from ..services import actor_of, record_activity

router = APIRouter(tags=["settings"])


@router.get("/settings", response_model=AppSettings)
async def read(db: DB, _: CurrentUser):
    return await app_settings.load(db)


@router.patch("/settings", response_model=AppSettings)
async def update(body: dict[str, Any], db: DB, user: Admin):
    unknown = set(body) - set(AppSettings.model_fields)
    if unknown:
        raise HTTPException(422, f"Unknown setting: {', '.join(sorted(unknown))}")
    if "plugins_restricted" in body:
        raise HTTPException(422, "plugins_restricted: change it with PUT /api/plugin-mode (Settings → Plugins)")
    if body.get("password_login") is False:
        # Never lock everyone out: a provider must be on, and the admin turning passwords off
        # must have signed in through one (so they know it works for them).
        from sqlalchemy import select

        from .. import models as m

        if not (await db.execute(select(m.SsoProvider.id).where(m.SsoProvider.enabled.is_(True)))).first():
            raise HTTPException(422, "password_login: turn on a sign-on provider first")
        if not (await db.execute(select(m.UserIdentity.id).where(m.UserIdentity.user_id == user.id))).first():
            raise HTTPException(422, "password_login: sign in with single sign-on yourself first, so you know it works")
    if body.get("default_theme"):
        from .. import models as m

        if await db.get(m.ThemeDefinition, str(body["default_theme"])) is None:
            raise HTTPException(422, f"default_theme: no theme “{body['default_theme']}”")
    try:
        settings = await app_settings.save(db, body, user.id)
    except ValidationError as exc:
        err = exc.errors()[0]
        raise HTTPException(422, f"{err['loc'][0]}: {err['msg']}") from exc
    await record_activity(db, type="settings.updated", message="Changed settings: " + ", ".join(
        k if isinstance(v, (dict, list)) else f"{k} = {v}" for k, v in body.items()),
                          project_id=None, **actor_of(user))
    await db.commit()
    bus.publish("settings.changed", None, settings.model_dump(mode="json"))
    if "default_theme" in body:
        bus.publish("themes.changed", None, {"default": settings.default_theme})
    return settings
