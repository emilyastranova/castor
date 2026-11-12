"""A plugin's release: a .zip with castor-plugin.yaml at its root (or in its one top folder).
Unpacked once into <data_dir>/plugins/<id>/<version>-<sha12>/ and never changed after: no paths
outside it, no links, size and file-count limits. Staged uploads wait in <data_dir>/plugins/.staged
for the admin to approve them."""

from __future__ import annotations

import hashlib
import io
import secrets
import shutil
import stat
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .config import settings
from .plugin_manifest import MANIFEST, Manifest, parse_manifest

MAX_ZIP = 100 * 1024**2
MAX_UNPACKED = 300 * 1024**2
MAX_FILES = 2000
STAGE_TTL = 3600


class PackageError(ValueError):
    pass


def root() -> Path:
    return settings.data_dir / "plugins"


def _staged_dir() -> Path:
    return root() / ".staged"


@dataclass
class Package:
    manifest: Manifest
    checksum: str  # SHA-256 of the .zip
    files: dict[str, bytes]  # path inside the plugin → content

    @property
    def size(self) -> int:
        return sum(len(b) for b in self.files.values())


def read_zip(data: bytes) -> Package:
    """Check a release and read it into memory (it's small; limits above)."""
    if len(data) > MAX_ZIP:
        raise PackageError(f"The file is over {MAX_ZIP // 1024**2} MB")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise PackageError("That isn't a .zip file") from exc
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > MAX_FILES:
        raise PackageError(f"More than {MAX_FILES} files")
    names = [i.filename for i in infos]
    prefix = ""
    if MANIFEST not in names:
        tops = {n.split("/", 1)[0] for n in names}
        if len(tops) == 1 and f"{next(iter(tops))}/{MANIFEST}" in names:
            prefix = next(iter(tops)) + "/"
        else:
            raise PackageError(f"No {MANIFEST} at the top of the .zip")
    files: dict[str, bytes] = {}
    total = 0
    for info in infos:
        name = info.filename
        if not name.startswith(prefix):
            continue
        rel = PurePosixPath(name[len(prefix):])
        if rel.is_absolute() or ".." in rel.parts or "\\" in name or not rel.parts:
            raise PackageError(f"{name}: a path outside the plugin")
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise PackageError(f"{name}: links aren't allowed")
        total += info.file_size
        if total > MAX_UNPACKED:
            raise PackageError(f"Over {MAX_UNPACKED // 1024**2} MB unpacked")
        files[str(rel)] = zf.read(info)
    manifest = parse_manifest(files[MANIFEST].decode("utf-8", "replace"))
    if manifest.main not in files:
        raise PackageError(f"main: {manifest.main} isn't in the .zip")
    return Package(manifest, hashlib.sha256(data).hexdigest(), files)


def stage(data: bytes) -> tuple[str, Package]:
    """Keep an upload until the admin approves it; returns a token for it."""
    pkg = read_zip(data)
    d = _staged_dir()
    d.mkdir(parents=True, exist_ok=True)
    for old in d.glob("*.zip"):
        if time.time() - old.stat().st_mtime > STAGE_TTL:
            old.unlink(missing_ok=True)
    token = secrets.token_urlsafe(18)
    (d / f"{token}.zip").write_bytes(data)
    return token, pkg


def staged(token: str) -> tuple[bytes, Package]:
    if not token.replace("-", "").replace("_", "").isalnum():
        raise PackageError("Unknown upload; upload it again")
    path = _staged_dir() / f"{token}.zip"
    if not path.is_file():
        raise PackageError("That upload has expired; upload it again")
    data = path.read_bytes()
    return data, read_zip(data)


def unstage(token: str) -> None:
    (_staged_dir() / f"{token}.zip").unlink(missing_ok=True)


def unpack(pkg: Package) -> Path:
    """Write the plugin's files where it will run from (a new folder per version)."""
    m = pkg.manifest
    target = root() / m.id / f"{m.version}-{pkg.checksum[:12]}"
    if target.exists():
        return target
    tmp = target.with_name(target.name + ".partial")
    shutil.rmtree(tmp, ignore_errors=True)
    for rel, content in pkg.files.items():
        out = tmp / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(content)
    tmp.rename(target)
    return target


def remove(plugin_id: str) -> None:
    shutil.rmtree(root() / plugin_id, ignore_errors=True)
    shutil.rmtree(root() / ".cache" / plugin_id, ignore_errors=True)


def prune(plugin_id: str, keep: Path) -> None:
    """Older versions' folders, once the new one runs."""
    for d in (root() / plugin_id).glob("*"):
        if d.is_dir() and d != keep:
            shutil.rmtree(d, ignore_errors=True)


def uploads() -> Path:
    """Files people give actions, until the run finishes."""
    d = root() / ".uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d
