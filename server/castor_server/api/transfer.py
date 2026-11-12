"""Import and export (Phase 3i): archives out, previews and imports in, and their history."""

from __future__ import annotations

import io
import tempfile
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from starlette.background import BackgroundTask

from .. import models as m
from .. import transfer as T
from ..access import project_or_404, visible_project_ids
from ..deps import DB, Admin, CurrentUser, Operator
from ..events import bus
from ..services import actor_of, record_activity

router = APIRouter(tags=["transfer"])


class ExportIn(BaseModel):
    everything: bool = False
    parsers: list[str] = []
    commands: list[str] = []
    tools: list[str] = []
    playbooks: list[str] = []
    themes: list[str] = []
    variable_kinds: list[str] = []
    variable_sets: list[uuid.UUID] = []
    projects: list[uuid.UUID] = []
    jobs: bool = False
    activity: bool = False
    passphrase: str | None = Field(default=None, max_length=500)
    label: str = Field(default="", max_length=100)


@router.post("/export")
async def export(body: ExportIn, db: DB, user: CurrentUser):
    """Stream an archive of what was asked for. Projects must be ones the user can see;
    everything is for admins; secrets (a passphrase) need an operator."""
    if body.everything and not user.is_admin:
        raise HTTPException(403, "Exporting everything is for admins")
    if body.passphrase and not user.can(m.UserRole.operator):
        raise HTTPException(403, "Exporting secrets needs an operator")
    if body.passphrase and len(body.passphrase) < 8:
        raise HTTPException(422, "passphrase: use at least 8 characters")
    for pid in body.projects:
        await project_or_404(db, user, pid)
    if body.variable_sets and (visible := await visible_project_ids(db, user)) is not None:
        rows = (await db.execute(select(m.VariableSet).where(m.VariableSet.id.in_(body.variable_sets)))).scalars()
        for row in rows:
            if row.project_ids and not any(uuid.UUID(str(p)) in visible for p in row.project_ids):
                raise HTTPException(404, "Variable set not found")
    opts = T.ExportOptions(**body.model_dump(exclude={"label"}))
    handle = tempfile.NamedTemporaryFile(suffix=T.SUFFIX, delete=False)  # noqa: SIM115 - removed after sending
    try:
        manifest = await T.export_archive(db, user, opts, handle)
    finally:
        handle.close()
    if not manifest["contents"]:
        T.Path(handle.name).unlink(missing_ok=True)
        raise HTTPException(422, "Nothing to export")
    label = body.label or ("castor-everything" if body.everything else "castor-export")
    name = T.archive_name(manifest, label)
    await record_activity(
        db, type="transfer.export", message=f"Exported {_summary(manifest['contents'])}",
        project_id=None, payload={"file": name, "contents": manifest["contents"], "secrets": manifest["secrets"]["mode"]},
        **actor_of(user),
    )
    await db.commit()
    return FileResponse(handle.name, media_type="application/zip", filename=name,
                        background=BackgroundTask(T.Path(handle.name).unlink, missing_ok=True))


def _summary(contents: dict[str, int]) -> str:
    words = {"parsers": "parser", "commands": "command", "tools": "tool", "playbooks": "playbook", "themes": "theme", "variable_kinds": "variable kind",
             "variable_sets": "variable set", "projects": "project", "users": "user"}
    parts = [f"{n} {words.get(k, k)}{'' if n == 1 else 's'}" for k, n in contents.items() if n]
    return ", ".join(parts) or "nothing"


class PreviewOut(BaseModel):
    token: str
    filename: str
    manifest: dict[str, Any]
    secrets: dict[str, Any]
    items: list[dict[str, Any]]
    people: dict[str, Any]
    warnings: list[str]


@router.post("/import/preview", response_model=PreviewOut)
async def preview(
    db: DB,
    user: Operator,
    file: Annotated[UploadFile | None, File()] = None,
    token: Annotated[str | None, Form()] = None,
    filename: Annotated[str, Form()] = "",
    passphrase: Annotated[str | None, Form()] = None,
):
    """Upload an archive (or a single library .yaml) and see what importing would do. Send
    the returned token back, with a passphrase, to preview again without re-uploading."""
    try:
        if file is not None:
            filename = file.filename or "upload"
            if filename.lower().endswith((".yaml", ".yml")):
                data = await file.read(T.MAX_UPLOAD)
                token, path = T.stage(io.BytesIO(T.single_yaml_archive(data, filename)))
            else:
                token, path = T.stage(file.file)
        elif token:
            path = T.staged(token)
        else:
            raise HTTPException(422, "Choose a file to import")
        archive = T.Archive(path)
        try:
            plan = await T.plan(db, user, archive, passphrase)
        finally:
            archive.close()
    except T.TransferError as exc:
        raise HTTPException(422, str(exc)) from exc
    return PreviewOut(token=token, filename=filename, **plan)


class ImportIn(BaseModel):
    token: str
    filename: str = Field(default="", max_length=255)
    choices: dict[str, Literal["import", "skip", "replace", "copy"]] = {}
    passphrase: str | None = None
    create_users: bool = False


class ImportOut(BaseModel):
    imported: dict[str, list[str]]
    skipped: int
    created_users: list[str]
    projects: list[uuid.UUID]


@router.post("/import", response_model=ImportOut)
async def run_import(body: ImportIn, db: DB, user: Operator):
    """Import with the chosen resolutions, all or nothing."""
    try:
        path = T.staged(body.token)
        archive = T.Archive(path)
    except T.TransferError as exc:
        raise HTTPException(422, str(exc)) from exc
    result = T.Result()
    try:
        result = await T.run_import(db, user, archive, body.choices, passphrase=body.passphrase,
                                    create_users=body.create_users and user.is_admin)
        total = sum(len(v) for v in result.imported.values())
        if total:
            await record_activity(
                db, type="transfer.import",
                message=f"Imported {_summary({k: len(v) for k, v in result.imported.items()})} from {body.filename or 'an archive'}",
                project_id=None, payload={"file": body.filename, "imported": result.imported, "created_users": result.created_users},
                **actor_of(user),
            )
        await db.commit()
    except T.TransferError as exc:
        await db.rollback()
        T.discard(result)
        raise HTTPException(422, str(exc)) from exc
    except Exception:
        await db.rollback()
        T.discard(result)
        raise
    finally:
        archive.close()
    path.unlink(missing_ok=True)
    if result.parsers:
        from ..parser_library import register

        for spec in result.parsers:
            register(spec)
    for pid in result.projects:
        members = (await db.execute(select(m.ProjectMember.user_id).where(m.ProjectMember.project_id == pid))).scalars()
        bus.publish("project.members", None, {"project_id": pid, "user_ids": [str(u) for u in members]})
    if result.imported.get("tools"):
        bus.publish("tools.changed", None, {})
    if result.imported.get("themes"):
        bus.publish("themes.changed", None, {})
    return ImportOut(imported=result.imported, skipped=result.skipped, created_users=result.created_users, projects=result.projects)


class HistoryItem(BaseModel):
    id: int
    type: str
    message: str
    actor_name: str
    created_at: Any
    payload: dict[str, Any]


@router.get("/transfer/history", response_model=list[HistoryItem])
async def history(db: DB, _: Admin, limit: int = 50):
    rows = (await db.execute(
        select(m.ActivityEvent).where(m.ActivityEvent.type.in_(("transfer.export", "transfer.import")))
        .order_by(m.ActivityEvent.id.desc()).limit(max(1, min(limit, 200)))
    )).scalars()
    return [HistoryItem(id=e.id, type=e.type, message=e.message, actor_name=e.actor_name, created_at=e.created_at,
                        payload=e.payload or {}) for e in rows]
