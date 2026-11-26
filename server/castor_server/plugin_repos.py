"""Plugin repositories (docs/PLUGINS.md): Git repositories, public or private, that plugins are
installed from. Castor runs the real `git` client, so any URL it takes works (https, http, ssh,
`git@host:org/repo`, git://, file://, and remote helpers an operator allows in
CASTOR_PLUGIN_GIT_PROTOCOLS), with no credentials, an HTTPS username and token/password, or an SSH key.

Every folder in the repository with a castor-plugin.yaml is a plugin. Installing one packs that
folder the way castor-plugins' scripts/pack.py does and hands the .zip to the usual preview, where
an admin reviews what it asks for. Nothing in a repository is vetted.

What keeps this safe to run on the server:
- secrets reach git only through its environment (HTTPS, via a GIT_ASKPASS script) or a 0600 file
  in a private temporary folder (the SSH key), never a command line, a URL or the repository config;
- git runs with no global config (HOME is the temporary folder), credential helpers off, no
  terminal prompts, only the transports allowed, and ssh with its own known_hosts (trusted on first
  use and kept per repository), no ssh config, no agent;
- the repository is a bare clone fetched one commit deep; files are read from git's objects
  (`ls-tree`, `cat-file`), never checked out, so symlinks and submodules are only skipped entries.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import io
import logging
import os
import re
import shlex
import shutil
import stat
import tempfile
import uuid
import zipfile
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m
from . import vault
from .config import settings
from .plugin_manifest import parse_manifest

log = logging.getLogger(__name__)

MANIFEST = "castor-plugin.yaml"
# As castor-plugins' scripts/pack.py: what only matters while working on a plugin.
SKIP_DIRS = {"samples", "dist", "node_modules", ".git"}
SKIP_FILES = {"deno.json", "deno.lock", ".DS_Store"}
MAX_PLUGINS = 200  # castor-plugin.yaml files read per repository
AUTH_KINDS = ("none", "https", "ssh")

_ASKPASS = """#!/bin/sh
case "$1" in
  [Uu]sername*) printf '%s\\n' "$CASTOR_GIT_USERNAME" ;;
  *) printf '%s\\n' "$CASTOR_GIT_PASSWORD" ;;
esac
"""


class RepoError(Exception):
    pass


@dataclass
class Found:
    """A plugin folder in a repository."""

    path: str  # the folder ("" = the repository's root)
    id: str = ""
    name: str = ""
    version: str = ""
    publisher: str = ""
    description: str = ""
    homepage: str = ""
    icon: str | None = None  # its icon's path in the repository
    problem: str | None = None

    def out(self) -> dict[str, Any]:
        return {"path": self.path, "id": self.id, "name": self.name, "version": self.version, "publisher": self.publisher,
                "description": self.description, "homepage": self.homepage, "has_icon": bool(self.icon), "problem": self.problem}


@dataclass
class _Tree:
    commit: str
    entries: dict[str, tuple[str, str, int]] = field(default_factory=dict)  # path → (mode, object, size)


# ---------- the URL ----------


def check_url(url: str) -> str:
    """What git will be given: no option lookalikes, no control characters, and no password in it
    (that belongs in the secret, which is encrypted; a URL is shown to every admin)."""
    url = url.strip()
    if not url:
        raise RepoError("Give the repository's URL")
    if url.startswith("-") or any(c.isspace() or ord(c) < 32 for c in url):
        raise RepoError("That isn't a repository URL")
    if "://" in url and not url.lower().startswith("file://"):
        parts = urlsplit(url)
        if parts.password:
            raise RepoError("Leave the password or token out of the URL: put it in the token field, where it's encrypted")
    return url


def check_ref(ref: str) -> str:
    """A branch, tag or commit to fetch ("" = the default branch)."""
    ref = ref.strip()
    if ref and (ref.startswith("-") or not re.match(r"^[A-Za-z0-9._/+@-]{1,200}$", ref) or ".." in ref):
        raise RepoError("That isn't a branch or tag name")
    return ref


def is_ssh(url: str) -> bool:
    low = url.lower()
    if low.startswith(("ssh://", "git+ssh://", "ssh+git://")):
        return True
    # scp-like: [user@]host:path, as long as it isn't a URL or a local path
    return "://" not in url and not url.startswith(("/", ".")) and bool(re.match(r"^[^/:]+:", url)) and "::" not in url


def host_keys(known_hosts: str) -> list[dict[str, str]]:
    """The trusted host keys, as people compare them (SHA256 fingerprints)."""
    out = []
    for line in known_hosts.splitlines():
        parts = line.split()
        if len(parts) < 3 or line.startswith("#"):
            continue
        try:
            raw = base64.b64decode(parts[2])
        except ValueError:
            continue
        fp = base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")
        out.append({"host": parts[0], "type": parts[1], "fingerprint": f"SHA256:{fp}"})
    return out


def check_key(key: str, passphrase: str | None) -> None:
    """An SSH private key Castor can use (and its passphrase opens it)."""
    import asyncssh

    try:
        asyncssh.import_private_key(key, passphrase or None)
    except (asyncssh.KeyImportError, asyncssh.KeyEncryptionError) as exc:
        raise RepoError(f"That SSH key can't be read: {exc}") from exc


def public_key(repo: m.PluginRepository) -> str | None:
    """The SSH key's public half (to add to the repository as a deploy key), or None."""
    if repo.auth != "ssh" or not repo.secret:
        return None
    import asyncssh

    try:
        passphrase = vault.decrypt(repo.passphrase).decode() if repo.passphrase else None
        key = asyncssh.import_private_key(vault.decrypt(repo.secret).decode(), passphrase)
        return key.export_public_key().decode().strip()
    except (asyncssh.KeyImportError, asyncssh.KeyEncryptionError, vault.VaultError):
        return None


# ---------- running git ----------


def repo_dir(repo_id: uuid.UUID | str) -> Path:
    return settings.data_dir / "plugins" / ".repos" / f"{repo_id}.git"


_locks: dict[str, asyncio.Lock] = {}


def _lock(repo_id: Any) -> asyncio.Lock:
    return _locks.setdefault(str(repo_id), asyncio.Lock())


@contextlib.asynccontextmanager
async def _session(repo: m.PluginRepository) -> AsyncIterator[tuple[dict[str, str], Path, list[str]]]:
    """git's environment for this repository, in a private temporary folder that's removed after:
    the ASKPASS script, the SSH key and the known_hosts file. Yields (env, known_hosts path, the
    secret strings to keep out of error messages)."""
    tmp = Path(tempfile.mkdtemp(prefix="castor-git-"))
    try:
        os.chmod(tmp, 0o700)
        askpass = tmp / "askpass"
        askpass.write_text(_ASKPASS)
        askpass.chmod(0o700)
        known = tmp / "known_hosts"
        known.write_text(repo.known_hosts or "")
        known.chmod(0o600)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "SSH_"))}
        env.update({
            "HOME": str(tmp), "XDG_CONFIG_HOME": str(tmp), "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": str(askpass), "SSH_ASKPASS": str(askpass), "SSH_ASKPASS_REQUIRE": "never",
            "GIT_ALLOW_PROTOCOL": settings.plugin_git_protocols,  # overrides any protocol.allow
            "CASTOR_GIT_USERNAME": "", "CASTOR_GIT_PASSWORD": "", "LC_ALL": "C",
        })
        hide: list[str] = []
        ssh = ["ssh", "-F", os.devnull, "-o", f"UserKnownHostsFile={known}", "-o", "GlobalKnownHostsFile=/dev/null",
               "-o", "StrictHostKeyChecking=accept-new", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20",
               "-o", "UpdateHostKeys=no", "-o", "IdentityAgent=none", "-o", "IdentitiesOnly=yes"]
        if repo.auth == "https":
            password = vault.decrypt(repo.secret).decode() if repo.secret else ""
            env["CASTOR_GIT_USERNAME"] = repo.username or "git"
            env["CASTOR_GIT_PASSWORD"] = password
            hide.append(password)
        elif repo.auth == "ssh" and repo.secret:
            import asyncssh

            text = vault.decrypt(repo.secret).decode()
            passphrase = vault.decrypt(repo.passphrase).decode() if repo.passphrase else None
            try:
                key = asyncssh.import_private_key(text, passphrase)
            except (asyncssh.KeyImportError, asyncssh.KeyEncryptionError) as exc:
                raise RepoError(f"The SSH key can't be read: {exc}") from exc
            key_file = tmp / "id_key"
            fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(key.export_private_key("openssh"))  # unencrypted, for ssh, only in this folder
            ssh += ["-i", str(key_file)]
            if repo.username:
                ssh += ["-l", repo.username]
        env["GIT_SSH_COMMAND"] = shlex.join(ssh)
        env["GIT_SSH_VARIANT"] = "ssh"
        yield env, known, hide
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _scrub(text: str, hide: list[str]) -> str:
    for h in hide:
        if h:
            text = text.replace(h, "[secret]")
    return text


async def _git(args: list[str], env: dict[str, str], *, cwd: Path | None = None, stdin: bytes | None = None,
               hide: list[str] = (), timeout: float | None = None) -> bytes:  # type: ignore[assignment]
    cmd = ["git", "-c", "credential.helper=", "-c", "core.askPass=", "-c", "core.hooksPath=/dev/null",
           "-c", "core.fsmonitor=false", *args]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=cwd, env=env, stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
    except FileNotFoundError as exc:
        raise RepoError("git isn't installed on the Castor server") from exc
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), timeout or settings.plugin_git_timeout)
    except TimeoutError as exc:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        raise RepoError(f"git took longer than {int(timeout or settings.plugin_git_timeout)} s") from exc
    if proc.returncode != 0:
        lines = [x for x in _scrub(err.decode("utf-8", "replace"), hide).splitlines() if x.strip()]
        raise RepoError(_explain(lines) or f"git {args[0]} failed ({proc.returncode})")
    return out


def _explain(lines: list[str]) -> str:
    text = "\n".join(lines[-6:])
    if "REMOTE HOST IDENTIFICATION HAS CHANGED" in text or "Host key verification failed" in text:
        return ("The server's SSH host key isn't the one trusted before. If it changed on purpose, forget the trusted "
                "key and fetch again.\n" + text)
    if "transport" in text and "not allowed" in text:
        return f"git refused the URL's transport ({text}); CASTOR_PLUGIN_GIT_PROTOCOLS lists the ones allowed"
    return text


# ---------- fetching and reading ----------


async def fetch(db: AsyncSession, repo: m.PluginRepository) -> str:
    """Fetch the repository's branch or tag (one commit deep) into its bare clone. Records the
    commit, the time, any error and newly trusted host keys on the row (the caller commits)."""
    async with _lock(repo.id):
        where = repo_dir(repo.id)
        try:
            url = check_url(repo.url)
            async with _session(repo) as (env, known, hide):
                if not (where / "HEAD").is_file():
                    where.mkdir(parents=True, exist_ok=True)
                    await _git(["init", "--bare", "-q", "--template=", str(where)], env, hide=hide)
                ref = check_ref(repo.ref) or "HEAD"
                try:
                    await _git(["fetch", "-q", "--depth=1", "--no-tags", "--no-recurse-submodules", "--", url, ref],
                               env, cwd=where, hide=hide)
                finally:
                    # whatever ssh trusted on first use is kept, even if the fetch failed later on
                    with contextlib.suppress(OSError):
                        repo.known_hosts = known.read_text()[:20000]
                commit = (await _git(["rev-parse", "--verify", "-q", "FETCH_HEAD^{commit}"], env, cwd=where)).decode().strip()
                await _git(["update-ref", "refs/castor/head", commit], env, cwd=where)
        except (RepoError, vault.VaultError, OSError) as exc:
            repo.error = str(exc)[:4000]
            repo.fetched_at = datetime.now(UTC)
            raise RepoError(repo.error) from exc
        repo.commit, repo.error, repo.fetched_at = commit, None, datetime.now(UTC)
        return commit


def _plain_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
    return env


_trees: dict[str, _Tree] = {}


async def _tree(repo: m.PluginRepository) -> _Tree:
    """Every file at the fetched commit (read from git's objects; cached per commit)."""
    if not repo.commit:
        raise RepoError(repo.error or "It hasn't been fetched yet")
    key = f"{repo.id}:{repo.commit}"
    if key in _trees:
        return _trees[key]
    out = await _git(["ls-tree", "-r", "-z", "-l", "--full-tree", repo.commit], _plain_env(), cwd=repo_dir(repo.id))
    tree = _Tree(repo.commit)
    for item in out.split(b"\0"):
        if not item:
            continue
        meta, _, path = item.partition(b"\t")
        mode, kind, obj, size = meta.decode().split()
        if kind == "blob" and mode in ("100644", "100755"):  # not symlinks (120000) or submodules
            tree.entries[path.decode("utf-8", "surrogateescape")] = (mode, obj, int(size))
    for k in [k for k in _trees if k.startswith(f"{repo.id}:")]:
        del _trees[k]
    _trees[key] = tree
    return tree


async def _read(repo: m.PluginRepository, objects: list[str]) -> dict[str, bytes]:
    """Blobs by object id, in one `git cat-file --batch`."""
    if not objects:
        return {}
    out = await _git(["cat-file", "--batch"], _plain_env(), cwd=repo_dir(repo.id), stdin=("\n".join(objects) + "\n").encode())
    found: dict[str, bytes] = {}
    i = 0
    while i < len(out):
        nl = out.index(b"\n", i)
        head = out[i:nl].decode().split()
        if len(head) < 3:  # "<object> missing"
            i = nl + 1
            continue
        size = int(head[2])
        found[head[0]] = out[nl + 1:nl + 1 + size]
        i = nl + 1 + size + 1
    return found


def _skipped(rel: PurePosixPath) -> bool:
    return rel.parts[0] in SKIP_DIRS or rel.name in SKIP_FILES or rel.name.endswith("_test.ts")


async def scan(repo: m.PluginRepository) -> list[Found]:
    """The plugin folders at the fetched commit, each with what its manifest says (or why it can't be read)."""
    tree = await _tree(repo)
    manifests = sorted(p for p in tree.entries if PurePosixPath(p).name == MANIFEST
                       and not any(part in SKIP_DIRS for part in PurePosixPath(p).parts[:-1]))[:MAX_PLUGINS]
    blobs = await _read(repo, [tree.entries[p][1] for p in manifests])
    out = []
    for p in manifests:
        folder = str(PurePosixPath(p).parent)
        folder = "" if folder == "." else folder
        f = Found(path=folder)
        try:
            man = parse_manifest(blobs[tree.entries[p][1]].decode("utf-8", "replace"))
            f.id, f.name, f.version, f.publisher = man.id, man.name, man.version, man.publisher
            f.description, f.homepage = man.description, man.homepage
            if man.icon:
                icon = f"{folder}/{man.icon}" if folder else man.icon
                f.icon = icon if icon in tree.entries else None
        except (ValueError, KeyError) as exc:
            f.problem = f"{p}: {exc}"[:500]
        out.append(f)
    return out


async def find(repo: m.PluginRepository, path: str) -> Found:
    f = next((x for x in await scan(repo) if x.path == path.strip("/")), None)
    if f is None:
        raise RepoError(f"There's no plugin at {path or 'the root'} in {repo.name}")
    return f


async def package(repo: m.PluginRepository, path: str) -> bytes:
    """The plugin folder as the .zip Castor installs, packed as scripts/pack.py would: the same
    files give the same .zip (fixed timestamps, sorted)."""
    from .plugin_package import MAX_FILES, MAX_UNPACKED

    found = await find(repo, path)
    if found.problem:
        raise RepoError(found.problem)
    tree = await _tree(repo)
    prefix = f"{found.path}/" if found.path else ""
    chosen: list[tuple[str, str]] = []
    total = 0
    for p, (_mode, obj, size) in sorted(tree.entries.items()):
        if not p.startswith(prefix):
            continue
        rel = PurePosixPath(p[len(prefix):])
        if _skipped(rel):
            continue
        total += size
        chosen.append((rel.as_posix(), obj))
        if len(chosen) > MAX_FILES or total > MAX_UNPACKED:
            raise RepoError(f"{found.name} is too big to install (over {MAX_FILES} files or {MAX_UNPACKED // 1024**2} MB)")
    blobs = await _read(repo, list(dict.fromkeys(o for _, o in chosen)))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, obj in chosen:
            info = zipfile.ZipInfo(rel, date_time=(2020, 1, 1, 0, 0, 0))
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, blobs[obj])
    return buf.getvalue()


async def icon(repo: m.PluginRepository, path: str) -> tuple[bytes, str] | None:
    from .plugin_package import PackageError, check_icon, icon_type

    found = await find(repo, path)
    if not found.icon:
        return None
    tree = await _tree(repo)
    data = (await _read(repo, [tree.entries[found.icon][1]])).get(tree.entries[found.icon][1])
    if data is None:
        return None
    try:
        check_icon(found.icon, data)
    except PackageError:
        return None
    return data, icon_type(found.icon)


def forget(repo_id: Any) -> None:
    shutil.rmtree(repo_dir(repo_id), ignore_errors=True)
    for k in [k for k in _trees if k.startswith(f"{repo_id}:")]:
        del _trees[k]


# ---------- installed plugins from repositories ----------


def source_of(repo_id: Any, path: str) -> str:
    return f"repo:{repo_id}:{path}"


def parse_source(source: str | None) -> tuple[uuid.UUID, str] | None:
    """(repository id, folder) for a plugin installed from a repository."""
    if not source or not source.startswith("repo:"):
        return None
    rid, _, path = source[5:].partition(":")
    try:
        return uuid.UUID(rid), path
    except ValueError:
        return None


async def check_updates(db: AsyncSession) -> list[dict[str, str]]:
    """Fetch each enabled repository a plugin was installed from and record a newer version
    (same id and publisher) as the plugin's latest; admins are told once per new version."""
    from .plugin_directory import note_latest

    found: list[dict[str, str]] = []
    plugins = [p for p in (await db.execute(select(m.Plugin))).scalars() if parse_source(p.source)]
    repos = {r.id: r for r in (await db.execute(select(m.PluginRepository))).scalars()}
    scanned: dict[uuid.UUID, list[Found] | None] = {}
    now = datetime.now(UTC)
    for p in plugins:
        rid, path = parse_source(p.source)  # type: ignore[misc]
        repo = repos.get(rid)
        if repo is None or not repo.enabled:
            continue
        if rid not in scanned:
            try:
                await fetch(db, repo)
                scanned[rid] = await scan(repo)
            except RepoError as exc:
                log.info("plugin repository %s: %s", repo.name, exc)
                scanned[rid] = None
        plugins_there = scanned[rid]
        if plugins_there is None:
            continue
        same = [f for f in plugins_there if f.id == p.id and f.publisher == p.publisher and not f.problem]
        match = next((f for f in same if f.path == path), same[0] if same else None)
        p.checked_at = now
        version = await note_latest(db, p, match.version if match else None)
        if version:
            found.append({"id": p.id, "name": p.name, "from": p.version, "to": version})
    return found
