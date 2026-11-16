"""Richer assets: what a run captured about an asset beyond text (`asset_evidence`).

A parser's row for an asset can name evidence next to its fields, and `assets._map` keeps it on
the asset (with the job, card and agent that captured it), not only as a job artifact:

- `screenshot`: the name of one of the job's artifacts (a picture), matched by its path or file name.
  Castor works out its **look**, a perceptual hash (`look_of`: a 256-bit difference hash of its shapes
  and a 4×4 grid of its brightness), so two screenshots of the same page match even when a clock or a session id on
  it differs. The asset's `look` field only changes when a new screenshot looks different from the
  current one (more than LOOK_DISTANCE bits apart), so "looks different" is an ordinary field change:
  history, Changes, and triggers (`look` with the `changed` condition) all see it.
- `certificate`: {subject, issuer, sans, valid_from, valid_to, fingerprint…}; `headers`: {name: value}.
  Kept when they differ from the asset's latest of the kind.
- `files`: names of other artifacts that belong to the asset.

Pictures and files stay when their card is deleted (`keep_on_delete`). The gallery groups assets by
look (`group_by_look`): the same login page on 40 hosts is one group.
"""

from __future__ import annotations

import asyncio
import posixpath
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m
from .config import settings

LOOK_DISTANCE = 24  # of 256 bits: within it, two screenshots look the same
_MAX_PIXELS = 80_000_000
MAX_HEADERS = 100
MAX_TEXT = 2000


# ---------- the look of a picture ----------


def look_of(path: Any) -> str | None:
    """A 256-bit difference hash (the picture in 17×16 greys, each bit whether a cell is brighter
    than the one to its right: its shapes), then its brightness in a 4×4 grid (16 levels each), which
    tells flat pages apart that have no shapes to speak of (a blue splash page and a blank one).
    80 hex digits; None for what isn't a readable picture."""
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = _MAX_PIXELS
    try:
        with Image.open(path) as img:
            img.draft("L", (256, 256))
            grey = img.convert("L")
            px = list(grey.resize((17, 16), Image.Resampling.LANCZOS).getdata())
            cells = list(grey.resize((4, 4), Image.Resampling.BOX).getdata())
    except (OSError, ValueError, Image.DecompressionBombError, SyntaxError):
        return None
    bits = 0
    for y in range(16):
        for x in range(16):
            bits = (bits << 1) | (px[y * 17 + x] > px[y * 17 + x + 1])
    return f"{bits:064x}" + "".join(f"{min(15, c // 16):x}" for c in cells)


LEVELS = 3  # of 16: how far one grid cell's brightness may be off and still look the same


def distance(a: str, b: str) -> int:
    """Bits apart in shape (of 256)."""
    try:
        return (int(a[:64], 16) ^ int(b[:64], 16)).bit_count()
    except ValueError:
        return 256


def similar(a: str | None, b: str | None) -> bool:
    if not a or not b or distance(a, b) > LOOK_DISTANCE:
        return False
    ga, gb = a[64:80], b[64:80]
    return len(ga) != 16 or len(gb) != 16 or all(abs(int(x, 16) - int(y, 16)) <= LEVELS for x, y in zip(ga, gb, strict=True))


def group_by_look(items: list[tuple[str, Any]]) -> list[list[Any]]:
    """Groups of things whose looks are within LOOK_DISTANCE of the group's first, biggest first
    (ties in the order given)."""
    groups: list[tuple[str, list[Any]]] = []
    for look, item in items:
        for head, members in groups:
            if similar(head, look):
                members.append(item)
                break
        else:
            groups.append((look, [item]))
    return [members for _, members in sorted(groups, key=lambda g: -len(g[1]))]


# ---------- from a parser's row ----------


@dataclass
class Pending:
    """Evidence read from a row before its asset is recorded: `fields` go onto the asset (its look)."""

    fields: dict[str, Any] = field(default_factory=dict)
    items: list[dict[str, Any]] = field(default_factory=list)


async def _artifacts(db: AsyncSession, job_id: uuid.UUID, cache: dict[Any, Any]) -> dict[str, m.Attachment]:
    key = ("artifacts", job_id)
    if key not in cache:
        rows = (await db.execute(select(m.Attachment).where(m.Attachment.job_id == job_id))).scalars().all()
        found: dict[str, m.Attachment] = {}
        for a in rows:
            found.setdefault(a.filename, a)
            found.setdefault(posixpath.basename(a.filename.replace("\\", "/")), a)
        cache[key] = found
    return cache[key]


def _find(artifacts: dict[str, m.Attachment], name: Any) -> m.Attachment | None:
    text = str(name or "").replace("\\", "/").strip()
    if not text:
        return None
    return artifacts.get(text.lstrip("/")) or artifacts.get(text.removeprefix("/work/")) or artifacts.get(posixpath.basename(text))


def _clean_map(value: Any, limit: int = MAX_HEADERS) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in list(value.items())[:limit]:
        if v is None or v == "":
            continue
        if isinstance(v, list):
            v = [str(x)[:MAX_TEXT] for x in v[:200]]
        elif not isinstance(v, (bool, int, float)):
            v = str(v)[:MAX_TEXT]
        out[str(k)[:200]] = v
    return out


async def prepare(db: AsyncSession, project_id: uuid.UUID, type_name: str, row: dict[str, Any], *, current: m.Asset | None,
                  job_id: uuid.UUID | None, cache: dict[Any, Any]) -> Pending:
    """Read a row's evidence; `current` is the asset it will be recorded on, if it exists yet."""
    p = Pending()
    artifacts = await _artifacts(db, job_id, cache) if job_id else {}
    shot = _find(artifacts, row.get("screenshot"))
    if shot is not None and shot.content_type.startswith("image/"):
        look = await asyncio.to_thread(look_of, settings.data_dir / shot.storage_path)
        was = (current.fields or {}).get("look") if current is not None else None
        changed = bool(look and was and not similar(look, was))
        if look:
            p.fields["look"] = look if (not was or changed) else was
        p.items.append({"kind": "screenshot", "attachment": shot, "look": look, "look_changed": changed})
    for name in row.get("files") or [] if isinstance(row.get("files"), list) else []:
        if (a := _find(artifacts, name)) is not None:
            p.items.append({"kind": "file", "attachment": a})
    if cert := _clean_map(row.get("certificate")):
        p.items.append({"kind": "certificate", "data": cert})
    if headers := _clean_map(row.get("headers")):
        p.items.append({"kind": "headers", "data": headers})
    return p


async def save(db: AsyncSession, asset: m.Asset, pending: Pending, *, job_id: uuid.UUID | None = None, task_id: uuid.UUID | None = None,
               agent_id: uuid.UUID | None = None, parser: str | None = None) -> None:
    """Keep a row's evidence on its asset: every picture or file once, a certificate or headers when
    they differ from the asset's latest of the kind."""
    for item in pending.items:
        att: m.Attachment | None = item.get("attachment")
        if att is not None:
            dup = (await db.execute(select(m.AssetEvidence.id).where(m.AssetEvidence.asset_id == asset.id,
                                                                     m.AssetEvidence.attachment_id == att.id))).first()
            if dup:
                continue
        else:
            latest = (await db.execute(select(m.AssetEvidence.data).where(m.AssetEvidence.asset_id == asset.id, m.AssetEvidence.kind == item["kind"])
                                       .order_by(m.AssetEvidence.observed_at.desc()).limit(1))).scalar_one_or_none()
            if latest == item["data"]:
                continue
        db.add(m.AssetEvidence(project_id=asset.project_id, asset_id=asset.id, kind=item["kind"], attachment_id=att.id if att else None,
                               job_id=job_id, task_id=task_id, agent_id=agent_id, parser=parser, look=item.get("look"),
                               look_changed=bool(item.get("look_changed")), data=item.get("data") or {}))
    await db.flush()


async def keep_on_delete(db: AsyncSession, task_id: uuid.UUID) -> None:
    """A card is being deleted: its pictures and files that are an asset's evidence stay (on no card)."""
    ids = select(m.AssetEvidence.attachment_id).where(m.AssetEvidence.attachment_id.is_not(None))
    await db.execute(update(m.Attachment).where(m.Attachment.task_id == task_id, m.Attachment.id.in_(ids)).values(task_id=None))


# ---------- reading it back ----------


async def latest_screenshots(db: AsyncSession, asset_ids: list[uuid.UUID]) -> dict[uuid.UUID, m.AssetEvidence]:
    """Each asset's newest screenshot."""
    out: dict[uuid.UUID, m.AssetEvidence] = {}
    if not asset_ids:
        return out
    rows = (await db.execute(select(m.AssetEvidence).where(m.AssetEvidence.asset_id.in_(asset_ids), m.AssetEvidence.kind == "screenshot",
                                                           m.AssetEvidence.attachment_id.is_not(None))
                             .order_by(m.AssetEvidence.observed_at.desc()))).scalars()
    for e in rows:
        out.setdefault(e.asset_id, e)
    return out


def out(e: m.AssetEvidence, attachment: m.Attachment | None, source: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": str(e.id), "kind": e.kind, "at": e.observed_at, "look": e.look, "look_changed": e.look_changed, "data": e.data or {},
        "parser": e.parser,
        "attachment": {"id": str(attachment.id), "filename": attachment.filename, "content_type": attachment.content_type,
                       "size": attachment.size} if attachment is not None else None,
        "source": source,
    }
