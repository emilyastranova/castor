"""Single sign-on with OpenID Connect: the authorization code flow with PKCE.

Admins add providers in Settings → Sign-on (`sso_providers`; the client secret is a vault
token). Signing in:
1. `start` sends the browser to the provider's authorization endpoint with a fresh `state`,
   `nonce` and PKCE challenge, remembered in a short-lived signed cookie (`castor_sso`).
2. The provider sends it back to `/api/auth/sso/<id>/callback`; `finish` checks `state`
   against the cookie, swaps the code (and PKCE verifier) for tokens, and verifies the
   `id_token` against the provider's published keys: asymmetric algorithms only, issuer,
   audience, expiry and the nonce. Missing email or groups come from the UserInfo endpoint.
3. `resolve_user` finds or makes the account: an identity already linked (provider + `sub`);
   else, when the provider allows it and the email is verified, an account with that email;
   else a new account with the provider's default role. Allowed domains and groups are checked
   first, and with a role map the role follows the groups at every sign-in (never demoting
   the last active admin).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m
from . import vault
from .accounts import active_admins
from .config import settings
from .security import hash_password
from .services import record_activity

STATE_COOKIE = "castor_sso"
STATE_TTL = 600  # seconds to finish signing in at the provider
DISCOVERY_TTL = 3600
ALGORITHMS = ["RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA"]
ROLE_RANK = {"viewer": 0, "operator": 1, "admin": 2}
TIMEOUT = 10

_cache: dict[str, tuple[float, dict[str, Any]]] = {}


class SsoError(Exception):
    """Why signing in didn't work, in words fit to show the person."""


# ---------- the provider's documents ----------


async def _get_json(url: str, *, ttl: int = 0, **kw: Any) -> dict[str, Any]:
    if ttl and (hit := _cache.get(url)) and hit[0] > time.monotonic():
        return hit[1]
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        r = await client.get(url, **kw)
    if r.status_code >= 400:
        raise SsoError(f"the provider answered {r.status_code} for {url}")
    try:
        data = r.json()
    except ValueError as exc:
        raise SsoError(f"the provider's {url} isn't JSON") from exc
    if ttl:
        _cache[url] = (time.monotonic() + ttl, data)
    return data


async def discover(issuer: str) -> dict[str, Any]:
    """The provider's OpenID configuration (cached for an hour)."""
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    try:
        doc = await _get_json(url, ttl=DISCOVERY_TTL)
    except httpx.HTTPError as exc:
        raise SsoError(f"couldn't reach {url}: {exc}") from exc
    missing = [k for k in ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri") if not doc.get(k)]
    if missing:
        raise SsoError(f"the provider's configuration lacks {', '.join(missing)}")
    if doc["issuer"].rstrip("/") != issuer.rstrip("/"):
        raise SsoError(f"the provider says its issuer is {doc['issuer']}, not {issuer}")
    return doc


def forget(issuer: str) -> None:
    """Drop cached documents (after a provider is edited)."""
    for key in [k for k in _cache if k.startswith(issuer.rstrip("/"))]:
        _cache.pop(key, None)


# ---------- starting ----------


def redirect_uri(base_url: str, provider_id: str) -> str:
    return f"{base_url.rstrip('/')}/api/auth/sso/{provider_id}/callback"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def safe_next(target: str | None) -> str:
    """Where to go after signing in: a path on this site only."""
    return target if target and target.startswith("/") and not target.startswith("//") and "\\" not in target else "/"


async def start(provider: m.SsoProvider, base_url: str, next_path: str | None) -> tuple[str, str]:
    """(the provider's authorization URL, the state cookie's value)."""
    doc = await discover(provider.issuer)
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    challenge = _b64(hashlib.sha256(verifier.encode()).digest())
    cookie = jwt.encode(
        {"p": provider.id, "s": state, "n": nonce, "v": verifier, "next": safe_next(next_path), "exp": int(time.time()) + STATE_TTL},
        settings.secret_key, algorithm="HS256",
    )
    query = urlencode({
        "response_type": "code", "client_id": provider.client_id, "redirect_uri": redirect_uri(base_url, provider.id),
        "scope": provider.scopes or "openid", "state": state, "nonce": nonce,
        "code_challenge": challenge, "code_challenge_method": "S256",
    })
    sep = "&" if "?" in doc["authorization_endpoint"] else "?"
    return f"{doc['authorization_endpoint']}{sep}{query}", cookie


# ---------- finishing ----------


def read_state(cookie: str | None, provider_id: str, state: str | None) -> dict[str, Any]:
    if not cookie:
        raise SsoError("the sign-in took too long or started in another browser; try again")
    try:
        data = jwt.decode(cookie, settings.secret_key, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise SsoError("the sign-in took too long; try again") from exc
    if data.get("p") != provider_id or not state or not hmac.compare_digest(str(data.get("s")), state):
        raise SsoError("the sign-in didn't match the one started here; try again")
    return data


async def _verify_id_token(token: str, doc: dict[str, Any], client_id: str, nonce: str) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise SsoError("the provider's id_token isn't a JWT") from exc
    alg = header.get("alg")
    if alg not in ALGORITHMS:
        raise SsoError(f"the provider signed its id_token with {alg}, which Castor doesn't accept")
    keys = await _get_json(doc["jwks_uri"], ttl=DISCOVERY_TTL)
    kid = header.get("kid")
    found = [k for k in keys.get("keys", []) if not kid or k.get("kid") == kid]
    if not found:  # rotated: fetch the keys again
        _cache.pop(doc["jwks_uri"], None)
        keys = await _get_json(doc["jwks_uri"], ttl=DISCOVERY_TTL)
        found = [k for k in keys.get("keys", []) if not kid or k.get("kid") == kid]
    if not found:
        raise SsoError("the provider's id_token was signed with a key it doesn't publish")
    try:
        key = jwt.PyJWK(found[0], algorithm=alg)
        claims = jwt.decode(
            token, key, algorithms=[alg], audience=client_id, issuer=doc["issuer"], leeway=60,
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise SsoError(f"the provider's id_token didn't check out: {exc}") from exc
    if not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
        raise SsoError("the provider's id_token is for another sign-in (nonce)")
    return claims


async def finish(provider: m.SsoProvider, base_url: str, code: str, saved: dict[str, Any]) -> dict[str, Any]:
    """Swap the code for tokens and return the verified claims (with UserInfo merged in)."""
    doc = await discover(provider.issuer)
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(base_url, provider.id),
            "client_id": provider.client_id, "code_verifier": saved["v"]}
    auth = None
    if provider.client_secret:
        secret = vault.decrypt(provider.client_secret).decode()
        methods = doc.get("token_endpoint_auth_methods_supported") or ["client_secret_basic"]
        if "client_secret_basic" in methods:
            auth = (provider.client_id, secret)
        else:
            data["client_secret"] = secret
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.post(doc["token_endpoint"], data=data, auth=auth, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise SsoError(f"couldn't reach the provider's token endpoint: {exc}") from exc
    try:
        tokens = r.json()
    except ValueError:
        tokens = {}
    if r.status_code >= 400 or "id_token" not in tokens:
        reason = tokens.get("error_description") or tokens.get("error") or f"HTTP {r.status_code}"
        raise SsoError(f"the provider refused the sign-in: {reason}")
    claims = await _verify_id_token(tokens["id_token"], doc, provider.client_id, saved["n"])
    wants = ("email" not in claims) or (provider.groups_claim and provider.groups_claim not in claims)
    if wants and doc.get("userinfo_endpoint") and tokens.get("access_token"):
        try:
            info = await _get_json(doc["userinfo_endpoint"], headers={"Authorization": f"Bearer {tokens['access_token']}"})
        except (SsoError, httpx.HTTPError):
            info = {}
        if info.get("sub") == claims["sub"]:
            claims = {**info, **claims}
    return claims


# ---------- the account ----------


def _groups(claims: dict[str, Any], claim: str) -> list[str]:
    value = claims.get(claim) if claim else None
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value] if isinstance(value, list) else []


def _verified(claims: dict[str, Any]) -> bool:
    return claims.get("email_verified") in (True, "true", "True")


def _mapped_role(provider: m.SsoProvider, groups: list[str]) -> str:
    roles = [r["role"] for r in provider.role_map or [] if r.get("group") in groups and r.get("role") in ROLE_RANK]
    return max(roles, key=ROLE_RANK.__getitem__) if roles else provider.default_role


async def _free_username(db: AsyncSession, wanted: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", wanted).strip("-._")[:56] or "user"
    if not base[0].isalnum():
        base = "u" + base
    name, n = base, 1
    while (await db.execute(select(m.User.id).where(func.lower(m.User.username) == name.lower()))).first() or \
            (await db.execute(select(m.Group.id).where(func.lower(m.Group.slug) == name.lower()))).first():
        n += 1
        name = f"{base}-{n}"
    return name


async def sync_groups(db: AsyncSession, user: m.User, provider_groups: set[str]) -> bool:
    """Castor groups that follow a provider group (`sso_group`): in them exactly when the
    provider says so. Only when the provider sent groups at all. Returns whether it changed."""
    following = (await db.execute(select(m.Group).where(m.Group.sso_group != ""))).scalars().all()
    if not following:
        return False
    current = set((await db.execute(select(m.GroupMember.group_id).where(m.GroupMember.user_id == user.id))).scalars())
    changed = False
    for group in following:
        should = group.sso_group in provider_groups
        if should and group.id not in current:
            db.add(m.GroupMember(group_id=group.id, user_id=user.id))
            changed = True
        elif not should and group.id in current:
            await db.delete(await db.get(m.GroupMember, (group.id, user.id)))
            changed = True
    if changed:
        from .events import bus

        bus.publish("project.members", None, {"user_ids": [str(user.id)]})
        bus.publish("groups.changed", None, {})
    return changed


async def resolve_user(db: AsyncSession, provider: m.SsoProvider, claims: dict[str, Any]) -> m.User:
    """The Castor account for these claims (linked, found by email, or new). Caller commits."""
    subject = str(claims["sub"])
    email = str(claims.get("email") or "").strip()
    verified = bool(email) and _verified(claims)
    groups = _groups(claims, provider.groups_claim)

    if provider.allowed_domains:
        domain = email.rsplit("@", 1)[-1].lower() if verified else ""
        if domain not in {d.lower().lstrip("@") for d in provider.allowed_domains}:
            raise SsoError(f"{provider.name} accounts from that email domain can't sign in to Castor")
    if provider.allowed_groups and not set(groups) & set(provider.allowed_groups):
        raise SsoError(f"your {provider.name} account isn't in a group that may sign in to Castor")

    identity = (await db.execute(select(m.UserIdentity).where(
        m.UserIdentity.provider_id == provider.id, m.UserIdentity.subject == subject))).scalar_one_or_none()
    user = await db.get(m.User, identity.user_id) if identity else None
    how = "signed in"
    if user is None and provider.link_by_email and verified:
        same = (await db.execute(select(m.User).where(func.lower(m.User.email) == email.lower()))).scalars().all()
        if len(same) == 1:
            user, how = same[0], "linked"
    async def email_free() -> bool:
        return verified and not (await db.execute(select(m.User.id).where(func.lower(m.User.email) == email.lower()))).first()

    if user is None:
        wanted = str(claims.get("preferred_username") or email.split("@")[0] or claims.get("name") or subject)
        user = m.User(
            username=await _free_username(db, wanted),
            display_name=str(claims.get("name") or wanted)[:128],
            email=email if await email_free() else "",
            password_hash=hash_password(secrets.token_urlsafe(32)),  # nobody knows it: SSO only
            role=m.UserRole(_mapped_role(provider, groups)),
        )
        db.add(user)
        await db.flush()
        how = "created"
    if identity is None:
        identity = m.UserIdentity(user_id=user.id, provider_id=provider.id, subject=subject)
        db.add(identity)
    if user.disabled_at is not None:
        raise SsoError("this Castor account is disabled")

    if provider.role_map and how != "created":
        role = m.UserRole(_mapped_role(provider, groups))
        last_admin = user.role == m.UserRole.admin and role != m.UserRole.admin and await active_admins(db) <= 1
        if role != user.role and not last_admin:
            user.role = role
    if not user.email and await email_free():
        user.email = email
    if provider.groups_claim and provider.groups_claim in claims:
        await sync_groups(db, user, set(groups))
    identity.email = email
    identity.last_login_at = m.utcnow()
    if how != "signed in":
        await record_activity(
            db, type=f"user.sso_{how}", message=f"{user.display_name} {how} through {provider.name}" if how == "linked"
            else f"Account {user.username} created by signing in through {provider.name}",
            project_id=None, actor_type="user", actor_id=str(user.id), actor_name=user.display_name,
            payload={"provider": provider.id},
        )
    return user
