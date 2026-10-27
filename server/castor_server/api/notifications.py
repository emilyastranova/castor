from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select, update

from .. import models as m
from ..deps import DB, CurrentUser, parse_uuid
from ..events import bus
from ..models import utcnow
from ..schemas import NotificationList, NotificationOut

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=NotificationList)
async def list_notifications(db: DB, user: CurrentUser, unread_only: bool = False, limit: int = 50):
    query = select(m.Notification).where(m.Notification.user_id == user.id)
    if unread_only:
        query = query.where(m.Notification.read_at.is_(None))
    rows = (await db.execute(query.order_by(m.Notification.created_at.desc()).limit(min(limit, 200)))).scalars().all()
    unread = (
        await db.execute(
            select(func.count()).where(m.Notification.user_id == user.id, m.Notification.read_at.is_(None))
        )
    ).scalar_one()
    return NotificationList(items=[NotificationOut.model_validate(r) for r in rows], unread=unread)


@router.post("/{notification_id}/read", response_model=NotificationOut)
async def mark_read(notification_id: str, db: DB, user: CurrentUser):
    row = await db.get(m.Notification, parse_uuid(notification_id))
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "Notification not found")
    if row.read_at is None:
        row.read_at = utcnow()
        await db.commit()
        bus.publish("notification.read", None, {"ids": [str(row.id)]})
    return row


@router.post("/read-all", status_code=204)
async def mark_all_read(db: DB, user: CurrentUser):
    await db.execute(
        update(m.Notification)
        .where(m.Notification.user_id == user.id, m.Notification.read_at.is_(None))
        .values(read_at=utcnow())
    )
    await db.commit()
    bus.publish("notification.read", None, {"user_id": str(user.id), "all": True})
