from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from .. import models as m
from ..deps import DB, CurrentUser
from ..schemas import ActivityOut

router = APIRouter(prefix="/activity", tags=["activity"])


@router.get("", response_model=list[ActivityOut])
async def recent_activity(db: DB, _: CurrentUser, limit: int = 50):
    rows = (
        await db.execute(
            select(m.ActivityEvent).order_by(m.ActivityEvent.id.desc()).limit(min(limit, 200))
        )
    ).scalars().all()
    return rows
