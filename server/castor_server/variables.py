"""Variable kinds and sets: typed, reusable values (credentials, keys, listeners, ...) that
commands consume through named slots.

A *kind* is a template of typed fields (`ssh-key`: username, private_key, passphrase).
A *set* is one filled-in instance of a kind ("Lab SSH key"). A command declares slots
(`vars: [{name: creds, kind: credential}]`) and uses `{{ creds.username }}` in its
template; a card or playbook step binds each slot to a set.

Values never enter a command line. A placeholder renders as a reference to an environment
variable (`"${CASTOR_VAR_CREDS_USERNAME}"`), and at dispatch the server sends the values
with the job: plain and secret fields as environment variables, file fields as files the
agent writes into the job directory (the variable then holds the file's path). Secrets and
all file contents are encrypted at rest (vault.py).
"""

from __future__ import annotations

import base64
import ipaddress
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m
from . import vault

FieldType = Literal[
    "text", "number", "boolean", "host", "port", "list", "secret", "file", "secret_file"
]
SECRET_TYPES = frozenset({"secret", "secret_file"})
FILE_TYPES = frozenset({"file", "secret_file"})
KIND_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
# Lowercase so slot and field names map cleanly onto CASTOR_VAR_<SLOT>_<FIELD>.
FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
HOSTNAME = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9_]([A-Za-z0-9_-]{0,62})(\.[A-Za-z0-9_-]{1,63})*\.?$")
# For the "$ command" display line. Expansions with a default/alternative word:
# ${X:+word} ${X+word} ${X:-word} ${X-word} (the word can't contain "}"), with the space
# before them so an expansion that comes out empty doesn't leave a double space.
DISPLAY_EXPANSION = re.compile(r"( ?)\$\{(CASTOR_VAR_[A-Z0-9_]+)(:?[-+])([^}]*)\}")
DISPLAY_LENGTH = re.compile(r"\$\{#(CASTOR_VAR_[A-Z0-9_]+)\}")
# A whole quoted word: "${X}" as rendered from {{ slot.field }}, or "$X"
DISPLAY_QUOTED = re.compile(r'"\$(?:\{(CASTOR_VAR_[A-Z0-9_]+)\}|(CASTOR_VAR_[A-Z0-9_]+)\b)"')
# Anything else: $X or ${X}, e.g. inside a longer double-quoted string
DISPLAY_BARE = re.compile(r"\$\{(CASTOR_VAR_[A-Z0-9_]+)\}|\$(CASTOR_VAR_[A-Z0-9_]+)\b")


def single_quote(text: str) -> str:
    """Always single-quoted (unlike shlex.quote, which leaves safe words bare)."""
    return "'" + text.replace("'", "'\\''") + "'"


# Shorter secrets are not redacted from logs: masking "ab" would mangle ordinary output.
MIN_REDACT = 4
REDACTED = "••••"


class VariableError(ValueError):
    pass


class KindField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: FieldType = "text"
    label: str = ""
    description: str = ""
    required: bool = False
    default: Any = None
    # Text: pick one of these (a dropdown). List: pick any of these (toggles).
    choices: list[str] | None = None

    @field_validator("choices")
    @classmethod
    def clean_choices(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = [*dict.fromkeys(str(c).strip() for c in value if str(c).strip())]
        return cleaned or None

    @field_validator("name")
    @classmethod
    def check_name(cls, value: str) -> str:
        if not FIELD_NAME.match(value):
            raise ValueError("field names are lowercase letters, digits and underscores, starting with a letter")
        return value

    @model_validator(mode="after")
    def check_default(self) -> KindField:
        if self.choices and self.type not in ("text", "list"):
            raise ValueError(f"field {self.name}: only text and list fields can have choices")
        if self.default is not None:
            if self.type in SECRET_TYPES or self.type in FILE_TYPES:
                raise ValueError(f"field {self.name}: {self.type} fields can't have a default")
            self.default = normalise(self, self.default)
        return self

    @property
    def secret(self) -> bool:
        return self.type in SECRET_TYPES

    @property
    def is_file(self) -> bool:
        return self.type in FILE_TYPES


class KindSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str = ""
    fields: list[KindField] = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def check_id(cls, value: str) -> str:
        if not KIND_ID.match(value):
            raise ValueError("kind id uses lowercase letters, digits and hyphens")
        return value

    @model_validator(mode="after")
    def unique_fields(self) -> KindSpec:
        names = [f.name for f in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("field names must be unique")
        return self

    def field(self, name: str) -> KindField | None:
        return next((f for f in self.fields if f.name == name), None)


BUILTIN_KINDS: list[dict[str, Any]] = [
    {
        "id": "credential",
        "name": "Credential",
        "description": "A username with a password or NT hash, optionally for a domain. Every field is optional (leave them empty for a null session).",
        "fields": [
            {"name": "username", "type": "text"},
            {"name": "password", "type": "secret"},
            {
                "name": "nt_hash",
                "type": "secret",
                "label": "NT hash",
                "description": "NTLM hash for pass-the-hash, instead of the password",
            },
            {"name": "domain", "type": "text", "description": "Windows / AD domain, if any"},
        ],
    },
    {
        "id": "ssh-key",
        "name": "SSH key",
        "description": "A user and private key for SSH",
        "fields": [
            {"name": "username", "type": "text", "required": True},
            {"name": "private_key", "type": "secret_file", "required": True, "label": "Private key"},
            {"name": "passphrase", "type": "secret", "description": "Leave empty if the key has none"},
        ],
    },
    {
        "id": "listener",
        "name": "Listener",
        "description": "Where a callback or reverse connection should land",
        "fields": [
            {"name": "host", "type": "host", "required": True},
        ],
    },
    {
        "id": "target-list",
        "name": "Target list",
        "description": "Hosts, IPs, CIDR ranges, domains or URLs to point commands at (or to exclude)",
        "fields": [
            {"name": "entries", "type": "list", "required": True, "description": "One per line"},
        ],
    },
    {
        "id": "choice-list",
        "name": "Choice list",
        "description": "A list of values commands can offer as a dropdown (choices_from)",
        "fields": [
            {"name": "options", "type": "list", "required": True, "description": "One value per line"},
        ],
    },
    {
        "id": "api-token",
        "name": "API token",
        "description": "A bearer token for an HTTP API",
        "fields": [
            {"name": "token", "type": "secret", "required": True},
            {"name": "base_url", "type": "text", "label": "Base URL"},
        ],
    },
]


def normalise(spec: KindField, raw: Any) -> Any:
    """Check and convert a non-file value to the field's type; raises ValueError."""
    if spec.type in FILE_TYPES:
        raise ValueError("upload a file")
    if spec.type == "boolean":
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in ("true", "yes", "1", "on"):
            return True
        if text in ("false", "no", "0", "off"):
            return False
        raise ValueError("must be true or false")
    if spec.type == "number":
        try:
            number = float(raw)
        except (TypeError, ValueError):
            raise ValueError("must be a number") from None
        return int(number) if number.is_integer() else number
    if spec.type == "port":
        try:
            port = int(str(raw).strip())
        except ValueError:
            raise ValueError("must be a port number") from None
        if not 1 <= port <= 65535:
            raise ValueError("must be between 1 and 65535")
        return port
    if spec.type == "list":
        items = raw if isinstance(raw, list) else re.split(r"[,\n]", str(raw))
        values = [str(i).strip() for i in items if str(i).strip()]
        bad = [v for v in values if spec.choices and v not in spec.choices]
        if bad:
            raise ValueError(f"{', '.join(bad)} not in the choices ({', '.join(spec.choices or [])})")
        return values
    text = str(raw)
    if spec.choices and text not in spec.choices:
        raise ValueError(f"must be one of: {', '.join(spec.choices)}")
    if spec.type == "host":
        text = text.strip()
        try:
            ipaddress.ip_address(text.strip("[]"))
        except ValueError:
            if not HOSTNAME.match(text):
                raise ValueError("must be a hostname or IP address") from None
    return text


def env_name(slot: str, field_name: str) -> str:
    return f"CASTOR_VAR_{slot.upper()}_{field_name.upper()}"


def env_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ",".join(str(v) for v in value)
    return "" if value is None else str(value)


# ---------- storage helpers ----------


def kind_spec(row: m.VariableKind) -> KindSpec:
    return KindSpec.model_validate(row.spec)


def apply_values(row: m.VariableSet, spec: KindSpec, values: dict[str, Any]) -> None:
    """Set non-file fields from `values`: secrets are encrypted, None clears a field.
    Fields not in `values` are left alone. Raises VariableError naming every bad field."""
    errors: dict[str, str] = {}
    plain = dict(row.values or {})
    secrets = dict(row.secrets or {})
    for name, raw in values.items():
        f = spec.field(name)
        if f is None:
            errors[name] = f"{spec.id} has no field {name}"
            continue
        if f.is_file:
            errors[name] = "files are uploaded separately"
            continue
        if raw is None or raw == "" or raw == []:
            plain.pop(name, None)
            secrets.pop(name, None)
            continue
        try:
            value = normalise(f, raw)
        except ValueError as exc:
            errors[name] = str(exc)
            continue
        if f.secret:
            secrets[name] = vault.encrypt(str(value).encode())
        else:
            plain[name] = value
    if errors:
        raise VariableError("; ".join(f"{k}: {v}" for k, v in errors.items()))
    row.values, row.secrets = plain, secrets


def store_file(row: m.VariableSet, f: KindField, filename: str, data: bytes) -> None:
    files = dict(row.files or {})
    files[f.name] = {"filename": filename, "size": len(data), "data": vault.encrypt(data)}
    row.files = files


def missing_required(row: m.VariableSet, spec: KindSpec) -> list[str]:
    have = set(row.values or {}) | set(row.secrets or {}) | set(row.files or {})
    return [f.name for f in spec.fields if f.required and f.default is None and f.name not in have]


def allowed_in(row: m.VariableSet, project_id: uuid.UUID | str | None) -> bool:
    return not row.project_ids or (project_id is not None and str(project_id) in row.project_ids)


def read_field(row: m.VariableSet, spec: KindSpec, name: str) -> tuple[KindField, Any]:
    """A field's current value: plain value, decrypted secret, or (filename, bytes)."""
    f = spec.field(name)
    if f is None:
        raise VariableError(f"{spec.id} has no field {name}")
    if f.is_file:
        entry = (row.files or {}).get(name)
        return f, (entry["filename"], vault.decrypt(entry["data"])) if entry else None
    if f.secret:
        token = (row.secrets or {}).get(name)
        return f, vault.decrypt(token).decode() if token else None
    return f, (row.values or {}).get(name, f.default)


# ---------- resolving a job's bindings ----------


@dataclass
class Resolved:
    """What a job needs from its variable sets: environment, files to write, and the
    secret strings to mask in its logs."""

    env: dict[str, str] = field(default_factory=dict)
    files: list[dict[str, Any]] = field(default_factory=list)
    secrets: list[str] = field(default_factory=list)
    # Values of plain (non-secret) fields. Masking never touches these, so a username or
    # domain stays readable even when it contains or equals a secret.
    plain: list[str] = field(default_factory=list)
    # What each variable looks like in the job's "$ command" line: the value for plain
    # fields, •••• for secrets, the file path (relative to the job directory) for files.
    display: dict[str, str] = field(default_factory=dict)

    def redact(self, text: str) -> str:
        """Mask every secret value in `text`, except inside plain field values."""
        if not self.secrets:
            return text
        protect = sorted(
            {p for p in self.plain if any(s in p for s in self.secrets)}, key=len, reverse=True
        )
        for index, value in enumerate(protect):
            text = text.replace(value, f"\x00{index}\x00")
        for secret in self.secrets:
            text = text.replace(secret, REDACTED)
        for index, value in enumerate(protect):
            text = text.replace(f"\x00{index}\x00", value)
        return text

    def _shown(self, name: str, in_container: bool) -> str | None:
        text = self.display.get(name)
        if text is not None and name in self._file_vars and in_container:
            text = f"/work/{text}"
        return text

    def display_command(self, command: str, in_container: bool) -> str:
        """The command as the job will run it, for the top of its output: variable
        references replaced by their values (single-quoted where they form a word),
        secrets as '••••', and ${X:+...} / ${X:-...} expanded the way the shell will."""

        def expansion(m: re.Match[str]) -> str:
            space, name, op, word = m.groups()
            value = self._shown(name, in_container)
            is_set = value is not None and (value != "" or ":" not in op)
            if op.endswith("+"):
                result = self.display_command(word, in_container) if is_set else ""
            else:
                result = value if is_set else self.display_command(word, in_container)
            return f"{space}{result}" if result else ""

        def length(m: re.Match[str]) -> str:
            value = self._shown(m.group(1), in_container)
            if value is None or value == REDACTED:  # unset, or a secret: keep as written
                return m.group(0)
            return str(len(value))

        def quoted(m: re.Match[str]) -> str:
            value = self._shown(m.group(1) or m.group(2), in_container)
            return "''" if value is None else single_quote(value)

        def bare(m: re.Match[str]) -> str:
            value = self._shown(m.group(1) or m.group(2), in_container)
            return m.group(0) if value is None else value

        command = DISPLAY_EXPANSION.sub(expansion, command)
        command = DISPLAY_LENGTH.sub(length, command)
        command = DISPLAY_QUOTED.sub(quoted, command)
        return DISPLAY_BARE.sub(bare, command)

    @property
    def _file_vars(self) -> set[str]:
        return {f["env"] for f in self.files}


async def resolve(
    db: AsyncSession,
    slots: list[Any],
    bindings: dict[str, Any],
    project_id: uuid.UUID | None,
) -> Resolved:
    """Turn {slot: set id} into env vars and files for a job. `slots` are the command's
    VarSlot declarations. Raises VariableError with a message for the card."""
    out = Resolved()
    for slot in slots:
        set_id = (bindings or {}).get(slot.name)
        if not set_id:
            if slot.required:
                raise VariableError(f"no {slot.kind} set chosen for {slot.name}")
            continue
        try:
            row = await db.get(m.VariableSet, uuid.UUID(str(set_id)))
        except ValueError:
            row = None
        if row is None:
            raise VariableError(f"the set chosen for {slot.name} no longer exists")
        if row.kind_id != slot.kind:
            raise VariableError(f"{row.name} is a {row.kind_id} set; {slot.name} needs {slot.kind}")
        if not allowed_in(row, project_id):
            raise VariableError(f"{row.name} can't be used in this project")
        kind = await db.get(m.VariableKind, row.kind_id)
        if kind is None:
            raise VariableError(f"variable kind {row.kind_id} no longer exists")
        spec = kind_spec(kind)
        missing = missing_required(row, spec)
        if missing:
            raise VariableError(f"{row.name} has no value for {', '.join(missing)}")
        for f in spec.fields:
            name = env_name(slot.name, f.name)
            try:
                _, value = read_field(row, spec, f.name)
            except vault.VaultError as exc:
                raise VariableError(str(exc)) from exc
            if value is None:
                continue
            if f.is_file:
                _, data = value
                out.files.append(
                    {
                        "env": name,
                        "path": f".castor/vars/{slot.name}/{f.name}",
                        "data": base64.b64encode(data).decode(),
                        "mode": 0o600,
                    }
                )
                out.display[name] = f".castor/vars/{slot.name}/{f.name}"
                continue
            text = env_text(value)
            out.env[name] = text
            out.display[name] = REDACTED if f.secret else text
            if not f.secret and text:
                out.plain.append(text)
            if f.secret and len(text) >= MIN_REDACT:
                out.secrets.append(text)
    out.secrets.sort(key=len, reverse=True)
    return out


async def drop_unclaimed(db: AsyncSession, older_than_hours: int = 24) -> int:
    """Delete one-off sets that were entered but never bound to a card or run."""
    from datetime import UTC, datetime, timedelta

    cutoff = datetime.now(UTC) - timedelta(hours=older_than_hours)
    rows = (
        await db.execute(
            select(m.VariableSet).where(
                m.VariableSet.ephemeral.is_(True),
                m.VariableSet.task_id.is_(None),
                m.VariableSet.playbook_run_id.is_(None),
                m.VariableSet.created_at < cutoff,
            )
        )
    ).scalars().all()
    for row in rows:
        await db.delete(row)
    await db.commit()
    return len(rows)


# Sets created once, together with their built-in kind; afterwards they are ordinary
# sets the user can edit or delete (and they are not re-created).
SEED_SETS: dict[str, list[dict[str, Any]]] = {
    "choice-list": [
        {
            "name": "NetExec protocols",
            "description": "Protocols NetExec (nxc) supports, for commands' protocol dropdown",
            "values": {
                "options": ["ftp", "ldap", "mssql", "nfs", "rdp", "smb", "ssh", "vnc", "winrm", "wmi"]
            },
        }
    ]
}


async def sync_builtin_kinds(db: AsyncSession) -> int:
    for raw in BUILTIN_KINDS:
        spec = KindSpec.model_validate(raw)
        row = await db.get(m.VariableKind, spec.id)
        if row is not None and row.origin != "builtin":
            continue
        new = row is None
        if new:
            row = m.VariableKind(id=spec.id, origin="builtin")
            db.add(row)
        row.name, row.description = spec.name, spec.description
        row.spec = spec.model_dump(mode="json")
        await drop_removed_fields(db, spec)
        if new:
            for seed in SEED_SETS.get(spec.id, []):
                if await find_set(db, seed["name"]) is None:
                    db.add(
                        m.VariableSet(
                            kind_id=spec.id, name=seed["name"], description=seed["description"],
                            values=seed["values"], secrets={}, files={}, project_ids=[],
                        )
                    )
    await db.commit()
    return len(BUILTIN_KINDS)


async def drop_removed_fields(db: AsyncSession, spec: KindSpec) -> None:
    """When a kind loses a field, remove that field's stored value from its sets."""
    names = {f.name for f in spec.fields}
    rows = (await db.execute(select(m.VariableSet).where(m.VariableSet.kind_id == spec.id))).scalars()
    for row in rows:
        for column in ("values", "secrets", "files"):
            stored = getattr(row, column) or {}
            if set(stored) - names:
                setattr(row, column, {k: val for k, val in stored.items() if k in names})


async def kinds_by_id(db: AsyncSession) -> dict[str, KindSpec]:
    rows = (await db.execute(select(m.VariableKind))).scalars().all()
    return {r.id: kind_spec(r) for r in rows}


async def find_set(db: AsyncSession, ref: Any) -> m.VariableSet | None:
    """A variable set by id or by name."""
    if not ref:
        return None
    try:
        row = await db.get(m.VariableSet, uuid.UUID(str(ref)))
    except ValueError:
        row = None
    if row is None:
        row = (
            await db.execute(select(m.VariableSet).where(m.VariableSet.name == str(ref)))
        ).scalar_one_or_none()
    return row


def owned_by(row: m.VariableSet, task: m.Task | None) -> bool:
    """Whether a one-off set may be used by this card: its own, its playbook run's, or not
    yet claimed by anything."""
    if not row.ephemeral:
        return True
    if row.task_id is None and row.playbook_run_id is None:
        return True
    if task is None:
        return False
    return row.task_id == task.id or (
        row.playbook_run_id is not None and row.playbook_run_id == task.playbook_run_id
    )


def claim(row: m.VariableSet, task: m.Task | None = None, run_id: uuid.UUID | None = None) -> None:
    """Give an unclaimed one-off set an owner: a playbook run's cards share it, a plain
    card owns it alone."""
    if not row.ephemeral or row.task_id is not None or row.playbook_run_id is not None:
        return
    run_id = run_id or (task.playbook_run_id if task is not None else None)
    if run_id is not None:
        row.playbook_run_id = run_id
    elif task is not None:
        row.task_id = task.id


async def release_ephemeral(db: AsyncSession, task: m.Task, keep: set[str]) -> None:
    """Delete this card's one-off sets that it no longer uses."""
    for set_id in (task.var_bindings or {}).values():
        if set_id in keep:
            continue
        row = await find_set(db, set_id)
        if row is not None and row.ephemeral and row.task_id == task.id:
            await db.delete(row)


async def check_bindings(
    db: AsyncSession,
    slots: list[Any],
    bindings: dict[str, Any],
    project_id: uuid.UUID | None,
    task: m.Task | None = None,
) -> dict[str, str]:
    """Normalise {slot: set id or None} for a card: drops empty slots and checks each set
    exists, has the slot's kind and may be used in the project and by this card (one-off
    sets belong to one card or run; an unclaimed one is claimed here). Raises VariableError."""
    by_name = {s.name: s for s in slots}
    out: dict[str, str] = {}
    for slot_name, set_id in (bindings or {}).items():
        if not set_id:
            continue
        slot = by_name.get(slot_name)
        if slot is None:
            raise VariableError(f"the command has no variable slot {slot_name}")
        try:
            row = await db.get(m.VariableSet, uuid.UUID(str(set_id)))
        except ValueError:
            row = None
        if row is None:
            raise VariableError(f"{slot_name}: variable set not found")
        if row.kind_id != slot.kind:
            raise VariableError(f"{slot_name}: {row.name} is a {row.kind_id} set, not {slot.kind}")
        if not allowed_in(row, project_id):
            raise VariableError(f"{slot_name}: {row.name} can't be used in this project")
        if not owned_by(row, task):
            raise VariableError(f"{slot_name}: that manual entry belongs to another card")
        claim(row, task)
        out[slot_name] = str(row.id)
    return out


# ---------- list references in command arguments ----------
# A text or list argument can hold {"list": "<set id or name>"} instead of a value: the
# set's entries (its `entries` field, or its first list field) are filled in each time the
# card's command is rendered, so editing the list updates every card that uses it.


def is_list_ref(value: Any) -> bool:
    return isinstance(value, dict) and set(value) == {"list"} and bool(value["list"])


async def list_entries(db: AsyncSession, ref: Any) -> tuple[m.VariableSet | None, list[str], str | None]:
    """(set, entries, problem) for a {"list": ...} reference."""
    row = await find_set(db, ref["list"])
    if row is None:
        return None, [], f"no list named or with id {ref['list']}"
    kind = await db.get(m.VariableKind, row.kind_id)
    fields = [f for f in kind_spec(kind).fields if f.type == "list"] if kind else []
    if not fields:
        return row, [], f"{row.name} has no list field"
    chosen = next((f for f in fields if f.name == "entries"), fields[0])
    value = (row.values or {}).get(chosen.name) or []
    return row, [str(v) for v in value], None


async def expand_list_refs(
    db: AsyncSession, spec: Any, args: dict[str, Any], project_id: uuid.UUID | None = None
) -> dict[str, Any]:
    """Replace {"list": ...} argument values with the list's entries: a list argument gets
    the items, a text argument the items joined with commas. Raises VariableError."""
    from .assets import is_asset_ref, ref_values

    by_name = {a.name: a for a in spec.args}
    out = dict(args or {})
    for name, value in (args or {}).items():
        if is_asset_ref(value):
            # Values from the project's assets (3f): "hosts where smb_signing:false" -> their ips.
            arg = by_name.get(name)
            if arg is None:
                continue
            if arg.type not in ("string", "list"):
                raise VariableError(f"{name}: only text and list arguments can take assets")
            if project_id is None:
                raise VariableError(f"{name}: assets need a project")
            entries = await ref_values(db, project_id, value)
            out[name] = entries if arg.type == "list" else ",".join(entries)
            continue
        if not is_list_ref(value):
            continue
        arg = by_name.get(name)
        if arg is None:
            continue  # resolve_args reports unknown arguments
        if arg.type not in ("string", "list"):
            raise VariableError(f"{name}: only text and list arguments can take a list")
        row, entries, problem = await list_entries(db, value)
        if problem:
            raise VariableError(f"{name}: {problem}")
        if row is not None and not allowed_in(row, project_id):
            raise VariableError(f"{name}: {row.name} can't be used in this project")
        out[name] = entries if arg.type == "list" else ",".join(entries)
    return out


async def choice_source(db: AsyncSession, ref: Any) -> tuple[list[str] | None, str | None]:
    """The options behind an argument's choices_from: (options, problem)."""
    row = await find_set(db, ref.set)
    if row is None or row.ephemeral:
        return None, f"no variable set named {ref.set}"
    kind = await db.get(m.VariableKind, row.kind_id)
    f = kind_spec(kind).field(ref.field) if kind else None
    if f is None:
        return None, f"variable set {ref.set} has no field {ref.field}"
    if f.type not in ("text", "list"):
        return None, f"{ref.set}.{ref.field} is a {f.type} field; choices need text or list"
    value = (row.values or {}).get(ref.field, f.default)
    if value in (None, "", []):
        return None, f"{ref.set}.{ref.field} is empty"
    return ([str(v) for v in value] if isinstance(value, list) else [str(value)]), None


async def expand_choices(db: AsyncSession, spec: Any) -> Any:
    """A copy of a command spec with every choices_from turned into choices. A source that
    is missing or empty leaves the argument without a list (a free text field)."""
    if not any(a.choices_from for a in spec.args):
        return spec
    args = []
    for arg in spec.args:
        if arg.choices_from:
            options, _problem = await choice_source(db, arg.choices_from)
            arg = arg.model_copy(update={"choices": options})
        args.append(arg)
    return spec.model_copy(update={"args": args})


async def check_choice_sources(db: AsyncSession, spec: Any) -> list[str]:
    problems = []
    for arg in spec.args:
        if arg.choices_from:
            _options, problem = await choice_source(db, arg.choices_from)
            if problem:
                problems.append(f"{arg.name}.choices_from: {problem}")
    return problems


def check_command_vars(spec: Any, kinds: dict[str, KindSpec]) -> list[str]:
    """Problems with a command's slots against the known kinds: unknown kind, or a
    `{{ slot.field }}` naming a field the kind doesn't have."""
    from .command_spec import VAR_PLACEHOLDER

    problems = []
    slots = {s.name: s for s in spec.vars}
    for slot in spec.vars:
        if slot.kind not in kinds:
            problems.append(f"vars.{slot.name}: unknown variable kind {slot.kind}")
    for slot_name, field_name in VAR_PLACEHOLDER.findall(spec.command):
        slot = slots.get(slot_name)
        kind = kinds.get(slot.kind) if slot else None
        if kind is not None and kind.field(field_name) is None:
            fields = ", ".join(f.name for f in kind.fields)
            problems.append(
                f"{{{{ {slot_name}.{field_name} }}}}: {kind.id} has no field {field_name} (fields: {fields})"
            )
    return problems
