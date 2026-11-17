"""How a sign-on provider's button looks on the sign-in page (Settings → Sign-on): its words, colour,
style and logo (an uploaded square icon or horizontal wordmark, with an optional one for dark themes,
or a Material Symbols icon).

Logos live in `<data_dir>/sso_logos` and are served publicly (the sign-in page needs them before
anyone has signed in). SVG is allowed, since wordmarks usually are SVG: an <img> never runs an SVG's
script, and the file itself is served with a Content-Security-Policy that forbids everything, so
opening it directly can't run script on Castor's origin either."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .avatars import image_type
from .config import settings

MAX_BYTES = 512 * 1024
COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
SYMBOL = re.compile(r"^[a-z0-9_]{1,40}$")
# Served with every logo: nothing in it may load or run, wherever it's opened.
LOGO_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox"


class BrandingError(ValueError):
    pass


class Branding(BaseModel):
    """What admins choose. Empty means Castor's default."""

    # The button's words; "" is "Continue with <name>". {name} is the provider's name.
    label: str = Field("", max_length=80)
    # theme: Castor's own (follows the theme); filled: the colour as the background; outlined:
    # the colour for the border and the words.
    style: Literal["theme", "filled", "outlined"] = "theme"
    color: str = ""
    # The words on a filled button; "" works it out from the colour (black or white, whichever reads).
    text_color: str = ""
    # none; symbol (a Material Symbols icon); icon (an uploaded square logo before the words);
    # wordmark (an uploaded horizontal logo instead of the words).
    logo: Literal["none", "symbol", "icon", "wordmark"] = "symbol"
    symbol: str = "key"

    @field_validator("color", "text_color")
    @classmethod
    def hex_color(cls, value: str) -> str:
        value = value.strip()
        if value and not COLOR.match(value):
            raise ValueError("a colour like #2f6feb")
        return value.lower()

    @field_validator("symbol")
    @classmethod
    def symbol_name(cls, value: str) -> str:
        value = value.strip() or "key"
        if not SYMBOL.match(value):
            raise ValueError("a Material Symbols icon name, like key or shield_person")
        return value

    @field_validator("label")
    @classmethod
    def plain(cls, value: str) -> str:
        return " ".join(value.split())


def stored(raw: dict[str, Any] | None) -> dict[str, Any]:
    """The branding kept on the row: the choices plus the uploaded logos' files."""
    raw = dict(raw or {})
    files = {k: raw.get(k) for k in ("logo_file", "logo_dark_file") if raw.get(k)}
    try:
        choices = Branding.model_validate({k: v for k, v in raw.items() if k in Branding.model_fields}).model_dump()
    except ValueError:
        choices = Branding().model_dump()
    return {**choices, **files, "logo_version": int(raw.get("logo_version") or 0)}


def contrast_text(color: str) -> str:
    """The words' colour on a filled button (WCAG contrast)."""
    r, g, b = (int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))

    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    lum = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
    white, black = 1.05 / (lum + 0.05), (lum + 0.05) / 0.05
    # White whenever it's readable (AA, 4.5:1), as brand buttons usually are; else the better one.
    return "#ffffff" if white >= 4.5 or white >= black else "#000000"


def public(provider_id: str, raw: dict[str, Any] | None) -> dict[str, Any]:
    """What the sign-in page needs to draw the button."""
    b = stored(raw)
    base = f"/api/auth/sso/{provider_id}/logo"
    v = b["logo_version"]
    logo = b["logo"]
    if logo in ("icon", "wordmark") and not b.get("logo_file"):
        logo = "symbol"  # chosen, but nothing uploaded yet
    text = b["text_color"] or (contrast_text(b["color"]) if b["color"] and b["style"] == "filled" else "")
    return {
        "label": b["label"], "style": b["style"] if b["color"] or b["style"] == "theme" else "theme", "color": b["color"],
        "text_color": text, "logo": logo, "symbol": b["symbol"],
        "logo_url": f"{base}?v={v}" if b.get("logo_file") else None,
        "logo_dark_url": f"{base}?variant=dark&v={v}" if b.get("logo_dark_file") else None,
    }


# ---------- logo files ----------

def directory() -> Path:
    path = settings.data_dir / "sso_logos"
    path.mkdir(parents=True, exist_ok=True)
    return path


def logo_type(data: bytes) -> tuple[str, str] | None:
    kind = image_type(data)
    if kind:
        return kind
    head = data[:512].lstrip().lower()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in data[:2048].lower()):
        return "image/svg+xml", "svg"
    return None


def save_logo(provider_id: str, version: int, variant: str, data: bytes) -> str:
    if len(data) > MAX_BYTES:
        raise BrandingError(f"the logo is larger than {MAX_BYTES // 1024} KB")
    kind = logo_type(data)
    if kind is None:
        raise BrandingError("upload an SVG, PNG, WebP, JPEG or GIF image")
    name = f"{provider_id}-{variant}-{version}.{kind[1]}"
    (directory() / name).write_bytes(data)
    return name


def remove_logo(name: str | None) -> None:
    if name and "/" not in name and "\\" not in name:
        (directory() / name).unlink(missing_ok=True)


def content_type(name: str) -> str:
    return {"svg": "image/svg+xml", "png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}.get(
        name.rsplit(".", 1)[-1], "application/octet-stream")
