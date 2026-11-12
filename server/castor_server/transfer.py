"""Import and export (3i): library items, variable sets, projects or everything as one
`.castor.zip`, and bringing such an archive into this instance.

Rows are written column by column from the ORM models, so a column added later travels
without touching this file; an archive missing a column gets the column's default. Every id
in the archive is the source's: importing gives each row a new id and rewrites every
reference, including ids inside JSON and text (step task maps, image links in descriptions,
activity payloads), so the same archive can be imported twice. People are matched by
username. Secrets leave only when the exporter gives a passphrase, encrypted with a key
derived from it; never the vault key, never plain text.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import secrets as pysecrets
import shutil
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum as PyEnum
from pathlib import Path
from typing import IO, Any

import yaml
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Enum, Integer, Uuid, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from . import avatars, vault
from . import models as m
from .config import settings

FORMAT = 1
SUFFIX = ".castor.zip"
MAX_UPLOAD = 2 * 1024**3
MAX_UNPACKED = 8 * 1024**3
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
LIBRARY = {  # archive folder → (model, label); parsers first: commands name them
    "parsers": (m.ParserDefinition, "parser"),
    "commands": (m.CommandDefinition, "command"),
    "tools": (m.ToolDefinition, "tool"),
    "playbooks": (m.PlaybookDefinition, "playbook"),
    "themes": (m.ThemeDefinition, "theme"),  # custom themes; only admins import them
}
# A project's rows, in insert order (parents first). Jobs and activity are optional.
PROJECT_TABLES: list[tuple[str, type]] = [
    ("columns", m.BoardColumn),
    ("runs", m.PlaybookRun),
    ("tasks", m.Task),
    ("jobs", m.Job),
    ("job_outputs", m.JobOutput),
    ("job_logs", m.JobLogChunk),
    ("ai_summaries", m.AiSummary),
    ("comments", m.Comment),
    ("attachments", m.Attachment),
    ("notes", m.ProjectNote),
    ("posts", m.ProjectPost),
    ("variable_sets", m.VariableSet),
    ("assets", m.Asset),
    ("asset_observations", m.AssetObservation),
    ("asset_links", m.AssetLink),
    ("asset_triggers", m.AssetTrigger),
    ("asset_trigger_fires", m.AssetTriggerFire),
    ("activity", m.ActivityEvent),
]
JOB_TABLES = {"jobs", "job_outputs", "job_logs", "ai_summaries"}  # summaries travel with the run history


class TransferError(Exception):
    pass


def version() -> str:
    try:
        from importlib.metadata import version as dist_version

        return dist_version("castor-server")
    except Exception:  # noqa: BLE001
        return "dev"


# ---------- rows ----------


def _plain(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, PyEnum):
        return value.value
    return value


def row_of(obj: Any, skip: tuple[str, ...] = ()) -> dict[str, Any]:
    return {c.key: _plain(getattr(obj, c.key)) for c in obj.__table__.columns if c.key not in skip}


def remap(value: Any, ids: dict[str, str]) -> Any:
    """Rewrite every id the map knows inside a JSON value (keys and strings included)."""
    if isinstance(value, str):
        return UUID_RE.sub(lambda mt: ids.get(mt.group(0), mt.group(0)), value) if "-" in value else value
    if isinstance(value, list):
        return [remap(v, ids) for v in value]
    if isinstance(value, dict):
        return {remap(k, ids): remap(v, ids) for k, v in value.items()}
    return value


def values_for(model: type, data: dict[str, Any], ids: dict[str, str]) -> dict[str, Any] | None:
    """Column values for inserting an archive row: new primary key, references mapped (a
    nullable one the map doesn't know becomes null; a required one drops the row), dates
    and enums parsed, ids inside JSON and text rewritten. Unknown keys are ignored."""
    out: dict[str, Any] = {}
    for col in model.__table__.columns:
        if col.key not in data:
            continue
        value = data[col.key]
        if col.primary_key and isinstance(col.type, Integer):
            continue  # autoincrement
        if value is None:
            out[col.key] = None
        elif isinstance(col.type, Uuid):
            mapped = ids.get(str(value))
            if mapped is None:
                if col.primary_key or not col.nullable:
                    return None
                out[col.key] = None
            else:
                out[col.key] = uuid.UUID(mapped)
        elif isinstance(col.type, m.UTCDateTime):
            out[col.key] = datetime.fromisoformat(value)
        elif isinstance(col.type, Enum) and col.type.enum_class is not None:
            out[col.key] = col.type.enum_class(value)
        else:
            out[col.key] = remap(value, ids)
    return out


async def insert_rows(db: AsyncSession, model: type, rows: list[dict[str, Any]]) -> None:
    """Insert in batches of rows that share the same columns."""
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(tuple(sorted(row)), []).append(row)
    for group in groups.values():
        for i in range(0, len(group), 500):
            await db.execute(insert(model), group[i : i + 500])


# ---------- secrets ----------


def _key(passphrase: str, salt: bytes) -> Fernet:
    raw = hashlib.pbkdf2_hmac("sha256", passphrase.encode(), salt, 390_000, dklen=32)
    return Fernet(base64.urlsafe_b64encode(raw))


@dataclass
class Secrets:
    """Moves vault-encrypted values in and out of an archive: re-encrypted under the
    passphrase key, or left out when there is no passphrase."""

    fernet: Fernet | None = None
    salt: str = ""
    check: str = ""

    @classmethod
    def for_export(cls, passphrase: str | None) -> Secrets:
        if not passphrase:
            return cls()
        salt = pysecrets.token_bytes(16)
        f = _key(passphrase, salt)
        return cls(f, base64.b64encode(salt).decode(), f.encrypt(b"castor").decode())

    @classmethod
    def for_import(cls, manifest: dict[str, Any], passphrase: str | None) -> Secrets:
        info = manifest.get("secrets") or {}
        if info.get("mode") != "passphrase" or not passphrase:
            return cls()
        f = _key(passphrase, base64.b64decode(info["salt"]))
        try:
            f.decrypt(info["check"].encode())
        except (InvalidToken, KeyError) as exc:
            raise TransferError("That passphrase doesn't open the secrets in this file") from exc
        return cls(f)

    def manifest(self) -> dict[str, Any]:
        return {"mode": "passphrase", "salt": self.salt, "check": self.check} if self.fernet else {"mode": "none"}

    def out(self, token: str | None) -> str | None:
        """Vault token → archive token (None when secrets are left out)."""
        if not token or self.fernet is None:
            return None
        return self.fernet.encrypt(vault.decrypt(token)).decode()

    def into(self, token: str | None) -> str | None:
        """Archive token → vault token (None when it can't be opened)."""
        if not token or self.fernet is None:
            return None
        try:
            return vault.encrypt(self.fernet.decrypt(token.encode()))
        except InvalidToken:
            return None


def set_out(row: m.VariableSet, sec: Secrets) -> dict[str, Any]:
    data = row_of(row)
    data["secrets"] = {k: t for k, v in (row.secrets or {}).items() if (t := sec.out(v))}
    data["files"] = {
        k: {**info, "data": t} for k, info in (row.files or {}).items() if (t := sec.out(info.get("data")))
    }
    data["left_out"] = sorted(set(row.secrets or {}) - set(data["secrets"]) | set(row.files or {}) - set(data["files"]))
    return data


def set_in(data: dict[str, Any], sec: Secrets) -> dict[str, Any]:
    data = dict(data)
    data["secrets"] = {k: t for k, v in (data.get("secrets") or {}).items() if (t := sec.into(v))}
    data["files"] = {
        k: {**info, "data": t} for k, info in (data.get("files") or {}).items() if (t := sec.into(info.get("data")))
    }
    data.pop("left_out", None)
    return data


# ---------- export ----------


@dataclass
class ExportOptions:
    everything: bool = False
    parsers: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    playbooks: list[str] = field(default_factory=list)
    themes: list[str] = field(default_factory=list)
    variable_kinds: list[str] = field(default_factory=list)
    variable_sets: list[uuid.UUID] = field(default_factory=list)
    projects: list[uuid.UUID] = field(default_factory=list)
    jobs: bool = False
    activity: bool = False
    passphrase: str | None = None


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1, default=_plain)


async def _rows(db: AsyncSession, query) -> list[Any]:
    return list((await db.execute(query)).scalars())


async def _project_bundle(db: AsyncSession, project: m.Project, opts: ExportOptions, sec: Secrets) -> tuple[dict[str, Any], list[m.Attachment]]:
    pid = project.id
    tasks = await _rows(db, select(m.Task).where(m.Task.project_id == pid))
    task_ids = [t.id for t in tasks]
    runs = await _rows(db, select(m.PlaybookRun).where(m.PlaybookRun.project_id == pid))
    run_ids = [r.id for r in runs]
    assets = await _rows(db, select(m.Asset).where(m.Asset.project_id == pid))
    asset_ids = [a.id for a in assets]
    triggers = await _rows(db, select(m.AssetTrigger).where(m.AssetTrigger.project_id == pid))
    attachments = await _rows(db, select(m.Attachment).where(m.Attachment.project_id == pid))
    sets = await _rows(db, select(m.VariableSet).where(
        m.VariableSet.ephemeral.is_(True),
        m.VariableSet.task_id.in_(task_ids) | m.VariableSet.playbook_run_id.in_(run_ids),
    ))
    bundle: dict[str, Any] = {
        "project": row_of(project),
        "columns": [row_of(c) for c in await _rows(db, select(m.BoardColumn).where(m.BoardColumn.project_id == pid))],
        "runs": [row_of(r) for r in runs],
        "tasks": [row_of(t) for t in tasks],
        "comments": [row_of(c) for c in await _rows(db, select(m.Comment).where(m.Comment.task_id.in_(task_ids)))],
        "attachments": [row_of(a, skip=("storage_path",)) for a in attachments],
        "notes": [row_of(n) for n in await _rows(db, select(m.ProjectNote).where(m.ProjectNote.project_id == pid))],
        "posts": [row_of(p) for p in await _rows(db, select(m.ProjectPost).where(m.ProjectPost.project_id == pid))],
        "variable_sets": [set_out(s, sec) for s in sets],
        "assets": [{**row_of(a), "secret": sec.out(a.secret)} for a in assets],
        "asset_observations": [row_of(o) for o in await _rows(db, select(m.AssetObservation).where(m.AssetObservation.asset_id.in_(asset_ids)))],
        "asset_links": [row_of(lk) for lk in await _rows(db, select(m.AssetLink).where(m.AssetLink.project_id == pid))],
        "asset_triggers": [row_of(t) for t in triggers],
        "asset_trigger_fires": [row_of(f) for f in await _rows(db, select(m.AssetTriggerFire).where(m.AssetTriggerFire.trigger_id.in_([t.id for t in triggers])))],
        "members": [str(u) for u in (await db.execute(select(m.ProjectMember.user_id).where(m.ProjectMember.project_id == pid))).scalars()],
        # Groups travel by @handle: the importing instance shares the project with its own
        # groups of the same handle, if it has them.
        "groups": list((await db.execute(select(m.Group.slug).join(m.ProjectGroup, m.ProjectGroup.group_id == m.Group.id)
                                         .where(m.ProjectGroup.project_id == pid))).scalars()),
        # Cards' group assignees: group id → @handle, so they map onto the importer's groups.
        "group_handles": {str(gid): slug for gid, slug in (await db.execute(select(m.Group.id, m.Group.slug).where(
            m.Group.id.in_({uuid.UUID(g) for t in tasks for g in (t.group_assignees or [])})))).all()},
    }
    if opts.jobs:
        jobs = await _rows(db, select(m.Job).where(m.Job.task_id.in_(task_ids)))
        job_ids = [j.id for j in jobs]
        bundle["jobs"] = [row_of(j, skip=("artifact_token_hash", "env")) for j in jobs]
        bundle["job_outputs"] = [row_of(o) for o in await _rows(db, select(m.JobOutput).where(m.JobOutput.job_id.in_(job_ids)))]
        bundle["job_logs"] = [row_of(c, skip=("id",)) for c in await _rows(db, select(m.JobLogChunk).where(m.JobLogChunk.job_id.in_(job_ids)).order_by(m.JobLogChunk.id))]
    if opts.activity:
        bundle["activity"] = [row_of(e, skip=("id",)) for e in await _rows(db, select(m.ActivityEvent).where(m.ActivityEvent.project_id == pid).order_by(m.ActivityEvent.id))]
    return bundle, attachments


async def export_archive(db: AsyncSession, user: m.User, opts: ExportOptions, out: IO[bytes]) -> dict[str, Any]:
    """Write the archive to `out`; returns its manifest."""
    sec = Secrets.for_export(opts.passphrase)
    contents: dict[str, int] = {}
    referenced: set[str] = set()  # every id written, to find the people and sets it names
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:

        def put(name: str, text: str) -> None:
            zf.writestr(name, text)
            referenced.update(UUID_RE.findall(text))

        for folder, (model, _) in LIBRARY.items():
            wanted = getattr(opts, folder)
            query = select(model).where(model.origin == "user") if opts.everything else select(model).where(model.id.in_(wanted))
            for row in await _rows(db, query.order_by(model.id)):
                zf.writestr(f"library/{folder}/{row.id}.yaml", row.source_yaml or yaml.safe_dump(row.spec, sort_keys=False))
                contents[folder] = contents.get(folder, 0) + 1
        kq = select(m.VariableKind).where(m.VariableKind.origin == "user") if opts.everything else select(m.VariableKind).where(m.VariableKind.id.in_(opts.variable_kinds))
        if kinds := await _rows(db, kq):
            put("library/variable_kinds.json", _dump([row_of(k) for k in kinds]))
            contents["variable_kinds"] = len(kinds)
        sq = select(m.VariableSet).where(m.VariableSet.ephemeral.is_(False))
        if not opts.everything:
            sq = sq.where(m.VariableSet.id.in_(opts.variable_sets))
        if sets := await _rows(db, sq):
            put("variable_sets.json", _dump([set_out(s, sec) for s in sets]))
            contents["variable_sets"] = len(sets)

        pq = select(m.Project) if opts.everything else select(m.Project).where(m.Project.id.in_(opts.projects))
        projects = await _rows(db, pq.order_by(m.Project.codename))
        for project in projects:
            bundle, attachments = await _project_bundle(db, project, opts, sec)
            put(f"projects/{project.id}.json", _dump(bundle))
            for a in attachments:
                path = settings.data_dir / a.storage_path
                if path.is_file():
                    zf.write(path, f"files/{a.id}")
        if projects:
            contents["projects"] = len(projects)
            client_ids = {p.client_id for p in projects if p.client_id}
            clients = await _rows(db, select(m.Client) if opts.everything else select(m.Client).where(m.Client.id.in_(client_ids)))
            if clients:
                put("clients.json", _dump([row_of(c) for c in clients]))

        # Variable sets a card names but the archive doesn't carry: matched by name on import.
        named = [uuid.UUID(i) for i in referenced]
        set_names = {str(i): n for i, n in (await db.execute(select(m.VariableSet.id, m.VariableSet.name).where(m.VariableSet.id.in_(named), m.VariableSet.ephemeral.is_(False)))).all()}
        if set_names:
            zf.writestr("set_names.json", _dump(set_names))
        uq = select(m.User) if opts.everything else select(m.User).where(m.User.id.in_(named))
        users = await _rows(db, uq.order_by(m.User.username))
        people = []
        for u in users:
            entry = {"id": str(u.id), "username": u.username, "display_name": u.display_name}
            if opts.everything:
                entry |= {"role": u.role.value, "disabled": u.disabled_at is not None, "avatar_kind": u.avatar_kind,
                          "avatar_link": u.avatar_link, "gravatar_email": u.gravatar_email}
                if u.avatar_file and (avatars.directory() / u.avatar_file).is_file():
                    zf.write(avatars.directory() / u.avatar_file, f"avatars/{u.id}")
                    entry["avatar"] = f"avatars/{u.id}"
            people.append(entry)
        if people:
            zf.writestr("users.json", _dump(people))
            if opts.everything:
                contents["users"] = len(people)

        manifest = {
            "format": FORMAT,
            "castor_version": version(),
            "created_at": datetime.now(UTC).isoformat(),
            "created_by": user.username,
            "scope": "everything" if opts.everything else "project" if projects else "library",
            "contents": contents,
            "includes": {"jobs": opts.jobs, "activity": opts.activity},
            "secrets": sec.manifest(),
        }
        zf.writestr("manifest.json", _dump(manifest))
    return manifest


def archive_name(manifest: dict[str, Any], label: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-") or "castor"
    return f"{safe}-{stamp}{SUFFIX}"


# ---------- reading an archive ----------


class Archive:
    """A checked archive: known names only, sizes bounded, format not newer than ours."""

    def __init__(self, path: Path):
        try:
            self.zf = zipfile.ZipFile(path)
        except zipfile.BadZipFile as exc:
            raise TransferError("That isn't a Castor archive (not a zip file)") from exc
        if sum(i.file_size for i in self.zf.infolist()) > MAX_UNPACKED:
            raise TransferError("The archive unpacks to more than Castor will import")
        self.names = set(self.zf.namelist())
        if "manifest.json" not in self.names:
            raise TransferError("That isn't a Castor archive (no manifest.json)")
        self.manifest = self.json("manifest.json")
        fmt = self.manifest.get("format")
        if not isinstance(fmt, int) or fmt < 1:
            raise TransferError("The archive's manifest has no format version")
        if fmt > FORMAT:
            raise TransferError(f"This archive is format {fmt}, from a newer Castor ({self.manifest.get('castor_version')}); update this instance to import it")

    def json(self, name: str, default: Any = None) -> Any:
        if name not in self.names:
            return default
        try:
            return json.loads(self.zf.read(name))
        except ValueError as exc:
            raise TransferError(f"{name} in the archive isn't valid JSON") from exc

    def text(self, name: str) -> str:
        return self.zf.read(name).decode("utf-8")

    def library(self, folder: str) -> list[str]:
        return sorted(n for n in self.names if n.startswith(f"library/{folder}/") and n.endswith(".yaml") and n.count("/") == 2)

    def projects(self) -> list[str]:
        return sorted(n for n in self.names if re.fullmatch(r"projects/[0-9a-f-]{36}\.json", n))

    def close(self) -> None:
        self.zf.close()


def single_yaml_archive(data: bytes, filename: str) -> bytes:
    """A plain .yaml library item, wrapped as an archive so it imports the same way."""
    try:
        raw = yaml.safe_load(data)
    except yaml.YAMLError as exc:
        raise TransferError(f"That YAML doesn't parse: {exc}") from exc
    if not isinstance(raw, dict) or not raw.get("id"):
        raise TransferError("That YAML isn't a command, tool, parser, playbook or theme (no id:)")
    themed = any(k in raw for k in ("colours", "modes", "colour", "default_colour", "font_faces", "shape", "elevation", "size"))
    folder = "themes" if themed and "command" not in raw and "steps" not in raw else "playbooks" if "steps" in raw else "parsers" if "format" in raw and "type" in raw and "command" not in raw else "tools" if ("image" in raw or "install" in raw or "check" in raw) and "command" not in raw else "commands"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"library/{folder}/{raw['id']}.yaml", data)
        zf.writestr("manifest.json", json.dumps({"format": FORMAT, "scope": "library", "created_by": "", "source": filename, "secrets": {"mode": "none"}}))
    return buf.getvalue()


# ---------- uploads waiting for their import ----------


def _imports_dir() -> Path:
    path = settings.data_dir / "imports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def staged(token: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,64}", token):
        raise TransferError("Unknown upload; choose the file again")
    path = _imports_dir() / f"{token}.zip"
    if not path.is_file():
        raise TransferError("That upload has expired; choose the file again")
    return path


def stage(src: IO[bytes]) -> tuple[str, Path]:
    """Save an upload for its preview and import; old ones (over a day) are cleared."""
    cutoff = datetime.now(UTC) - timedelta(days=1)
    for old in _imports_dir().glob("*.zip"):
        if datetime.fromtimestamp(old.stat().st_mtime, UTC) < cutoff:
            old.unlink(missing_ok=True)
    token = pysecrets.token_urlsafe(24)
    path = _imports_dir() / f"{token}.zip"
    size = 0
    with path.open("wb") as fh:
        while chunk := src.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD:
                fh.close()
                path.unlink(missing_ok=True)
                raise TransferError("The file is larger than Castor will import")
            fh.write(chunk)
    return token, path


# ---------- planning ----------


def _count(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _copy_code(code: str, taken: set[str]) -> str:
    base = f"{code[:59]}-copy"
    return base if base not in taken else _free(base, taken)


def _free(base: str, taken: set[str], sep: str = "-", limit: int = 64) -> str:
    n = 2
    while True:
        candidate = f"{base[: limit - len(sep) - 4]}{sep}{n}"
        if candidate not in taken:
            return candidate
        n += 1


def _spec_parser(folder: str):
    from .command_spec import parse_spec
    from .parser_spec import parse_parser_spec
    from .playbook_spec import parse_playbook_spec
    from .theme_spec import parse_theme_spec
    from .tool_spec import parse_tool_spec

    return {"parsers": parse_parser_spec, "commands": parse_spec, "tools": parse_tool_spec, "playbooks": parse_playbook_spec,
            "themes": parse_theme_spec}[folder]


def _archive_parsers(archive: Archive) -> list[Any]:
    """The parser definitions in an archive (so its commands can be checked against them)."""
    from .parser_spec import parse_parser_spec

    found = []
    for name in archive.library("parsers"):
        try:
            found.append(parse_parser_spec(archive.text(name)))
        except ValueError:
            continue  # reported in the preview
    return found


async def plan(db: AsyncSession, user: m.User, archive: Archive, passphrase: str | None = None) -> dict[str, Any]:
    """What importing would do: each item with its state and choices, the people it names
    and whether they exist, and anything that won't come across."""
    from .parser_library import overlay

    with overlay(_archive_parsers(archive)):  # its commands may name its parsers
        return await _plan(db, user, archive, passphrase)


async def _plan(db: AsyncSession, user: m.User, archive: Archive, passphrase: str | None = None) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    warnings: list[str] = []
    manifest = archive.manifest
    secret_mode = (manifest.get("secrets") or {}).get("mode", "none")
    unlocked = False
    if secret_mode == "passphrase" and passphrase:
        Secrets.for_import(manifest, passphrase)  # raises on a wrong passphrase
        unlocked = True

    def item(kind: str, key: str, name: str, state: str, *, new_name: str = "", notes: list[str] | None = None) -> None:
        choices = {"new": ["import", "skip"], "exists": ["skip", "replace", "copy"], "builtin": ["skip", "copy"],
                   "project": ["skip", "copy"], "same": ["skip"]}[state]
        default = {"new": "import", "same": "skip"}.get(state, "skip")
        items.append({"key": f"{kind}:{key}", "kind": kind, "id": key, "name": name, "state": state,
                      "choices": choices, "default": default, "new_name": new_name, "notes": notes or []})

    kinds = archive.json("library/variable_kinds.json", [])
    known_kinds = {k for (k,) in (await db.execute(select(m.VariableKind.id))).all()}
    for k in kinds:
        row = await db.get(m.VariableKind, k["id"])
        state = "new" if row is None else "builtin" if row.origin == "builtin" else "same" if row.spec == k.get("spec") else "exists"
        item("variable_kind", k["id"], k.get("name") or k["id"], state, new_name=_free(k["id"], known_kinds) if row else "")
    archive_kinds = {k["id"] for k in kinds}

    for folder, (model, label) in LIBRARY.items():
        taken = {i for (i,) in (await db.execute(select(model.id))).all()}
        for name in archive.library(folder):
            text = archive.text(name)
            try:
                spec = _spec_parser(folder)(text)
            except Exception as exc:  # noqa: BLE001 - the spec parsers raise their own errors
                warnings.append(f"{name}: doesn't parse ({str(exc).splitlines()[0][:200]}); it will be skipped")
                continue
            row = await db.get(model, spec.id)
            notes = []
            if folder == "commands" and getattr(spec, "tool", None):
                in_file = f"library/tools/{spec.tool}.yaml" in archive.names
                if not in_file and await db.get(m.ToolDefinition, spec.tool) is None:
                    notes.append(f"uses the tool {spec.tool}, which isn't here")
            if folder == "playbooks":
                for step in getattr(spec, "steps", []):
                    ref = getattr(step, "command_ref", None)
                    if ref and f"library/commands/{ref}.yaml" not in archive.names and await db.get(m.CommandDefinition, ref) is None:
                        notes.append(f"step {step.id} runs {ref}, which isn't here")
            if folder == "commands":
                for slot in getattr(spec, "vars", []) or []:
                    kind = getattr(slot, "kind", None)
                    if kind and kind not in known_kinds and kind not in archive_kinds:
                        notes.append(f"needs the variable kind {kind}, which isn't here")
            if row is None:
                state = "new"
            elif row.origin == "builtin":
                state = "same" if row.spec == spec.model_dump(mode="json") else "builtin"
            else:
                state = "same" if row.spec == spec.model_dump(mode="json") else "exists"
            if folder == "themes" and not user.is_admin and state != "same":
                notes.append("only admins add themes; it will be skipped")
                items.append({"key": f"{label}:{spec.id}", "kind": label, "id": spec.id, "name": spec.name, "state": state,
                              "choices": ["skip"], "default": "skip", "new_name": "", "notes": notes})
                continue
            item(label, spec.id, spec.name, state, new_name=_free(spec.id, taken) if row else "", notes=notes)

    set_names = {n for (n,) in (await db.execute(select(m.VariableSet.name))).all()}
    for s in archive.json("variable_sets.json", []):
        notes = []
        if s["kind_id"] not in known_kinds and s["kind_id"] not in archive_kinds:
            notes.append(f"its kind {s['kind_id']} isn't here; it will be skipped")
        if s.get("left_out"):
            notes.append(f"secrets not in the file: {', '.join(s['left_out'])}")
        elif (s.get("secrets") or s.get("files")) and not unlocked:
            notes.append("has secrets; give the passphrase to bring them, or they come in empty")
        state = "exists" if s["name"] in set_names else "new"
        item("variable_set", s["id"], s["name"], state, new_name=_free(s["name"], set_names, sep=" ", limit=200) if state == "exists" else "", notes=notes)

    codes = {c for (c,) in (await db.execute(select(m.Project.codename))).all()}
    for name in archive.projects():
        bundle = archive.json(name)
        p = bundle["project"]
        counts = [("tasks", "card"), ("comments", "comment"), ("posts", "post"), ("attachments", "file"), ("jobs", "job run"), ("assets", "asset")]
        notes = [_count(len(bundle[k]), word) for k, word in counts if bundle.get(k)]
        exists = p["codename"] in codes
        items.append({
            "key": f"project:{p['id']}", "kind": "project", "id": p["id"], "name": f"{p['codename']} · {p['name']}",
            "state": "exists" if exists else "new", "choices": ["copy", "skip"] if exists else ["import", "skip"],
            "default": "copy" if exists else "import", "new_name": _copy_code(p["codename"], codes) if exists else "",
            "notes": notes,
        })

    people = archive.json("users.json", [])
    existing = {u.username.lower(): u for u in (await db.execute(select(m.User))).scalars()}
    matched = [p["username"] for p in people if p["username"].lower() in existing]
    missing = [{"username": p["username"], "display_name": p.get("display_name", "")} for p in people if p["username"].lower() not in existing]
    if secret_mode == "none" and any(i["kind"] in ("variable_set", "project") for i in items):
        warnings.append("The file was exported without secrets: variable sets and credentials come in with their secrets empty.")
    return {
        "manifest": {k: manifest.get(k) for k in ("format", "castor_version", "created_at", "created_by", "scope", "contents", "includes", "source")},
        "secrets": {"mode": secret_mode, "unlocked": unlocked},
        "items": items,
        "people": {"matched": matched, "missing": missing, "can_create": user.is_admin},
        "warnings": warnings,
    }


# ---------- importing ----------


@dataclass
class Result:
    imported: dict[str, list[str]] = field(default_factory=dict)
    skipped: int = 0
    created_users: list[str] = field(default_factory=list)
    projects: list[uuid.UUID] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)  # written so far; removed if the import fails
    parsers: list[Any] = field(default_factory=list)  # parser specs added, for the registry once committed

    def add(self, kind: str, name: str) -> None:
        self.imported.setdefault(kind, []).append(name)


def _with_id(text: str, new_id: str) -> str:
    """The YAML with its top-level id: changed (keep both)."""
    out, n = re.subn(r"(?m)^id:\s*.*$", f"id: {new_id}", text, count=1)
    return out if n else f"id: {new_id}\n{text}"


async def run_import(
    db: AsyncSession,
    user: m.User,
    archive: Archive,
    choices: dict[str, str],
    *,
    passphrase: str | None = None,
    create_users: bool = False,
) -> Result:
    """Apply the plan with the chosen resolutions. The caller commits (or rolls back and
    calls `discard` on failure), then registers `result.parsers` (parser_library.register)."""
    from .parser_library import overlay

    with overlay(_archive_parsers(archive)):
        return await _run_import(db, user, archive, choices, passphrase=passphrase, create_users=create_users)


async def _run_import(
    db: AsyncSession,
    user: m.User,
    archive: Archive,
    choices: dict[str, str],
    *,
    passphrase: str | None = None,
    create_users: bool = False,
) -> Result:
    from .command_library import definition_from_spec as command_from_spec
    from .parser_library import definition_from_spec as parser_from_spec
    from .playbook_library import definition_from_spec as playbook_from_spec
    from .theme_library import definition_from_spec as theme_from_spec
    from .tool_library import definition_from_spec as tool_from_spec

    sec = Secrets.for_import(archive.manifest, passphrase)
    result = Result()
    preview = await plan(db, user, archive, passphrase if sec.fernet else None)
    by_key = {i["key"]: i for i in preview["items"]}

    def choice(key: str) -> str:
        info = by_key.get(key)
        if info is None:
            return "skip"
        picked = choices.get(key, info["default"])
        if picked not in info["choices"]:
            raise TransferError(f"{info['name']}: “{picked}” isn't possible here")
        if picked == "skip":
            result.skipped += 1
        return picked

    # People first: everything else refers to them.
    ids: dict[str, str] = {}
    existing = {u.username.lower(): u for u in (await db.execute(select(m.User))).scalars()}
    for p in archive.json("users.json", []):
        target = existing.get(p["username"].lower())
        if target is None and create_users and user.is_admin:
            target = m.User(
                username=p["username"], display_name=p.get("display_name") or p["username"],
                password_hash=_unusable_password(), role=m.UserRole(p.get("role", "viewer")),
                avatar_kind=p.get("avatar_kind", "initials"), avatar_link=p.get("avatar_link", ""),
                gravatar_email=p.get("gravatar_email", ""),
            )
            if p.get("disabled"):
                target.disabled_at = datetime.now(UTC)
            db.add(target)
            await db.flush()
            if p.get("avatar") in archive.names:
                try:
                    target.avatar_file = avatars.save(target.id, 1, archive.zf.read(p["avatar"]))
                    target.avatar_version = 1
                    result.files.append(avatars.directory() / target.avatar_file)
                except avatars.AvatarError:
                    target.avatar_kind = "initials"
            elif target.avatar_kind == "upload":
                target.avatar_kind = "initials"
            existing[target.username.lower()] = target
            result.created_users.append(target.username)
        if target is not None:
            ids[p["id"]] = str(target.id)
    known_users = set(ids.values())

    # Library, in dependency order: kinds, tools, commands, playbooks.
    for k in archive.json("library/variable_kinds.json", []):
        picked = choice(f"variable_kind:{k['id']}")
        if picked == "skip":
            continue
        data = {key: k[key] for key in ("name", "description", "spec") if key in k}
        if picked == "replace":
            row = await db.get(m.VariableKind, k["id"])
            for key, value in data.items():
                setattr(row, key, value)
        else:
            new_id = by_key[f"variable_kind:{k['id']}"]["new_name"] if picked == "copy" else k["id"]
            db.add(m.VariableKind(id=new_id, origin="user", **{**data, "spec": {**data.get("spec", {}), "id": new_id}}))
        result.add("variable kinds", k.get("name") or k["id"])
    await db.flush()

    setters = {"parsers": parser_from_spec, "commands": command_from_spec, "tools": tool_from_spec, "playbooks": playbook_from_spec,
               "themes": theme_from_spec}
    for folder in ("parsers", "tools", "commands", "playbooks", "themes"):
        model, label = LIBRARY[folder]
        for name in archive.library(folder):
            text = archive.text(name)
            try:
                spec = _spec_parser(folder)(text)
            except Exception:  # noqa: BLE001, S112 - reported in the preview
                continue
            picked = choice(f"{label}:{spec.id}")
            if picked == "skip":
                continue
            if picked == "copy":
                text = _with_id(text, by_key[f"{label}:{spec.id}"]["new_name"])
                spec = _spec_parser(folder)(text)
            row = await db.get(model, spec.id) if picked == "replace" else None
            if row is None:
                row = model(id=spec.id, origin="user", version=1)
                db.add(row)
            setters[folder](row, spec, text)
            result.add(f"{label}s", spec.name)
            if folder == "parsers":
                result.parsers.append(spec)
        await db.flush()

    # Variable sets (not tied to a card).
    kinds_here = {k for (k,) in (await db.execute(select(m.VariableKind.id))).all()}
    for s in archive.json("variable_sets.json", []):
        key = f"variable_set:{s['id']}"
        picked = choice(key)
        if picked == "skip" or s["kind_id"] not in kinds_here:
            continue
        data = set_in(s, sec)
        if picked == "replace":
            row = (await db.execute(select(m.VariableSet).where(m.VariableSet.name == s["name"]))).scalar_one()
            row.values, row.secrets, row.files = data.get("values") or {}, data["secrets"], data["files"]
            row.description = data.get("description", "")
            ids[s["id"]] = str(row.id)
        else:
            new = str(uuid.uuid4())
            ids[s["id"]] = new
            values = values_for(m.VariableSet, data, {**ids, s["id"]: new}) or {}
            if picked == "copy":
                values["name"] = by_key[key]["new_name"]
            values["created_by"] = user.id
            await insert_rows(db, m.VariableSet, [values])
        result.add("variable sets", s["name"])
    # Sets a card names that the file doesn't carry: the set of the same name here, if any.
    here = {n: str(i) for i, n in (await db.execute(select(m.VariableSet.id, m.VariableSet.name).where(m.VariableSet.ephemeral.is_(False)))).all()}
    for old, name in (archive.json("set_names.json", {}) or {}).items():
        if old not in ids and name in here:
            ids[old] = here[name]

    clients = {c.name: c for c in (await db.execute(select(m.Client))).scalars()}
    for c in archive.json("clients.json", []):
        target = clients.get(c["name"])
        if target is None:
            target = m.Client(name=c["name"], notes=c.get("notes", ""))
            db.add(target)
            await db.flush()
            clients[target.name] = target
        ids[c["id"]] = str(target.id)

    for name in archive.projects():
        bundle = archive.json(name)
        key = f"project:{bundle['project']['id']}"
        picked = choice(key)
        if picked == "skip":
            continue
        codename = by_key[key]["new_name"] if picked == "copy" else bundle["project"]["codename"]
        old = bundle["project"]["id"]
        ids[old] = str(await _import_project(db, user, archive, bundle, ids, known_users, codename, sec, result))
    # Sets limited to projects: point at the imported copies (projects not in the file stay
    # as they were, so a limited set never becomes visible to everyone).
    for s in archive.json("variable_sets.json", []):
        if s.get("project_ids") and s["id"] in ids:
            await db.execute(update(m.VariableSet).where(m.VariableSet.id == uuid.UUID(ids[s["id"]]))
                             .values(project_ids=remap(s["project_ids"], ids)))
    return result


def _unusable_password() -> str:
    from .security import hash_password

    return hash_password(pysecrets.token_urlsafe(32))


async def _import_project(
    db: AsyncSession, user: m.User, archive: Archive, bundle: dict[str, Any], ids: dict[str, str],
    known_users: set[str], codename: str, sec: Secrets, result: Result,
) -> None:
    from .access import add_member
    from .services import actor_of, record_activity

    ids = dict(ids)
    # Every row gets its new id up front, so references in any direction resolve.
    for table, _ in [("project", None), *PROJECT_TABLES]:
        rows = [bundle["project"]] if table == "project" else bundle.get(table) or []
        for row in rows:
            if isinstance(row.get("id"), str) and UUID_RE.fullmatch(row["id"]):
                ids[row["id"]] = str(uuid.uuid4())

    p = values_for(m.Project, bundle["project"], ids)
    p["codename"] = codename
    await insert_rows(db, m.Project, [p])
    project_id = p["id"]

    card_ids: dict[uuid.UUID, uuid.UUID] = {}
    mapped = set(ids.values())
    # Cards' group assignees follow the @handle onto this instance's groups.
    handles: dict[str, str] = bundle.get("group_handles") or {}
    local_groups = {slug: str(gid) for gid, slug in (await db.execute(select(m.Group.id, m.Group.slug))).all()}
    set_names = {n for (n,) in (await db.execute(select(m.VariableSet.name))).all()}
    for table, model in PROJECT_TABLES:
        rows = []
        for raw in bundle.get(table) or []:
            if table == "variable_sets":
                raw = set_in(raw, sec)
                if raw.get("name") in set_names:  # one-off sets' names are unique too
                    raw["name"] = _free(raw["name"], set_names, sep=" ", limit=200)
                set_names.add(raw.get("name"))
            values = values_for(model, raw, ids)
            if values is None:
                continue
            if table == "runs" and values.get("card_id"):
                card_ids[values["id"]] = values.pop("card_id")
            elif table == "tasks":
                values["assignees"] = [a for a in values.get("assignees") or [] if a in known_users]
                values["group_assignees"] = [local_groups[handles[g]] for g in raw.get("group_assignees") or []
                                             if handles.get(g) in local_groups]
                values["var_bindings"] = {k: v for k, v in (values.get("var_bindings") or {}).items() if v in mapped}
            elif table in ("comments", "posts"):
                values["mentions"] = [x for x in values.get("mentions") or [] if x in known_users]
            elif table == "assets":
                values["secret"] = sec.into(raw.get("secret"))
            elif table == "attachments":
                source = f"files/{raw['id']}"
                if source not in archive.names:
                    continue
                directory = settings.uploads_dir / str(project_id)
                directory.mkdir(parents=True, exist_ok=True)
                safe = re.sub(r"[^A-Za-z0-9._-]+", "_", raw.get("filename") or "file")[:120]
                path = directory / f"{values['id']}-{safe}"
                with archive.zf.open(source) as src, path.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                result.files.append(path)
                values["storage_path"] = str(path.relative_to(settings.data_dir))
            elif table == "activity":
                values["project_id"] = project_id
            rows.append(values)
        await insert_rows(db, model, rows)
    for run_id, card_id in card_ids.items():
        await db.execute(update(m.PlaybookRun).where(m.PlaybookRun.id == run_id).values(card_id=card_id))

    members = {uuid.UUID(ids[u]) for u in bundle.get("members", []) if u in ids}
    if not user.is_admin:
        members.add(user.id)
    for uid in members:
        await add_member(db, project_id, uid)
    if bundle.get("groups"):
        for gid in (await db.execute(select(m.Group.id).where(m.Group.slug.in_([str(s) for s in bundle["groups"]])))).scalars():
            db.add(m.ProjectGroup(project_id=project_id, group_id=gid))
    source = archive.manifest.get("created_by") or "another Castor"
    await record_activity(db, type="project.imported", message=f"Imported project {codename} (exported by {source})",
                          project_id=project_id, payload={"from": archive.manifest.get("castor_version")}, **actor_of(user))
    result.projects.append(project_id)
    result.add("projects", codename)
    return project_id


def discard(result: Result) -> None:
    """Remove files written by an import that rolled back."""
    for path in result.files:
        path.unlink(missing_ok=True)
