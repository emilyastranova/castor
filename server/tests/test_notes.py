"""Pinned project notes: CRUD, history and restore, order, access, feed and summary."""

import uuid


def _project(client, auth):
    code = f"PN-{uuid.uuid4().hex[:6]}"
    return client.post("/api/projects", json={"codename": code, "name": code, "kind": "internal"}, headers=auth).json()


def _login(client, auth, role):
    name = f"note-{role}-{uuid.uuid4().hex[:6]}"
    user = client.post("/api/users", json={"username": name, "role": role, "password": "a password"}, headers=auth).json()
    token = client.post("/api/auth/login", json={"username": name, "password": "a password"}).json()["token"]
    client.cookies.clear()
    return user, {"Authorization": f"Bearer {token}"}


def test_notes_history_and_order(client, auth):
    p = _project(client, auth)
    base = f"/api/projects/{p['id']}/notes"
    status = client.post(base, json={"title": "Status", "body": "## Now\n- External done"}, headers=auth)
    assert status.status_code == 201, status.text
    status = status.json()
    goals = client.post(base, json={"title": "Objectives", "body": "DA via relay"}, headers=auth).json()
    assert [n["title"] for n in client.get(base, headers=auth).json()] == ["Status", "Objectives"]
    assert status["updated_by"]["name"] and status["versions"] == 0

    # Edits keep the previous version; restoring is itself undoable.
    edited = client.patch(f"/api/notes/{status['id']}", json={"body": "AD phase starts Monday"}, headers=auth).json()
    assert edited["body"] == "AD phase starts Monday" and edited["versions"] == 1
    same = client.patch(f"/api/notes/{status['id']}", json={"body": "AD phase starts Monday"}, headers=auth).json()
    assert same["versions"] == 1  # no change, no version
    history = client.get(f"/api/notes/{status['id']}/history", headers=auth).json()
    assert history[0]["body"] == "## Now\n- External done"
    restored = client.post(f"/api/notes/{status['id']}/restore/0", headers=auth).json()
    assert restored["body"] == "## Now\n- External done" and restored["versions"] == 2
    assert client.post(f"/api/notes/{status['id']}/restore/9", headers=auth).status_code == 404

    order = client.post(f"{base}/order", json={"ids": [goals["id"], status["id"]]}, headers=auth)
    assert [n["title"] for n in order.json()] == ["Objectives", "Status"]
    assert client.post(f"{base}/order", json={"ids": [goals["id"]]}, headers=auth).status_code == 422

    # The home summary lists the project's first note as one line; the feed has the edits.
    pinned = client.get("/api/dashboard/summary", headers=auth).json()["pinned"]
    mine = [x for x in pinned if x["project"]["id"] == p["id"]]
    assert [(x["title"], x["line"]) for x in mine] == [("Objectives", "DA via relay"), ("Status", "Now")]
    feed = client.get(f"/api/dashboard/feed?project_id={p['id']}&kinds=note", headers=auth).json()["items"]
    assert any(i["type"] == "note.updated" for i in feed) and all(i["kind"] == "note" for i in feed)

    assert client.delete(f"/api/notes/{goals['id']}", headers=auth).status_code == 204
    assert [n["title"] for n in client.get(base, headers=auth).json()] == ["Status"]


def test_note_access(client, auth):
    p = _project(client, auth)
    note = client.post(f"/api/projects/{p['id']}/notes", json={"title": "Scope", "body": "10.0.0.0/24"}, headers=auth).json()
    viewer, vh = _login(client, auth, "viewer")
    # Not a member: the note doesn't exist for them.
    assert client.get(f"/api/projects/{p['id']}/notes", headers=vh).status_code == 404
    assert client.get(f"/api/notes/{note['id']}/history", headers=vh).status_code == 404
    client.put(f"/api/projects/{p['id']}/members", json=[viewer["id"]], headers=auth)
    # A member viewer reads but can't edit.
    assert [n["title"] for n in client.get(f"/api/projects/{p['id']}/notes", headers=vh).json()] == ["Scope"]
    assert client.patch(f"/api/notes/{note['id']}", json={"body": "x"}, headers=vh).status_code == 403
    assert client.post(f"/api/projects/{p['id']}/notes", json={"title": "Mine"}, headers=vh).status_code == 403


def test_note_images(client, auth):
    p = _project(client, auth)
    r = client.post(f"/api/projects/{p['id']}/attachments", files={"file": ("shot.png", b"\x89PNG fake", "image/png")}, headers=auth)
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["kind"] == "screenshot" and a["task_id"] is None
    got = client.get(f"/api/attachments/{a['id']}/download?inline=true", headers=auth)
    assert got.status_code == 200 and got.content == b"\x89PNG fake"
    _, vh = _login(client, auth, "viewer")
    assert client.get(f"/api/attachments/{a['id']}/download", headers=vh).status_code == 404  # not a member
    assert client.post(f"/api/projects/{p['id']}/attachments", files={"file": ("x.png", b"x", "image/png")}, headers=vh).status_code == 404


def test_note_colors(client, auth):
    p = _project(client, auth)
    note = client.post(f"/api/projects/{p['id']}/notes", json={"title": "Scope", "color": "yellow"}, headers=auth).json()
    assert note["color"] == "yellow"
    recolored = client.patch(f"/api/notes/{note['id']}", json={"color": "blue"}, headers=auth).json()
    assert recolored["color"] == "blue" and recolored["versions"] == 0  # cosmetic: no version
    assert client.patch(f"/api/notes/{note['id']}", json={"color": "chartreuse"}, headers=auth).status_code == 422
    assert client.post(f"/api/projects/{p['id']}/notes", json={"title": "x", "color": "nope"}, headers=auth).status_code == 422
    pinned = client.get(f"/api/dashboard/summary?project_id={p['id']}", headers=auth).json()["pinned"]
    assert pinned[0]["color"] == "blue"
