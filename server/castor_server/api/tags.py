"""Tags in use, for suggesting them as people type (TagInput in the web app): each with how
often it's used, most used first, so tags don't drift into near-duplicates (smb, SMB, smbs).

Three kinds, each scoped like what it reads: card tags in the projects you can see (counted
in one project first when given), agent tags (agents and their enrollment tokens), and library
tags (commands and playbooks)."""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Iterable

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from .. import models as m
from ..access import visible_project_ids
from ..deps import DB, CurrentUser, parse_uuid

router = APIRouter(prefix="/tags", tags=["tags"])


class TagCount(BaseModel):
    tag: str
    count: int  # everywhere you can see
    here: int = 0  # in the project asked about (card tags only)


def _ranked(all_tags: Iterable[str], here: Iterable[str] = ()) -> list[TagCount]:
    total, local = Counter(all_tags), Counter(here)
    return sorted(
        (TagCount(tag=t, count=n, here=local.get(t, 0)) for t, n in total.items() if t),
        key=lambda t: (-t.here, -t.count, t.tag.lower()),
    )


@router.get("/cards", response_model=list[TagCount])
async def card_tags(db: DB, user: CurrentUser, project_id: str | None = None):
    """Tags on cards in the projects you can see; with project_id, that project's first."""
    query = select(m.Task.project_id, m.Task.tags)
    visible = await visible_project_ids(db, user)
    if visible is not None:
        query = query.where(m.Task.project_id.in_(visible))
    here_id: uuid.UUID | None = parse_uuid(project_id) if project_id else None
    everywhere, here = [], []
    for pid, tags in (await db.execute(query)).all():
        for tag in tags or []:
            everywhere.append(tag)
            if pid == here_id:
                here.append(tag)
    return _ranked(everywhere, here)


@router.get("/agents", response_model=list[TagCount])
async def agent_tags(db: DB, _: CurrentUser):
    """Tags on agents and SSH hosts, and on enrollment tokens not used yet."""
    agents = (await db.execute(select(m.Agent.tags))).scalars().all()
    tokens = (await db.execute(select(m.AgentToken.tags).where(m.AgentToken.agent_id.is_(None)))).scalars().all()
    return _ranked(t for tags in [*agents, *tokens] for t in tags or [])


@router.get("/library", response_model=list[TagCount])
async def library_tags(db: DB, _: CurrentUser):
    """Tags on library commands and playbooks."""
    commands = (await db.execute(select(m.CommandDefinition.tags))).scalars().all()
    playbooks = (await db.execute(select(m.PlaybookDefinition.tags))).scalars().all()
    return _ranked(t for tags in [*commands, *playbooks] for t in tags or [])
