from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select

from .. import models as m
from ..config import settings
from ..deps import DB, CurrentUser
from ..schemas import LoginRequest, LoginResponse, UserOut
from ..security import create_session_token, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, response: Response, db: DB):
    user = (
        await db.execute(select(m.User).where(m.User.username == body.username))
    ).scalar_one_or_none()
    if user is None or user.plugin_id or not verify_password(body.password, user.password_hash):  # plugins never sign in
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")
    if user.disabled_at is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This account is disabled")
    from .sso import password_login_allowed

    if not await password_login_allowed(db):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Password sign-in is turned off: use single sign-on")
    token = create_session_token(user.id)
    response.set_cookie(
        settings.cookie_name,
        token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.session_ttl_hours * 3600,
        path="/",
    )
    return LoginResponse(token=token, user=UserOut.model_validate(user))


@router.post("/logout", status_code=204)
async def logout(response: Response):
    response.delete_cookie(settings.cookie_name, path="/")


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser):
    return user
