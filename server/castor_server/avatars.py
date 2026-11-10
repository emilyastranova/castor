"""Profile pictures: checking uploads and URLs, and storing uploaded files.

Uploads must really be PNG, JPEG, WebP or GIF (checked from their first bytes, whatever
the file is called); SVG is refused because, served from Castor's own origin, it could
run script. Image URLs must be https (they load in everyone's browser)."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from .config import settings

MAX_BYTES = 2 * 1024 * 1024
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AvatarError(ValueError):
    pass


def image_type(data: bytes) -> tuple[str, str] | None:
    """(content type, extension) from the file's magic bytes, or None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif", "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", "webp"
    return None


def check_url(url: str) -> str:
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc or any(c.isspace() for c in url):
        raise AvatarError("the picture's URL must start with https://")
    return url


def check_email(email: str) -> str:
    email = email.strip()
    if not EMAIL.match(email):
        raise AvatarError("that doesn't look like an email address")
    return email


def directory() -> Path:
    path = settings.data_dir / "avatars"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save(user_id, version: int, data: bytes) -> str:
    """Store an upload; returns its file name (relative to the avatars directory)."""
    if len(data) > MAX_BYTES:
        raise AvatarError(f"the picture is larger than {MAX_BYTES // (1024 * 1024)} MB")
    kind = image_type(data)
    if kind is None:
        raise AvatarError("upload a PNG, JPEG, WebP or GIF image")
    name = f"{user_id}-{version}.{kind[1]}"
    (directory() / name).write_bytes(data)
    return name


COMPANION_MAX = 3 * 1024 * 1024
DEFAULT_SEAT = 0.605  # web/static/companion/sitting.webp


def companion_dir() -> Path:
    path = settings.data_dir / "companions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_companion(user_id, version: int, data: bytes) -> str:
    """Store someone's own companion picture; returns its file name."""
    if len(data) > COMPANION_MAX:
        raise AvatarError(f"the picture is larger than {COMPANION_MAX // (1024 * 1024)} MB")
    kind = image_type(data)
    if kind is None:
        raise AvatarError("upload a PNG, WebP, GIF or JPEG image (PNG or WebP with a transparent background works best)")
    name = f"{user_id}-{version}.{kind[1]}"
    (companion_dir() / name).write_bytes(data)
    return name


def remove_companion(name: str) -> None:
    if name and "/" not in name and "\\" not in name:
        (companion_dir() / name).unlink(missing_ok=True)


def remove(name: str) -> None:
    if name and "/" not in name and "\\" not in name:
        (directory() / name).unlink(missing_ok=True)
