"""SAML sign-on: a fake identity provider that signs real responses with signxml, so the whole
round trip runs (AuthnRequest out, signed response in through the ACS, …/done with the cookie),
and the ways a response can be forged or misused are refused."""

import base64
import uuid
import zlib
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse
from xml.sax.saxutils import escape

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from lxml import etree
from signxml import XMLSigner

IDP_ENTITY = "https://idp.example/saml"
SSO_URL = "https://idp.example/saml/sso"
SAMLP = "urn:oasis:names:tc:SAML:2.0:protocol"
SAML = "urn:oasis:names:tc:SAML:2.0:assertion"
PERSISTENT = "urn:oasis:names:tc:SAML:2.0:nameid-format:persistent"


def _keypair(cn: str):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = datetime.now(UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=365)).sign(key, hashes.SHA256()))
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return key_pem, cert.public_bytes(serialization.Encoding.PEM).decode()


def _t(delta: int = 0) -> str:
    return (datetime.now(UTC) + timedelta(seconds=delta)).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeIdP:
    def __init__(self) -> None:
        self.key, self.cert = _keypair("idp.example")
        self.other_key, self.other_cert = _keypair("someone-else")

    def metadata(self) -> str:
        bare = "".join(self.cert.strip().splitlines()[1:-1])
        return f"""<?xml version="1.0"?>
<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata" xmlns:ds="http://www.w3.org/2000/09/xmldsig#" entityID="{IDP_ENTITY}">
  <md:IDPSSODescriptor protocolSupportEnumeration="{SAMLP}">
    <md:KeyDescriptor use="signing"><ds:KeyInfo><ds:X509Data><ds:X509Certificate>{bare}</ds:X509Certificate></ds:X509Data></ds:KeyInfo></md:KeyDescriptor>
    <md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" Location="{SSO_URL}"/>
    <md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" Location="{SSO_URL}/post"/>
  </md:IDPSSODescriptor>
</md:EntityDescriptor>"""

    def assertion(self, *, subject: str, request_id: str, acs: str, audience: str, attrs: dict | None = None,
                  issuer: str = IDP_ENTITY, name_format: str = PERSISTENT, not_after: int = 300, recipient: str | None = None) -> str:
        values = "".join(
            f'<saml:Attribute Name="{escape(k)}">' + "".join(f"<saml:AttributeValue>{escape(v)}</saml:AttributeValue>" for v in vs)
            + "</saml:Attribute>" for k, vs in (attrs or {}).items())
        return (
            f'<saml:Assertion xmlns:saml="{SAML}" ID="_a{uuid.uuid4().hex}" Version="2.0" IssueInstant="{_t()}">'
            f"<saml:Issuer>{issuer}</saml:Issuer>"
            f'<saml:Subject><saml:NameID Format="{name_format}">{escape(subject)}</saml:NameID>'
            f'<saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">'
            f'<saml:SubjectConfirmationData InResponseTo="{request_id}" Recipient="{recipient or acs}" NotOnOrAfter="{_t(not_after)}"/>'
            f"</saml:SubjectConfirmation></saml:Subject>"
            f'<saml:Conditions NotBefore="{_t(-60)}" NotOnOrAfter="{_t(not_after)}">'
            f"<saml:AudienceRestriction><saml:Audience>{escape(audience)}</saml:Audience></saml:AudienceRestriction></saml:Conditions>"
            f'<saml:AuthnStatement AuthnInstant="{_t()}"><saml:AuthnContext><saml:AuthnContextClassRef>'
            f"urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport</saml:AuthnContextClassRef></saml:AuthnContext></saml:AuthnStatement>"
            f"<saml:AttributeStatement>{values}</saml:AttributeStatement>"
            f"</saml:Assertion>"
        )

    def sign(self, xml: str, key: bytes | None = None, cert: str | None = None) -> str:
        el = etree.fromstring(xml.encode())
        signed = XMLSigner(signature_algorithm="rsa-sha256", digest_algorithm="sha256",
                           c14n_algorithm="http://www.w3.org/2001/10/xml-exc-c14n#").sign(el, key=key or self.key, cert=cert or self.cert)
        return etree.tostring(signed).decode()

    def response(self, inner: str, request_id: str, acs: str, *, sign_response: bool = False) -> str:
        xml = (f'<samlp:Response xmlns:samlp="{SAMLP}" xmlns:saml="{SAML}" ID="_r{uuid.uuid4().hex}" Version="2.0" '
               f'IssueInstant="{_t()}" Destination="{acs}" InResponseTo="{request_id}">'
               f"<saml:Issuer>{IDP_ENTITY}</saml:Issuer>"
               f'<samlp:Status><samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/></samlp:Status>'
               f"{inner}</samlp:Response>")
        return self.sign(xml) if sign_response else xml


@pytest.fixture(scope="module")
def idp():
    return FakeIdP()


@pytest.fixture
def provider(client, auth, idp):
    pid = f"s{uuid.uuid4().hex[:6]}"
    read = client.post("/api/sso-providers/saml/metadata", json={"xml": idp.metadata()}, headers=auth).json()
    assert read["ok"], read
    body = {"id": pid, "name": "Test SAML", "protocol": "saml", "issuer": read["entity_id"], "default_role": "viewer",
            "groups_claim": "groups", "saml": {"sso_url": read["sso_url"], "certificates": read["certificates"]}}
    r = client.post("/api/sso-providers", json=body, headers=auth)
    assert r.status_code == 201, r.text
    yield r.json()
    client.delete(f"/api/sso-providers/{pid}", headers=auth)
    client.cookies.clear()


def _request(location: str) -> tuple[etree._Element, str]:
    assert location.startswith(SSO_URL + "?")
    q = {k: v[0] for k, v in parse_qs(urlparse(location).query).items()}
    xml = zlib.decompress(base64.b64decode(q["SAMLRequest"]), -15)
    return etree.fromstring(xml), q["RelayState"]


def _start(client, provider):
    client.cookies.clear()
    start = client.get(f"/api/auth/sso/{provider['id']}/start", params={"next": "/projects"}, follow_redirects=False)
    assert start.status_code == 303, start.text
    req, relay = _request(start.headers["location"])
    return req.get("ID"), req.get("AssertionConsumerServiceURL"), relay


def _post(client, provider, xml: str, relay: str):
    """The identity provider's POST to the ACS, then (if it passed) the hop to …/done."""
    path = f"/api/auth/sso/{provider['id']}/saml/acs"
    acs = client.post(path, data={"SAMLResponse": base64.b64encode(xml.encode()).decode(), "RelayState": relay}, follow_redirects=False)
    assert acs.status_code == 303
    if "/saml/done?" not in acs.headers["location"]:
        return acs
    return client.get(acs.headers["location"], follow_redirects=False)


def _sign_in(client, idp, provider, subject: str, attrs: dict | None = None, **kw):
    rid, acs, relay = _start(client, provider)
    signed = idp.sign(idp.assertion(subject=subject, request_id=rid, acs=acs, audience=provider["saml"]["entity_id"], attrs=attrs, **kw))
    return _post(client, provider, idp.response(signed, rid, acs), relay)


def _me(client):
    from castor_server.config import settings

    token = client.cookies.get(settings.cookie_name)
    return client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).json() if token else None


def _error(r) -> str:
    loc = r.headers["location"]
    assert loc.startswith("/login?sso_error="), loc
    return parse_qs(urlparse(loc).query)["sso_error"][0]


def test_provider_settings_and_castors_metadata(client, auth, provider, idp):
    s = provider["saml"]
    assert provider["protocol"] == "saml" and provider["issuer"] == IDP_ENTITY and s["sso_url"] == SSO_URL
    assert s["acs_url"].endswith(f"/api/auth/sso/{provider['id']}/saml/acs") and provider["redirect_uri"] == s["acs_url"]
    assert s["entity_id"] == s["metadata"] and s["described"][0]["subject"] == "CN=idp.example"
    md = client.get(f"/api/auth/sso/{provider['id']}/saml/metadata")
    assert md.status_code == 200 and md.headers["content-type"].startswith("application/samlmetadata+xml")
    root = etree.fromstring(md.content)
    assert root.get("entityID") == s["entity_id"] and s["acs_url"] in md.text and 'WantAssertionsSigned="true"' in md.text
    assert any(p["id"] == provider["id"] and p["name"] == "Test SAML" for p in client.get("/api/auth/sso").json()["providers"])
    # The request names Castor and where to answer.
    req, _ = _request(client.get(f"/api/auth/sso/{provider['id']}/start", follow_redirects=False).headers["location"])
    assert req.findtext(f"{{{SAML}}}Issuer") == s["entity_id"] and req.get("Destination") == SSO_URL
    # A provider needs a sign-on URL and a certificate it signs with; its protocol stays.
    body = {k: provider[k] for k in ("id", "name", "protocol", "issuer", "default_role")}
    assert client.put(f"/api/sso-providers/{provider['id']}", json={**body, "saml": {"sso_url": SSO_URL, "certificates": ["nope"]}},
                      headers=auth).status_code == 422
    assert client.put(f"/api/sso-providers/{provider['id']}", json={**body, "saml": {"sso_url": SSO_URL}}, headers=auth).status_code == 422
    assert client.put(f"/api/sso-providers/{provider['id']}", json={**body, "protocol": "oidc", "client_id": "x"},
                      headers=auth).status_code == 422
    bad = client.post("/api/sso-providers/saml/metadata", json={"xml": "<x/>"}, headers=auth).json()
    assert bad["ok"] is False and "no identity provider" in bad["error"]
    dtd = client.post("/api/sso-providers/saml/metadata", json={"xml": '<!DOCTYPE x [<!ENTITY e "x">]><x>&e;</x>'}, headers=auth).json()
    assert dtd["ok"] is False


def test_sign_in_makes_an_account_and_groups_decide(client, auth, idp, provider):
    w = uuid.uuid4().hex[:6]
    attrs = {"email": [f"ada-{w}@example.com"], "displayName": ["Ada Lovelace"], "uid": [f"ada-{w}"], "groups": ["castor-ops"]}
    r = _sign_in(client, idp, provider, f"ada-{w}-id", attrs)
    assert r.status_code == 303 and r.headers["location"] == "/projects", r.headers
    me = _me(client)
    assert me["username"] == f"ada-{w}" and me["display_name"] == "Ada Lovelace" and me["email"] == f"ada-{w}@example.com"
    assert me["role"] == "viewer"

    g = client.post("/api/groups", json={"slug": f"ops-{w}", "name": "Ops", "sso_group": "castor-ops"}, headers=auth).json()
    body = {k: provider[k] for k in ("id", "name", "protocol", "issuer", "default_role", "groups_claim")}
    body["saml"] = {"sso_url": SSO_URL, "certificates": provider["saml"]["certificates"]}
    body["role_map"] = [{"group": "castor-ops", "role": "operator"}]
    assert client.put(f"/api/sso-providers/{provider['id']}", json=body, headers=auth).status_code == 200
    _sign_in(client, idp, provider, f"ada-{w}-id", attrs)
    assert _me(client)["id"] == me["id"] and _me(client)["role"] == "operator"
    members = {u["id"] for u in client.get(f"/api/groups/{g['id']}", headers=auth).json()["members"]}
    assert me["id"] in members  # the provider's groups put people in Castor groups too
    _sign_in(client, idp, provider, f"ada-{w}-id", {**attrs, "groups": ["else"]})
    assert _me(client)["role"] == "viewer"
    assert me["id"] not in {u["id"] for u in client.get(f"/api/groups/{g['id']}", headers=auth).json()["members"]}
    client.delete(f"/api/groups/{g['id']}", headers=auth)


def test_signed_response_and_metadata_url_style_certificates(client, auth, idp, provider):
    """A provider that signs the whole Response instead of the assertion."""
    rid, acs, relay = _start(client, provider)
    inner = idp.assertion(subject=f"r-{uuid.uuid4().hex[:6]}", request_id=rid, acs=acs, audience=provider["saml"]["entity_id"])
    r = _post(client, provider, idp.response(inner, rid, acs, sign_response=True), relay)
    assert r.headers["location"] == "/projects" and _me(client)


def test_forged_and_misused_responses_are_refused(client, auth, idp, provider):
    audience = provider["saml"]["entity_id"]

    def attempt(make, relay_override=None):
        rid, acs, relay = _start(client, provider)
        r = _post(client, provider, make(rid, acs), relay_override or relay)
        assert _me(client) is None, "signed in when it shouldn't have"
        return _error(r)

    good = lambda rid, acs, **kw: idp.assertion(subject="alice", request_id=rid, acs=acs, audience=audience, **kw)
    # Signed by someone else.
    assert "signature" in attempt(lambda rid, acs: idp.response(idp.sign(good(rid, acs), idp.other_key, idp.other_cert), rid, acs))
    # Not signed at all.
    assert "isn't signed" in attempt(lambda rid, acs: idp.response(good(rid, acs), rid, acs))
    # Changed after signing.
    assert "signature" in attempt(lambda rid, acs: idp.response(idp.sign(good(rid, acs)).replace(">alice<", ">admin<"), rid, acs))

    # Signature wrapping: a genuinely signed assertion carried along with an unsigned one.
    def wrapped(rid, acs):
        evil = idp.assertion(subject="mallory", request_id=rid, acs=acs, audience=audience)
        return idp.response(evil.replace("</saml:Assertion>", idp.sign(good(rid, acs)) + "</saml:Assertion>"), rid, acs)
    assert "exactly one assertion" in attempt(wrapped)
    assert "exactly one assertion" in attempt(lambda rid, acs: idp.response(idp.sign(good(rid, acs)) + good(rid, acs), rid, acs))

    # For another service, expired, sent somewhere else, issued by someone else.
    assert "another service" in attempt(lambda rid, acs: idp.response(idp.sign(
        idp.assertion(subject="alice", request_id=rid, acs=acs, audience="https://other.example")), rid, acs))
    assert "expired" in attempt(lambda rid, acs: idp.response(idp.sign(good(rid, acs, not_after=-600)), rid, acs))
    assert "addressed" in attempt(lambda rid, acs: idp.response(idp.sign(good(rid, acs, recipient="https://other.example/acs")), rid, acs))
    assert "issued by" in attempt(lambda rid, acs: idp.response(idp.sign(good(rid, acs, issuer="https://evil.example")), rid, acs))
    # A transient NameID would make a new account at every sign-in.
    assert "transient" in attempt(lambda rid, acs: idp.response(idp.sign(
        good(rid, acs, name_format="urn:oasis:names:tc:SAML:2.0:nameid-format:transient")), rid, acs))
    # An answer to another sign-in, and a RelayState that isn't this browser's.
    assert "wasn't started in this browser" in attempt(lambda rid, acs: idp.response(idp.sign(good("_other", acs)), "_other", acs))
    assert "didn't match" in attempt(lambda rid, acs: idp.response(idp.sign(good(rid, acs)), rid, acs), relay_override="forged")
    # XML with a DTD never reaches the parser that checks signatures.
    assert "isn't XML" in attempt(lambda rid, acs: '<!DOCTYPE r [<!ENTITY e "x">]>' + idp.response(idp.sign(good(rid, acs)), rid, acs))
    # The provider said no.
    def refused(rid, acs):
        return (f'<samlp:Response xmlns:samlp="{SAMLP}" ID="_x" Version="2.0" IssueInstant="{_t()}"><samlp:Status>'
                '<samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Responder"/>'
                "<samlp:StatusMessage>No access for you</samlp:StatusMessage></samlp:Status></samlp:Response>")
    assert "No access for you" in attempt(refused)


def test_a_response_works_once_and_only_in_the_browser_that_asked(client, auth, idp, provider):
    rid, acs, relay = _start(client, provider)
    xml = idp.response(idp.sign(idp.assertion(subject="bob", request_id=rid, acs=acs, audience=provider["saml"]["entity_id"])), rid, acs)
    path = f"/api/auth/sso/{provider['id']}/saml/acs"
    data = {"SAMLResponse": base64.b64encode(xml.encode()).decode(), "RelayState": relay}
    acs_r = client.post(path, data=data, follow_redirects=False)
    done = acs_r.headers["location"]
    # Without the state cookie (another browser, or a response nobody asked for): refused.
    saved = dict(client.cookies)
    client.cookies.clear()
    assert "sso_error" in client.get(done, follow_redirects=False).headers["location"] and _me(client) is None
    for k, v in saved.items():
        client.cookies.set(k, v, path="/api/auth/sso")
    assert client.get(done, follow_redirects=False).headers["location"] == "/projects" and _me(client)
    # The same response again (a replay): refused at the ACS.
    _start(client, provider)
    again = client.post(path, data=data, follow_redirects=False)
    assert "already used" in _error(again)
    # A tampered hand-on token.
    assert "intact" in _error(client.get(f"/api/auth/sso/{provider['id']}/saml/done?r=garbage", follow_redirects=False))
