"""Users (admins manage them) and each user's own account."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from .. import models as m
from ..access import add_member
from ..deps import DB, Admin, CurrentUser, parse_uuid
from ..events import bus
from ..models import utcnow
from ..schemas import PasswordChange, ProfilePatch, UserBrief, UserIn, UserOut, UserPatch
from ..security import hash_password, verify_password
from ..services import actor_of, record_activity

router = APIRouter(tags=["users"])


@router.get("/users", response_model=list[UserOut])
async def list_users(db: DB, _: Admin):
    rows = await db.execute(select(m.User).order_by(m.User.username))
    return [UserOut.model_validate(u) for u in rows.scalars()]


@router.get("/users/directory", response_model=list[UserBrief])
async def directory(db: DB, _: CurrentUser):
    """Active users, for pickers such as project members and assignees."""
    rows = await db.execute(select(m.User).where(m.User.disabled_at.is_(None)).order_by(m.User.display_name))
    return [UserBrief.model_validate(u) for u in rows.scalars()]


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(body: UserIn, db: DB, admin: Admin):
    taken = await db.execute(select(m.User.id).where(func.lower(m.User.username) == body.username.lower()))
    if taken.first() is not None:
        raise HTTPException(409, f"The username {body.username} is taken")
    user = m.User(
        username=body.username,
        display_name=body.display_name.strip() or body.username,
        role=body.role,
        password_hash=hash_password(body.password),
    )
    db.add(user)
    await db.flush()
    if body.all_projects:
        for pid in (await db.execute(select(m.Project.id))).scalars():
            await add_member(db, pid, user.id)
    await record_activity(
        db, type="user.created", message=f"Added user {user.username} ({user.role.value})",
        project_id=None, **actor_of(admin),
    )
    await db.commit()
    return UserOut.model_validate(user)


async def _admins_left(db: DB, excluding) -> int:
    return (
        await db.execute(
            select(func.count()).where(
                m.User.role == m.UserRole.admin, m.User.disabled_at.is_(None), m.User.id != excluding
            )
        )
    ).scalar_one()


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(user_id: str, body: UserPatch, db: DB, admin: Admin):
    user = await db.get(m.User, parse_uuid(user_id))
    if user is None:
        raise HTTPException(404, "User not found")
    demoting = (body.role is not None and body.role != m.UserRole.admin) or body.disabled
    if user.is_admin and demoting and await _admins_left(db, user.id) == 0:
        raise HTTPException(409, "Castor needs at least one active admin")
    changes = []
    if body.display_name is not None:
        user.display_name = body.display_name.strip()
    if body.role is not None and body.role != user.role:
        changes.append(f"role {user.role.value} → {body.role.value}")
        user.role = body.role
    if body.disabled is not None and body.disabled != (user.disabled_at is not None):
        user.disabled_at = utcnow() if body.disabled else None
        changes.append("disabled" if body.disabled else "enabled")
    if body.password:
        user.password_hash = hash_password(body.password)
        changes.append("password reset")
    if changes:
        await record_activity(
            db, type="user.updated", message=f"User {user.username}: {', '.join(changes)}",
            project_id=None, **actor_of(admin),
        )
    await db.commit()
    bus.publish("user.changed", None, {"id": user.id}, ref=user.id)
    return UserOut.model_validate(user)


@router.patch("/auth/me", response_model=UserOut)
async def update_me(body: ProfilePatch, db: DB, user: CurrentUser):
    user.display_name = body.display_name.strip()
    await db.commit()
    return UserOut.model_validate(user)


@router.post("/auth/password", status_code=204)
async def change_password(body: PasswordChange, db: DB, user: CurrentUser):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(400, "The current password is wrong")
    user.password_hash = hash_password(body.new_password)
    await record_activity(
        db, type="user.password", message=f"{user.username} changed their password",
        project_id=None, **actor_of(user),
    )
    await db.commit()
