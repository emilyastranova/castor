"""Plugin repositories for admins (plugin_repos.py): add Git repositories, public or private, fetch
them and browse the plugins in them. Installing goes through the usual preview
(`POST /api/plugins/preview` with `repository` and `path`). Secrets are write-only."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import models as m
from .. import plugin_repos as R
from .. import vault
from ..deps import DB, Admin, parse_uuid

router = APIRouter(tags=["plugins"])


class RepoIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=1, max_length=1000)
    ref: str = Field("", max_length=200)
    auth: Literal["none", "https", "ssh"] = "none"
    username: str = Field("", max_length=200)
    # Write-only. Left out on a change: kept; "" clears it.
    secret: str | None = Field(None, max_length=20000)
    passphrase: str | None = Field(None, max_length=1000)
    # SSH: make a key here (its public half is in `public_key`, to add to the repository as a deploy key).
    generate_key: bool = False
    enabled: bool = True


class RepoPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    url: str | None = Field(None, min_length=1, max_length=1000)
    ref: str | None = Field(None, max_length=200)
    auth: Literal["none", "https", "ssh"] | None = None
    username: str | None = Field(None, max_length=200)
    secret: str | None = Field(None, max_length=20000)
    passphrase: str | None = Field(None, max_length=1000)
    generate_key: bool = False
    enabled: bool | None = None


def repo_out(r: m.PluginRepository, plugins: list[R.Found] | None = None) -> dict[str, Any]:
    return {"id": str(r.id), "name": r.name, "url": r.url, "ref": r.ref, "auth": r.auth, "username": r.username,
            "has_secret": bool(r.secret), "has_passphrase": bool(r.passphrase), "ssh_url": R.is_ssh(r.url),
            "public_key": R.public_key(r),
            "host_keys": R.host_keys(r.known_hosts), "enabled": r.enabled, "commit": r.commit,
            "fetched_at": r.fetched_at.isoformat() if r.fetched_at else None, "error": r.error,
            "plugins": None if plugins is None else len([p for p in plugins if not p.problem]),
            "created_at": r.created_at.isoformat() if r.created_at else None}


async def _or_404(db: DB, repo_id: str) -> m.PluginRepository:
    r = await db.get(m.PluginRepository, parse_uuid(repo_id))
    if r is None:
        raise HTTPException(404, "No such plugin repository")
    return r


async def _count(r: m.PluginRepository) -> list[R.Found] | None:
    try:
        return await R.scan(r) if r.commit else None
    except R.RepoError:
        return None


def _apply_secrets(r: m.PluginRepository, secret: str | None, passphrase: str | None, generate: bool = False) -> None:
    if generate:
        import asyncssh

        r.auth = "ssh"
        r.secret = vault.encrypt(asyncssh.generate_private_key("ssh-ed25519", comment=f"castor plugins: {r.name}")
                                 .export_private_key("openssh"))
        r.passphrase = None
        return
    if secret is not None:
        r.secret = vault.encrypt(secret.strip().encode() if r.auth == "https" else (secret.strip() + "\n").encode()) if secret.strip() else None
    if passphrase is not None:
        r.passphrase = vault.encrypt(passphrase.encode()) if passphrase else None


def _check(r: m.PluginRepository) -> None:
    try:
        r.url = R.check_url(r.url)
        r.ref = R.check_ref(r.ref)
        if r.auth == "ssh" and r.secret:
            R.check_key(vault.decrypt(r.secret).decode(), vault.decrypt(r.passphrase).decode() if r.passphrase else None)
        if r.auth == "https" and not r.secret:
            raise R.RepoError("HTTPS sign-in needs a token or password")
        if r.auth == "ssh" and not r.secret:
            raise R.RepoError("SSH sign-in needs a private key")
    except R.RepoError as exc:
        raise HTTPException(422, str(exc)) from exc
    if r.auth == "none":
        r.secret = r.passphrase = None
    if r.auth != "ssh":
        r.passphrase = None


@router.get("/plugin-repositories")
async def list_repos(db: DB, _: Admin) -> list[dict[str, Any]]:
    rows = (await db.execute(select(m.PluginRepository).order_by(m.PluginRepository.name))).scalars().all()
    return [repo_out(r, await _count(r)) for r in rows]


@router.post("/plugin-repositories", status_code=201)
async def add_repo(body: RepoIn, db: DB, user: Admin) -> dict[str, Any]:
    """Add a Git repository (any URL git takes) and fetch it. It's kept even if the fetch fails:
    its `error` says why."""
    r = m.PluginRepository(name=body.name.strip(), url=body.url, ref=body.ref, auth=body.auth, username=body.username.strip(),
                           enabled=body.enabled, created_by=user.id, known_hosts="")
    _apply_secrets(r, body.secret, body.passphrase, body.generate_key)
    _check(r)
    db.add(r)
    await db.flush()
    if r.enabled:
        try:
            await R.fetch(db, r)
        except R.RepoError:
            pass  # recorded on the row
    await db.commit()
    return repo_out(r, await _count(r))


@router.patch("/plugin-repositories/{repo_id}")
async def update_repo(repo_id: str, body: RepoPatch, db: DB, _: Admin) -> dict[str, Any]:
    r = await _or_404(db, repo_id)
    if body.auth is not None and body.auth != r.auth and body.secret is None and not body.generate_key:
        r.secret = r.passphrase = None  # one way of signing in's secret is never another's
    moved = False
    for name in ("name", "url", "ref", "auth", "username", "enabled"):
        value = getattr(body, name)
        if value is not None:
            value = value.strip() if isinstance(value, str) else value
            moved |= name in ("url", "ref") and value != getattr(r, name)
            setattr(r, name, value)
    if moved:  # another repository, or another branch: its host keys and files are someone else's
        if body.url is not None:
            r.known_hosts = ""
        r.commit = None
        R.forget(r.id)
    _apply_secrets(r, body.secret, body.passphrase, body.generate_key)
    _check(r)
    if r.enabled and (moved or body.secret is not None or body.auth is not None or body.generate_key):
        try:
            await R.fetch(db, r)
        except R.RepoError:
            pass
    await db.commit()
    return repo_out(r, await _count(r))


@router.delete("/plugin-repositories/{repo_id}", status_code=204)
async def delete_repo(repo_id: str, db: DB, _: Admin) -> Response:
    """Remove it. Plugins installed from it stay, without update checks."""
    r = await _or_404(db, repo_id)
    await db.delete(r)
    await db.commit()
    R.forget(r.id)
    return Response(status_code=204)


@router.post("/plugin-repositories/{repo_id}/fetch")
async def fetch_repo(repo_id: str, db: DB, _: Admin) -> dict[str, Any]:
    """Fetch it now (its branch or tag, one commit deep)."""
    r = await _or_404(db, repo_id)
    try:
        await R.fetch(db, r)
    except R.RepoError:
        pass
    await db.commit()
    return repo_out(r, await _count(r))


@router.post("/plugin-repositories/{repo_id}/forget-host-keys")
async def forget_host_keys(repo_id: str, db: DB, _: Admin) -> dict[str, Any]:
    """Stop trusting the SSH host keys seen before; the next fetch trusts what the server shows then."""
    r = await _or_404(db, repo_id)
    r.known_hosts = ""
    await db.commit()
    return repo_out(r, await _count(r))


@router.get("/plugin-repositories/plugins")
async def repo_plugins(db: DB, _: Admin) -> dict[str, Any]:
    """Every plugin in the enabled repositories (as last fetched), and whether it's installed here."""
    installed = {p.id: p for p in (await db.execute(select(m.Plugin))).scalars()}
    repos = (await db.execute(select(m.PluginRepository).where(m.PluginRepository.enabled.is_(True))
                              .order_by(m.PluginRepository.name))).scalars().all()
    out: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for r in repos:
        if r.error:
            errors.append({"repository": str(r.id), "name": r.name, "error": r.error})
        try:
            found = await R.scan(r) if r.commit else []
        except R.RepoError as exc:
            errors.append({"repository": str(r.id), "name": r.name, "error": str(exc)})
            continue
        for f in found:
            here = installed.get(f.id) if f.id else None
            src = R.parse_source(here.source) if here else None
            mine = bool(here and here.publisher == f.publisher)
            out.append({**f.out(), "repository": {"id": str(r.id), "name": r.name}, "source": R.source_of(r.id, f.path),
                        "icon_url": f"/api/plugin-repositories/{r.id}/icon?path={f.path}" if f.icon else None,
                        "installed": here.version if mine else None,
                        "installed_here": bool(mine and src == (r.id, f.path)),
                        "taken": bool(here and not mine),
                        "update": bool(mine and f.version and not f.problem and _newer(f.version, here.version))})  # type: ignore[union-attr]
    return {"plugins": out, "errors": errors}


def _newer(candidate: str, installed: str) -> bool:
    from ..plugin_directory import newer

    try:
        return newer(candidate, installed)
    except ValueError:
        return False


@router.get("/plugin-repositories/{repo_id}/icon")
async def repo_icon(repo_id: str, db: DB, _: Admin, path: str = "") -> Response:
    """A plugin's icon at the fetched commit, checked and served like an installed plugin's."""
    r = await _or_404(db, repo_id)
    try:
        found = await R.icon(r, path)
    except R.RepoError:
        found = None
    if found is None:
        raise HTTPException(404, "No icon")
    data, kind = found
    return Response(data, media_type=kind, headers={
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox",
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=300"})


async def package_for_preview(db: DB, repository: str, path: str) -> tuple[bytes, str]:
    """The newest of a repository's plugin, fetched now and packed, for `POST /api/plugins/preview`."""
    r = await db.get(m.PluginRepository, parse_uuid(repository))
    if r is None:
        raise HTTPException(404, "No such plugin repository")
    try:
        await R.fetch(db, r)
        await db.commit()
        return await R.package(r, path), R.source_of(r.id, path.strip("/"))
    except R.RepoError as exc:
        await db.commit()
        raise HTTPException(422, str(exc)) from exc

