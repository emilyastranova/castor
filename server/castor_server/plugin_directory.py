"""The plugin directory (docs/PLUGINS.md, phase E): a public `directory.json` listing publishers and
plugins, each plugin's releases published on GitHub. Castor reads the directory, finds a plugin's
newest release, downloads its .zip and checks it against the release's SHA-256 before the usual
preview (nothing in the directory is vetted: the install review is what keeps an instance safe).
Update checks run daily and tell admins; nothing updates by itself."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select

from . import models as m
from .config import settings
from .db import SessionLocal
from .events import bus
from .plugin_manifest import ID, VERSION, version_key

log = logging.getLogger(__name__)

CACHE_TTL = 3600  # the directory and each repository's releases, in memory
MAX_DIRECTORY = 5 * 1024**2
MAX_SMALL = 1024**2  # a manifest, README or checksum file
MAX_ZIP = 100 * 1024**2
REPO = r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$"


class DirectoryError(Exception):
    pass


class Publisher(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(pattern=ID)
    name: str = Field(min_length=1, max_length=120)
    # The GitHub accounts (users or organisations) whose repositories it may list plugins from.
    github: list[str] = Field(min_length=1, max_length=10)
    url: str = Field("", max_length=300)
    description: str = Field("", max_length=500)


class Entry(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(pattern=ID)
    name: str = Field(min_length=1, max_length=120)
    publisher: str = Field(pattern=ID)
    description: str = Field("", max_length=500)
    repo: str = Field(pattern=REPO)  # owner/name on GitHub
    path: str = Field("", max_length=200)  # its folder in the repository, when that holds several
    # Releases whose tag starts with this are this plugin's (a repository with several plugins);
    # the rest of the tag is the version, with or without a leading v.
    tag_prefix: str = Field("", max_length=80)
    tags: list[str] = Field(default_factory=list, max_length=12)

    @property
    def source_url(self) -> str:
        base = f"https://github.com/{self.repo}"
        return f"{base}/tree/HEAD/{self.path}" if self.path else base


class Directory(BaseModel):
    model_config = ConfigDict(extra="ignore")
    version: int = 1
    publishers: list[Publisher] = Field(default_factory=list)
    plugins: list[Entry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> Directory:
        pubs = {p.id: p for p in self.publishers}
        if len(pubs) != len(self.publishers):
            raise ValueError("two publishers have the same id")
        ids = [p.id for p in self.plugins]
        if len(ids) != len(set(ids)):
            raise ValueError("two plugins have the same id")
        for e in self.plugins:
            pub = pubs.get(e.publisher)
            if pub is None:
                raise ValueError(f"{e.id}: no publisher {e.publisher}")
            owner = e.repo.split("/", 1)[0].lower()
            if owner not in {g.lower() for g in pub.github}:
                raise ValueError(f"{e.id}: {e.repo} isn't one of {pub.name}'s GitHub accounts")
        return self

    def entry(self, plugin_id: str) -> Entry | None:
        return next((e for e in self.plugins if e.id == plugin_id), None)

    def publisher(self, publisher_id: str) -> Publisher | None:
        return next((p for p in self.publishers if p.id == publisher_id), None)


class Release(BaseModel):
    version: str
    tag: str
    published_at: str | None = None
    notes: str = ""
    url: str = ""  # its page on GitHub
    zip_url: str
    sha_url: str | None = None
    manifest_url: str | None = None
    readme_url: str | None = None
    icon_url: str | None = None


def enabled() -> bool:
    return bool(settings.plugin_directory_url.strip())


# ---------- fetching ----------


def _allowed(url: str) -> bool:
    """https, or the configured GitHub API's own origin (a local stand-in in tests)."""
    if url.startswith("https://"):
        return True
    api = urlsplit(settings.github_api_url)
    got = urlsplit(url)
    return (got.scheme, got.netloc) == (api.scheme, api.netloc)


async def _get(url: str, limit: int, *, api: bool = False) -> bytes:
    if not _allowed(url):
        raise DirectoryError(f"{url}: only https:// links")
    headers = {"User-Agent": "castor-plugin-directory"}
    if api:
        headers["Accept"] = "application/vnd.github+json"
        if settings.github_token:
            headers["Authorization"] = f"Bearer {settings.github_token}"
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client, \
                client.stream("GET", url, headers=headers) as r:
            if r.status_code == 403 and api and r.headers.get("x-ratelimit-remaining") == "0":
                raise DirectoryError("GitHub's rate limit is used up for now (CASTOR_GITHUB_TOKEN raises it)")
            if r.status_code >= 400:
                raise DirectoryError(f"{url}: HTTP {r.status_code}")
            chunks, size = [], 0
            async for chunk in r.aiter_bytes():
                size += len(chunk)
                if size > limit:
                    raise DirectoryError(f"{url}: over {limit // 1024**2 or 1} MB")
                chunks.append(chunk)
            return b"".join(chunks)
    except httpx.HTTPError as exc:
        raise DirectoryError(f"{url}: {exc}") from exc


def _cache_dir() -> Path:
    return settings.data_dir / "plugins" / ".directory"


_directory: tuple[float, Directory] | None = None
_releases: dict[str, tuple[float, list[dict[str, Any]]]] = {}
last_error: str | None = None


def clear_cache() -> None:
    global _directory, last_error
    _directory, last_error = None, None
    _releases.clear()


async def directory(force: bool = False) -> Directory:
    """The directory, at most an hour old; the last copy that was read if it can't be reached now."""
    global _directory, last_error
    if not enabled():
        raise DirectoryError("The plugin directory is turned off on this server (CASTOR_PLUGIN_DIRECTORY_URL)")
    if _directory and not force and time.monotonic() - _directory[0] < CACHE_TTL:
        return _directory[1]
    saved = _cache_dir() / "directory.json"
    url = settings.plugin_directory_url.strip()
    try:
        if url.startswith(("file://", "/")):
            raw = Path(url.removeprefix("file://")).read_bytes()
        else:
            raw = await _get(url, MAX_DIRECTORY)
        found = Directory.model_validate(json.loads(raw))
        last_error = None
        with contextlib.suppress(OSError):
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_bytes(raw)
    except (DirectoryError, OSError, ValueError, ValidationError) as exc:
        last_error = f"Couldn't read the plugin directory: {exc}"
        log.warning(last_error)
        if _directory:
            return _directory[1]
        try:
            found = Directory.model_validate(json.loads(saved.read_bytes()))
        except (OSError, ValueError, ValidationError):
            raise DirectoryError(last_error) from exc
    _directory = (time.monotonic(), found)
    return found


async def _repo_releases(repo: str, force: bool = False) -> list[dict[str, Any]]:
    cached = _releases.get(repo)
    if cached and not force and time.monotonic() - cached[0] < CACHE_TTL:
        return cached[1]
    raw = await _get(f"{settings.github_api_url.rstrip('/')}/repos/{repo}/releases?per_page=100", MAX_DIRECTORY, api=True)
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise DirectoryError(f"{repo}: GitHub's answer isn't JSON") from exc
    if not isinstance(data, list):
        raise DirectoryError(f"{repo}: no releases")
    _releases[repo] = (time.monotonic(), data)
    return data


def release_of(entry: Entry, raw: dict[str, Any]) -> Release | None:
    """A GitHub release as one of this plugin's, if it is one and has the plugin's .zip."""
    if raw.get("draft") or raw.get("prerelease"):
        return None
    tag = str(raw.get("tag_name") or "")
    if not tag.startswith(entry.tag_prefix):
        return None
    version = tag[len(entry.tag_prefix):].removeprefix("v")
    if not re.match(VERSION, version) or "-" in version:
        return None
    assets = {str(a.get("name")): str(a.get("browser_download_url") or "") for a in raw.get("assets") or []
              if isinstance(a, dict)}
    zip_name = f"{entry.id}-{version}.zip"
    if not assets.get(zip_name):
        return None
    icon = next((assets[n] for n in ("icon.svg", "icon.png", "icon.webp") if assets.get(n)), None)
    return Release(version=version, tag=tag, published_at=raw.get("published_at"), notes=str(raw.get("body") or "")[:20000],
                   url=str(raw.get("html_url") or ""), zip_url=assets[zip_name], sha_url=assets.get(f"{zip_name}.sha256"),
                   manifest_url=assets.get("castor-plugin.yaml"), readme_url=assets.get("README.md"), icon_url=icon)


async def latest(entry: Entry, force: bool = False) -> Release | None:
    found = [r for raw in await _repo_releases(entry.repo, force) if (r := release_of(entry, raw))]
    return max(found, key=lambda r: version_key(r.version), default=None)


async def text_asset(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return (await _get(url, MAX_SMALL)).decode("utf-8", "replace")
    except DirectoryError as exc:
        log.info("plugin directory: %s", exc)
        return None


async def download(entry: Entry) -> tuple[bytes, Release]:
    """The newest release's .zip, checked against its published SHA-256."""
    release = await latest(entry, force=True)
    if release is None:
        raise DirectoryError(f"{entry.name} has no release Castor can install yet")
    if not release.sha_url:
        raise DirectoryError(f"{entry.name} {release.version} has no {entry.id}-{release.version}.zip.sha256 to check it against")
    expected = (await _get(release.sha_url, MAX_SMALL)).decode("ascii", "replace").split()
    data = await _get(release.zip_url, MAX_ZIP)
    if not expected or hashlib.sha256(data).hexdigest() != expected[0].lower():
        raise DirectoryError(f"{entry.name} {release.version}: the download doesn't match its published SHA-256")
    return data, release


async def icon(entry: Entry) -> tuple[bytes, str] | None:
    """The newest release's icon (cached on disk per release), checked like an installed one's."""
    from .plugin_package import PackageError, check_icon, icon_type

    release = await latest(entry)
    if release is None or not release.icon_url:
        return None
    name = release.icon_url.rsplit("/", 1)[-1]
    cached = _cache_dir() / "icons" / f"{entry.id}-{release.version}-{name}"
    if cached.is_file():
        return cached.read_bytes(), icon_type(name)
    data = await _get(release.icon_url, MAX_SMALL)
    try:
        check_icon(name, data)
    except PackageError:
        return None
    with contextlib.suppress(OSError):
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
    return data, icon_type(name)


# ---------- update checks ----------


def newer(candidate: str | None, installed: str) -> bool:
    return bool(candidate) and version_key(candidate) > version_key(installed)  # type: ignore[arg-type]


async def check_updates(force: bool = True) -> list[dict[str, str]]:
    """Look up each installed plugin the directory lists (same id and publisher) and record the
    newest release when it's newer. Admins are told once per new version."""
    from .accounts import active_admin_ids
    from .notifications import notify

    found: list[dict[str, str]] = []
    d = await directory(force=force)
    if force:
        _releases.clear()
    async with SessionLocal() as db:
        plugins = (await db.execute(select(m.Plugin))).scalars().all()
        now = datetime.now(UTC)
        for p in plugins:
            entry = d.entry(p.id)
            if entry is None or entry.publisher != p.publisher:
                continue
            try:
                release = await latest(entry)
            except DirectoryError as exc:
                log.info("update check for %s: %s", p.id, exc)
                continue
            p.checked_at = now
            version = release.version if release and newer(release.version, p.version) else None
            if version and version != p.latest_version:
                await notify(db, kind="plugin.update", title=f"{p.name} {version} is available",
                             body=f"Installed: {p.version}. Review what it asks for before updating.",
                             link="/settings/plugins", subject=f"plugin-update:{p.id}", user_ids=await active_admin_ids(db),
                             payload={"id": p.id, "from": p.version, "to": version}, webhooks=False)
            p.latest_version = version
            if version:
                found.append({"id": p.id, "name": p.name, "from": p.version, "to": version})
        await db.commit()
    bus.publish("plugins.changed", None, {"updates": len(found)})
    return found


async def check_forever() -> None:
    """Started with the app: an update check now and then every CASTOR_PLUGIN_UPDATE_INTERVAL
    seconds, while the directory is on and Settings → Plugins allows it."""
    from . import app_settings

    await asyncio.sleep(30)  # let startup finish first
    while True:
        try:
            if enabled():
                async with SessionLocal() as db:
                    on = (await app_settings.load(db)).plugin_update_checks
                if on:
                    await check_updates()
        except asyncio.CancelledError:
            raise
        except Exception:  # never let a bad answer stop the loop
            log.exception("plugin update check failed")
        await asyncio.sleep(max(300, settings.plugin_update_interval))
