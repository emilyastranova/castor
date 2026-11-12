"""Users (admins manage them) and each user's own account."""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from .. import avatars
from .. import models as m
from ..access import add_member
from ..deps import DB, Admin, CurrentUser, parse_uuid
from ..events import bus
from ..models import utcnow
from ..schemas import AvatarIn, PasswordChange, ProfilePatch, UserBrief, UserIn, UserOut, UserPatch
from ..search import _text
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
    if (await db.execute(select(m.Group.id).where(func.lower(m.Group.slug) == body.username.lower()))).first():
        raise HTTPException(409, f"@{body.username} is a group's handle; pick another username")
    user = m.User(
        username=body.username,
        display_name=body.display_name.strip() or body.username,
        role=body.role,
        password_hash=hash_password(body.password),
        email=await _email(db, body.email),
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


async def _email(db: DB, value: str, user_id=None) -> str:
    """An email for an account: one account per email, since single sign-on links by it.
    Only admins set them (a person choosing their own could claim someone else's sign-on)."""
    email = value.strip()
    if not email:
        return ""
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(422, "email: that doesn't look like an email address")
    taken = (await db.execute(select(m.User.username).where(func.lower(m.User.email) == email.lower(), m.User.id != user_id))).first()
    if taken:
        raise HTTPException(409, f"{taken[0]} already has that email")
    return email


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
    if body.email is not None and body.email.strip() != user.email:
        user.email = await _email(db, body.email, user.id)
        changes.append(f"email {user.email or 'removed'}")
    if changes:
        await record_activity(
            db, type="user.updated", message=f"User {user.username}: {', '.join(changes)}",
            project_id=None, **actor_of(admin),
        )
    await db.commit()
    bus.publish("user.changed", None, {"id": user.id}, ref=user.id)
    return UserOut.model_validate(user)


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(user_id: str, db: DB, admin: Admin):
    """Delete an account for good. Their sign-ins, API tokens, memberships and notifications go
    with it; what they wrote and made stays (shown as by a deleted user), and they're taken off
    the cards they were assigned to and the alerts they'd asked for. Disabling keeps their name
    on their work instead."""
    user = await db.get(m.User, parse_uuid(user_id))
    if user is None:
        raise HTTPException(404, "User not found")
    if user.id == admin.id:
        raise HTTPException(409, "You can't delete your own account; ask another admin")
    if user.is_admin and user.disabled_at is None and await _admins_left(db, user.id) == 0:
        raise HTTPException(409, "Castor needs at least one active admin")
    uid = str(user.id)
    for task in (await db.execute(select(m.Task).where(_text(m.Task.assignees).like(f"%{uid}%")
                                                       | _text(m.Task.alerts).like(f"%{uid}%")))).scalars():
        if uid in (task.assignees or []):
            task.assignees = [a for a in task.assignees if a != uid]
        if task.alerts and uid in [str(p) for p in task.alerts.get("people") or []]:
            task.alerts = {**task.alerts, "people": [p for p in task.alerts["people"] if str(p) != uid]}
    name, username, picture, companion = user.display_name, user.username, user.avatar_file, user.companion_file
    await db.delete(user)
    await record_activity(db, type="user.deleted", message=f"Deleted the account {username} ({name})", project_id=None,
                          **actor_of(admin))
    await db.commit()
    avatars.remove(picture)
    avatars.remove_companion(companion)
    bus.publish("user.changed", None, {"id": uid, "deleted": True}, ref=uid)  # their open pages sign out
    bus.publish("project.members", None, {"user_ids": [uid]})


@router.patch("/auth/me", response_model=UserOut)
async def update_me(body: ProfilePatch, db: DB, user: CurrentUser):
    if body.display_name is not None:
        if not body.display_name.strip():
            raise HTTPException(422, "display_name: enter a name")
        user.display_name = body.display_name.strip()
    if body.theme_seed is not None:
        user.theme_seed = body.theme_seed.lower()
    if "theme" in body.model_fields_set:  # null: back to the instance default
        if body.theme and await db.get(m.ThemeDefinition, body.theme) is None:
            raise HTTPException(422, f"theme: no theme “{body.theme}”")
        user.theme = body.theme
    if body.companion is not None:
        user.companion = body.companion
    if body.companion_seat is not None:
        user.companion_seat = round(body.companion_seat, 4)
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


# ----- profile pictures (your own, or anyone's for admins) -----


async def _avatar_target(db: DB, user: m.User, user_id: str) -> m.User:
    target = await db.get(m.User, parse_uuid(user_id))
    if target is None:
        raise HTTPException(404, "User not found")
    if target.id != user.id and not user.is_admin:
        raise HTTPException(403, "You can only change your own picture")
    return target


@router.put("/users/{user_id}/avatar", response_model=UserOut)
async def set_avatar(user_id: str, body: AvatarIn, db: DB, user: CurrentUser):
    """Choose initials, the uploaded picture, an https image URL, or a Gravatar email."""
    target = await _avatar_target(db, user, user_id)
    try:
        if body.kind == "url":
            target.avatar_link = avatars.check_url(body.url)
        elif body.kind == "gravatar":
            target.gravatar_email = avatars.check_email(body.gravatar_email)
        elif body.kind == "upload" and not target.avatar_file:
            raise avatars.AvatarError("upload a picture first")
    except avatars.AvatarError as exc:
        raise HTTPException(422, str(exc)) from exc
    target.avatar_kind = body.kind
    await db.commit()
    bus.publish("user.changed", None, {"id": target.id}, ref=target.id)
    return UserOut.model_validate(target)


@router.post("/users/{user_id}/avatar/upload", response_model=UserOut)
async def upload_avatar(user_id: str, db: DB, user: CurrentUser, file: Annotated[UploadFile, File()]):
    target = await _avatar_target(db, user, user_id)
    data = await file.read(avatars.MAX_BYTES + 1)
    old = target.avatar_file
    try:
        target.avatar_file = avatars.save(target.id, target.avatar_version + 1, data)
    except avatars.AvatarError as exc:
        raise HTTPException(422, str(exc)) from exc
    target.avatar_version += 1
    target.avatar_kind = "upload"
    await db.commit()
    if old != target.avatar_file:
        avatars.remove(old)
    bus.publish("user.changed", None, {"id": target.id}, ref=target.id)
    return UserOut.model_validate(target)


@router.post("/auth/me/companion", response_model=UserOut)
async def upload_companion(db: DB, user: CurrentUser, file: Annotated[UploadFile, File()]):
    """Your own companion picture (only you see it). Set where it sits with companion_seat."""
    data = await file.read(avatars.COMPANION_MAX + 1)
    old = user.companion_file
    try:
        user.companion_file = avatars.save_companion(user.id, user.companion_version + 1, data)
    except avatars.AvatarError as exc:
        raise HTTPException(422, str(exc)) from exc
    user.companion_version += 1
    await db.commit()
    if old != user.companion_file:
        avatars.remove_companion(old)
    bus.publish("user.changed", None, {"id": user.id}, ref=user.id)
    return UserOut.model_validate(user)


@router.delete("/auth/me/companion", response_model=UserOut)
async def reset_companion(db: DB, user: CurrentUser):
    """Back to the built-in companion (and her seat line)."""
    old = user.companion_file
    user.companion_file = ""
    user.companion_seat = avatars.DEFAULT_SEAT
    await db.commit()
    avatars.remove_companion(old)
    bus.publish("user.changed", None, {"id": user.id}, ref=user.id)
    return UserOut.model_validate(user)


@router.get("/auth/me/companion")
async def get_companion(user: CurrentUser):
    path = avatars.companion_dir() / user.companion_file if user.companion_file else None
    if path is None or not path.is_file():
        raise HTTPException(404, "No companion picture")
    kind = avatars.image_type(path.read_bytes()[:16])
    return FileResponse(
        path,
        media_type=kind[0] if kind else "application/octet-stream",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=31536000, immutable"},
    )


@router.get("/users/{user_id}/avatar")
async def get_avatar(user_id: str, db: DB, _: CurrentUser):
    target = await db.get(m.User, parse_uuid(user_id))
    path = avatars.directory() / target.avatar_file if target and target.avatar_file else None
    if path is None or not path.is_file():
        raise HTTPException(404, "No picture")
    kind = avatars.image_type(path.read_bytes()[:16])
    return FileResponse(
        path,
        media_type=kind[0] if kind else "application/octet-stream",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=31536000, immutable"},
    )
