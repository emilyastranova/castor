"""Instance-wide settings (Settings → General): one row per key in `app_settings`, falling
back to the defaults here. Add a setting by adding a field to `AppSettings`."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m


class ModelChoice(BaseModel):
    provider: str  # an ai_providers id
    model: str = Field(min_length=1, max_length=200)


class AppSettings(BaseModel):
    # Pinned notes a project can have. More than 4 wraps onto a second row of the dashboard.
    notes_per_project: int = Field(default=4, ge=1, le=12)
    # LLM models per purpose, tried in order (later ones are fallbacks): "default" for
    # generating things, "resolver" for deciding paused playbook steps (empty: use default).
    ai_chains: dict[str, list[ModelChoice]] = Field(default_factory=dict)
    # Signing in with a username and password. Off (with a sign-on provider enabled): only
    # single sign-on; `castor-admin password-login on` turns it back on from the shell.
    password_login: bool = True
    # The theme for everyone who hasn't picked one, and for the sign-in page on a browser that has
    # never signed in: a theme_definitions id, or "" for Castor's own.
    default_theme: str = Field(default="", max_length=64)
    # Plugins: while on, unrestricted plugins (full access to this server) can't be installed or run.
    # CASTOR_PLUGINS_RESTRICTED=1 keeps it on whatever this says.
    plugins_restricted: bool = True


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
    stored = settings.model_dump(mode="json")
    for key in changes:
        row = await db.get(m.AppSetting, key)
        if row is None:
            db.add(m.AppSetting(key=key, value=stored[key], updated_by=user_id))
        else:
            row.value = stored[key]
            row.updated_by = user_id
    return settings
