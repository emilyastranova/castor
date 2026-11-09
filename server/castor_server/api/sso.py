"""Single sign-on (sso.py): signing in through a provider (public), and the providers
themselves (Settings → Sign-on, admins)."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .. import app_settings, sso, vault
from .. import models as m
from ..config import settings
from ..deps import DB, Admin
from ..security import create_session_token

router = APIRouter(prefix="/auth/sso", tags=["auth"])
admin_router = APIRouter(prefix="/sso-providers", tags=["sso"])

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


def _base_url(request: Request) -> str:
    """Where people reach Castor: CASTOR_PUBLIC_URL, else how this request came in."""
    return (settings.public_url or str(request.base_url)).rstrip("/")


# ---------- signing in ----------


class SignInOptions(BaseModel):
    providers: list[dict[str, str]]  # [{id, name}]
    password_login: bool


@router.get("", response_model=SignInOptions)
async def sign_in_options(db: DB):
    """What the sign-in page offers."""
    rows = (await db.execute(select(m.SsoProvider).where(m.SsoProvider.enabled.is_(True)).order_by(m.SsoProvider.name))).scalars()
    providers = [{"id": p.id, "name": p.name} for p in rows]
    return SignInOptions(providers=providers, password_login=await password_login_allowed(db))


async def password_login_allowed(db: Any) -> bool:
    """Off only when an admin turned it off and some provider is enabled to sign in with."""
    if (await app_settings.load(db)).password_login:
        return True
    return not (await db.execute(select(m.SsoProvider.id).where(m.SsoProvider.enabled.is_(True)))).first()


def _back_to_login(message: str, next_path: str = "/") -> RedirectResponse:
    target = f"/login?sso_error={quote(message)}"
    if next_path != "/":
        target += f"&next={quote(next_path)}"
    response = RedirectResponse(target, status_code=303)
    response.delete_cookie(sso.STATE_COOKIE, path="/api/auth/sso")
    return response


@router.get("/{provider_id}/start")
async def start(provider_id: str, request: Request, db: DB, next: str | None = None):
    """Send the browser to the provider to sign in."""
    provider = await db.get(m.SsoProvider, provider_id)
    if provider is None or not provider.enabled:
        return _back_to_login("That sign-in option isn't available")
    try:
        url, cookie = await sso.start(provider, _base_url(request), next)
    except sso.SsoError as exc:
        return _back_to_login(f"{provider.name} sign-in isn't working: {exc}")
    response = RedirectResponse(url, status_code=303)
    response.set_cookie(sso.STATE_COOKIE, cookie, max_age=sso.STATE_TTL, httponly=True, samesite="lax",
                        secure=settings.cookie_secure, path="/api/auth/sso")
    return response


@router.get("/{provider_id}/callback")
async def callback(provider_id: str, request: Request, db: DB, code: str | None = None, state: str | None = None,
                   error: str | None = None, error_description: str | None = None):
    """Where the provider sends the browser back: sign the person in, or say why not."""
    provider = await db.get(m.SsoProvider, provider_id)
    if provider is None or not provider.enabled:
        return _back_to_login("That sign-in option isn't available")
    name = provider.name  # kept: a rollback below expires the row
    if error:
        return _back_to_login(f"{name} didn't sign you in: {error_description or error}")
    try:
        saved = sso.read_state(request.cookies.get(sso.STATE_COOKIE), provider.id, state)
        if not code:
            raise sso.SsoError("the provider sent no code back")
        claims = await sso.finish(provider, _base_url(request), code, saved)
        user = await sso.resolve_user(db, provider, claims)
    except sso.SsoError as exc:
        await db.rollback()
        return _back_to_login(f"Couldn't sign in with {name}: {exc}")
    await db.commit()
    response = RedirectResponse(sso.safe_next(saved.get("next")), status_code=303)
    response.delete_cookie(sso.STATE_COOKIE, path="/api/auth/sso")
    response.set_cookie(settings.cookie_name, create_session_token(user.id), httponly=True, samesite="lax",
                        secure=settings.cookie_secure, max_age=settings.session_ttl_hours * 3600, path="/")
    return response


# ---------- providers (admins) ----------


class RoleRule(BaseModel):
    group: str = Field(min_length=1, max_length=255)
    role: str = Field(pattern="^(viewer|operator|admin)$")


class ProviderIn(BaseModel):
    id: str
    name: str = Field(min_length=1, max_length=64)
    issuer: str = Field(min_length=1, max_length=500)
    client_id: str = Field(min_length=1, max_length=255)
    # Blank on an update keeps the stored one; empty on a new provider: a public client.
    client_secret: str | None = None
    scopes: str = Field("openid email profile", max_length=255)
    enabled: bool = True
    default_role: str = Field("viewer", pattern="^(viewer|operator|admin)$")
    allowed_domains: list[str] = Field(default_factory=list)
    allowed_groups: list[str] = Field(default_factory=list)
    groups_claim: str = Field("groups", max_length=64)
    role_map: list[RoleRule] = Field(default_factory=list)
    link_by_email: bool = False


class ProviderOut(BaseModel):
    id: str
    name: str
    issuer: str
    client_id: str
    has_secret: bool
    scopes: str
    enabled: bool
    default_role: str
    allowed_domains: list[str]
    allowed_groups: list[str]
    groups_claim: str
    role_map: list[RoleRule]
    link_by_email: bool
    # Register this at the provider as the redirect (callback) URI.
    redirect_uri: str
    users: int  # people who have signed in through it


class TestOut(BaseModel):
    ok: bool
    error: str | None = None
    issuer: str | None = None
    authorization_endpoint: str | None = None


async def _out(db: DB, row: m.SsoProvider, request: Request) -> ProviderOut:
    users = (await db.execute(select(func.count()).where(m.UserIdentity.provider_id == row.id))).scalar_one()
    return ProviderOut(
        id=row.id, name=row.name, issuer=row.issuer, client_id=row.client_id, has_secret=bool(row.client_secret),
        scopes=row.scopes, enabled=row.enabled, default_role=row.default_role, allowed_domains=row.allowed_domains or [],
        allowed_groups=row.allowed_groups or [], groups_claim=row.groups_claim, role_map=[RoleRule(**r) for r in row.role_map or []],
        link_by_email=row.link_by_email, redirect_uri=sso.redirect_uri(_base_url(request), row.id), users=users,
    )


def _apply(row: m.SsoProvider, body: ProviderIn) -> None:
    issuer = body.issuer.strip().rstrip("/")
    if not issuer.startswith(("https://", "http://")):
        raise HTTPException(422, "issuer: a URL, like https://login.example.com/realms/main")
    if "openid" not in body.scopes.split():
        raise HTTPException(422, "scopes: must include openid")
    if issuer != row.issuer:
        sso.forget(row.issuer or issuer)
    row.name, row.issuer, row.client_id = body.name.strip(), issuer, body.client_id.strip()
    row.scopes, row.enabled, row.default_role = " ".join(body.scopes.split()), body.enabled, body.default_role
    row.allowed_domains = sorted({d.strip().lower().lstrip("@") for d in body.allowed_domains if d.strip()})
    row.allowed_groups = sorted({g.strip() for g in body.allowed_groups if g.strip()})
    row.groups_claim = body.groups_claim.strip()
    row.role_map = [r.model_dump() for r in body.role_map]
    row.link_by_email = body.link_by_email
    if body.client_secret and body.client_secret.strip():
        row.client_secret = vault.encrypt(body.client_secret.strip().encode())


@admin_router.get("", response_model=list[ProviderOut])
async def list_providers(request: Request, db: DB, _: Admin):
    rows = (await db.execute(select(m.SsoProvider).order_by(m.SsoProvider.name))).scalars().all()
    return [await _out(db, r, request) for r in rows]


@admin_router.post("", response_model=ProviderOut, status_code=201)
async def create_provider(body: ProviderIn, request: Request, db: DB, _: Admin):
    if not SLUG.match(body.id):
        raise HTTPException(422, "id: lowercase letters, digits and dashes (it's part of the redirect URI)")
    if await db.get(m.SsoProvider, body.id) is not None:
        raise HTTPException(409, f"A provider with id {body.id} already exists")
    row = m.SsoProvider(id=body.id, issuer="", client_secret="")
    _apply(row, body)
    db.add(row)
    await db.commit()
    return await _out(db, row, request)


@admin_router.put("/{provider_id}", response_model=ProviderOut)
async def update_provider(provider_id: str, body: ProviderIn, request: Request, db: DB, _: Admin):
    row = await db.get(m.SsoProvider, provider_id)
    if row is None:
        raise HTTPException(404, "Provider not found")
    _apply(row, body)
    await db.commit()
    return await _out(db, row, request)


@admin_router.delete("/{provider_id}", status_code=204)
async def delete_provider(provider_id: str, db: DB, _: Admin):
    """Remove a provider; people who signed in through it keep their accounts (and can be
    given a password, or sign in through another provider)."""
    row = await db.get(m.SsoProvider, provider_id)
    if row is None:
        raise HTTPException(404, "Provider not found")
    await db.delete(row)
    await db.commit()


@admin_router.post("/test", response_model=TestOut)
async def test_issuer(body: dict[str, str], _: Admin):
    """Fetch an issuer's OpenID configuration, to check it before saving."""
    issuer = (body.get("issuer") or "").strip().rstrip("/")
    sso.forget(issuer)
    try:
        doc = await sso.discover(issuer)
    except sso.SsoError as exc:
        return TestOut(ok=False, error=str(exc))
    return TestOut(ok=True, issuer=doc["issuer"], authorization_endpoint=doc["authorization_endpoint"])
