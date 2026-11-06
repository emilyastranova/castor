"""Instance-wide settings (Settings → General): one row per key in `app_settings`, falling
back to the defaults here. Add a setting by adding a field to `AppSettings`."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m


class AppSettings(BaseModel):
    # Pinned notes a project can have. More than 4 wraps onto a second row of the dashboard.
    notes_per_project: int = Field(default=4, ge=1, le=12)


async def load(db: AsyncSession) -> AppSettings:
    rows = (await db.execute(select(m.AppSetting))).scalars()
    stored = {r.key: r.value for r in rows if r.key in AppSettings.model_fields}
    try:
        return AppSettings.model_validate(stored)
    except ValueError:
        # A stored value that no longer fits its limits falls back to the default.
        return AppSettings.model_validate({k: v for k, v in stored.items() if _fits(k, v)})


def _fits(key: str, value: Any) -> bool:
    try:
        AppSettings.model_validate({key: value})
        return True
    except ValueError:
        return False


async def save(db: AsyncSession, changes: dict[str, Any], user_id) -> AppSettings:
    """Validate and store the given keys (the caller commits)."""
    merged = (await load(db)).model_dump() | changes
    settings = AppSettings.model_validate(merged)
    for key in changes:
        row = await db.get(m.AppSetting, key)
        if row is None:
            db.add(m.AppSetting(key=key, value=getattr(settings, key), updated_by=user_id))
        else:
            row.value = getattr(settings, key)
            row.updated_by = user_id
    return settings
