"""Instance-wide settings (Settings → General): one row per key in `app_settings`, falling
back to the defaults here. Add a setting by adding a field to `AppSettings`."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator
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
    # Check the plugin directory daily for newer releases of installed plugins (nothing updates
    # by itself; admins are told). Off for instances that shouldn't reach GitHub.
    plugin_update_checks: bool = True
    # Outbound requests (HTTP actions, alert channels; outbound.py): addresses exempt from the
    # block on loopback, link-local and reserved ones (CIDRs like 127.0.0.1/32), whether private
    # networks are blocked too, and the hosts HTTP actions may call (empty: any; *.example.com).
    http_allow_addresses: list[str] = Field(default_factory=list, max_length=100)
    http_block_private: bool = False
    http_allowed_hosts: list[str] = Field(default_factory=list, max_length=500)

    @field_validator("http_allow_addresses")
    @classmethod
    def cidrs(cls, value: list[str]) -> list[str]:
        import ipaddress

        out = []
        for item in value:
            try:
                out.append(str(ipaddress.ip_network(item.strip(), strict=False)))
            except ValueError:
                raise ValueError(f"{item!r} isn't an address or network (127.0.0.1/32, 10.0.0.0/8)") from None
        return list(dict.fromkeys(out))

    @field_validator("http_allowed_hosts")
    @classmethod
    def hosts(cls, value: list[str]) -> list[str]:
        import re

        out = []
        for item in value:
            host = item.strip().lower().rstrip(".")
            if not re.fullmatch(r"(\*\.)?[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*", host):
                raise ValueError(f"{item!r} isn't a host name (api.example.com or *.example.com)")
            out.append(host)
        return list(dict.fromkeys(out))


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
