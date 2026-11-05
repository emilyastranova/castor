"""Pinned project notes (Phase 3d): a few titled markdown notes at the top of a project's
dashboard. Anyone who sees the project reads them; operators edit them. Each edit keeps
the previous version, so an edit can be undone."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .. import models as m
from ..access import check_project
from ..deps import DB, CurrentUser, parse_uuid
from ..events import bus
from ..models import utcnow
from ..services import actor_of, record_activity

router = APIRouter(tags=["notes"])

HISTORY_LIMIT = 20
MAX_NOTES = 12
# Sticky-note colors; the web app maps each to a light and a dark shade (app.css --note-*).
COLORS = ("default", "red", "orange", "yellow", "green", "teal", "blue", "navy", "purple", "pink", "brown", "gray")
Color = Literal[COLORS]  # type: ignore[valid-type]


class NoteIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    body: str = Field("", max_length=20_000)
    color: Color = "default"


class NotePatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=120)
    body: str | None = Field(None, max_length=20_000)
    color: Color | None = None


class NoteOrder(BaseModel):
    ids: list[uuid.UUID]


class Editor(BaseModel):
    id: str
    name: str
    avatar_url: str | None = None


class NoteOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    body: str
    position: int
    color: str
    created_at: datetime
    updated_at: datetime
    updated_by: Editor | None
    versions: int  # earlier versions kept


class NoteVersion(BaseModel):
    index: int
    title: str
    body: str
    by_name: str
    at: datetime


def note_out(note: m.ProjectNote) -> NoteOut:
    editor = note.editor
    return NoteOut(
        id=note.id,
        project_id=note.project_id,
        title=note.title,
        body=note.body,
        position=note.position,
        color=note.color or "default",
        created_at=note.created_at,
        updated_at=note.updated_at,
        updated_by=Editor(id=str(editor.id), name=editor.display_name, avatar_url=editor.avatar_url) if editor else None,
        versions=len(note.history or []),
    )


async def _notes(db: DB, project_id: uuid.UUID) -> list[m.ProjectNote]:
    rows = await db.execute(
        select(m.ProjectNote)
        .where(m.ProjectNote.project_id == project_id)
        .order_by(m.ProjectNote.position, m.ProjectNote.created_at)
    )
    notes = list(rows.scalars())
    for note in notes:
        await db.refresh(note, ["editor"])
    return notes


async def _note_or_404(db: DB, user: m.User, note_id: str, *, write: bool = False) -> m.ProjectNote:
    note = await db.get(m.ProjectNote, parse_uuid(note_id))
    if note is None:
        raise HTTPException(404, "Note not found")
    try:
        await check_project(db, user, note.project_id, write=write)
    except HTTPException as err:
        if err.status_code == 404:
            raise HTTPException(404, "Note not found") from None
        raise
    return note


async def _changed(db: DB, project_id: uuid.UUID) -> None:
    """Tell browsers the project's notes changed (they reload them)."""
    bus.publish("project.notes", project_id, {"project_id": str(project_id)})


def _keep_version(note: m.ProjectNote) -> None:
    editor = note.editor
    entry = {
        "title": note.title,
        "body": note.body,
        "by": str(note.updated_by) if note.updated_by else None,
        "by_name": editor.display_name if editor else "someone",
        "at": note.updated_at.isoformat(),
    }
    note.history = [entry, *(note.history or [])][:HISTORY_LIMIT]


@router.get("/projects/{project_id}/notes", response_model=list[NoteOut])
async def list_notes(project_id: str, db: DB, user: CurrentUser):
    pid = parse_uuid(project_id)
    await check_project(db, user, pid)
    return [note_out(n) for n in await _notes(db, pid)]


@router.post("/projects/{project_id}/notes", response_model=NoteOut, status_code=201)
async def create_note(project_id: str, body: NoteIn, db: DB, user: CurrentUser):
    pid = parse_uuid(project_id)
    await check_project(db, user, pid, write=True)
    count, last = (
        await db.execute(
            select(func.count(), func.max(m.ProjectNote.position)).where(m.ProjectNote.project_id == pid)
        )
    ).one()
    if count >= MAX_NOTES:
        raise HTTPException(409, f"A project can have up to {MAX_NOTES} pinned notes")
    note = m.ProjectNote(
        id=m.new_id(), project_id=pid, title=body.title.strip(), body=body.body, position=(last or 0) + 1, updated_by=user.id,
        color=body.color,
    )
    db.add(note)
    await record_activity(
        db, type="note.created", message=f"Pinned the note “{note.title}”", project_id=pid,
        payload={"note_id": str(note.id)}, **actor_of(user),
    )
    await db.commit()
    await db.refresh(note, ["editor"])
    await _changed(db, pid)
    return note_out(note)


@router.patch("/notes/{note_id}", response_model=NoteOut)
async def update_note(note_id: str, body: NotePatch, db: DB, user: CurrentUser):
    note = await _note_or_404(db, user, note_id, write=True)
    title = body.title.strip() if body.title is not None else note.title
    text = body.body if body.body is not None else note.body
    recolored = body.color is not None and body.color != note.color
    if recolored:
        note.color = body.color  # cosmetic: no version, no feed entry, and not "edited by"
    if (title, text) == (note.title, note.body):
        if recolored:
            await db.commit()
            await _changed(db, note.project_id)
        await db.refresh(note, ["editor"])
        return note_out(note)
    await db.refresh(note, ["editor"])
    _keep_version(note)
    renamed = title != note.title
    message = f"Renamed the note “{note.title}” to “{title}”" if renamed and text == note.body else f"Updated the note “{title}”"
    note.title, note.body, note.updated_by, note.updated_at = title, text, user.id, utcnow()
    await record_activity(
        db, type="note.updated", message=message, project_id=note.project_id,
        payload={"note_id": str(note.id)}, **actor_of(user),
    )
    await db.commit()
    await db.refresh(note, ["editor"])
    await _changed(db, note.project_id)
    return note_out(note)


@router.delete("/notes/{note_id}", status_code=204)
async def delete_note(note_id: str, db: DB, user: CurrentUser):
    note = await _note_or_404(db, user, note_id, write=True)
    pid = note.project_id
    await record_activity(
        db, type="note.deleted", message=f"Removed the note “{note.title}”", project_id=pid, **actor_of(user)
    )
    await db.delete(note)
    await db.commit()
    await _changed(db, pid)


@router.get("/notes/{note_id}/history", response_model=list[NoteVersion])
async def note_history(note_id: str, db: DB, user: CurrentUser):
    note = await _note_or_404(db, user, note_id)
    return [
        NoteVersion(index=i, title=v["title"], body=v["body"], by_name=v.get("by_name") or "someone", at=v["at"])
        for i, v in enumerate(note.history or [])
    ]


@router.post("/notes/{note_id}/restore/{index}", response_model=NoteOut)
async def restore_note(note_id: str, index: int, db: DB, user: CurrentUser):
    """Bring back an earlier version. The current one goes into the history, so this is undoable too."""
    note = await _note_or_404(db, user, note_id, write=True)
    history = note.history or []
    if not 0 <= index < len(history):
        raise HTTPException(404, "No such version")
    version = history[index]
    await db.refresh(note, ["editor"])
    _keep_version(note)
    note.title, note.body, note.updated_by, note.updated_at = version["title"], version["body"], user.id, utcnow()
    await record_activity(
        db, type="note.updated", message=f"Restored an earlier version of the note “{note.title}”",
        project_id=note.project_id, payload={"note_id": str(note.id)}, **actor_of(user),
    )
    await db.commit()
    await db.refresh(note, ["editor"])
    await _changed(db, note.project_id)
    return note_out(note)


@router.post("/projects/{project_id}/notes/order", response_model=list[NoteOut])
async def order_notes(project_id: str, body: NoteOrder, db: DB, user: CurrentUser):
    pid = parse_uuid(project_id)
    await check_project(db, user, pid, write=True)
    notes = await _notes(db, pid)
    if {n.id for n in notes} != set(body.ids) or len(body.ids) != len(notes):
        raise HTTPException(422, "Give every note of the project exactly once")
    place = {nid: i for i, nid in enumerate(body.ids)}
    for note in notes:
        note.position = place[note.id]
    await db.commit()
    await _changed(db, pid)
    return [note_out(n) for n in sorted(notes, key=lambda n: n.position)]
