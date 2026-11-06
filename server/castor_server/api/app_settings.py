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
    return settings
