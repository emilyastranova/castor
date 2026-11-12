"""Keeping Castor administrable: there is always an active admin, and someone with access
to the server can always get back in (`castor-admin`, see manage.py)."""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m

log = logging.getLogger("castor")


async def active_admins(db: AsyncSession) -> int:
    return (
        await db.execute(
            select(func.count()).where(m.User.role == m.UserRole.admin, m.User.disabled_at.is_(None))
        )
    ).scalar_one()


async def ensure_active_admin(db: AsyncSession) -> m.User | None:
    """If users exist but none is an active admin (hand-edited data, or an upgrade where
    nobody had is_admin), make the longest-standing active user an admin, or failing
    that re-enable the longest-standing user. Returns the user it changed. Commits."""
    if await active_admins(db):
        return None
    users = (await db.execute(select(m.User).where(m.User.plugin_id.is_(None))
                              .order_by(m.User.created_at, m.User.username))).scalars().all()
    if not users:
        return None
    chosen = next((u for u in users if u.disabled_at is None), users[0])
    was = f"{chosen.role.value}{', disabled' if chosen.disabled_at else ''}"
    chosen.role = m.UserRole.admin
    chosen.disabled_at = None
    await db.commit()
    log.warning(
        "no active admin: made %r an admin (was %s) so Castor stays manageable; "
        "use `castor-admin` to change this",
        chosen.username,
        was,
    )
    return chosen
