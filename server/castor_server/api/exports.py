"""Exporting a project's timeline as HTML or PDF (report.py), with Home's filters and a date
range. Anyone who can see the project can export what they can see of it; each export is
recorded in the activity log."""

from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Response

from .. import models as m
from ..access import check_project
from ..deps import DB, CurrentUser, parse_uuid
from ..report import LIMIT, Filters, no_fetch, timeline_html
from ..services import actor_of, record_activity
from .dashboard import _enrich, select_events

router = APIRouter(tags=["dashboard"])


def _day(value: str | None, tz: ZoneInfo, field: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(422, f"{field}: a date, like 2026-11-09") from None


@router.get("/projects/{project_id}/timeline/export")
async def export_timeline(
    project_id: str,
    db: DB,
    user: CurrentUser,
    format: Literal["html", "pdf"] = "html",
    kinds: str | None = None,
    mine: bool = False,
    tag: str | None = None,
    since: str | None = None,
    until: str | None = None,
    tz: str = "UTC",
    order: Literal["newest", "oldest"] = "newest",
):
    """The project's timeline as a document: `kinds` (comma separated, as on Home), `mine`, a
    `tag` (only posts with it), `since`/`until` (dates, inclusive, in `tz`), `order` (newest or
    oldest first)."""
    pid = parse_uuid(project_id)
    await check_project(db, user, pid)
    project = await db.get(m.Project, pid)
    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, "tz: a time zone, like Europe/London") from None
    start, end = _day(since, zone, "since"), _day(until, zone, "until")
    if start and end and end < start:
        raise HTTPException(422, "until: comes before since")
    events = await select_events(
        db, user, str(pid), kinds=kinds, mine=mine, tag=tag, limit=LIMIT + 1,
        since=datetime.combine(start, time.min, zone) if start else None,
        until=datetime.combine(end + timedelta(days=1), time.min, zone) if end else None,
    )
    truncated = len(events) > LIMIT
    # By when things happened (a moved comment's entry is dated when it was written).
    events = sorted(events[:LIMIT], key=lambda e: e.created_at, reverse=order == "newest")
    items = await _enrich(db, events, full=True)
    filters = Filters(kinds=[k for k in (kinds or "").split(",") if k], mine=mine, tag=tag, since=since, until=until, tz=tz,
                      oldest_first=order == "oldest")
    document = await timeline_html(db, project=project, items=items, filters=filters, by=user, truncated=truncated,
                                   for_pdf=format == "pdf")

    await record_activity(db, type="transfer.timeline", message=f"Exported the timeline as {format.upper()} ({filters.summary()})",
                          project_id=pid, **actor_of(user))
    await db.commit()

    stamp = datetime.now(zone).strftime("%Y%m%d")
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{project.codename}-timeline-{stamp}")
    if format == "pdf":
        from weasyprint import HTML

        pdf = await asyncio.to_thread(lambda: HTML(string=document, url_fetcher=no_fetch()).write_pdf())
        return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}.pdf"'})
    return Response(document, media_type="text/html; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}.html"'})
