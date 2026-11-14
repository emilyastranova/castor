"""Every file in a project in one place: what people attached to cards, pictures pasted into notes
and posts, snapshots, and the artifacts jobs collected, with where each came from (card, run and
step, agent), filters with counts, small pictures for the gallery and a zip of a selection."""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from starlette.background import BackgroundTask

from .. import models as m
from ..access import check_project, project_or_404
from ..config import settings
from ..deps import DB, CurrentUser, parse_uuid

router = APIRouter(tags=["files"])

# ---- what sort of file it is ----

CATEGORIES: dict[str, str] = {
    "image": "Images",
    "text": "Text and logs",
    "data": "Data",
    "document": "Documents",
    "archive": "Archives and captures",
    "other": "Other",
}
SOURCES: dict[str, str] = {
    "upload": "Added by people",
    "artifact": "Collected from jobs",
    "snapshot": "Snapshots",
    "project": "In notes and posts",
}
_DATA = {".json", ".jsonl", ".ndjson", ".xml", ".csv", ".tsv", ".yaml", ".yml", ".toml", ".gnmap", ".nessus", ".sqlite", ".db"}
_TEXT = {".txt", ".log", ".out", ".md", ".nmap", ".conf", ".cfg", ".ini", ".sh", ".py", ".ps1", ".js", ".ts", ".html", ".htm", ".css",
         ".go", ".rs", ".c", ".h", ".java", ".rb", ".php", ".sql", ".pem", ".crt", ".key", ".diff", ".patch"}
_DOCUMENT = {".pdf", ".doc", ".docx", ".odt", ".rtf", ".xls", ".xlsx", ".ods", ".ppt", ".pptx", ".odp", ".epub"}
_ARCHIVE = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".zst", ".pcap", ".pcapng", ".cap", ".iso", ".img"}


def category(content_type: str, filename: str) -> str:
    """Images, text, data, documents, archives (and packet captures) or other, from the type and
    the name's extension (agents often upload with a generic type)."""
    ct = (content_type or "").split(";")[0].strip().lower()
    ext = PurePosixPath(filename.lower()).suffix
    if ct.startswith("image/"):
        return "image"
    if ext in _DATA or ct in ("application/json", "application/xml", "text/csv", "application/yaml", "text/xml"):
        return "data"
    if ext in _DOCUMENT or ct == "application/pdf" or ct.startswith(("application/vnd.openxmlformats", "application/msword")):
        return "document"
    if ext in _ARCHIVE or ct in ("application/zip", "application/gzip", "application/x-tar", "application/vnd.tcpdump.pcap"):
        return "archive"
    if ext in _TEXT or ct.startswith("text/"):
        return "text"
    return "other"


# ---- the listing ----


class FileOut(BaseModel):
    id: uuid.UUID
    filename: str
    content_type: str
    size: int
    created_at: datetime
    category: str
    source: str
    kind: str
    task_id: uuid.UUID | None = Field(description="The card it's attached to (a step's own card for a playbook step)")
    card_id: uuid.UUID | None = Field(description="The card on the board it belongs to (the run's card for a step)")
    card_title: str | None
    run_id: uuid.UUID | None
    run_name: str | None
    run_started: datetime | None = Field(None, description="When the run started (runs of one playbook share a name)")
    step: str | None = Field(description="The playbook step's card title, for a step's files")
    job_id: uuid.UUID | None
    agent_id: uuid.UUID | None
    agent_name: str | None
    uploader: str | None


class Facet(BaseModel):
    value: str
    label: str
    count: int
    when: datetime | None = Field(None, description="For runs: when it started")


class FileList(BaseModel):
    items: list[FileOut]
    total: int = Field(description="Files matching the filters")
    total_size: int = Field(description="Their size together, in bytes")
    everything: int = Field(description="Files in the project, whatever the filters")
    facets: dict[str, list[Facet]] = Field(
        description="For category, source, card, run and agent: each value's count with the other filters applied"
    )


Sort = Literal["newest", "oldest", "largest", "smallest", "name"]


class FileFilter(BaseModel):
    category: str | None = Field(None, description="Comma separated: image, text, data, document, archive, other")
    source: str | None = Field(None, description="Comma separated: upload, artifact, snapshot, project")
    card: str | None = Field(None, description="A board card's id (a run's card includes its steps' files); `none` for notes and posts")
    run: str | None = Field(None, description="A playbook run's id")
    agent: str | None = Field(None, description="The agent that ran the job (artifacts and snapshots of its output)")
    since: date | None = Field(None, description="Added on or after this day")
    until: date | None = Field(None, description="Added on or before this day")
    q: str | None = Field(None, description="Words in the file name, card, run or step")
    tz: str | None = Field(None, description="Time zone the days are in (IANA name); UTC if left out")


class FileQuery(FileFilter):
    sort: Sort = "newest"
    limit: int = Field(120, ge=1, le=500)
    offset: int = Field(0, ge=0)


@dataclass
class _Row:
    out: FileOut
    storage_path: str
    words: str


async def _rows(db: DB, project_id: uuid.UUID) -> list[_Row]:
    """Every file of the project with its card, run, step and agent (newest first)."""
    A, T, J = m.Attachment, m.Task, m.Job
    res = await db.execute(
        select(A.id, A.filename, A.content_type, A.size, A.created_at, A.kind, A.task_id, A.job_id, A.storage_path,
               A.snapshot, T.title, T.playbook_run_id, T.playbook_step_id, J.agent_id, m.User.display_name)
        .outerjoin(T, T.id == A.task_id)
        .outerjoin(J, J.id == A.job_id)
        .outerjoin(m.User, m.User.id == A.uploader_id)
        .where(A.project_id == project_id)
        .order_by(A.created_at.desc())
    )
    rows = res.all()
    runs = {r.id: r for r in (await db.execute(
        select(m.PlaybookRun.id, m.PlaybookRun.playbook_name, m.PlaybookRun.card_id, m.PlaybookRun.created_at)
        .where(m.PlaybookRun.project_id == project_id))).all()}
    run_of_card = {r.card_id: r for r in runs.values() if r.card_id}
    agent_ids = {r.agent_id for r in rows if r.agent_id}
    agents = dict((await db.execute(select(m.Agent.id, m.Agent.name).where(m.Agent.id.in_(agent_ids)))).all()) if agent_ids else {}
    run_cards = {r.card_id for r in runs.values() if r.card_id}
    titles = dict((await db.execute(select(T.id, T.title).where(T.id.in_(run_cards)))).all()) if run_cards else {}

    out: list[_Row] = []
    for r in rows:
        run = runs.get(r.playbook_run_id) if r.playbook_run_id else run_of_card.get(r.task_id)
        step = r.title if r.playbook_run_id and r.playbook_step_id else None
        if step and run and run.card_id:
            card_id, card_title = run.card_id, titles.get(run.card_id) or run.playbook_name
        else:
            card_id, card_title = r.task_id, r.title
        if r.snapshot:
            source = "snapshot"
        elif r.kind == "artifact":
            source = "artifact"
        elif r.task_id is None:
            source = "project"
        else:
            source = "upload"
        f = FileOut(
            id=r.id, filename=r.filename, content_type=r.content_type, size=r.size or 0, created_at=r.created_at,
            category=category(r.content_type, r.filename), source=source, kind=r.kind, task_id=r.task_id, card_id=card_id,
            card_title=card_title, run_id=run.id if run else None, run_name=_run_label(run) if run else None,
            run_started=run.created_at if run else None, step=step,
            job_id=r.job_id, agent_id=r.agent_id, agent_name=agents.get(r.agent_id) if r.agent_id else None,
            uploader=r.display_name,
        )
        words = " ".join(x for x in (f.filename, f.card_title, f.run_name, f.step, f.agent_name, f.uploader) if x).lower()
        out.append(_Row(f, r.storage_path, words))
    return out


def _run_label(run: Any) -> str:
    return run.playbook_name


def _zone(tz: str | None) -> Any:
    """The time zone days are counted in; UTC for none or one Python doesn't know."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        return ZoneInfo(tz) if tz else UTC
    except (ZoneInfoNotFoundError, ValueError):
        return UTC


def _tests(f: FileFilter) -> dict[str, Any]:
    """One predicate per filter that's set, keyed by its facet (so a facet's counts can leave its own
    filter out)."""
    tests: dict[str, Any] = {}
    split = lambda s: {x.strip() for x in s.split(",") if x.strip()}
    if f.category:
        want_c = split(f.category)
        tests["category"] = lambda r: r.out.category in want_c
    if f.source:
        want_s = split(f.source)
        tests["source"] = lambda r: r.out.source in want_s
    if f.card:
        card = f.card
        tests["card"] = (lambda r: r.out.card_id is None) if card == "none" else (lambda r: str(r.out.card_id) == card)
    if f.run:
        tests["run"] = lambda r: str(r.out.run_id) == f.run
    if f.agent:
        tests["agent"] = lambda r: str(r.out.agent_id) == f.agent
    zone = _zone(f.tz)
    if f.since:
        start = datetime.combine(f.since, time.min, zone)
        tests["since"] = lambda r: r.out.created_at >= start
    if f.until:
        end = datetime.combine(f.until + timedelta(days=1), time.min, zone)
        tests["until"] = lambda r: r.out.created_at < end
    if f.q and f.q.strip():
        words = f.q.lower().split()
        tests["q"] = lambda r: all(w in r.words for w in words)
    return tests


def _apply(rows: list[_Row], tests: dict[str, Any], leave_out: str | None = None) -> list[_Row]:
    checks = [t for k, t in tests.items() if k != leave_out]
    return [r for r in rows if all(t(r) for t in checks)]


def _facets(rows: list[_Row], tests: dict[str, Any]) -> dict[str, list[Facet]]:
    def count(facet: str, key: Any, label: Any, order: list[str] | None = None, when: Any = None) -> list[Facet]:
        seen: dict[str, Facet] = {}
        for r in _apply(rows, tests, facet):
            k = key(r)
            if k is None:
                continue
            if k not in seen:
                seen[k] = Facet(value=k, label=label(r), count=0, when=when(r) if when else None)
            seen[k].count += 1
        if order is not None:
            return [seen[k] for k in order if k in seen]
        return sorted(seen.values(), key=lambda x: (-x.count, x.label.lower()))

    return {
        "category": count("category", lambda r: r.out.category, lambda r: CATEGORIES[r.out.category], list(CATEGORIES)),
        "source": count("source", lambda r: r.out.source, lambda r: SOURCES[r.out.source], list(SOURCES)),
        "card": count("card", lambda r: str(r.out.card_id) if r.out.card_id else "none",
                      lambda r: r.out.card_title or "Notes and posts"),
        "run": count("run", lambda r: str(r.out.run_id) if r.out.run_id else None, lambda r: r.out.run_name or "",
                     when=lambda r: r.out.run_started),
        "agent": count("agent", lambda r: str(r.out.agent_id) if r.out.agent_id else None, lambda r: r.out.agent_name or "An agent"),
    }


def _sorted(rows: list[_Row], sort: Sort) -> list[_Row]:
    if sort == "oldest":
        return list(reversed(rows))
    if sort == "largest":
        return sorted(rows, key=lambda r: -r.out.size)
    if sort == "smallest":
        return sorted(rows, key=lambda r: r.out.size)
    if sort == "name":
        return sorted(rows, key=lambda r: r.out.filename.lower())
    return rows


@router.get("/projects/{project_id}/files", response_model=FileList)
async def list_files(
    project_id: str,
    db: DB,
    user: CurrentUser,
    f: Annotated[FileQuery, Query()],
):
    """Every file and screenshot in a project (card attachments, pictures in notes and posts,
    snapshots, job artifacts), with where each came from, filtered, and counts per filter value."""
    pid = parse_uuid(project_id)
    await project_or_404(db, user, pid)
    rows = await _rows(db, pid)
    tests = _tests(f)
    matching = _sorted(_apply(rows, tests), f.sort)
    return FileList(
        items=[r.out for r in matching[f.offset: f.offset + f.limit]],
        total=len(matching),
        total_size=sum(r.out.size for r in matching),
        everything=len(rows),
        facets=_facets(rows, tests),
    )


# ---- a zip of a selection ----

ZIP_MAX_FILES = 5000
_STORED = {"image", "archive"}  # already compressed


class ZipIn(BaseModel):
    ids: list[uuid.UUID] | None = Field(None, description="These files; or leave out for every file matching `filter`")
    filter: FileFilter | None = None


def _folder(f: FileOut) -> str:
    """Where a file goes in the zip: its card (a run's card, then the step), or Notes and posts."""
    def clean(s: str) -> str:
        s = "".join(c if c.isalnum() or c in " ._-()[]&+,'" else "_" for c in s).strip(" .")
        return s[:80] or "untitled"

    if f.card_id is None:
        return "Notes and posts"
    parts = [clean(f.card_title or "Card")]
    if f.step:
        parts.append(clean(f.step))
    return "/".join(parts)


def _write_zip(path: str, entries: list[tuple[str, str, str]]) -> None:
    with zipfile.ZipFile(path, "w", allowZip64=True) as z:
        for source, name, cat in entries:
            z.write(source, name, compress_type=zipfile.ZIP_STORED if cat in _STORED else zipfile.ZIP_DEFLATED)


@router.post("/projects/{project_id}/files/zip")
async def zip_files(project_id: str, body: ZipIn, db: DB, user: CurrentUser):
    """Download files of a project as one zip, in a folder per card (a run's steps inside its card)."""
    pid = parse_uuid(project_id)
    project = await project_or_404(db, user, pid)
    rows = await _rows(db, pid)
    if body.ids is not None:
        want = set(body.ids)
        chosen = [r for r in rows if r.out.id in want]
    else:
        chosen = _apply(rows, _tests(body.filter or FileFilter()))
    if not chosen:
        raise HTTPException(422, "No files to download")
    if len(chosen) > ZIP_MAX_FILES:
        raise HTTPException(422, f"That's {len(chosen)} files; download at most {ZIP_MAX_FILES} at a time")

    used: set[str] = set()
    entries: list[tuple[str, str, str]] = []
    for r in reversed(chosen):  # oldest first, so the first copy of a name keeps it
        source = settings.data_dir / r.storage_path
        if not source.is_file():
            continue
        # An artifact keeps the folders it was collected from (never anything climbing out).
        parts = [p for p in r.out.filename.replace("\\", "/").split("/") if p not in ("", ".", "..")] or ["file"]
        folder = "/".join([_folder(r.out), *parts[:-1]])
        stem, dot, ext = parts[-1].rpartition(".")
        if not dot or not stem:
            stem, ext = parts[-1], ""
        name, n = f"{folder}/{stem}{'.' + ext if ext else ''}", 2
        while name.lower() in used:
            name, n = f"{folder}/{stem} ({n}){'.' + ext if ext else ''}", n + 1
        used.add(name.lower())
        entries.append((str(source), name, r.out.category))
    if not entries:
        raise HTTPException(410, "Those files are missing from storage")

    fd, tmp = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    try:
        await asyncio.to_thread(_write_zip, tmp, entries)
    except Exception:
        os.unlink(tmp)
        raise
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    return FileResponse(tmp, media_type="application/zip", filename=f"{project.codename}-files-{stamp}.zip",
                        background=BackgroundTask(os.unlink, tmp))


# ---- small pictures for the gallery ----

THUMB_SIZES = (160, 360, 720)
_MAX_PIXELS = 80_000_000  # refuse anything bigger than that (decompression bombs)


def thumbnails_dir(project_id: uuid.UUID) -> Any:
    return settings.data_dir / "thumbnails" / str(project_id)


def _thumbnail(source: str, target: str, size: int) -> bool:
    from PIL import Image, ImageOps

    Image.MAX_IMAGE_PIXELS = _MAX_PIXELS
    try:
        with Image.open(source) as img:
            img.draft("RGB", (size * 2, size * 2))  # JPEGs decode smaller, much faster
            img = ImageOps.exif_transpose(img)
            img.thumbnail((size, size * 3), Image.Resampling.LANCZOS)
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA" if "A" in img.getbands() or img.mode == "P" else "RGB")
            tmp = f"{target}.{uuid.uuid4().hex[:6]}.tmp"
            img.save(tmp, "WEBP", quality=80, method=4)
            os.replace(tmp, target)
            return True
    except (OSError, ValueError, Image.DecompressionBombError, SyntaxError):
        return False


@router.get("/attachments/{attachment_id}/thumbnail")
async def attachment_thumbnail(attachment_id: str, db: DB, user: CurrentUser, size: int = 360):
    """A small WebP of an image attachment (made once, then kept). 415 for what isn't a picture
    Castor can shrink (SVG, say): show the original or an icon instead."""
    attachment = await db.get(m.Attachment, parse_uuid(attachment_id))
    if attachment is None:
        raise HTTPException(404, "Attachment not found")
    await check_project(db, user, attachment.project_id)
    if not attachment.content_type.startswith("image/") or "svg" in attachment.content_type:
        raise HTTPException(415, "Not a picture with a thumbnail")
    size = min(THUMB_SIZES, key=lambda s: (s < size, abs(s - size)))
    source = settings.data_dir / attachment.storage_path
    if not source.is_file():
        raise HTTPException(410, "File is missing from storage")
    # The stored path changes when a snapshot is redrawn, so its thumbnail does too.
    key = hashlib.sha256(attachment.storage_path.encode()).hexdigest()[:10]
    directory = thumbnails_dir(attachment.project_id)
    target = directory / f"{attachment.id}-{size}-{key}.webp"
    if not target.is_file():
        directory.mkdir(parents=True, exist_ok=True)
        for old in directory.glob(f"{attachment.id}-{size}-*.webp"):
            old.unlink(missing_ok=True)
        if not await asyncio.to_thread(_thumbnail, str(source), str(target), size):
            raise HTTPException(415, "Castor couldn't read this picture")
    return Response(target.read_bytes(), media_type="image/webp", headers={"Cache-Control": "private, max-age=604800"})


def forget_thumbnails(attachment: m.Attachment) -> None:
    """Remove an attachment's thumbnails (it was deleted)."""
    for old in thumbnails_dir(attachment.project_id).glob(f"{attachment.id}-*.webp"):
        old.unlink(missing_ok=True)
