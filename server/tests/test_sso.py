"""Single sign-on with OpenID Connect, against a fake provider in a thread: discovery, keys,
codes with PKCE, signed id_tokens and UserInfo, so the whole round trip runs for real."""

import base64
import hashlib
import json
import secrets
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

CLIENT_ID, CLIENT_SECRET = "castor-test", "s3cret"


class FakeIdP:
    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "k1"
        self.codes: dict[str, dict] = {}
        self.userinfo: dict[str, dict] = {}
        idp = self

        class Handler(BaseHTTPRequestHandler):
            def _json(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                path = urlparse(self.path).path
                if path == "/.well-known/openid-configuration":
                    return self._json(200, {
                        "issuer": idp.issuer, "authorization_endpoint": f"{idp.issuer}/authorize",
                        "token_endpoint": f"{idp.issuer}/token", "jwks_uri": f"{idp.issuer}/jwks",
                        "userinfo_endpoint": f"{idp.issuer}/userinfo",
                        "token_endpoint_auth_methods_supported": ["client_secret_basic"],
                    })
                if path == "/jwks":
                    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(idp.key.public_key()))
                    return self._json(200, {"keys": [{**jwk, "kid": idp.kid, "alg": "RS256", "use": "sig"}]})
                if path == "/userinfo":
                    token = self.headers.get("Authorization", "").removeprefix("Bearer ")
                    return self._json(200, idp.userinfo.get(token, {}))
                return self._json(404, {})

            def do_POST(self):
                form = parse_qs(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode())
                one = {k: v[0] for k, v in form.items()}
                basic = base64.b64decode(self.headers.get("Authorization", "Basic ").split()[1] or b"").decode()
                if basic != f"{CLIENT_ID}:{CLIENT_SECRET}":
                    return self._json(401, {"error": "invalid_client"})
                grant = idp.codes.pop(one.get("code", ""), None)
                if grant is None:
                    return self._json(400, {"error": "invalid_grant", "error_description": "unknown code"})
                challenge = base64.urlsafe_b64encode(hashlib.sha256(one["code_verifier"].encode()).digest()).rstrip(b"=").decode()
                if challenge != grant["challenge"] or one["redirect_uri"] != grant["redirect_uri"]:
                    return self._json(400, {"error": "invalid_grant", "error_description": "PKCE or redirect_uri mismatch"})
                now = int(time.time())
                id_token = jwt.encode({"iss": idp.issuer, "aud": CLIENT_ID, "iat": now, "exp": now + 300, "nonce": grant["nonce"],
                                       **grant["claims"]}, idp.key, algorithm="RS256", headers={"kid": idp.kid})
                access = secrets.token_urlsafe(16)
                idp.userinfo[access] = grant.get("userinfo", {})
                return self._json(200, {"id_token": id_token, "access_token": access, "token_type": "Bearer"})

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.issuer = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def authorize(self, url: str, claims: dict, userinfo: dict | None = None, nonce: str | None = None) -> tuple[str, str]:
        """What the provider does when the person signs in there: (code, state)."""
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        assert q["response_type"] == "code" and q["client_id"] == CLIENT_ID and q["code_challenge_method"] == "S256"
        code = secrets.token_urlsafe(12)
        self.codes[code] = {"challenge": q["code_challenge"], "nonce": nonce or q["nonce"], "redirect_uri": q["redirect_uri"],
                            "claims": claims, "userinfo": userinfo or {}}
        return code, q["state"]


@pytest.fixture(scope="module")
def idp():
    server = FakeIdP()
    yield server
    server.server.shutdown()


@pytest.fixture
def provider(client, auth, idp):
    pid = f"t{uuid.uuid4().hex[:6]}"
    r = client.post("/api/sso-providers", json={"id": pid, "name": "Test SSO", "issuer": idp.issuer, "client_id": CLIENT_ID,
                                                "client_secret": CLIENT_SECRET, "default_role": "viewer"}, headers=auth)
    assert r.status_code == 201, r.text
    yield r.json()
    client.delete(f"/api/sso-providers/{pid}", headers=auth)
    client.cookies.clear()


def _sign_in(client, idp, provider, claims, userinfo=None, nonce=None, next_path="/projects"):
    """The browser's round trip; returns the callback's response (a redirect)."""
    client.cookies.clear()
    start = client.get(f"/api/auth/sso/{provider['id']}/start", params={"next": next_path}, follow_redirects=False)
    assert start.status_code == 303 and start.headers["location"].startswith(f"{idp.issuer}/authorize?")
    code, state = idp.authorize(start.headers["location"], claims, userinfo, nonce)
    return client.get(f"/api/auth/sso/{provider['id']}/callback", params={"code": code, "state": state}, follow_redirects=False)


def _me(client):
    from castor_server.config import settings

    token = client.cookies.get(settings.cookie_name)
    return client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).json() if token else None


def test_provider_settings_keep_the_secret(client, auth, provider, idp):
    assert provider["has_secret"] and "client_secret" not in provider
    assert provider["redirect_uri"].endswith(f"/api/auth/sso/{provider['id']}/callback")
    listed = client.get("/api/sso-providers", headers=auth).text
    assert CLIENT_SECRET not in listed
    assert client.post("/api/sso-providers/test", json={"issuer": idp.issuer}, headers=auth).json()["ok"] is True
    assert client.post("/api/sso-providers/test", json={"issuer": "http://127.0.0.1:9"}, headers=auth).json()["ok"] is False
    options = client.get("/api/auth/sso").json()  # public, for the sign-in page
    assert {"id": provider["id"], "name": "Test SSO"} in options["providers"] and options["password_login"] is True


def test_first_sign_in_makes_an_account_and_groups_set_the_role(client, auth, idp, provider):
    sub = uuid.uuid4().hex
    claims = {"sub": sub, "preferred_username": f"ada-{sub[:4]}", "name": "Ada Lovelace", "email": f"ada-{sub[:4]}@example.com",
              "email_verified": True}
    r = _sign_in(client, idp, provider, claims)
    assert r.status_code == 303 and r.headers["location"] == "/projects", r.headers
    me = _me(client)
    assert me["username"] == f"ada-{sub[:4]}" and me["role"] == "viewer" and me["email"] == claims["email"]

    # With a role map, groups (here from UserInfo) decide the role at every sign-in.
    body = {**{k: provider[k] for k in ("id", "name", "issuer", "client_id", "scopes", "default_role")},
            "role_map": [{"group": "castor-admins", "role": "admin"}, {"group": "castor-ops", "role": "operator"}]}
    assert client.put(f"/api/sso-providers/{provider['id']}", json=body, headers=auth).status_code == 200
    _sign_in(client, idp, provider, claims, userinfo={"sub": sub, "groups": ["castor-ops", "other"]})
    assert _me(client)["role"] == "operator" and _me(client)["id"] == me["id"]  # the same account
    _sign_in(client, idp, provider, claims, userinfo={"sub": sub, "groups": []})
    assert _me(client)["role"] == "viewer"


def test_who_may_sign_in(client, auth, idp, provider):
    body = {**{k: provider[k] for k in ("id", "name", "issuer", "client_id", "scopes", "default_role")},
            "allowed_domains": ["corp.example"], "allowed_groups": ["castor-users"]}
    client.put(f"/api/sso-providers/{provider['id']}", json=body, headers=auth)
    sub = uuid.uuid4().hex
    outsider = _sign_in(client, idp, provider, {"sub": sub, "email": "x@gmail.com", "email_verified": True, "groups": ["castor-users"]})
    assert outsider.headers["location"].startswith("/login?sso_error=") and "domain" in outsider.headers["location"]
    unverified = _sign_in(client, idp, provider, {"sub": sub, "email": "x@corp.example", "email_verified": False, "groups": ["castor-users"]})
    assert "domain" in unverified.headers["location"]  # an unverified email proves nothing
    no_group = _sign_in(client, idp, provider, {"sub": sub, "email": "x@corp.example", "email_verified": True, "groups": ["else"]})
    assert "group" in no_group.headers["location"]
    ok = _sign_in(client, idp, provider, {"sub": sub, "email": "x@corp.example", "email_verified": True, "groups": ["castor-users"]})
    assert ok.headers["location"] == "/projects" and _me(client)


def test_links_an_existing_account_only_by_verified_email(client, auth, idp, provider):
    w = uuid.uuid4().hex[:6]
    email = f"grace-{w}@example.com"
    made = client.post("/api/users", json={"username": f"grace-{w}", "password": "longenough", "email": email}, headers=auth).json()
    body = {**{k: provider[k] for k in ("id", "name", "issuer", "client_id", "scopes", "default_role")}, "link_by_email": True}
    client.put(f"/api/sso-providers/{provider['id']}", json=body, headers=auth)

    _sign_in(client, idp, provider, {"sub": f"u-{w}", "email": email, "email_verified": False, "preferred_username": f"gh-{w}"})
    assert _me(client)["id"] != made["id"] and _me(client)["email"] == ""  # not vouched for: a separate account
    _sign_in(client, idp, provider, {"sub": f"v-{w}", "email": email.upper(), "email_verified": True})
    assert _me(client)["id"] == made["id"]  # verified: linked
    assert client.post("/api/users", json={"username": f"dup-{w}", "password": "longenough", "email": email}, headers=auth).status_code == 409


def test_the_round_trip_is_checked(client, auth, idp, provider):
    claims = {"sub": uuid.uuid4().hex, "email": "z@example.com", "email_verified": True}
    # A replayed or foreign state, a nonce from another sign-in, an unknown code.
    client.cookies.clear()
    start = client.get(f"/api/auth/sso/{provider['id']}/start", follow_redirects=False)
    code, _ = idp.authorize(start.headers["location"], claims)
    bad_state = client.get(f"/api/auth/sso/{provider['id']}/callback", params={"code": code, "state": "nope"}, follow_redirects=False)
    assert "sso_error" in bad_state.headers["location"] and _me(client) is None
    wrong_nonce = _sign_in(client, idp, provider, claims, nonce="someone-elses")
    assert "nonce" in wrong_nonce.headers["location"] and _me(client) is None
    client.cookies.clear()
    start = client.get(f"/api/auth/sso/{provider['id']}/start", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    unknown = client.get(f"/api/auth/sso/{provider['id']}/callback", params={"code": "made-up", "state": state}, follow_redirects=False)
    assert "refused" in unknown.headers["location"]
    denied = client.get(f"/api/auth/sso/{provider['id']}/callback", params={"error": "access_denied"}, follow_redirects=False)
    assert "access_denied" in denied.headers["location"]
    # Only paths on this site after signing in.
    evil = _sign_in(client, idp, provider, claims, next_path="//evil.example/")
    assert evil.headers["location"] == "/"


def test_password_sign_in_can_be_turned_off_safely(client, auth, idp, provider):
    # The admin hasn't signed in through SSO: refused, so nobody locks themselves out.
    refused = client.patch("/api/settings", json={"password_login": False}, headers=auth)
    assert refused.status_code == 422 and "yourself first" in refused.text
    me = client.get("/api/auth/me", headers=auth).json()
    email = f"admin-{uuid.uuid4().hex[:6]}@example.com"
    client.patch(f"/api/users/{me['id']}", json={"email": email}, headers=auth)
    body = {**{k: provider[k] for k in ("id", "name", "issuer", "client_id", "scopes", "default_role")}, "link_by_email": True}
    client.put(f"/api/sso-providers/{provider['id']}", json=body, headers=auth)
    _sign_in(client, idp, provider, {"sub": uuid.uuid4().hex, "email": email, "email_verified": True})
    assert _me(client)["id"] == me["id"] and _me(client)["role"] == "admin"
    client.cookies.clear()
    try:
        assert client.patch("/api/settings", json={"password_login": False}, headers=auth).status_code == 200
        assert client.get("/api/auth/sso").json()["password_login"] is False
        assert client.post("/api/auth/login", json={"username": "admin", "password": "secret"}).status_code == 403
    finally:
        assert client.patch("/api/settings", json={"password_login": True}, headers=auth).status_code == 200
        client.cookies.clear()
    assert client.post("/api/auth/login", json={"username": "admin", "password": "secret"}).status_code == 200
    client.cookies.clear()


def test_groups_follow_the_providers_groups(client, auth, idp, provider):
    w = uuid.uuid4().hex[:6]
    g = client.post("/api/groups", json={"slug": f"ops-{w}", "name": "Ops", "sso_group": f"castor-ops-{w}"}, headers=auth).json()
    manual = client.post("/api/groups", json={"slug": f"hand-{w}", "name": "By hand"}, headers=auth).json()
    sub = uuid.uuid4().hex
    claims = {"sub": sub, "email": f"o-{w}@example.com", "email_verified": True, "preferred_username": f"o-{w}"}
    _sign_in(client, idp, provider, {**claims, "groups": [f"castor-ops-{w}"]})
    me = _me(client)
    members = lambda gid: {u["id"] for u in client.get(f"/api/groups/{gid}", headers=auth).json()["members"]}
    assert me["id"] in members(g["id"])
    client.put(f"/api/groups/{manual['id']}/members", json=[me["id"]], headers=auth)
    _sign_in(client, idp, provider, {**claims, "groups": ["something-else"]})
    assert me["id"] not in members(g["id"])  # left the provider group: out
    assert me["id"] in members(manual["id"])  # groups managed by hand are left alone
    _sign_in(client, idp, provider, claims)  # no groups sent at all: nothing changes
    assert me["id"] in members(manual["id"])
    client.delete(f"/api/groups/{g['id']}", headers=auth)
    client.delete(f"/api/groups/{manual['id']}", headers=auth)
