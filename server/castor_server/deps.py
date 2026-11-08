"""FastAPI dependencies."""

from __future__ import annotations

import secrets
import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request, WebSocket, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from . import api_tokens
from .config import settings
from .db import SessionLocal, get_session
from .models import ApiToken, User, UserRole, utcnow
from .security import decode_session_token

DB = Annotated[AsyncSession, Depends(get_session)]


def _token_from_request(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get(settings.cookie_name)


def _token_from_websocket(ws: WebSocket) -> str | None:
    auth = ws.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    token = ws.query_params.get("token")
    if token and not api_tokens.is_api_token(token):  # long-lived tokens stay out of URLs (and logs)
        return token
    return ws.cookies.get(settings.cookie_name)


class TokenRefused(Exception):
    """An API token that doesn't work (any more); the message says why."""


class TokenForbidden(Exception):
    """A working API token that may not make this request (its scopes); the message says why."""


async def _load_user(db: AsyncSession, token: str | None, *, method: str, path: str, client: str = "") -> User | None:
    if not token:
        return None
    if api_tokens.is_api_token(token):
        return await _token_user(db, token, method=method, path=path, client=client)
    user_id = decode_session_token(token)
    if user_id is None:
        return None
    return await db.get(User, user_id)


async def _token_user(db: AsyncSession, token: str, *, method: str, path: str, client: str) -> User | None:
    row = (await db.execute(select(ApiToken).where(ApiToken.token_hash == api_tokens.hash_token(token)))).scalar_one_or_none()
    if row is None:
        return None
    if reason := api_tokens.usable(row):
        raise TokenRefused(reason)
    user = await db.get(User, row.user_id)
    if user is None:
        return None
    if reason := api_tokens.check(row, method, path):
        raise TokenForbidden(reason)
    user.api_token = row
    now = utcnow()
    if row.last_used_at is None or now - row.last_used_at > timedelta(minutes=1):
        # Its own session: the request's may never commit, and must not commit early.
        async with SessionLocal() as s:
            await s.execute(update(ApiToken).where(ApiToken.id == row.id).values(last_used_at=now, last_used_ip=client[:64]))
            await s.commit()
    return user


async def current_user(request: Request, db: DB) -> User:
    try:
        user = await _load_user(db, _token_from_request(request), method=request.method, path=request.url.path,
                                client=request.client.host if request.client else "")
    except TokenRefused as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    except TokenForbidden as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    if user.disabled_at is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This account is disabled")
    if secrets.compare_digest(request.headers.get(api_tokens.VIA_HEADER, ""), api_tokens.MCP_KEY):
        api_tokens.via.set("mcp")
    return user


async def websocket_user(ws: WebSocket, db: AsyncSession) -> User | None:
    """The socket's user, or None (closed with 4401) if it has none or its token may not
    open this socket."""
    try:
        user = await _load_user(db, _token_from_websocket(ws), method="GET", path=ws.url.path,
                                client=ws.client.host if ws.client else "")
    except (TokenRefused, TokenForbidden):
        return None
    return user if user is not None and user.disabled_at is None else None


CurrentUser = Annotated[User, Depends(current_user)]

ROLE_ERRORS = {
    UserRole.operator: "Viewers can look but not change anything; ask an admin for the operator role",
    UserRole.admin: "Only admins can do this",
}


def _needs(role: UserRole):
    async def check(user: CurrentUser) -> User:
        if not user.can(role):
            raise HTTPException(status.HTTP_403_FORBIDDEN, ROLE_ERRORS[role])
        return user

    return check


# An operator or admin (anything that changes something); an admin.
Operator = Annotated[User, Depends(_needs(UserRole.operator))]
Admin = Annotated[User, Depends(_needs(UserRole.admin))]


def parse_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found") from exc
