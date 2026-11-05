"""Notifications: in-app (one row per user, pushed as `notification.created`) and, when
configured, webhooks (CASTOR_NOTIFY_WEBHOOKS) for people who aren't looking at Castor."""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.request
import uuid
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m
from .config import settings
from .events import bus
from .models import utcnow
from .schemas import NotificationOut

log = logging.getLogger(__name__)
_background: set[asyncio.Task[None]] = set()
SEVERITY_EMOJI = {"info": "ℹ️", "success": "✅", "warning": "✋", "error": "❌"}


async def notify(
    db: AsyncSession,
    *,
    kind: str,
    title: str,
    body: str = "",
    link: str = "",
    severity: str = "info",
    project_id: uuid.UUID | None = None,
    subject: str = "",
    payload: dict[str, Any] | None = None,
    user_ids: Iterable[uuid.UUID] | None = None,
    webhooks: bool = True,
) -> list[m.Notification]:
    """Notify the active users who can see the project (all of them for a notification
    without one), or only `user_ids` among them, and the configured webhooks (unless
    `webhooks` is False: personal notifications such as mentions stay in-app). Flushes; the
    caller commits."""
    query = select(m.User.id).where(m.User.disabled_at.is_(None))
    if user_ids is not None:
        wanted = list(user_ids)
        if not wanted:
            return []
        query = query.where(m.User.id.in_(wanted))
    if project_id is not None:
        members = select(m.ProjectMember.user_id).where(m.ProjectMember.project_id == project_id)
        query = query.where((m.User.role == m.UserRole.admin) | m.User.id.in_(members))
    users = (await db.execute(query)).scalars().all()
    rows = [
        m.Notification(
            user_id=uid,
            project_id=project_id,
            kind=kind,
            severity=severity,
            title=title[:300],
            body=body,
            link=link,
            subject=subject,
            payload=payload or {},
        )
        for uid in users
    ]
    db.add_all(rows)
    await db.flush()
    for row in rows:
        bus.publish("notification.created", project_id, NotificationOut.model_validate(row), user_id=row.user_id)
    if webhooks:
        _send_webhooks(title=title, body=body, link=link, severity=severity, kind=kind, payload=payload or {})
    return rows


async def clear(db: AsyncSession, subject: str, *, prefix: bool = False) -> None:
    """Mark everyone's notifications about `subject` read (it has been dealt with).
    prefix: everything starting with it, e.g. every step of a run."""
    if not subject:
        return
    match = m.Notification.subject.startswith(subject) if prefix else m.Notification.subject == subject
    ids = (
        await db.execute(select(m.Notification.id).where(match, m.Notification.read_at.is_(None)))
    ).scalars().all()
    if not ids:
        return
    await db.execute(update(m.Notification).where(m.Notification.id.in_(ids)).values(read_at=utcnow()))
    bus.publish("notification.read", None, {"ids": [str(i) for i in ids]})


# ---------- webhooks ----------


def webhook_urls() -> list[str]:
    return [u.strip() for u in settings.notify_webhooks.split(",") if u.strip()]


def absolute(link: str) -> str:
    return settings.public_url.rstrip("/") + link if link and settings.public_url else link


def webhook_request(url: str, *, title: str, body: str, link: str, severity: str, kind: str, payload: dict[str, Any]) -> urllib.request.Request:
    """The request for one webhook, shaped for what's at the other end."""
    host = urlparse(url).hostname or ""
    full = absolute(link)
    text = f"{SEVERITY_EMOJI.get(severity, '')} *{title}*" + (f"\n{body}" if body else "") + (f"\n{full}" if full.startswith("http") else "")
    headers = {"Content-Type": "application/json", "User-Agent": "castor"}
    if host == "hooks.slack.com":
        data = json.dumps({"text": text}).encode()
    elif host.endswith(("discord.com", "discordapp.com")):
        data = json.dumps({"content": text.replace("*", "**")[:2000]}).encode()
    elif host.startswith("ntfy.") or "/ntfy" in url:
        data = (body or title).encode()
        headers = {
            "Title": title.encode("ascii", "replace").decode(),
            "Priority": "high" if severity in ("warning", "error") else "default",
            "Tags": {"warning": "raised_hand", "error": "x", "success": "white_check_mark"}.get(severity, "information_source"),
            "User-Agent": "castor",
        }
        if full.startswith("http"):
            headers["Click"] = full
    else:
        data = json.dumps(
            {"kind": kind, "severity": severity, "title": title, "body": body, "link": full, "text": text, **payload},
            default=str,
        ).encode()
    return urllib.request.Request(url, data=data, headers=headers, method="POST")


def _post(request: urllib.request.Request) -> None:
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:
            resp.read(1024)
    except Exception as exc:  # noqa: BLE001 - a webhook being down must not affect the run
        log.warning("notification webhook %s failed: %s", urlparse(request.full_url).hostname, exc)


def _send_webhooks(**message: Any) -> None:
    urls = webhook_urls()
    if not urls:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    for url in urls:
        task = loop.create_task(asyncio.to_thread(_post, webhook_request(url, **message)))
        _background.add(task)
        task.add_done_callback(_background.discard)
