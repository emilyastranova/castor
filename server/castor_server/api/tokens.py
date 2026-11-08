"""Personal API tokens (api_tokens.py): anyone makes their own; admins see and revoke
everyone's. Signed-in sessions only (a token can't manage tokens). A token is returned once."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from .. import api_tokens
from .. import models as m
from ..access import can_see
from ..deps import DB, CurrentUser, parse_uuid
from ..events import bus
from ..models import utcnow
from ..schemas import ApiScopeOut, ApiTokenCreated, ApiTokenIn, ApiTokenOut, UserBrief
from ..services import actor_of, record_activity

router = APIRouter(prefix="/tokens", tags=["api tokens"])


async def _out(db: DB, row: m.ApiToken, token: str | None = None) -> ApiTokenOut:
    owner = await db.get(m.User, row.user_id)
    projects = []
    for pid in row.project_ids or []:
        project = await db.get(m.Project, parse_uuid(pid))
        projects.append(project.codename if project else "(deleted)")
    data = {**ApiTokenOut.model_validate(row).model_dump(), "projects": projects,
            "user": UserBrief.model_validate(owner) if owner else None}
    return ApiTokenCreated(**data, token=token) if token else ApiTokenOut(**data)


async def _get(db: DB, user: m.User, token_id: str) -> m.ApiToken:
    row = await db.get(m.ApiToken, parse_uuid(token_id))
    if row is None or (row.user_id != user.id and not user.is_admin):
        raise HTTPException(404, "Token not found")
    return row


def _changed(row: m.ApiToken) -> None:
    bus.publish("api_tokens.changed", None, {"id": row.id}, user_id=row.user_id)


@router.get("/scopes", response_model=list[ApiScopeOut])
async def scopes(_: CurrentUser):
    return [ApiScopeOut(name=s.name, label=s.label, description=s.description, group=s.group, role=s.role)
            for s in api_tokens.SCOPES.values()]


@router.get("", response_model=list[ApiTokenOut])
async def list_tokens(db: DB, user: CurrentUser, everyone: bool = False):
    """Your tokens; with everyone=true (admins), every user's."""
    query = select(m.ApiToken).order_by(m.ApiToken.created_at.desc())
    if not (everyone and user.is_admin):
        query = query.where(m.ApiToken.user_id == user.id)
    return [await _out(db, r) for r in (await db.execute(query)).scalars().all()]


@router.post("", response_model=ApiTokenCreated, status_code=201)
async def create_token(body: ApiTokenIn, db: DB, user: CurrentUser):
    try:
        scopes = api_tokens.scopes_for(user, body.scopes)
    except ValueError as exc:
        raise HTTPException(422, f"scopes: {exc}") from exc
    project_ids = None
    if body.project_ids is not None:
        if not body.project_ids:
            raise HTTPException(422, "project_ids: choose at least one project, or leave it unset for all of them")
        for pid in body.project_ids:
            if await db.get(m.Project, pid) is None or not await can_see(db, user, pid):
                raise HTTPException(422, f"project_ids: unknown project {pid}")
        project_ids = sorted({str(p) for p in body.project_ids})
    token, digest, prefix = api_tokens.new_token()
    row = m.ApiToken(
        user_id=user.id, name=body.name.strip(), token_hash=digest, prefix=prefix, scopes=scopes,
        project_ids=project_ids,
        expires_at=utcnow() + timedelta(days=body.expires_in_days) if body.expires_in_days else None,
    )
    db.add(row)
    await db.flush()
    await record_activity(
        db, type="api_token.created", message=f"Created API token “{row.name}” ({prefix}…): {', '.join(scopes)}",
        project_id=None, payload={"token_id": str(row.id)}, **actor_of(user),
    )
    await db.commit()
    _changed(row)
    return await _out(db, row, token)


@router.post("/{token_id}/revoke", response_model=ApiTokenOut)
async def revoke_token(token_id: str, db: DB, user: CurrentUser):
    """Stop the token working now (its live connections are closed too)."""
    row = await _get(db, user, token_id)
    if row.revoked_at is None:
        row.revoked_at = utcnow()
        owner = "" if row.user_id == user.id else f" of {(await db.get(m.User, row.user_id)).display_name}"
        await record_activity(
            db, type="api_token.revoked", message=f"Revoked API token “{row.name}” ({row.prefix}…){owner}",
            project_id=None, payload={"token_id": str(row.id)}, **actor_of(user),
        )
        await db.commit()
        _changed(row)
        bus.publish("api_token.revoked", None, {"id": row.id}, user_id=row.user_id, ref=row.id)
    return await _out(db, row)


@router.delete("/{token_id}", status_code=204)
async def delete_token(token_id: str, db: DB, user: CurrentUser):
    """Remove a token from the list; one still working is revoked first."""
    row = await _get(db, user, token_id)
    if row.revoked_at is None:
        await revoke_token(token_id, db, user)
    await db.delete(row)
    await db.commit()
    _changed(row)
