"""People on a project: who can see it (admins and members), @mentions in comments, and
card assignees. Assignees and mentions are stored as user ids (strings in JSON lists)."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m

# @username, not part of an email address or a path; usernames follow schemas.USERNAME.
MENTION = re.compile(r"(?<![\w@./])@([A-Za-z0-9][A-Za-z0-9._-]{0,63})")
_CODE = re.compile(r"```.*?```|`[^`\n]*`", re.DOTALL)


async def project_people(db: AsyncSession, project_id: uuid.UUID) -> list[m.User]:
    """Active users who can see the project: admins, its members, and people in its groups."""
    members = select(m.ProjectMember.user_id).where(m.ProjectMember.project_id == project_id)
    via_groups = (
        select(m.GroupMember.user_id)
        .join(m.ProjectGroup, m.ProjectGroup.group_id == m.GroupMember.group_id)
        .where(m.ProjectGroup.project_id == project_id)
    )
    rows = await db.execute(
        select(m.User)
        .where(m.User.disabled_at.is_(None), (m.User.role == m.UserRole.admin) | m.User.id.in_(members) | m.User.id.in_(via_groups))
        .order_by(m.User.display_name)
    )
    return list(rows.scalars())


def mention_names(text: str) -> set[str]:
    """Lower-cased usernames written as @name, outside code spans and blocks."""
    names = set()
    for match in MENTION.finditer(_CODE.sub(" ", text)):
        names.add(match.group(1).rstrip("._-").lower())
    return names


async def mentioned(db: AsyncSession, project_id: uuid.UUID, text: str) -> list[m.User]:
    """The people a comment mentions who can see the project (others are just text): by
    @username, or everyone in an @group."""
    names = mention_names(text)
    if not names:
        return []
    groups = select(m.Group.id).where(m.Group.slug.in_(names))
    in_groups = set((await db.execute(select(m.GroupMember.user_id).where(m.GroupMember.group_id.in_(groups)))).scalars())
    return [u for u in await project_people(db, project_id) if u.username.lower() in names or u.id in in_groups]


async def handle_taken(db: AsyncSession, handle: str, *, group_id: uuid.UUID | None = None, user_id: uuid.UUID | None = None) -> str | None:
    """Usernames and group slugs share one @namespace: what already uses this one, if anything."""
    from sqlalchemy import func

    h = handle.lower()
    user = (await db.execute(select(m.User.username).where(func.lower(m.User.username) == h, m.User.id != user_id))).first()
    if user:
        return f"the user @{user[0]}"
    group = (await db.execute(select(m.Group.slug).where(func.lower(m.Group.slug) == h, m.Group.id != group_id))).first()
    return f"the group @{group[0]}" if group else None


async def check_assignees(db: AsyncSession, project_id: uuid.UUID, values: Iterable[str]) -> list[str]:
    """Validate assignees (user ids, or usernames for convenience) against the people who can
    see the project; returns their ids as strings, in the given order, without duplicates."""
    people = await project_people(db, project_id)
    by_key = {str(u.id): u for u in people} | {u.username.lower(): u for u in people}
    out: list[str] = []
    for value in values:
        user = by_key.get(str(value).strip().lower()) or by_key.get(str(value).strip())
        if user is None:
            raise HTTPException(422, f"Can't assign {value}: not someone who can see this project")
        if str(user.id) not in out:
            out.append(str(user.id))
    return out


async def names_of(db: AsyncSession, ids: Iterable[str]) -> list[str]:
    wanted = [uuid.UUID(i) for i in ids]
    if not wanted:
        return []
    rows = await db.execute(select(m.User.id, m.User.display_name).where(m.User.id.in_(wanted)))
    names = {str(uid): name for uid, name in rows.all()}
    return [names.get(str(i), "someone") for i in wanted]
