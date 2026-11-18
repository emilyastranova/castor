"""Agents only some people may use (Agent.people): {users: [user ids], groups: [group ids]}; None is
everyone. A job runs with someone's permission (Job.run_as: who started it, else the playbook run's
starter, else the card's maker); admins may use every agent. Wherever an agent is chosen for a job
this is checked next to `dispatcher.project_allowed`. Web: lib/agentSelector.ts `usableBy` (keep the
two rules the same)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m


class PeopleError(ValueError):
    pass


@dataclass
class Who:
    """A person as the agent rule sees them."""

    user_id: str
    admin: bool
    groups: set[str] = field(default_factory=set)


def allowed(people: dict[str, Any] | None, who: Who | None) -> bool:
    """Whether this person may have work run on an agent limited to `people`."""
    if people is None:
        return True
    if who is None:  # nobody to vouch for it (their account is gone)
        return False
    if who.admin:
        return True
    return who.user_id in {str(u) for u in people.get("users") or []} or bool(
        who.groups & {str(g) for g in people.get("groups") or []})


async def who(db: AsyncSession, user_id: uuid.UUID | str | None, cache: dict[str, Who | None] | None = None) -> Who | None:
    if user_id is None:
        return None
    key = str(user_id)
    if cache is not None and key in cache:
        return cache[key]
    user = await db.get(m.User, uuid.UUID(key))
    found = None
    if user is not None and user.disabled_at is None:
        groups = {str(g) for g in (await db.execute(
            select(m.GroupMember.group_id).where(m.GroupMember.user_id == user.id))).scalars()}
        found = Who(user_id=key, admin=bool(user.is_admin), groups=groups)
    if cache is not None:
        cache[key] = found
    return found


def of_user(user: m.User | None, groups: set[str] | None = None) -> Who | None:
    return Who(user_id=str(user.id), admin=bool(user.is_admin), groups=groups or set()) if user is not None else None


async def run_as_for(db: AsyncSession, task: m.Task, user: m.User | None) -> uuid.UUID | None:
    """Whose permission a job of this card runs with: the person starting it, else whoever set the
    automation up (the playbook run's starter, else the card's maker)."""
    if user is not None:
        return user.id
    if task.playbook_run_id is not None:
        run = await db.get(m.PlaybookRun, task.playbook_run_id)
        if run is not None and run.created_by is not None:
            return run.created_by
    return task.created_by


async def check(db: AsyncSession, given: dict[str, Any] | None, before: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The stored form: known users and groups (ones it already had stay, even if gone since).
    None: everyone. An empty list is allowed: then only admins may use it."""
    if given is None:
        return None
    out: dict[str, list[str]] = {"users": [], "groups": []}
    for key, model in (("users", m.User), ("groups", m.Group)):
        had = {str(i) for i in (before or {}).get(key) or []}
        for raw in given.get(key) or []:
            try:
                ident = str(uuid.UUID(str(raw)))
            except ValueError:
                raise PeopleError(f"people: no {key[:-1]} {raw}") from None
            if ident not in had and await db.get(model, uuid.UUID(ident)) is None:
                raise PeopleError(f"people: no {key[:-1]} {raw}")
            if ident not in out[key]:
                out[key].append(ident)
    return out


async def words(db: AsyncSession, people: dict[str, Any] | None) -> str:
    """“Ana, Sam and the group Red team”, for the activity log."""
    if people is None:
        return "everyone"
    names: list[str] = []
    for uid in people.get("users") or []:
        user = await db.get(m.User, uuid.UUID(uid))
        names.append(user.display_name if user else "a removed user")
    for gid in people.get("groups") or []:
        group = await db.get(m.Group, uuid.UUID(gid))
        names.append(f"the group {group.name}" if group else "a removed group")
    if not names:
        return "admins only"
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
