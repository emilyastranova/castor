from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from .. import models as m
from ..access import visible_project_ids
from ..deps import DB, CurrentUser
from ..schemas import ActivityOut

router = APIRouter(prefix="/activity", tags=["activity"])


@router.get("", response_model=list[ActivityOut])
async def recent_activity(db: DB, user: CurrentUser, limit: int = 50):
    query = select(m.ActivityEvent).order_by(m.ActivityEvent.id.desc()).limit(min(limit, 200))
    visible = await visible_project_ids(db, user)
    if visible is not None:
        query = query.where(m.ActivityEvent.project_id.is_(None) | m.ActivityEvent.project_id.in_(visible))
    rows = (await db.execute(query)).scalars().all()
    return rows
