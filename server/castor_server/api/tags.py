"""Tags in use, for suggesting them as people type (TagInput in the web app): each with how
often it's used, most used first, so tags don't drift into near-duplicates (smb, SMB, smbs).

Three kinds, each scoped like what it reads: card tags in the projects you can see (counted
in one project first when given), agent tags (agents and their enrollment tokens), and library
tags (commands and playbooks)."""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Iterable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import models as m
from ..access import project_or_404, visible_project_ids
from ..deps import DB, Admin, CurrentUser, parse_uuid
from ..services import TagError, rename_tags

router = APIRouter(prefix="/tags", tags=["tags"])
project_router = APIRouter(tags=["tags"])


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


class RenameIn(BaseModel):
    # One tag to rename, or several to merge; onto an existing tag merges too.
    from_: list[str] = Field(alias="from", min_length=1)
    to: str

    model_config = {"populate_by_name": True}


class RenameOut(BaseModel):
    # How many of each changed: cards; or agents, tokens, cards and runs.
    changed: dict[str, int]


@project_router.post("/projects/{project_id}/tags/rename", response_model=RenameOut)
async def rename_card_tags(project_id: str, body: RenameIn, db: DB, user: CurrentUser):
    """Rename a tag on this project's cards, or merge several into one."""
    project = await project_or_404(db, user, parse_uuid(project_id), write=True)
    try:
        changed = await rename_tags(db, "cards", body.from_, body.to, user, project.id)
    except TagError as exc:
        raise HTTPException(422, str(exc)) from exc
    await db.commit()
    return RenameOut(changed=changed)


@router.post("/agents/rename", response_model=RenameOut)
async def rename_agent_tags(body: RenameIn, db: DB, user: Admin):
    """Rename an agent tag, or merge several, on agents and SSH hosts, enrollment tokens, and
    every card and run that targets agents by it. An agent started with --tags brings its own
    tags back when it reconnects; change them there too."""
    try:
        changed = await rename_tags(db, "agents", body.from_, body.to, user)
    except TagError as exc:
        raise HTTPException(422, str(exc)) from exc
    await db.commit()
    return RenameOut(changed=changed)
