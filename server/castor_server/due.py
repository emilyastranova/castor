"""Due date reminders: a card's people hear once when its due date is near and once when it
has passed (in-app notifications, so on the desktop too for those who allow it).

A card is reminded while it's open: not done, reported, archived or a playbook step's card.
`tasks.due_reminded` remembers what was sent for the current date ("soon", "overdue"); changing
the date clears it (api/tasks.py), so a new date is reminded again.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m
from . import notifications
from .services import FINISHED_ROLES, card_people

log = logging.getLogger(__name__)

SOON = timedelta(hours=24)  # "due soon" from this long before


def when(due: datetime, now: datetime) -> str:
    """'in 3 hours', 'tomorrow at 09:00 UTC'… short and plain, for a notification."""
    left = due - now
    if left <= timedelta(0):
        late = now - due
        hours = int(late.total_seconds() // 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago" if hours else "just now"
    hours = int(left.total_seconds() // 3600)
    if hours >= 1:
        return f"in {hours} hour{'s' if hours != 1 else ''}"
    minutes = max(1, int(left.total_seconds() // 60))
    return f"in {minutes} minute{'s' if minutes != 1 else ''}"


async def check_due(db: AsyncSession, now: datetime | None = None) -> int:
    """Send the reminders that are due. Returns how many cards were reminded. Commits."""
    now = now or datetime.now(UTC)
    rows = (await db.execute(
        select(m.Task, m.BoardColumn.role)
        .join(m.BoardColumn, m.BoardColumn.id == m.Task.column_id)
        .where(m.Task.due_at.is_not(None), m.Task.due_at <= now + SOON, m.Task.archived_at.is_(None),
               m.Task.playbook_step_id.is_(None))
    )).all()
    sent = 0
    for task, role in rows:
        if role in FINISHED_ROLES:
            continue
        overdue = task.due_at <= now
        stage = "overdue" if overdue else "soon"
        if task.due_reminded == stage or (stage == "soon" and task.due_reminded == "overdue"):
            continue
        people = await card_people(db, task)
        link = f"/projects/{task.project_id}?task={task.id}"
        if overdue:
            await notifications.notify(
                db, kind="task.overdue", title=f"“{task.title}” is overdue", severity="warning",
                body=f"It was due {when(task.due_at, now)}.", link=link, project_id=task.project_id,
                subject=f"task:{task.id}:due", user_ids=people, webhooks=False,
            )
        else:
            await notifications.notify(
                db, kind="task.due_soon", title=f"“{task.title}” is due {when(task.due_at, now)}",
                body=f"Due {task.due_at.strftime('%a %d %b %H:%M')} UTC.", link=link, project_id=task.project_id,
                subject=f"task:{task.id}:due", user_ids=people, webhooks=False,
            )
        task.due_reminded = stage
        sent += 1
    if sent:
        await db.commit()
    return sent


async def remind_forever(interval: float = 60.0) -> None:
    from .db import SessionLocal

    while True:
        await asyncio.sleep(interval)
        try:
            async with SessionLocal() as db:
                await check_due(db)
        except Exception:
            log.exception("due date reminder sweep failed")
