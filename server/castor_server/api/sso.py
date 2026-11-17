"""Single sign-on (sso.py for OpenID Connect, saml.py for SAML): signing in through a provider
(public), and the providers themselves (Settings → Sign-on, admins)."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .. import app_settings, saml, sso, sso_branding, vault
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
    # [{id, name, label, style, color, text_color, logo, symbol, logo_url, logo_dark_url}], in order
    providers: list[dict[str, Any]]
    password_login: bool
    # A provider to go straight to (the page offers a way back; see sso_redirect).
    redirect: str | None = None
    message: str = ""
    password_form: str = "shown"


@router.get("", response_model=SignInOptions)
async def sign_in_options(db: DB):
    """What the sign-in page offers, and how its buttons look."""
    rows = (await db.execute(select(m.SsoProvider).where(m.SsoProvider.enabled.is_(True))
                             .order_by(m.SsoProvider.position, m.SsoProvider.name))).scalars().all()
    providers = [{"id": p.id, "name": p.name, **sso_branding.public(p.id, p.branding)} for p in rows]
    s = await app_settings.load(db)
    redirect = s.sso_redirect if any(p["id"] == s.sso_redirect for p in providers) else None
    return SignInOptions(providers=providers, password_login=await password_login_allowed(db), redirect=redirect,
                         message=s.sign_in_message, password_form=s.password_form)


@router.get("/{provider_id}/logo")
async def provider_logo(provider_id: str, db: DB, variant: Literal["light", "dark"] = "light"):
    """A provider's uploaded logo (public: the sign-in page shows it)."""
    row = await db.get(m.SsoProvider, provider_id)
    name = ((row.branding or {}).get("logo_dark_file" if variant == "dark" else "logo_file")) if row else None
    path = sso_branding.directory() / name if name and "/" not in name else None
    if row is None or not row.enabled or path is None or not path.is_file():
        raise HTTPException(404, "No logo")
    return Response(path.read_bytes(), media_type=sso_branding.content_type(name), headers={
        "Content-Security-Policy": sso_branding.LOGO_CSP, "X-Content-Type-Options": "nosniff",
        "Cache-Control": "public, max-age=31536000, immutable"})


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
        if provider.protocol == "saml":
            url, cookie = saml.start(provider, _base_url(request), next)
        else:
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
    return _signed_in(user, saved)


def _signed_in(user: m.User, saved: dict[str, Any]) -> RedirectResponse:
    response = RedirectResponse(sso.safe_next(saved.get("next")), status_code=303)
    response.delete_cookie(sso.STATE_COOKIE, path="/api/auth/sso")
    response.set_cookie(settings.cookie_name, create_session_token(user.id), httponly=True, samesite="lax",
                        secure=settings.cookie_secure, max_age=settings.session_ttl_hours * 3600, path="/")
    return response


async def _saml_provider(db: DB, provider_id: str) -> m.SsoProvider | None:
    provider = await db.get(m.SsoProvider, provider_id)
    return provider if provider is not None and provider.protocol == "saml" else None


@router.get("/{provider_id}/saml/metadata")
async def saml_metadata(provider_id: str, request: Request, db: DB):
    """Castor's SAML metadata for this provider: give it to the identity provider."""
    provider = await _saml_provider(db, provider_id)
    if provider is None:
        raise HTTPException(404, "No SAML provider with that id")
    return Response(saml.sp_metadata(provider, _base_url(request)), media_type="application/samlmetadata+xml",
                    headers={"Content-Disposition": f'inline; filename="castor-{provider.id}-metadata.xml"'})


@router.post("/{provider_id}/saml/acs")
async def saml_acs(provider_id: str, request: Request, db: DB, SAMLResponse: Annotated[str, Form()] = "",
                   RelayState: Annotated[str, Form()] = ""):
    """Where the identity provider POSTs its response. The browser's state cookie doesn't come
    with a cross-site POST, so this checks the response and hands it on to …/saml/done."""
    provider = await _saml_provider(db, provider_id)
    if provider is None or not provider.enabled:
        return _back_to_login("That sign-in option isn't available")
    if not SAMLResponse:
        return _back_to_login(f"{provider.name} sent no response back")
    try:
        result = saml.read_response(provider, _base_url(request), SAMLResponse)
    except sso.SsoError as exc:
        return _back_to_login(f"Couldn't sign in with {provider.name}: {exc}")
    sealed = saml.seal(provider.id, result, RelayState)
    return RedirectResponse(f"/api/auth/sso/{provider.id}/saml/done?r={quote(sealed)}", status_code=303)


@router.get("/{provider_id}/saml/done")
async def saml_done(provider_id: str, request: Request, db: DB, r: str | None = None):
    """The second half of the ACS, with the cookie: is this the sign-in started here?"""
    provider = await _saml_provider(db, provider_id)
    if provider is None or not provider.enabled:
        return _back_to_login("That sign-in option isn't available")
    name = provider.name
    try:
        sealed = saml.unseal(provider.id, r)
        saved = sso.read_state(request.cookies.get(sso.STATE_COOKIE), provider.id, sealed.get("s"))
        claims = saml.finish(provider.id, sealed, saved)
        user = await sso.resolve_user(db, provider, claims)
    except sso.SsoError as exc:
        await db.rollback()
        return _back_to_login(f"Couldn't sign in with {name}: {exc}")
    await db.commit()
    return _signed_in(user, saved)


# ---------- providers (admins) ----------


class RoleRule(BaseModel):
    group: str = Field(min_length=1, max_length=255)
    role: str = Field(pattern="^(viewer|operator|admin)$")


class SamlIn(saml.SamlConfig):
    pass


class ProviderIn(BaseModel):
    id: str
    name: str = Field(min_length=1, max_length=64)
    protocol: Literal["oidc", "saml"] = "oidc"
    # OpenID Connect: the issuer URL. SAML: the identity provider's entity ID.
    issuer: str = Field(min_length=1, max_length=500)
    client_id: str = Field("", max_length=255)  # OpenID Connect only
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
    saml: SamlIn | None = None  # SAML only
    # Its button on the sign-in page; left out: unchanged (logos are uploaded separately).
    branding: sso_branding.Branding | None = None


class CertificateOut(BaseModel):
    subject: str
    not_after: str
    sha256: str


class SamlOut(saml.SamlConfig):
    described: list[CertificateOut] = Field(default_factory=list)  # the certificates, readable
    # Give these to the identity provider (or just the metadata URL).
    acs_url: str = ""
    entity_id: str = ""  # Castor's: sp_entity_id, or the metadata URL when that's blank
    metadata: str = ""


class ProviderOut(BaseModel):
    id: str
    name: str
    protocol: str
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
    # Register this at the provider as the redirect (callback) URI (SAML: the ACS URL).
    redirect_uri: str
    users: int  # people who have signed in through it
    saml: SamlOut | None = None
    # Its button: the choices (sso_branding.Branding), whether logos are uploaded, and their URLs.
    branding: dict[str, Any] = Field(default_factory=dict)
    position: int = 0


class TestOut(BaseModel):
    ok: bool
    error: str | None = None
    issuer: str | None = None
    authorization_endpoint: str | None = None


async def _out(db: DB, row: m.SsoProvider, request: Request) -> ProviderOut:
    users = (await db.execute(select(func.count()).where(m.UserIdentity.provider_id == row.id))).scalar_one()
    base = _base_url(request)
    saml_out = None
    if row.protocol == "saml":
        cfg = saml.config(row)
        described = []
        for pem in cfg.certificates:
            try:
                described.append(CertificateOut(**saml.describe_certificate(pem)))
            except sso.SsoError:
                described.append(CertificateOut(subject="(unreadable)", not_after="", sha256=""))
        saml_out = SamlOut(**cfg.model_dump(), described=described, acs_url=saml.acs_url(base, row.id),
                           entity_id=saml.sp_entity_id(row, base), metadata=saml.metadata_url(base, row.id))
    return ProviderOut(
        id=row.id, name=row.name, protocol=row.protocol, issuer=row.issuer, saml=saml_out, client_id=row.client_id, has_secret=bool(row.client_secret),
        scopes=row.scopes, enabled=row.enabled, default_role=row.default_role, allowed_domains=row.allowed_domains or [],
        allowed_groups=row.allowed_groups or [], groups_claim=row.groups_claim, role_map=[RoleRule(**r) for r in row.role_map or []],
        link_by_email=row.link_by_email, users=users, position=row.position or 0,
        branding={**{k: v for k, v in sso_branding.stored(row.branding).items() if k not in ("logo_file", "logo_dark_file")},
                  "has_logo": bool((row.branding or {}).get("logo_file")), "has_logo_dark": bool((row.branding or {}).get("logo_dark_file")),
                  "logo_url": sso_branding.public(row.id, row.branding)["logo_url"],
                  "logo_dark_url": sso_branding.public(row.id, row.branding)["logo_dark_url"]},
        redirect_uri=saml.acs_url(base, row.id) if row.protocol == "saml" else sso.redirect_uri(base, row.id),
    )


def _apply_saml(row: m.SsoProvider, body: ProviderIn) -> None:
    cfg = body.saml or SamlIn()
    if not cfg.sso_url.strip().startswith(("https://", "http://")):
        raise HTTPException(422, "saml.sso_url: the identity provider's single sign-on URL (HTTP-Redirect binding)")
    try:
        certs = list(dict.fromkeys(saml.to_pem(c) for c in cfg.certificates if c.strip()))
    except sso.SsoError as exc:
        raise HTTPException(422, f"saml.certificates: {exc}") from exc
    if not certs:
        raise HTTPException(422, "saml.certificates: at least one, the certificate the provider signs with")
    clean = {k: v.strip() if isinstance(v, str) else v for k, v in cfg.model_dump().items()}
    row.saml = {**clean, "certificates": certs}
    row.issuer, row.client_id, row.scopes = body.issuer.strip(), "", ""


def _apply(row: m.SsoProvider, body: ProviderIn) -> None:
    if row.protocol and row.protocol != body.protocol:
        raise HTTPException(422, "protocol: a provider's protocol can't change; add another provider")
    row.protocol = body.protocol
    if body.protocol == "saml":
        _apply_saml(row, body)
        _apply_common(row, body)
        return
    issuer = body.issuer.strip().rstrip("/")
    if not issuer.startswith(("https://", "http://")):
        raise HTTPException(422, "issuer: a URL, like https://login.example.com/realms/main")
    if not body.client_id.strip():
        raise HTTPException(422, "client_id: the client ID the provider gave Castor")
    if "openid" not in body.scopes.split():
        raise HTTPException(422, "scopes: must include openid")
    if issuer != row.issuer:
        sso.forget(row.issuer or issuer)
    row.issuer, row.client_id, row.scopes = issuer, body.client_id.strip(), " ".join(body.scopes.split())
    if body.client_secret and body.client_secret.strip():
        row.client_secret = vault.encrypt(body.client_secret.strip().encode())
    _apply_common(row, body)


def _apply_common(row: m.SsoProvider, body: ProviderIn) -> None:
    row.name = body.name.strip()
    row.enabled, row.default_role = body.enabled, body.default_role
    row.allowed_domains = sorted({d.strip().lower().lstrip("@") for d in body.allowed_domains if d.strip()})
    row.allowed_groups = sorted({g.strip() for g in body.allowed_groups if g.strip()})
    row.groups_claim = body.groups_claim.strip()
    row.role_map = [r.model_dump() for r in body.role_map]
    row.link_by_email = body.link_by_email
    if body.branding is not None:
        kept = {k: v for k, v in (row.branding or {}).items() if k in ("logo_file", "logo_dark_file", "logo_version")}
        row.branding = {**body.branding.model_dump(), **kept}


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
    row = m.SsoProvider(id=body.id, issuer="", client_secret="", protocol="")
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
    for key in ("logo_file", "logo_dark_file"):
        sso_branding.remove_logo((row.branding or {}).get(key))
    if (await app_settings.load(db)).sso_redirect == provider_id:
        await app_settings.save(db, {"sso_redirect": ""}, None)
    await db.delete(row)
    await db.commit()


class OrderIn(BaseModel):
    ids: list[str]


@admin_router.post("/order", status_code=204)
async def order_providers(body: OrderIn, db: DB, _: Admin):
    """The order of the buttons on the sign-in page (ids first to last; others after them)."""
    rows = {r.id: r for r in (await db.execute(select(m.SsoProvider))).scalars()}
    rest = sorted((r for i, r in rows.items() if i not in body.ids), key=lambda r: (r.position, r.name))
    for pos, row in enumerate([rows[i] for i in body.ids if i in rows] + rest):
        row.position = pos
    await db.commit()


@admin_router.put("/{provider_id}/logo", response_model=ProviderOut)
async def upload_logo(provider_id: str, request: Request, db: DB, _: Admin,
                      file: Annotated[UploadFile, File()], variant: Literal["light", "dark"] = "light"):
    """Its logo (SVG, PNG, WebP, JPEG or GIF, up to 512 KB): a square icon or a horizontal wordmark,
    as its branding says; `dark` is the one for dark themes (optional)."""
    row = await db.get(m.SsoProvider, provider_id)
    if row is None:
        raise HTTPException(404, "Provider not found")
    branding = dict(row.branding or {})
    version = int(branding.get("logo_version") or 0) + 1
    try:
        name = sso_branding.save_logo(provider_id, version, variant, await file.read(sso_branding.MAX_BYTES + 1))
    except sso_branding.BrandingError as exc:
        raise HTTPException(422, str(exc)) from exc
    key = "logo_dark_file" if variant == "dark" else "logo_file"
    sso_branding.remove_logo(branding.get(key))
    branding.update({key: name, "logo_version": version})
    row.branding = branding
    await db.commit()
    return await _out(db, row, request)


@admin_router.delete("/{provider_id}/logo", response_model=ProviderOut)
async def delete_logo(provider_id: str, request: Request, db: DB, _: Admin, variant: Literal["light", "dark"] = "light"):
    row = await db.get(m.SsoProvider, provider_id)
    if row is None:
        raise HTTPException(404, "Provider not found")
    branding = dict(row.branding or {})
    key = "logo_dark_file" if variant == "dark" else "logo_file"
    sso_branding.remove_logo(branding.pop(key, None))
    branding["logo_version"] = int(branding.get("logo_version") or 0) + 1
    row.branding = branding
    await db.commit()
    return await _out(db, row, request)


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


class MetadataIn(BaseModel):
    url: str = Field("", max_length=1000)
    xml: str = Field("", max_length=saml.MAX_METADATA)
    entity_id: str = Field("", max_length=500)  # when the metadata describes several


class MetadataOut(BaseModel):
    ok: bool
    error: str | None = None
    entity_id: str = ""
    sso_url: str = ""
    certificates: list[str] = Field(default_factory=list)
    described: list[CertificateOut] = Field(default_factory=list)


@admin_router.post("/saml/metadata", response_model=MetadataOut)
async def read_saml_metadata(body: MetadataIn, _: Admin):
    """Read a SAML identity provider's metadata (from its URL, or pasted) to fill in a provider."""
    try:
        xml = body.xml.encode() if body.xml.strip() else await saml.fetch_metadata(body.url.strip())
        found = saml.read_metadata(xml, body.entity_id.strip() or None)
    except sso.SsoError as exc:
        return MetadataOut(ok=False, error=str(exc))
    return MetadataOut(ok=True, **found, described=[CertificateOut(**saml.describe_certificate(c)) for c in found["certificates"]])
