"""Single sign-on with SAML 2.0: the Web Browser SSO profile, started here (SP-initiated).

A SAML provider is an `sso_providers` row with `protocol = "saml"`: `issuer` is the identity
provider's entity ID, `saml` holds the rest (`SamlConfig`). Accounts, roles and groups are
`sso.resolve_user`'s, the same as for OpenID Connect; this module only turns a signed SAML
response into the claims it reads.

Signing in:
1. `start` sends the browser to the identity provider's single sign-on URL with an
   AuthnRequest (HTTP-Redirect binding, deflated) and a RelayState, both remembered in the same
   short-lived signed cookie as OpenID Connect (`sso.STATE_COOKIE`, with the request's ID).
2. The provider POSTs a SAMLResponse to the assertion consumer service (`…/saml/acs`). That's a
   cross-site POST, which doesn't carry the SameSite=Lax cookie, so `read_response` checks
   everything it can without it: the XML signature (signxml, against the certificates the admin
   gave; only what the signature covers is read afterwards), issuer, audience, recipient,
   destination, times, and that the assertion wasn't seen before. The result travels encrypted
   (vault) in a redirect to `…/saml/done`, a top-level GET that does carry the cookie.
3. `…/saml/done` (`finish`) checks the RelayState and that the response answers the request
   started in this browser (`InResponseTo`); responses nobody asked for (IdP-initiated) are
   refused.

Not supported (yet): encrypted assertions, signed AuthnRequests, single logout.
"""

from __future__ import annotations

import base64
import binascii
import json
import secrets
import time
import zlib
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from xml.sax.saxutils import escape, quoteattr

import defusedxml.ElementTree as SafeET
import httpx
import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import Encoding
from defusedxml import DefusedXmlException
from lxml import etree
from pydantic import BaseModel, Field
from signxml import SignatureConfiguration, XMLVerifier
from signxml.exceptions import SignXMLException

from . import models as m
from . import vault
from .config import settings
from .sso import STATE_TTL, TIMEOUT, SsoError, safe_next

SAMLP = "urn:oasis:names:tc:SAML:2.0:protocol"
SAML = "urn:oasis:names:tc:SAML:2.0:assertion"
MD = "urn:oasis:names:tc:SAML:2.0:metadata"
DS = "http://www.w3.org/2000/09/xmldsig#"
XENC = "http://www.w3.org/2001/04/xmlenc#"
REDIRECT = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
POST = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
SUCCESS = "urn:oasis:names:tc:SAML:2.0:status:Success"
BEARER = "urn:oasis:names:tc:SAML:2.0:cm:bearer"
TRANSIENT = "urn:oasis:names:tc:SAML:2.0:nameid-format:transient"
EMAIL_FORMAT = "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"

LEEWAY = 90  # seconds of clock difference allowed
RESULT_TTL = 120  # seconds from the ACS to …/done
MAX_RESPONSE = 512 * 1024  # bytes of SAMLResponse XML
MAX_METADATA = 2 * 1024 * 1024

# Where providers usually put things, tried after the attribute the admin named (case-insensitive,
# by Name or FriendlyName).
EMAIL_ATTRS = ("email", "mail", "emailaddress", "urn:oid:0.9.2342.19200300.100.1.3",
               "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress", "user.email")
NAME_ATTRS = ("displayname", "name", "urn:oid:2.16.840.1.113730.3.1.241", "http://schemas.microsoft.com/identity/claims/displayname",
              "fullname", "cn")
USERNAME_ATTRS = ("username", "uid", "preferred_username", "urn:oid:0.9.2342.19200300.100.1.1", "samaccountname",
                  "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name", "nickname")
GIVEN_ATTRS = ("givenname", "firstname", "urn:oid:2.5.4.42", "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/givenname")
SURNAME_ATTRS = ("sn", "surname", "lastname", "urn:oid:2.5.4.4", "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/surname")

_seen: dict[str, float] = {}  # assertion IDs already used → when they'd expire anyway (monotonic)


class SamlConfig(BaseModel):
    """`sso_providers.saml`: what Castor needs to know about a SAML identity provider."""

    sso_url: str = Field("", max_length=1000)  # its single sign-on service (HTTP-Redirect binding)
    certificates: list[str] = Field(default_factory=list)  # PEM; any of them may sign
    metadata_url: str = Field("", max_length=1000)  # where they were loaded from, to load again
    sp_entity_id: str = Field("", max_length=500)  # Castor's entity ID there; blank: the metadata URL
    name_id_format: str = Field("", max_length=200)  # asked for in the request; blank: the provider's choice
    subject_attribute: str = Field("", max_length=200)  # who the person is; blank: the NameID
    email_attribute: str = Field("", max_length=200)
    name_attribute: str = Field("", max_length=200)
    username_attribute: str = Field("", max_length=200)
    trust_email: bool = True  # treat its email addresses as verified (domains, linking by email)


def config(provider: m.SsoProvider) -> SamlConfig:
    return SamlConfig.model_validate(provider.saml or {})


def acs_url(base_url: str, provider_id: str) -> str:
    return f"{base_url.rstrip('/')}/api/auth/sso/{provider_id}/saml/acs"


def metadata_url(base_url: str, provider_id: str) -> str:
    return f"{base_url.rstrip('/')}/api/auth/sso/{provider_id}/saml/metadata"


def sp_entity_id(provider: m.SsoProvider, base_url: str) -> str:
    return config(provider).sp_entity_id or metadata_url(base_url, provider.id)


# ---------- certificates ----------


def load_certificate(text: str) -> x509.Certificate:
    """A certificate from PEM, or the bare base64 that metadata and admin consoles show."""
    text = text.strip()
    try:
        if "-----BEGIN" in text:
            return x509.load_pem_x509_certificate(text.encode())
        return x509.load_der_x509_certificate(base64.b64decode("".join(text.split()), validate=True))
    except (ValueError, binascii.Error) as exc:
        raise SsoError("that isn't an X.509 certificate (paste the PEM, or the base64 from the metadata)") from exc


def to_pem(text: str) -> str:
    return load_certificate(text).public_bytes(Encoding.PEM).decode()


def describe_certificate(pem: str) -> dict[str, Any]:
    cert = load_certificate(pem)
    digest = cert.fingerprint(hashes.SHA256()).hex().upper()
    return {
        "subject": cert.subject.rfc4514_string() or "(no subject)",
        "not_after": cert.not_valid_after_utc.isoformat(),
        "sha256": ":".join(digest[i:i + 2] for i in range(0, len(digest), 2)),
    }


# ---------- the identity provider's metadata ----------


def read_metadata(xml: bytes, entity_id: str | None = None) -> dict[str, Any]:
    """Entity ID, single sign-on URL and signing certificates from IdP metadata."""
    try:
        root = SafeET.fromstring(xml, forbid_dtd=True)
    except (SafeET.ParseError, DefusedXmlException) as exc:
        raise SsoError(f"the metadata isn't XML Castor will read: {exc}") from exc
    entities = [root] if root.tag == f"{{{MD}}}EntityDescriptor" else root.iter(f"{{{MD}}}EntityDescriptor")
    for entity in entities:
        idp = entity.find(f"{{{MD}}}IDPSSODescriptor")
        if idp is None or (entity_id and entity.get("entityID") != entity_id):
            continue
        urls = {s.get("Binding"): s.get("Location") for s in idp.findall(f"{{{MD}}}SingleSignOnService")}
        if not urls.get(REDIRECT):
            raise SsoError("the identity provider offers no HTTP-Redirect single sign-on service, which Castor uses")
        certs = []
        for key in idp.findall(f"{{{MD}}}KeyDescriptor"):
            if key.get("use") not in (None, "", "signing"):
                continue
            for data in key.iter(f"{{{DS}}}X509Certificate"):
                pem = to_pem(data.text or "")
                if pem not in certs:
                    certs.append(pem)
        if not certs:
            raise SsoError("the metadata has no signing certificate")
        return {"entity_id": entity.get("entityID") or "", "sso_url": urls[REDIRECT], "certificates": certs}
    raise SsoError("the metadata describes no identity provider" + (f" with entity ID {entity_id}" if entity_id else ""))


async def fetch_metadata(url: str) -> bytes:
    if not url.startswith(("https://", "http://")):
        raise SsoError("the metadata URL must start with https://")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            r = await client.get(url)
    except httpx.HTTPError as exc:
        raise SsoError(f"couldn't reach {url}: {exc}") from exc
    if r.status_code >= 400:
        raise SsoError(f"the provider answered {r.status_code} for {url}")
    if len(r.content) > MAX_METADATA:
        raise SsoError("the metadata is too large")
    return r.content


# ---------- Castor's metadata ----------


def sp_metadata(provider: m.SsoProvider, base_url: str) -> str:
    """What to give the identity provider: who Castor is and where responses go."""
    cfg = config(provider)
    fmt = f"\n    <md:NameIDFormat>{escape(cfg.name_id_format)}</md:NameIDFormat>" if cfg.name_id_format else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<md:EntityDescriptor xmlns:md="{MD}" entityID={quoteattr(sp_entity_id(provider, base_url))}>\n'
        f'  <md:SPSSODescriptor AuthnRequestsSigned="false" WantAssertionsSigned="true" protocolSupportEnumeration="{SAMLP}">'
        f'{fmt}\n'
        f'    <md:AssertionConsumerService Binding="{POST}" Location={quoteattr(acs_url(base_url, provider.id))} index="0" isDefault="true"/>\n'
        '  </md:SPSSODescriptor>\n'
        '</md:EntityDescriptor>\n'
    )


# ---------- starting ----------


def _instant(at: datetime | None = None) -> str:
    return (at or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")


def start(provider: m.SsoProvider, base_url: str, next_path: str | None) -> tuple[str, str]:
    """(the identity provider's sign-in URL with the AuthnRequest, the state cookie's value)."""
    cfg = config(provider)
    if not cfg.sso_url or not cfg.certificates:
        raise SsoError("it has no single sign-on URL or certificate yet")
    request_id = "_" + secrets.token_hex(20)
    relay = secrets.token_urlsafe(24)
    policy = f' Format={quoteattr(cfg.name_id_format)}' if cfg.name_id_format else ""
    request = (
        f'<samlp:AuthnRequest xmlns:samlp="{SAMLP}" xmlns:saml="{SAML}" ID="{request_id}" Version="2.0" '
        f'IssueInstant="{_instant()}" Destination={quoteattr(cfg.sso_url)} ProtocolBinding="{POST}" '
        f'AssertionConsumerServiceURL={quoteattr(acs_url(base_url, provider.id))}>'
        f'<saml:Issuer>{escape(sp_entity_id(provider, base_url))}</saml:Issuer>'
        f'<samlp:NameIDPolicy AllowCreate="true"{policy}/>'
        '</samlp:AuthnRequest>'
    )
    deflate = zlib.compressobj(9, zlib.DEFLATED, -15)
    encoded = base64.b64encode(deflate.compress(request.encode()) + deflate.flush()).decode()
    cookie = jwt.encode(
        {"p": provider.id, "s": relay, "rid": request_id, "next": safe_next(next_path), "exp": int(time.time()) + STATE_TTL},
        settings.secret_key, algorithm="HS256",
    )
    sep = "&" if "?" in cfg.sso_url else "?"
    return f"{cfg.sso_url}{sep}{urlencode({'SAMLRequest': encoded, 'RelayState': relay})}", cookie


# ---------- the response ----------


def _time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        t = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise SsoError(f"the response has a time Castor can't read: {value}") from exc
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _text(el: Any) -> str:
    return (el.text or "").strip() if el is not None else ""


def _parse(raw: bytes) -> Any:
    # defusedxml first: it refuses DTDs and entities outright, so lxml below never meets one.
    try:
        SafeET.fromstring(raw, forbid_dtd=True)
    except (SafeET.ParseError, DefusedXmlException) as exc:
        raise SsoError("the provider's response isn't XML Castor will read") from exc
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False, remove_comments=False)
    return etree.fromstring(raw, parser)


def _verify(raw: bytes, root: Any, certs: list[str]) -> Any:
    """The signed Assertion element: signed itself, or inside a signed Response. Only what the
    signature covers is returned, so nothing unsigned is ever read as the person's details."""
    assertions = root.findall(f"{{{SAML}}}Assertion")
    if root.find(f"{{{SAML}}}EncryptedAssertion") is not None or root.find(f".//{{{XENC}}}EncryptedData") is not None:
        raise SsoError("the assertion is encrypted, which Castor doesn't support yet; turn assertion encryption off for Castor")
    if len(assertions) != 1 or len(root.findall(f".//{{{SAML}}}Assertion")) != 1:
        raise SsoError("the response should hold exactly one assertion")
    if assertions[0].find(f"{{{DS}}}Signature") is not None:
        location = f"./{{{SAML}}}Assertion/"
    elif root.find(f"{{{DS}}}Signature") is not None:
        location = "./"
    else:
        raise SsoError("the response isn't signed; have the provider sign the assertion")
    expect = SignatureConfiguration(location=location)
    last: Exception | None = None
    for pem in certs:
        try:
            result = XMLVerifier().verify(raw, x509_cert=pem, expect_config=expect)
        except (SignXMLException, ValueError) as exc:
            last = exc
            continue
        signed = result.signed_xml  # type: ignore[union-attr]
        if signed is None:
            break
        if signed.tag == f"{{{SAML}}}Assertion":
            return signed
        if signed.tag == f"{{{SAMLP}}}Response":
            inner = signed.findall(f"{{{SAML}}}Assertion")
            if len(inner) == 1:
                return inner[0]
        raise SsoError("the signature doesn't cover the assertion")
    reason = f" ({last})" if last else ""
    raise SsoError(f"the response's signature didn't check out against the provider's certificates{reason}")


def _attributes(assertion: Any) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for attr in assertion.iter(f"{{{SAML}}}Attribute"):
        values = [_text(v) for v in attr.findall(f"{{{SAML}}}AttributeValue") if _text(v)]
        for key in {attr.get("Name"), attr.get("FriendlyName")} - {None, ""}:
            found.setdefault(str(key).lower(), []).extend(values)
    return found


def _first(attrs: dict[str, list[str]], *names: str) -> str:
    for name in names:
        if name and (values := attrs.get(name.lower())):
            return values[0]
    return ""


def read_response(provider: m.SsoProvider, base_url: str, encoded: str) -> dict[str, Any]:
    """Check a SAMLResponse as far as it can be without the browser's cookie.
    Returns {"claims": …, "rid": the request it answers}."""
    cfg = config(provider)
    if not cfg.certificates:
        raise SsoError("it has no certificate to check responses with")
    try:
        raw = base64.b64decode("".join(encoded.split()), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SsoError("the provider's response isn't base64") from exc
    if len(raw) > MAX_RESPONSE:
        raise SsoError("the provider's response is too large")
    root = _parse(raw)
    if root.tag != f"{{{SAMLP}}}Response":
        raise SsoError("that isn't a SAML response")

    status = root.find(f"{{{SAMLP}}}Status/{{{SAMLP}}}StatusCode")
    if status is None or status.get("Value") != SUCCESS:
        inner = status.find(f"{{{SAMLP}}}StatusCode") if status is not None else None
        code = (inner.get("Value") if inner is not None else None) or (status.get("Value") if status is not None else "no status")
        message = _text(root.find(f"{{{SAMLP}}}Status/{{{SAMLP}}}StatusMessage"))
        raise SsoError(f"the provider didn't sign you in: {message or code.rsplit(':', 1)[-1]}")
    acs = acs_url(base_url, provider.id)
    if root.get("Destination") and root.get("Destination") != acs:
        raise SsoError(f"the response is for {root.get('Destination')}, not this Castor ({acs})")

    assertion = _verify(raw, root, cfg.certificates)
    now = datetime.now(UTC)
    leeway = timedelta(seconds=LEEWAY)

    if _text(assertion.find(f"{{{SAML}}}Issuer")) != provider.issuer:
        raise SsoError(f"the assertion was issued by {_text(assertion.find(f'{{{SAML}}}Issuer')) or 'nobody'}, not {provider.issuer}")
    outer_issuer = _text(root.find(f"{{{SAML}}}Issuer"))
    if outer_issuer and outer_issuer != provider.issuer:
        raise SsoError(f"the response was issued by {outer_issuer}, not {provider.issuer}")

    conditions = assertion.find(f"{{{SAML}}}Conditions")
    if conditions is not None:
        before, after = _time(conditions.get("NotBefore")), _time(conditions.get("NotOnOrAfter"))
        if before and now + leeway < before:
            raise SsoError("the assertion isn't valid yet (is the server's clock right?)")
        if after and now - leeway >= after:
            raise SsoError("the assertion has expired; try again")
        audience = sp_entity_id(provider, base_url)
        for restriction in conditions.findall(f"{{{SAML}}}AudienceRestriction"):
            if audience not in [_text(a) for a in restriction.findall(f"{{{SAML}}}Audience")]:
                raise SsoError(f"the assertion is for another service, not {audience}")

    subject = assertion.find(f"{{{SAML}}}Subject")
    if subject is None:
        raise SsoError("the assertion has no subject")
    answered = None
    for confirmation in subject.findall(f"{{{SAML}}}SubjectConfirmation"):
        data = confirmation.find(f"{{{SAML}}}SubjectConfirmationData")
        if confirmation.get("Method") != BEARER or data is None:
            continue
        until = _time(data.get("NotOnOrAfter"))
        if data.get("Recipient") != acs or not until or now - leeway >= until or not data.get("InResponseTo"):
            continue
        answered = data.get("InResponseTo")
        break
    if answered is None:
        raise SsoError("the assertion isn't addressed to this Castor, has expired, or answers no sign-in started here")
    if root.get("InResponseTo") and root.get("InResponseTo") != answered:
        raise SsoError("the response and its assertion answer different sign-ins")

    assertion_id = assertion.get("ID") or ""
    tick = time.monotonic()
    for key in [k for k, t in _seen.items() if t < tick]:
        _seen.pop(key, None)
    if not assertion_id or assertion_id in _seen:
        raise SsoError("that response was already used; sign in again")
    _seen[assertion_id] = tick + STATE_TTL + LEEWAY

    name_id = subject.find(f"{{{SAML}}}NameID")
    attrs = _attributes(assertion)
    if cfg.subject_attribute:
        sub = _first(attrs, cfg.subject_attribute)
        if not sub:
            raise SsoError(f"the assertion has no {cfg.subject_attribute} attribute to know you by")
    else:
        if name_id is not None and name_id.get("Format") == TRANSIENT:
            raise SsoError("the provider sent a transient NameID, which changes at every sign-in: send a persistent one, "
                           "or set the subject attribute")
        sub = _text(name_id)
        if not sub:
            raise SsoError("the assertion has no NameID")
    email = _first(attrs, cfg.email_attribute, *EMAIL_ATTRS)
    if not email and name_id is not None and name_id.get("Format") == EMAIL_FORMAT:
        email = _text(name_id)
    name = _first(attrs, cfg.name_attribute, *NAME_ATTRS) or " ".join(
        x for x in (_first(attrs, *GIVEN_ATTRS), _first(attrs, *SURNAME_ATTRS)) if x)
    claims: dict[str, Any] = {
        "sub": sub, "email": email, "email_verified": bool(email) and cfg.trust_email,
        "name": name, "preferred_username": _first(attrs, cfg.username_attribute, *USERNAME_ATTRS),
    }
    if provider.groups_claim and provider.groups_claim.lower() in attrs:  # groups follow only when sent
        claims[provider.groups_claim] = attrs[provider.groups_claim.lower()]
    return {"claims": claims, "rid": answered}


def seal(provider_id: str, result: dict[str, Any], relay: str) -> str:
    """The checked response, for the hop from the ACS to …/done (encrypted: it holds an email)."""
    body = {"p": provider_id, "c": result["claims"], "rid": result["rid"], "s": relay, "exp": int(time.time()) + RESULT_TTL}
    return vault.encrypt(json.dumps(body).encode())


def unseal(provider_id: str, token: str | None) -> dict[str, Any]:
    if not token:
        raise SsoError("the provider sent nothing back")
    try:
        body = json.loads(vault.decrypt(token))
    except (vault.VaultError, ValueError) as exc:
        raise SsoError("the sign-in didn't come back intact; try again") from exc
    if body.get("p") != provider_id or int(body.get("exp", 0)) < time.time():
        raise SsoError("the sign-in took too long; try again")
    return body


def finish(provider_id: str, sealed: dict[str, Any], saved: dict[str, Any]) -> dict[str, Any]:
    """The claims, once the response is known to answer the sign-in this browser started."""
    expected = str(saved.get("rid") or "")
    if not expected or not secrets.compare_digest(expected, str(sealed.get("rid") or "")):
        raise SsoError("the response answers a sign-in that wasn't started in this browser; try again")
    return sealed["c"]
