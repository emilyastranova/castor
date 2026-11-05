"""Assignees and @mentions: validation, notifications, activity, and the "Mine" timeline."""

import uuid

from castor_server.people import mention_names


def _project(client, auth):
    code = f"PP-{uuid.uuid4().hex[:6]}"
    return client.post("/api/projects", json={"codename": code, "name": code, "kind": "internal"}, headers=auth).json()


def _user(client, auth, role="operator"):
    name = f"pp{role[:3]}{uuid.uuid4().hex[:6]}"
    user = client.post("/api/users", json={"username": name, "role": role, "password": "a password"}, headers=auth).json()
    token = client.post("/api/auth/login", json={"username": name, "password": "a password"}).json()["token"]
    client.cookies.clear()
    return user, {"Authorization": f"Bearer {token}"}


def _notes(client, headers, kind):
    return [n for n in client.get("/api/notifications", headers=headers).json()["items"] if n["kind"] == kind]


def test_mention_names():
    text = "@alice and @Bob. mail me@example.com, see `@code` or\n```\n@block\n```\npath/@x (@carol_1)"
    assert mention_names(text) == {"alice", "bob", "carol_1"}


def test_assignees(client, auth):
    p = _project(client, auth)
    bob, bh = _user(client, auth)
    outsider, _ = _user(client, auth)
    client.put(f"/api/projects/{p['id']}/members", json=[bob["id"]], headers=auth)
    people = {u["username"] for u in client.get(f"/api/projects/{p['id']}/people", headers=auth).json()}
    assert bob["username"] in people and "admin" in people and outsider["username"] not in people

    task = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Recon", "assignees": [bob["username"]]}, headers=auth).json()
    assert task["assignees"] == [bob["id"]]  # usernames are accepted and stored as ids
    assert any("Recon" in n["title"] for n in _notes(client, bh, "task.assigned"))
    bad = client.patch(f"/api/tasks/{task['id']}", json={"assignees": [outsider["id"]]}, headers=auth)
    assert bad.status_code == 422 and "can see this project" in bad.json()["detail"]

    # Changes go to the activity feed; assigning yourself isn't a notification.
    me = client.get("/api/auth/me", headers=auth).json()
    before = len(_notes(client, auth, "task.assigned"))
    client.patch(f"/api/tasks/{task['id']}", json={"assignees": [me["id"]]}, headers=auth)
    assert len(_notes(client, auth, "task.assigned")) == before
    feed = client.get(f"/api/dashboard/feed?project_id={p['id']}", headers=auth).json()["items"]
    assert any(i["type"] == "task.assigned" and "Assigned" in i["message"] and "unassigned" in i["message"] for i in feed)
    summary = client.get(f"/api/dashboard/summary?project_id={p['id']}", headers=auth).json()
    assert summary["mine"]["assigned"] == 1


def test_mentions_and_mine(client, auth):
    p = _project(client, auth)
    bob, bh = _user(client, auth)
    cat, ch = _user(client, auth)
    outsider, oh = _user(client, auth)
    client.put(f"/api/projects/{p['id']}/members", json=[bob["id"], cat["id"]], headers=auth)
    task = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Relay"}, headers=auth).json()

    c = client.post(
        f"/api/tasks/{task['id']}/comments", json={"body": f"@{bob['username']} can you check? cc @{outsider['username']}"}, headers=auth
    ).json()
    assert c["mentions"] == [bob["id"]]  # someone who can't see the project is just text
    assert len(_notes(client, bh, "comment.mention")) == 1 and not _notes(client, oh, "comment.mention")
    summary = client.get("/api/dashboard/summary", headers=bh).json()
    assert summary["mine"]["mentions"] == 1
    item = next(a for a in summary["attention"] if a["kind"] == "mention")
    assert "mentioned you" in item["title"] and f"comment={c['id']}" in item["link"]
    # Read, it's no longer waiting on Bob.
    client.post(f"/api/notifications/{item['notification_id']}/read", headers=bh)
    assert not [a for a in client.get("/api/dashboard/summary", headers=bh).json()["attention"] if a["kind"] == "mention"]

    # An edit notifies only the people it adds.
    client.patch(f"/api/tasks/{task['id']}/comments/{c['id']}", json={"body": f"@{bob['username']} and @{cat['username']}"}, headers=auth)
    assert len(_notes(client, bh, "comment.mention")) == 1 and len(_notes(client, ch, "comment.mention")) == 1

    # Mine: the comment mentioning Bob is in his timeline; Cat's own comment isn't in hers,
    # but a reply on the card she commented on is.
    mine = client.get(f"/api/dashboard/feed?project_id={p['id']}&mine=true", headers=bh).json()["items"]
    assert [i["comment"]["id"] for i in mine if i["comment"]] == [c["id"]]
    client.post(f"/api/tasks/{task['id']}/comments", json={"body": "on it"}, headers=ch)
    cat_mine = client.get(f"/api/dashboard/feed?project_id={p['id']}&mine=true&kinds=comment", headers=ch).json()["items"]
    assert "on it" not in [i["comment"]["body"] for i in cat_mine]
    reply = client.post(f"/api/tasks/{task['id']}/comments", json={"body": "thanks"}, headers=auth).json()
    cat_mine = client.get(f"/api/dashboard/feed?project_id={p['id']}&mine=true&kinds=comment", headers=ch).json()["items"]
    assert reply["id"] in [i["comment"]["id"] for i in cat_mine]
