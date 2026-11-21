"""Company branding (Settings → Branding): the instance's name, a tagline, a square mark and a
horizontal wordmark (each with an optional version for dark themes), a footer line for reports, and
logos per client. They show on the sign-in page, at the top of the navigation rail, in the browser
tab and installed app, and on timeline exports (with the project's client's logo).

The words are app_settings (brand_name, brand_tagline, report_footer, powered_by). Uploaded files
live in `<data_dir>/branding`; which file is which is the `branding_logos` app_settings row, which
isn't an AppSettings field, so the settings form can't point it anywhere. The instance's logos are
public (the sign-in page needs them); a client's are for signed-in people. Files are served like
sign-on logos: SVG allowed, with a Content-Security-Policy that forbids everything."""

from __future__ import annotations

import base64
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m
from .config import settings
from .sso_branding import LOGO_CSP, MAX_BYTES, content_type, logo_type

KINDS = ("mark", "mark_dark", "wordmark", "wordmark_dark")
LOGOS_KEY = "branding_logos"
DEFAULT_NAME = "Castor"
HEADERS = {"Content-Security-Policy": LOGO_CSP, "X-Content-Type-Options": "nosniff"}


class BrandingError(ValueError):
    pass


def directory() -> Path:
    path = settings.data_dir / "branding"
    path.mkdir(parents=True, exist_ok=True)
    return path


def file_path(name: str | None) -> Path | None:
    if not name or "/" in name or "\\" in name:
        return None
    path = directory() / name
    return path if path.is_file() else None


def save(prefix: str, data: bytes) -> str:
    """Store an uploaded logo; returns its file name."""
    if len(data) > MAX_BYTES:
        raise BrandingError(f"the logo is larger than {MAX_BYTES // 1024} KB")
    kind = logo_type(data)
    if kind is None:
        raise BrandingError("upload an SVG, PNG, WebP, JPEG or GIF image")
    name = f"{prefix}-{uuid.uuid4().hex[:8]}.{kind[1]}"
    (directory() / name).write_bytes(data)
    return name


def remove(name: str | None) -> None:
    if path := file_path(name):
        path.unlink(missing_ok=True)


def data_uri(name: str | None) -> str | None:
    """A logo inside a self-contained document (exports)."""
    path = file_path(name)
    if path is None:
        return None
    return f"data:{content_type(path.name)};base64,{base64.b64encode(path.read_bytes()).decode()}"


# ---------- the instance's ----------

async def logos(db: AsyncSession) -> dict[str, str]:
    row = await db.get(m.AppSetting, LOGOS_KEY)
    value = row.value if row is not None and isinstance(row.value, dict) else {}
    return {k: v for k, v in value.items() if k in KINDS and isinstance(v, str)}


async def set_logo(db: AsyncSession, kind: str, name: str | None, user_id: uuid.UUID | None) -> None:
    """Keep (or, with None, forget) the instance's logo of a kind; the old file goes. Caller commits."""
    current = await logos(db)
    remove(current.get(kind))
    if name:
        current[kind] = name
    else:
        current.pop(kind, None)
    row = await db.get(m.AppSetting, LOGOS_KEY)
    if row is None:
        db.add(m.AppSetting(key=LOGOS_KEY, value=current, updated_by=user_id))
    else:
        row.value = current
        row.updated_by = user_id


def _url(kind: str, name: str | None) -> str | None:
    # The file name changes with each upload, so it versions the address (cached for good).
    return f"/api/branding/logo/{kind}?v={name.rsplit('-', 1)[-1].split('.')[0]}" if name else None


async def public(db: AsyncSession) -> dict[str, Any]:
    """What every page (and the sign-in page) needs. `name` is "" when it's Castor's own."""
    from .app_settings import load

    s = await load(db)
    files = await logos(db)
    return {
        "name": s.brand_name, "tagline": s.brand_tagline, "powered_by": s.powered_by,
        **{f"{k}_url": _url(k, files.get(k)) for k in KINDS},
        "mark_type": content_type(files["mark"]) if files.get("mark") else None,
    }


# ---------- a client's ----------

def client_logo_url(client: m.Client, variant: str = "light") -> str | None:
    name = client.logo_dark_file if variant == "dark" else client.logo_file
    if not name:
        return None
    version = name.rsplit("-", 1)[-1].split(".")[0]
    return f"/api/clients/{client.id}/logo?{'variant=dark&' if variant == 'dark' else ''}v={version}"


async def for_report(db: AsyncSession, project: m.Project) -> dict[str, Any]:
    """The instance's and the project's client's logos, as data URIs, for a timeline export."""
    from .app_settings import load

    s = await load(db)
    files = await logos(db)
    client = await db.get(m.Client, project.client_id) if project.client_id else None
    return {
        "name": s.brand_name, "footer": s.report_footer, "powered_by": s.powered_by,
        "wordmark": data_uri(files.get("wordmark")), "mark": data_uri(files.get("mark")),
        "client_name": client.name if client else "", "client_logo": data_uri(client.logo_file) if client else None,
    }
