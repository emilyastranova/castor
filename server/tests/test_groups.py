"""Groups: people put together. @group mentions everyone in it, and a project shared with a
group can be seen by all of them."""

import uuid


def _project(client, auth):
    code = f"GR-{uuid.uuid4().hex[:6]}"
    return client.post("/api/projects", json={"codename": code, "name": code, "kind": "internal"}, headers=auth).json()


def _user(client, auth, role="operator"):
    name = f"gr{role[:3]}{uuid.uuid4().hex[:6]}"
    user = client.post("/api/users", json={"username": name, "role": role, "password": "a password"}, headers=auth).json()
    token = client.post("/api/auth/login", json={"username": name, "password": "a password"}).json()["token"]
    client.cookies.clear()
    return user, {"Authorization": f"Bearer {token}"}


def _group(client, auth, members=(), **extra):
    slug = f"team-{uuid.uuid4().hex[:6]}"
    g = client.post("/api/groups", json={"slug": slug, "name": slug.title(), **extra}, headers=auth)
    assert g.status_code == 201, g.text
    g = g.json()
    if members:
        g = client.put(f"/api/groups/{g['id']}/members", json=[u["id"] for u in members], headers=auth).json()
    return g


def test_handles_are_unique_across_users_and_groups(client, auth):
    user, _ = _user(client, auth)
    g = _group(client, auth)
    assert client.post("/api/groups", json={"slug": user["username"], "name": "x"}, headers=auth).status_code == 409
    assert client.post("/api/groups", json={"slug": g["slug"].upper(), "name": "x"}, headers=auth).status_code == 409
    clash = client.post("/api/users", json={"username": g["slug"], "password": "a password"}, headers=auth)
    assert clash.status_code == 409 and "group" in clash.text
    assert client.post("/api/groups", json={"slug": "bad slug", "name": "x"}, headers=auth).status_code == 422


def test_a_project_shared_with_a_group_is_seen_by_its_members(client, auth):
    p = _project(client, auth)
    ada, ah = _user(client, auth)
    bob, bh = _user(client, auth, role="viewer")
    g = _group(client, auth, [ada, bob])
    assert g["member_count"] == 2 and {u["id"] for u in g["members"]} == {ada["id"], bob["id"]}
    assert client.get(f"/api/projects/{p['id']}", headers=ah).status_code == 404

    shared = client.put(f"/api/projects/{p['id']}/groups", json=[g["id"]], headers=auth)
    assert shared.status_code == 200 and [x["slug"] for x in shared.json()] == [g["slug"]]
    assert client.get(f"/api/projects/{p['id']}", headers=ah).status_code == 200
    assert p["id"] in {x["id"] for x in client.get("/api/projects", headers=bh).json()}
    people = {u["id"] for u in client.get(f"/api/projects/{p['id']}/people", headers=auth).json()}
    assert {ada["id"], bob["id"]} <= people  # can be assigned and mentioned

    # Leaving the group (or the group being removed) takes the project away.
    client.put(f"/api/groups/{g['id']}/members", json=[ada["id"]], headers=auth)
    assert client.get(f"/api/projects/{p['id']}", headers=bh).status_code == 404
    assert client.delete(f"/api/groups/{g['id']}", headers=auth).status_code == 204
    assert client.get(f"/api/projects/{p['id']}", headers=ah).status_code == 404


def test_mentioning_a_group_notifies_its_people_who_can_see_the_project(client, auth):
    p = _project(client, auth)
    ada, ah = _user(client, auth)
    bob, bh = _user(client, auth)
    outsider, oh = _user(client, auth)
    client.put(f"/api/projects/{p['id']}/members", json=[ada["id"], bob["id"]], headers=auth)
    g = _group(client, auth, [ada, bob, outsider])
    task = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Scope call"}, headers=auth).json()
    comment = client.post(f"/api/tasks/{task['id']}/comments", json={"body": f"@{g['slug']} please review"}, headers=auth).json()
    assert set(comment["mentions"]) == {ada["id"], bob["id"]}  # not the outsider: they can't see it

    def mentions(h):
        return [n for n in client.get("/api/notifications", headers=h).json()["items"] if n["kind"] == "comment.mention"]
    assert any("Scope call" in n["title"] for n in mentions(ah)) and any("Scope call" in n["title"] for n in mentions(bh))
    assert not any("Scope call" in n["title"] for n in mentions(oh))


def test_who_may_change_groups(client, auth):
    p = _project(client, auth)
    op, oh = _user(client, auth)
    g = _group(client, auth)
    assert client.post("/api/groups", json={"slug": f"x{uuid.uuid4().hex[:6]}", "name": "x"}, headers=oh).status_code == 403
    assert client.get("/api/groups", headers=oh).status_code == 200  # anyone can list them, for @mentions
    assert client.put(f"/api/projects/{p['id']}/groups", json=[g["id"]], headers=oh).status_code == 404  # not their project
    client.put(f"/api/projects/{p['id']}/members", json=[op["id"]], headers=auth)
    assert client.put(f"/api/projects/{p['id']}/groups", json=[g["id"]], headers=oh).status_code == 200


def test_a_card_assigned_to_a_group(client, auth):
    p = _project(client, auth)
    ada, ah = _user(client, auth)
    bob, bh = _user(client, auth)
    outsider, oh = _user(client, auth)
    client.put(f"/api/projects/{p['id']}/members", json=[ada["id"], bob["id"]], headers=auth)
    g = _group(client, auth, [ada, bob, outsider])
    title = f"Triage {uuid.uuid4().hex[:4]}"
    # By @handle (stored as the group's id); bob is also assigned directly.
    task = client.post(f"/api/projects/{p['id']}/tasks", json={"title": title, "group_assignees": [f"@{g['slug']}"], "assignees": [bob["id"]]},
                       headers=auth).json()
    assert task["group_assignees"] == [g["id"]]
    assert client.post(f"/api/projects/{p['id']}/tasks", json={"title": "x", "group_assignees": ["no-such-group"]}, headers=auth).status_code == 422

    def assigned(h):
        return [n["title"] for n in client.get("/api/notifications", headers=h).json()["items"] if n["kind"] == "task.assigned" and title in n["title"]]
    assert assigned(ah) and "you're in it" in assigned(ah)[0]
    assert len(assigned(bh)) == 1 and "assigned you" in assigned(bh)[0]  # once, as the direct assignee
    assert not assigned(oh)  # can't see the project

    # It's theirs: Home counts it and is:mine finds it.
    assert client.get(f"/api/dashboard/summary?project_id={p['id']}", headers=ah).json()["mine"]["assigned"] == 1
    found = client.get("/api/search", params={"q": f"is:mine {title}"}, headers=ah).json()
    assert any(h["title"] == title for grp in found["groups"] for h in grp["hits"]), found

    # Unassigning the group; duplicating without assignees drops groups too.
    changed = client.patch(f"/api/tasks/{task['id']}", json={"group_assignees": []}, headers=auth).json()
    assert changed["group_assignees"] == [] and changed["assignees"] == [bob["id"]]
    client.patch(f"/api/tasks/{task['id']}", json={"group_assignees": [g["id"]]}, headers=auth)
    feed = client.get(f"/api/dashboard/feed?project_id={p['id']}", headers=auth).json()["items"]
    assert any(i["type"] == "task.assigned" and f"@{g['slug']}" in i["message"] for i in feed)
    copy = client.post(f"/api/tasks/{task['id']}/duplicate", json={"assignees": False}, headers=auth).json()
    assert copy["group_assignees"] == [] and copy["assignees"] == []


def test_group_assignees_travel_by_handle(client, auth):
    p = _project(client, auth)
    g = _group(client, auth)
    client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Carried", "group_assignees": [g["id"]]}, headers=auth)
    data = client.post("/api/export", json={"projects": [p["id"]]}, headers=auth).content
    preview = client.post("/api/import/preview", files={"file": ("x.castor.zip", data, "application/zip")}, headers=auth).json()
    result = client.post("/api/import", json={"token": preview["token"], "filename": preview["filename"]}, headers=auth).json()
    board = client.get(f"/api/projects/{result['projects'][0]}/board", headers=auth).json()
    assert next(t for t in board["tasks"] if t["title"] == "Carried")["group_assignees"] == [g["id"]]
