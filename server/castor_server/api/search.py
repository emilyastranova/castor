from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from .. import search as engine
from ..deps import DB, CurrentUser

router = APIRouter(tags=["search"])


@router.get("/search")
async def search(db: DB, user: CurrentUser, q: str = "", limit: int = 6) -> dict[str, Any]:
    """Everything the user can see that matches; see castor_server/search.py for the filters."""
    return await engine.search(db, user, q[:300], limit=max(1, min(limit, 25)))
