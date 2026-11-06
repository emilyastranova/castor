"""Roles (viewer/operator/admin), project membership, accounts, and what each socket sees."""

import json
import uuid

import pytest


def _user(client, auth, role, **extra):
    name = f"{role}-{uuid.uuid4().hex[:6]}"
    r = client.post("/api/users", json={"username": name, "role": role, "password": "correct horse", **extra}, headers=auth)
    assert r.status_code == 201, r.text
    login = client.post("/api/auth/login", json={"username": name, "password": "correct horse"})
    client.cookies.clear()
    return r.json(), {"Authorization": f"Bearer {login.json()['token']}"}


def _project(client, auth, members=()):
    code = f"RB-{uuid.uuid4().hex[:6]}"
    project = client.post("/api/projects", json={"codename": code, "name": code, "kind": "internal"}, headers=auth).json()
    if members:
        r = client.put(f"/api/projects/{project['id']}/members", json=list(members), headers=auth)
        assert r.status_code == 200, r.text
    return project


def test_viewer_reads_member_projects_only(client, auth):
    viewer, vh = _user(client, auth, "viewer")
    mine = _project(client, auth, [viewer["id"]])
    other = _project(client, auth)
    task = client.post(f"/api/projects/{mine['id']}/tasks", json={"title": "Look"}, headers=auth).json()
    hidden = client.post(f"/api/projects/{other['id']}/tasks", json={"title": "Secret"}, headers=auth).json()

    listed = {p["id"] for p in client.get("/api/projects", headers=vh).json()}
    assert mine["id"] in listed and other["id"] not in listed
    assert client.get(f"/api/projects/{mine['id']}/board", headers=vh).status_code == 200
    assert client.get(f"/api/projects/{other['id']}/board", headers=vh).status_code == 404
    assert client.get(f"/api/tasks/{hidden['id']}", headers=vh).status_code == 404
    assert all(a["project_id"] != other["id"] for a in client.get("/api/activity?limit=200", headers=vh).json())

    # Read-only, with a clear reason.
    r = client.post(f"/api/projects/{mine['id']}/tasks", json={"title": "Nope"}, headers=vh)
    assert r.status_code == 403 and "Viewers" in r.json()["detail"]
    assert client.post(f"/api/tasks/{task['id']}/comments", json={"body": "hi"}, headers=vh).status_code == 403
    assert client.post("/api/projects", json={"codename": "X-1", "name": "x", "kind": "internal"}, headers=vh).status_code == 403


def test_operator_works_in_member_projects_and_cannot_administer(client, auth):
    op, oh = _user(client, auth, "operator")
    mine = _project(client, auth, [op["id"]])
    other = _project(client, auth)
    assert client.post(f"/api/projects/{mine['id']}/tasks", json={"title": "Do"}, headers=oh).status_code == 201
    assert client.post(f"/api/projects/{other['id']}/tasks", json={"title": "Do"}, headers=oh).status_code == 404

    # A project it creates is its own.
    own = client.post("/api/projects", json={"codename": f"OP-{uuid.uuid4().hex[:5]}", "name": "own", "kind": "internal"}, headers=oh)
    assert own.status_code == 201
    assert op["id"] in [u["id"] for u in client.get(f"/api/projects/{own.json()['id']}/members", headers=oh).json()]
    # It can share it, but can't remove itself by accident.
    viewer, _ = _user(client, auth, "viewer")
    members = client.put(f"/api/projects/{own.json()['id']}/members", json=[viewer["id"]], headers=oh).json()
    assert {u["id"] for u in members} == {op["id"], viewer["id"]}

    for method, path in (("get", "/api/users"), ("post", "/api/agent-tokens"), ("delete", f"/api/projects/{mine['id']}")):
        r = getattr(client, method)(path, headers=oh, **({"json": {}} if method == "post" else {}))
        assert r.status_code == 403, (path, r.text)


def test_accounts(client, auth):
    user, uh = _user(client, auth, "operator", display_name="Bob B")
    assert client.get("/api/auth/me", headers=uh).json()["role"] == "operator"

    r = client.post("/api/auth/password", json={"current_password": "wrong", "new_password": "a new password"}, headers=uh)
    assert r.status_code == 400
    assert client.post("/api/auth/password", json={"current_password": "correct horse", "new_password": "a new password"}, headers=uh).status_code == 204
    assert client.post("/api/auth/login", json={"username": user["username"], "password": "a new password"}).status_code == 200
    client.cookies.clear()

    # Disabling stops new sign-ins and existing sessions at once.
    assert client.patch(f"/api/users/{user['id']}", json={"disabled": True}, headers=auth).json()["disabled_at"]
    assert client.get("/api/auth/me", headers=uh).status_code == 401
    assert client.post("/api/auth/login", json={"username": user["username"], "password": "a new password"}).status_code == 401
    client.patch(f"/api/users/{user['id']}", json={"disabled": False, "role": "viewer"}, headers=auth)
    assert client.get("/api/auth/me", headers=uh).json()["role"] == "viewer"

    taken = client.post("/api/users", json={"username": user["username"].upper(), "password": "12345678"}, headers=auth)
    assert taken.status_code == 409


def test_last_admin_stays(client, auth):
    me = client.get("/api/auth/me", headers=auth).json()
    others = [u for u in client.get("/api/users", headers=auth).json() if u["role"] == "admin" and not u["disabled_at"] and u["id"] != me["id"]]
    for u in others:  # leave the test admin as the only one
        client.patch(f"/api/users/{u['id']}", json={"role": "operator"}, headers=auth)
    r = client.patch(f"/api/users/{me['id']}", json={"role": "operator"}, headers=auth)
    assert r.status_code == 409 and "at least one active admin" in r.json()["detail"]
    for u in others:
        client.patch(f"/api/users/{u['id']}", json={"role": "admin"}, headers=auth)


def test_socket_only_carries_visible_projects(client, auth):
    viewer, vh = _user(client, auth, "viewer")
    token = vh["Authorization"].removeprefix("Bearer ")
    mine = _project(client, auth, [viewer["id"]])
    other = _project(client, auth)
    with client.websocket_connect(f"/ws/ui?token={token}") as ws:
        client.post(f"/api/projects/{other['id']}/tasks", json={"title": "Hidden"}, headers=auth)
        client.post(f"/api/projects/{mine['id']}/tasks", json={"title": "Seen"}, headers=auth)
        seen = []
        while True:
            event = json.loads(ws.receive_text())
            seen.append(event)
            if event["name"] == "task.created" and event["data"]["title"] == "Seen":
                break
        assert all(e["project_id"] != other["id"] for e in seen)


@pytest.mark.parametrize("role", ["viewer", "operator"])
def test_libraries_need_operator(client, auth, role):
    _, h = _user(client, auth, role)
    r = client.post("/api/playbooks", json={"yaml": "id: x\nname: x\nsteps: []\n"}, headers=h)
    assert (r.status_code == 403) == (role == "viewer")


def test_theme_colour_is_per_user(client, auth):
    _, uh = _user(client, auth, "viewer")
    assert client.get("/api/auth/me", headers=uh).json()["theme_seed"] == ""
    r = client.patch("/api/auth/me", json={"theme_seed": "#0B8043"}, headers=uh)
    assert r.status_code == 200 and r.json()["theme_seed"] == "#0b8043"
    name = r.json()["display_name"]
    assert client.patch("/api/auth/me", json={"theme_seed": "green"}, headers=uh).status_code == 422
    assert client.get("/api/auth/me", headers=uh).json()["display_name"] == name  # untouched
    assert client.get("/api/auth/me", headers=auth).json()["theme_seed"] == ""  # only theirs
    assert client.patch("/api/auth/me", json={"theme_seed": ""}, headers=uh).json()["theme_seed"] == ""
