"""Personal API tokens (4a): every route has a scope, a token acts as its user narrowed to
its scopes and projects, and tokens can't manage tokens or the account."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from starlette.websockets import WebSocketDisconnect

from castor_server import api_tokens
from castor_server.models import ApiToken


def test_every_route_has_a_scope(client):
    """A new route must be given a scope in api_tokens.RULES (until then, tokens get 403)."""
    paths = client.get("/api/openapi.json").json()["paths"]
    missing = [(method, path) for path, ops in paths.items() for method in ops
               if api_tokens.required_scope(method, path) is None]
    assert missing == []
    for socket in ("/ws/ui", "/ws/jobs/{job_id}/terminal"):
        assert api_tokens.required_scope("GET", socket) is not None
    # The docs say which scope each operation needs.
    op = paths["/api/tasks/{task_id}/run"]["post"]
    assert op["x-castor-scope"] == "jobs:write" and "jobs:write" in op["description"]


def test_scope_rules():
    rs = api_tokens.required_scope
    assert rs("GET", "/api/projects") == "projects:read"
    assert rs("POST", "/api/projects/x/tasks") == "projects:write"
    assert rs("POST", "/api/tasks/x/run") == "jobs:write"
    assert rs("POST", "/api/playbooks/validate") == "library:read"
    assert rs("PUT", "/api/playbooks/x") == "library:write"
    assert rs("POST", "/api/variables/sets/x/reveal/password") == "secrets:reveal"
    assert rs("POST", "/api/tokens") == api_tokens.SESSION
    assert rs("POST", "/api/auth/password") == api_tokens.SESSION
    assert rs("GET", "/api/nothing-here") is None
    assert api_tokens.grants(["jobs:write"], "jobs:read")
    assert not api_tokens.grants(["jobs:read"], "jobs:write")
    assert not api_tokens.grants(api_tokens.EVERYTHING, api_tokens.SESSION)


def test_expired_and_revoked():
    now = datetime.now(UTC)
    assert api_tokens.usable(ApiToken(expires_at=now + timedelta(days=1)), now) is None
    assert "expired" in api_tokens.usable(ApiToken(expires_at=now - timedelta(seconds=1)), now)
    assert "revoked" in api_tokens.usable(ApiToken(revoked_at=now), now)


def _project(client, auth, name="Tok"):
    return client.post("/api/projects", json={"codename": f"AT-{uuid.uuid4().hex[:6]}", "name": name, "kind": "internal"},
                       headers=auth).json()


def _token(client, auth, **body):
    r = client.post("/api/tokens", json={"name": "script", **body}, headers=auth)
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["token"].startswith("castor_pat_") and created["token"].startswith(created["prefix"])
    return created, {"Authorization": f"Bearer {created['token']}"}


def test_token_is_narrowed_to_scopes_and_projects(client, auth):
    mine, other = _project(client, auth), _project(client, auth)
    created, tok = _token(client, auth, scopes=["projects:read"], project_ids=[mine["id"]])
    assert created["projects"] == [mine["codename"]]

    assert client.get("/api/auth/me", headers=tok).json()["username"] == "admin"
    seen = {p["id"] for p in client.get("/api/projects", headers=tok).json()}
    assert seen == {mine["id"]}  # an admin's token still sees only its project
    assert client.get(f"/api/projects/{mine['id']}/board", headers=tok).status_code == 200
    assert client.get(f"/api/projects/{other['id']}/board", headers=tok).status_code == 404

    r = client.post(f"/api/projects/{mine['id']}/tasks", json={"title": "x", "kind": "manual"}, headers=tok)
    assert r.status_code == 403 and "projects:write" in r.json()["detail"]
    assert client.get("/api/commands", headers=tok).status_code == 403
    assert client.get("/api/users", headers=tok).status_code == 403

    # Session only: a token can't make itself a better token, or touch the account.
    assert client.post("/api/tokens", json={"name": "more", "scopes": api_tokens.EVERYTHING}, headers=tok).status_code == 403
    assert client.post("/api/auth/password", json={"current_password": "secret", "new_password": "a new password"}, headers=tok).status_code == 403

    # The list shows the prefix, never the token; last use is recorded.
    listed = next(t for t in client.get("/api/tokens", headers=auth).json() if t["id"] == created["id"])
    assert "token" not in listed and listed["last_used_at"] and listed["scopes"] == ["projects:read"]

    assert client.post(f"/api/tokens/{created['id']}/revoke", headers=auth).json()["revoked_at"]
    r = client.get("/api/auth/me", headers=tok)
    assert r.status_code == 401 and "revoked" in r.json()["detail"]
    assert client.delete(f"/api/tokens/{created['id']}", headers=auth).status_code == 204


def test_limited_token_cant_create_projects(client, auth):
    mine = _project(client, auth)
    _, tok = _token(client, auth, scopes=["projects:write"], project_ids=[mine["id"]])
    r = client.post("/api/projects", json={"codename": f"AT-{uuid.uuid4().hex[:6]}", "name": "x", "kind": "internal"}, headers=tok)
    assert r.status_code == 403
    _, everywhere = _token(client, auth, scopes=["projects:write"])
    r = client.post("/api/projects", json={"codename": f"AT-{uuid.uuid4().hex[:6]}", "name": "x", "kind": "internal"}, headers=everywhere)
    assert r.status_code == 201


def test_run_a_card_with_a_token(client, auth):
    p = _project(client, auth)
    _, tok = _token(client, auth, scopes=["projects:write", "jobs:write"], project_ids=[p["id"]])
    task = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Echo", "kind": "command", "command": "echo hi",
                                                               "agent_selector": {"tags": ["nobody-has-this"]}}, headers=tok).json()
    job = client.post(f"/api/tasks/{task['id']}/run", headers=tok)
    assert job.status_code in (200, 201), job.text
    assert client.get(f"/api/jobs/{job.json()['id']}", headers=tok).json()["status"] == "queued"
    assert client.post(f"/api/jobs/{job.json()['id']}/cancel", headers=tok).status_code == 200


def test_scopes_are_limited_by_role(client, auth):
    name = f"tv{uuid.uuid4().hex[:6]}"
    client.post("/api/users", json={"username": name, "role": "viewer", "password": "a password", "all_projects": True}, headers=auth)
    session = {"Authorization": f"Bearer {client.post('/api/auth/login', json={'username': name, 'password': 'a password'}).json()['token']}"}
    client.cookies.clear()
    r = client.post("/api/tokens", json={"name": "x", "scopes": ["projects:write"]}, headers=session)
    assert r.status_code == 422 and "operator" in r.json()["detail"]
    assert client.post("/api/tokens", json={"name": "x", "scopes": ["admin"]}, headers=session).status_code == 422
    created, _ = _token(client, session, scopes=["projects:read"])
    # Admins see everyone's tokens and can revoke them; others see only their own.
    assert any(t["id"] == created["id"] for t in client.get("/api/tokens?everyone=true", headers=auth).json())
    assert all(t["user"]["username"] == name for t in client.get("/api/tokens?everyone=true", headers=session).json())
    assert client.post(f"/api/tokens/{created['id']}/revoke", headers=auth).status_code == 200


def test_sockets_with_a_token(client, auth):
    p = _project(client, auth)
    _, reader = _token(client, auth, scopes=["projects:read"])
    with client.websocket_connect("/ws/ui", headers=reader) as ws:
        client.patch(f"/api/projects/{p['id']}", json={"name": "Renamed"}, headers=auth)
        while (event := ws.receive_json())["name"] != "project.updated":
            pass
        assert event["project_id"] == p["id"]
    # Never in a URL; and the terminal needs jobs:write.
    token = reader["Authorization"].split()[1]
    with pytest.raises(WebSocketDisconnect) as exc, client.websocket_connect(f"/ws/ui?token={token}") as ws:
        ws.receive_text()
    assert exc.value.code == 4401
    with pytest.raises(WebSocketDisconnect) as exc, client.websocket_connect(f"/ws/jobs/{uuid.uuid4()}/terminal", headers=reader) as ws:
        ws.receive_text()
    assert exc.value.code == 4401
