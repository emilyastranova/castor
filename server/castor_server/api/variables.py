
"""Variable kinds (typed field templates) and variable sets (filled-in instances)."""

from __future__ import annotations

import base64
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select

from .. import models as m
from .. import variables as v
from .. import vault
from ..access import visible_project_ids
from ..config import settings
from ..deps import DB, CurrentUser, Operator, parse_uuid
from ..services import actor_of, library_activity, record_activity

router = APIRouter(prefix="/variables", tags=["variables"])


# ---------- schemas ----------


class KindOut(BaseModel):
    id: str
    origin: str
    name: str
    description: str
    fields: list[dict[str, Any]]
    set_count: int = 0


class KindIn(BaseModel):
    id: str
    name: str
    description: str = ""
    fields: list[dict[str, Any]]


class FileInfo(BaseModel):
    filename: str
    size: int


class SetOut(BaseModel):
    id: uuid.UUID
    kind_id: str
    name: str
    description: str
    project_ids: list[str]
    # Plain fields only. Secret fields appear in `secrets` as true when set.
    values: dict[str, Any]
    secrets: dict[str, bool]
    files: dict[str, FileInfo]
    missing: list[str]  # required fields that have no value yet
    # A one-off set entered on a card or at run time (not in the library).
    ephemeral: bool = False
    created_at: Any
    updated_at: Any


class SetIn(BaseModel):
    kind_id: str
    name: str = ""
    description: str = ""
    # One-off values for a single card or playbook run: hidden from the library, owned by
    # the first card/run that binds it, deleted with it. No name needed.
    ephemeral: bool = False
    project_ids: list[uuid.UUID] = Field(default_factory=list)
    values: dict[str, Any] = Field(default_factory=dict)


class SetPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    project_ids: list[uuid.UUID] | None = None
    # Only the fields given change; null or "" clears a field. Secrets are write-through:
    # send the new value to replace one.
    values: dict[str, Any] | None = None
    # false promotes a one-off set into the library (give it a name too).
    ephemeral: bool | None = None


class FieldValue(BaseModel):
    field: str
    type: str
    value: Any = None  # text value, or base64 contents for files
    filename: str | None = None


# ---------- helpers ----------


def _kind_out(row: m.VariableKind, count: int = 0) -> KindOut:
    spec = v.kind_spec(row)
    return KindOut(
        id=row.id,
        origin=row.origin,
        name=row.name,
        description=row.description,
        fields=[f.model_dump(mode="json") for f in spec.fields],
        set_count=count,
    )


def _parse_kind(body: KindIn) -> v.KindSpec:
    try:
        return v.KindSpec.model_validate(body.model_dump())
    except ValidationError as exc:
        err = exc.errors()[0]
        where = ".".join(str(p) for p in err["loc"])
        raise HTTPException(422, f"{where}: {err['msg'].removeprefix('Value error, ')}") from exc


async def _kind_or_404(db, kind_id: str) -> tuple[m.VariableKind, v.KindSpec]:
    row = await db.get(m.VariableKind, kind_id)
    if row is None:
        raise HTTPException(404, f"Variable kind “{kind_id}” not found")
    return row, v.kind_spec(row)


def _set_out(row: m.VariableSet, spec: v.KindSpec) -> SetOut:
    return SetOut(
        id=row.id,
        kind_id=row.kind_id,
        name=row.name,
        description=row.description,
        project_ids=row.project_ids or [],
        values=row.values or {},
        secrets={f.name: f.name in (row.secrets or {}) for f in spec.fields if f.type == "secret"},
        files={
            name: FileInfo(filename=info["filename"], size=info["size"])
            for name, info in (row.files or {}).items()
        },
        missing=v.missing_required(row, spec),
        ephemeral=row.ephemeral,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _set_or_404(db, set_id: str, user: m.User) -> tuple[m.VariableSet, v.KindSpec]:
    row = await db.get(m.VariableSet, parse_uuid(set_id))
    if row is None or not _can_see_set(row, await visible_project_ids(db, user)):
        raise HTTPException(404, "Variable set not found")
    _, spec = await _kind_or_404(db, row.kind_id)
    return row, spec


async def _check_name(db, name: str, exclude: uuid.UUID | None = None) -> str:
    name = name.strip()
    if not name:
        raise HTTPException(422, "name: enter a name")
    query = select(m.VariableSet.id).where(m.VariableSet.name == name)
    if exclude is not None:
        query = query.where(m.VariableSet.id != exclude)
    if (await db.execute(query)).first():
        raise HTTPException(409, f"A variable set named “{name}” already exists")
    return name


def _apply(row: m.VariableSet, spec: v.KindSpec, values: dict[str, Any]) -> None:
    try:
        v.apply_values(row, spec, values)
    except v.VariableError as exc:
        raise HTTPException(422, str(exc)) from exc
    except vault.VaultError as exc:
        raise HTTPException(500, str(exc)) from exc


# ---------- kinds ----------


@router.get("/kinds", response_model=list[KindOut])
async def list_kinds(db: DB, _: CurrentUser):
    counts = dict(
        (await db.execute(select(m.VariableSet.kind_id, func.count()).group_by(m.VariableSet.kind_id))).all()
    )
    rows = (await db.execute(select(m.VariableKind).order_by(m.VariableKind.name))).scalars().all()
    return [_kind_out(r, counts.get(r.id, 0)) for r in rows]


@router.post("/kinds", response_model=KindOut, status_code=201)
async def create_kind(body: KindIn, db: DB, user: Operator):
    spec = _parse_kind(body)
    if await db.get(m.VariableKind, spec.id) is not None:
        raise HTTPException(409, f"A variable kind with id “{spec.id}” already exists")
    row = m.VariableKind(
        id=spec.id, origin="user", name=spec.name, description=spec.description,
        spec=spec.model_dump(mode="json"),
    )
    db.add(row)
    await library_activity(db, user, "variable_kind", "created", spec.id, row.name)
    await db.commit()
    return _kind_out(row)


@router.put("/kinds/{kind_id}", response_model=KindOut)
async def update_kind(kind_id: str, body: KindIn, db: DB, user: Operator):
    row, old = await _kind_or_404(db, kind_id)
    if row.origin == "builtin":
        raise HTTPException(409, "Built-in kinds are read-only; create your own to customise")
    spec = _parse_kind(body)
    if spec.id != kind_id:
        raise HTTPException(422, "id: cannot be changed")
    # A field whose type changes would leave stored values of the old type behind.
    changed = [
        f.name for f in spec.fields
        if (o := old.field(f.name)) is not None and (o.type != f.type)
    ]
    count = (await db.execute(select(func.count()).where(m.VariableSet.kind_id == kind_id))).scalar()
    if changed and count:
        raise HTTPException(
            409, f"Can't change the type of {', '.join(changed)} while sets of this kind exist"
        )
    row.name, row.description, row.spec = spec.name, spec.description, spec.model_dump(mode="json")
    await v.drop_removed_fields(db, spec)
    await library_activity(db, user, "variable_kind", "updated", kind_id, row.name)
    await db.commit()
    return _kind_out(row, count or 0)


@router.delete("/kinds/{kind_id}", status_code=204)
async def delete_kind(kind_id: str, db: DB, user: Operator):
    row, _spec = await _kind_or_404(db, kind_id)
    if row.origin == "builtin":
        raise HTTPException(409, "Built-in kinds cannot be deleted")
    count = (await db.execute(select(func.count()).where(m.VariableSet.kind_id == kind_id))).scalar()
    if count:
        raise HTTPException(409, f"{count} variable set(s) use this kind; delete them first")
    await db.delete(row)
    await library_activity(db, user, "variable_kind", "deleted", kind_id, row.name)
    await db.commit()


# ---------- sets ----------


def _can_see_set(row: m.VariableSet, visible) -> bool:
    """Sets for every project are for everyone; a set limited to some projects is seen
    by those who can see one of them."""
    if visible is None or not row.project_ids:
        return True
    return any(uuid.UUID(str(p)) in visible for p in row.project_ids)


@router.get("/sets", response_model=list[SetOut])
async def list_sets(
    db: DB, user: CurrentUser, kind: str | None = None, project_id: str | None = None
):
    query = select(m.VariableSet).where(m.VariableSet.ephemeral.is_(False)).order_by(m.VariableSet.name)
    if kind:
        query = query.where(m.VariableSet.kind_id == kind)
    rows = (await db.execute(query)).scalars().all()
    visible = await visible_project_ids(db, user)
    rows = [r for r in rows if _can_see_set(r, visible)]
    if project_id:
        rows = [r for r in rows if v.allowed_in(r, project_id)]
    kinds = await v.kinds_by_id(db)
    return [_set_out(r, kinds[r.kind_id]) for r in rows if r.kind_id in kinds]


@router.get("/sets/{set_id}", response_model=SetOut)
async def get_set(set_id: str, db: DB, user: CurrentUser):
    row, spec = await _set_or_404(db, set_id, user)
    return _set_out(row, spec)


@router.post("/sets", response_model=SetOut, status_code=201)
async def create_set(body: SetIn, db: DB, user: Operator):
    _, spec = await _kind_or_404(db, body.kind_id)
    if body.ephemeral:
        name = f"manual-{uuid.uuid4().hex[:12]}"
    else:
        name = await _check_name(db, body.name)
    row = m.VariableSet(
        kind_id=spec.id,
        name=name,
        description=body.description,
        ephemeral=body.ephemeral,
        project_ids=[] if body.ephemeral else [str(p) for p in body.project_ids],
        values={},
        secrets={},
        files={},
        created_by=user.id,
    )
    _apply(row, spec, body.values)
    db.add(row)
    if not row.ephemeral:
        await record_activity(db, type="variable.set_created", message=f"Added the variable set {row.name}", project_id=None, **actor_of(user))
    await db.commit()
    return _set_out(row, spec)


@router.patch("/sets/{set_id}", response_model=SetOut)
async def update_set(set_id: str, body: SetPatch, db: DB, user: Operator):
    row, spec = await _set_or_404(db, set_id, user)
    if body.ephemeral is False and row.ephemeral:
        if not body.name:
            raise HTTPException(422, "name: enter a name to save it to Variables")
        row.ephemeral = False
        row.task_id = None
        row.playbook_run_id = None
    elif body.ephemeral is True and not row.ephemeral:
        raise HTTPException(422, "a library set can't become a one-off set")
    if body.name is not None:
        row.name = await _check_name(db, body.name, exclude=row.id)
    if body.description is not None:
        row.description = body.description
    if body.project_ids is not None:
        row.project_ids = [str(p) for p in body.project_ids]
    if body.values:
        _apply(row, spec, body.values)
    if not row.ephemeral:
        await record_activity(db, type="variable.set_updated", message=f"Changed the variable set {row.name}", project_id=None, **actor_of(user))
    await db.commit()
    return _set_out(row, spec)


@router.delete("/sets/{set_id}", status_code=204)
async def delete_set(set_id: str, db: DB, user: Operator):
    row, _spec = await _set_or_404(db, set_id, user)
    await db.delete(row)
    if not row.ephemeral:
        await record_activity(db, type="variable.set_deleted", message=f"Removed the variable set {row.name}", project_id=None, **actor_of(user))
    await db.commit()


@router.put("/sets/{set_id}/files/{field}", response_model=SetOut)
async def upload_file(
    set_id: str, field: str, db: DB, user: Operator, file: Annotated[UploadFile, File()]
):
    row, spec = await _set_or_404(db, set_id, user)
    f = spec.field(field)
    if f is None or not f.is_file:
        raise HTTPException(422, f"{field} is not a file field of {spec.id}")
    data = await file.read(settings.variable_file_max_bytes + 1)
    if len(data) > settings.variable_file_max_bytes:
        raise HTTPException(413, f"files are limited to {settings.variable_file_max_bytes} bytes")
    v.store_file(row, f, (file.filename or field)[:200], data)
    if not row.ephemeral:
        await record_activity(db, type="variable.set_updated", message=f"Uploaded {field} to the variable set {row.name}", project_id=None, **actor_of(user))
    await db.commit()
    return _set_out(row, spec)


@router.delete("/sets/{set_id}/files/{field}", response_model=SetOut)
async def delete_file(set_id: str, field: str, db: DB, user: Operator):
    row, spec = await _set_or_404(db, set_id, user)
    files = dict(row.files or {})
    files.pop(field, None)
    row.files = files
    if not row.ephemeral:
        await record_activity(db, type="variable.set_updated", message=f"Removed {field} from the variable set {row.name}", project_id=None, **actor_of(user))
    await db.commit()
    return _set_out(row, spec)


@router.post("/sets/{set_id}/reveal/{field}", response_model=FieldValue)
async def reveal(set_id: str, field: str, db: DB, user: Operator):
    """A field's stored value. Reading a secret or secret file is recorded in the
    activity feed."""
    row, spec = await _set_or_404(db, set_id, user)
    try:
        f, value = v.read_field(row, spec, field)
    except v.VariableError as exc:
        raise HTTPException(404, str(exc)) from exc
    except vault.VaultError as exc:
        raise HTTPException(500, str(exc)) from exc
    if value is None:
        raise HTTPException(404, f"{field} has no value")
    if f.secret:
        await record_activity(
            db,
            type="variable.revealed",
            message=f"Revealed {field} of variable set “{row.name}”",
            project_id=None,
            payload={"set_id": str(row.id), "field": field},
            **actor_of(user),
        )
        await db.commit()
    if f.is_file:
        filename, data = value
        return FieldValue(field=field, type=f.type, value=base64.b64encode(data).decode(), filename=filename)
    return FieldValue(field=field, type=f.type, value=value)
