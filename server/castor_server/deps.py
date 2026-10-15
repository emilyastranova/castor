"""FastAPI dependencies."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, Request, WebSocket, status
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import get_session
from .models import User
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
    if token:
        return token
    return ws.cookies.get(settings.cookie_name)


async def _load_user(db: AsyncSession, token: str | None) -> User | None:
    if not token:
        return None
    user_id = decode_session_token(token)
    if user_id is None:
        return None
    return await db.get(User, user_id)


async def current_user(request: Request, db: DB) -> User:
    user = await _load_user(db, _token_from_request(request))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return user


async def websocket_user(ws: WebSocket, db: AsyncSession) -> User | None:
    return await _load_user(db, _token_from_websocket(ws))


CurrentUser = Annotated[User, Depends(current_user)]


def parse_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found") from exc
