"""Groups (Settings → Groups): people put together. Anyone can list them (for @mentions);
admins manage them. A project can be shared with groups (its Members dialog), which lets
everyone in them see it."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .. import models as m
from ..access import project_or_404
from ..deps import DB, Admin, CurrentUser, parse_uuid
from ..events import bus
from ..people import handle_taken
from ..schemas import USERNAME, UserBrief
from ..services import actor_of, record_activity

router = APIRouter(prefix="/groups", tags=["groups"])
project_router = APIRouter(prefix="/projects", tags=["groups"])


class GroupIn(BaseModel):
    slug: str = Field(pattern=USERNAME)
    name: str = Field(min_length=1, max_length=128)
    description: str = Field("", max_length=2000)
    sso_group: str = Field("", max_length=255)


class GroupOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    description: str
    sso_group: str
    member_count: int
    project_count: int
    member_ids: list[uuid.UUID] = Field(default_factory=list)


class GroupDetail(GroupOut):
    members: list[UserBrief]


async def _counts(db: DB) -> tuple[dict[uuid.UUID, list[uuid.UUID]], dict[uuid.UUID, int]]:
    members: dict[uuid.UUID, list[uuid.UUID]] = {}
    for gid, uid in (await db.execute(select(m.GroupMember.group_id, m.GroupMember.user_id))).all():
        members.setdefault(gid, []).append(uid)
    projects = dict((await db.execute(select(m.ProjectGroup.group_id, func.count()).group_by(m.ProjectGroup.group_id))).all())
    return members, projects


def _out(g: m.Group, members: dict, projects: dict) -> GroupOut:
    ids = members.get(g.id, [])
    return GroupOut(id=g.id, slug=g.slug, name=g.name, description=g.description, sso_group=g.sso_group,
                    member_count=len(ids), project_count=projects.get(g.id, 0), member_ids=ids)


async def _detail(db: DB, g: m.Group) -> GroupDetail:
    members, projects = await _counts(db)
    people = (await db.execute(
        select(m.User).join(m.GroupMember, m.GroupMember.user_id == m.User.id).where(m.GroupMember.group_id == g.id)
        .order_by(m.User.display_name)
    )).scalars()
    return GroupDetail(**_out(g, members, projects).model_dump(), members=[UserBrief.model_validate(u) for u in people])


async def _group_or_404(db: DB, group_id: str) -> m.Group:
    g = await db.get(m.Group, parse_uuid(group_id))
    if g is None:
        raise HTTPException(404, "Group not found")
    return g


async def _check(db: DB, body: GroupIn, group_id: uuid.UUID | None = None) -> None:
    if taken := await handle_taken(db, body.slug, group_id=group_id):
        raise HTTPException(409, f"@{body.slug} is already {taken}")


def _changed(group_id: uuid.UUID, user_ids=()) -> None:
    bus.publish("groups.changed", None, {"id": group_id})
    # Who can see which project may have changed: sockets reload what they show.
    bus.publish("project.members", None, {"group_id": group_id, "user_ids": [str(u) for u in user_ids]})


@router.get("", response_model=list[GroupOut])
async def list_groups(db: DB, _: CurrentUser):
    rows = (await db.execute(select(m.Group).order_by(m.Group.name))).scalars().all()
    members, projects = await _counts(db)
    return [_out(g, members, projects) for g in rows]


@router.get("/{group_id}", response_model=GroupDetail)
async def get_group(group_id: str, db: DB, _: CurrentUser):
    return await _detail(db, await _group_or_404(db, group_id))


@router.post("", response_model=GroupDetail, status_code=201)
async def create_group(body: GroupIn, db: DB, user: Admin):
    await _check(db, body)
    g = m.Group(slug=body.slug, name=body.name.strip(), description=body.description.strip(), sso_group=body.sso_group.strip())
    db.add(g)
    await db.flush()
    await record_activity(db, type="group.created", message=f"Added group @{g.slug}", project_id=None, **actor_of(user))
    await db.commit()
    _changed(g.id)
    return await _detail(db, g)


@router.put("/{group_id}", response_model=GroupDetail)
async def update_group(group_id: str, body: GroupIn, db: DB, user: Admin):
    g = await _group_or_404(db, group_id)
    await _check(db, body, g.id)
    renamed = g.slug != body.slug
    old = g.slug
    g.slug, g.name, g.description, g.sso_group = body.slug, body.name.strip(), body.description.strip(), body.sso_group.strip()
    await record_activity(db, type="group.updated", message=f"Changed group @{g.slug}" + (f" (was @{old})" if renamed else ""),
                          project_id=None, **actor_of(user))
    await db.commit()
    _changed(g.id)
    return await _detail(db, g)


@router.put("/{group_id}/members", response_model=GroupDetail)
async def set_group_members(group_id: str, body: list[str], db: DB, user: Admin):
    """Replace who's in the group (user ids)."""
    g = await _group_or_404(db, group_id)
    wanted = {parse_uuid(u) for u in body}
    found = set((await db.execute(select(m.User.id).where(m.User.id.in_(wanted)))).scalars())
    if missing := wanted - found:
        raise HTTPException(422, f"Unknown user {next(iter(missing))}")
    current = set((await db.execute(select(m.GroupMember.user_id).where(m.GroupMember.group_id == g.id))).scalars())
    for uid in current - wanted:
        await db.delete(await db.get(m.GroupMember, (g.id, uid)))
    for uid in wanted - current:
        db.add(m.GroupMember(group_id=g.id, user_id=uid))
    await record_activity(db, type="group.members", message=f"Changed who's in @{g.slug}", project_id=None,
                          payload={"added": [str(u) for u in wanted - current], "removed": [str(u) for u in current - wanted]},
                          **actor_of(user))
    await db.commit()
    _changed(g.id, wanted ^ current)
    return await _detail(db, g)


@router.delete("/{group_id}", status_code=204)
async def delete_group(group_id: str, db: DB, user: Admin):
    """Remove the group: its people keep their accounts, but lose the projects they could see
    only through it."""
    g = await _group_or_404(db, group_id)
    members = set((await db.execute(select(m.GroupMember.user_id).where(m.GroupMember.group_id == g.id))).scalars())
    await db.delete(g)
    await record_activity(db, type="group.deleted", message=f"Removed group @{g.slug}", project_id=None, **actor_of(user))
    await db.commit()
    _changed(g.id, members)


# ---------- a project's groups ----------


@project_router.get("/{project_id}/groups", response_model=list[GroupOut])
async def project_groups(project_id: str, db: DB, user: CurrentUser):
    """The groups a project is shared with."""
    project = await project_or_404(db, user, parse_uuid(project_id))
    rows = (await db.execute(
        select(m.Group).join(m.ProjectGroup, m.ProjectGroup.group_id == m.Group.id).where(m.ProjectGroup.project_id == project.id)
        .order_by(m.Group.name)
    )).scalars().all()
    members, projects = await _counts(db)
    return [_out(g, members, projects) for g in rows]


@project_router.put("/{project_id}/groups", response_model=list[GroupOut])
async def set_project_groups(project_id: str, body: list[str], db: DB, user: CurrentUser):
    """Replace the groups a project is shared with (group ids); like members, operators who
    can see the project and admins can."""
    project = await project_or_404(db, user, parse_uuid(project_id), write=True)
    wanted = {parse_uuid(g) for g in body}
    found = set((await db.execute(select(m.Group.id).where(m.Group.id.in_(wanted)))).scalars())
    if missing := wanted - found:
        raise HTTPException(422, f"Unknown group {next(iter(missing))}")
    current = set((await db.execute(select(m.ProjectGroup.group_id).where(m.ProjectGroup.project_id == project.id))).scalars())
    for gid in current - wanted:
        await db.delete(await db.get(m.ProjectGroup, (project.id, gid)))
    for gid in wanted - current:
        db.add(m.ProjectGroup(project_id=project.id, group_id=gid))
    if wanted != current:
        await record_activity(db, type="project.members", message=f"Changed which groups can see {project.codename}",
                              project_id=project.id,
                              payload={"groups_added": [str(g) for g in wanted - current], "groups_removed": [str(g) for g in current - wanted]},
                              **actor_of(user))
    await db.commit()
    bus.publish("project.members", None, {"project_id": project.id})
    return await project_groups(project_id, db, user)

