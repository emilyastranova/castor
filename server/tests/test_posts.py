"""Posts: comments on nothing, written straight into a project's timeline."""

import base64
import uuid


def _project(client, auth):
    code = f"PO-{uuid.uuid4().hex[:6]}"
    return client.post("/api/projects", json={"codename": code, "name": code, "kind": "internal"}, headers=auth).json()


def _user(client, auth, role="operator"):
    name = f"po{role[:3]}{uuid.uuid4().hex[:6]}"
    user = client.post("/api/users", json={"username": name, "role": role, "password": "a password"}, headers=auth).json()
    token = client.post("/api/auth/login", json={"username": name, "password": "a password"}).json()["token"]
    client.cookies.clear()
    return user, {"Authorization": f"Bearer {token}"}


def _feed(client, headers, pid, **params):
    return client.get("/api/dashboard/feed", params={"project_id": pid, **params}, headers=headers).json()["items"]


def test_posting_to_the_timeline(client, auth):
    p = _project(client, auth)
    bob, bh = _user(client, auth)
    viewer, vh = _user(client, auth, role="viewer")
    outsider, oh = _user(client, auth)
    client.put(f"/api/projects/{p['id']}/members", json=[bob["id"], viewer["id"]], headers=auth)

    # A screenshot is a project attachment without a card, embedded in the post.
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")
    shot = client.post(f"/api/projects/{p['id']}/attachments", files={"file": ("shot.png", png, "image/png")}, headers=auth).json()
    body = f"Kickoff notes, ask @{bob['username']}\n\n![shot](/api/attachments/{shot['id']}/download)"
    r = client.post(f"/api/projects/{p['id']}/posts", json={"body": body}, headers=auth)
    assert r.status_code == 201, r.text
    post = r.json()
    assert post["mentions"] == [bob["id"]] and post["via"] is None

    # In the timeline (as a comment kind), with its whole text; mentioned people are told.
    item = next(i for i in _feed(client, vh, p["id"]) if i["type"] == "post.created")
    assert item["kind"] == "comment" and item["post"]["id"] == post["id"] and item["post"]["body"] == body and item["task"] is None
    notes = client.get("/api/notifications", headers=bh).json()["items"]
    assert any(n["kind"] == "comment.mention" and f"post={post['id']}" in n["link"] for n in notes)
    assert any(i["type"] == "post.created" for i in _feed(client, bh, p["id"], mine="true"))

    # Viewers read but don't post; outsiders don't see it; only the author (or an admin) edits.
    assert client.post(f"/api/projects/{p['id']}/posts", json={"body": "hi"}, headers=vh).status_code == 403
    assert client.get(f"/api/projects/{p['id']}/posts", headers=oh).status_code == 404
    assert client.patch(f"/api/posts/{post['id']}", json={"body": "mine now"}, headers=bh).status_code == 403
    assert client.post(f"/api/projects/{p['id']}/posts", json={"body": "   "}, headers=auth).status_code == 400

    edited = client.patch(f"/api/posts/{post['id']}", json={"body": "Kickoff notes (fixed)"}, headers=auth).json()
    assert edited["edited_at"] and edited["mentions"] == []
    feed = _feed(client, auth, p["id"])
    assert [i["post"]["body"] for i in feed if i["post"]] == ["Kickoff notes (fixed)"]  # one entry, as it is now
    assert not [i for i in feed if i["type"] in ("post.edited", "post.deleted")]
    found = client.get("/api/search", params={"q": f"in:{p['codename']} fixed"}, headers=auth).json()
    assert any(g["type"] == "post" for g in found["groups"])

    # Deleted, it leaves the timeline (the activity log keeps the record).
    assert client.delete(f"/api/posts/{post['id']}", headers=auth).status_code == 204
    assert not [i for i in _feed(client, auth, p["id"]) if i["post"]]
    types = [a["type"] for a in client.get(f"/api/projects/{p['id']}/activity", headers=auth).json()]
    assert {"post.created", "post.edited", "post.deleted"} <= set(types)


def test_posts_travel_with_the_project(client, auth):
    p = _project(client, auth)
    client.post(f"/api/projects/{p['id']}/posts", json={"body": "carried along"}, headers=auth)
    data = client.post("/api/export", json={"projects": [p["id"]]}, headers=auth).content
    preview = client.post("/api/import/preview", files={"file": ("x.castor.zip", data, "application/zip")}, headers=auth).json()
    result = client.post("/api/import", json={"token": preview["token"], "filename": preview["filename"]}, headers=auth).json()
    posts = client.get(f"/api/projects/{result['projects'][0]}/posts", headers=auth).json()
    assert [x["body"] for x in posts] == ["carried along"]


def test_posting_through_mcp_is_marked(client, auth):
    import json

    p = _project(client, auth)
    tok = client.post("/api/tokens", json={"name": "assistant", "scopes": ["projects:write"], "project_ids": [p["id"]]}, headers=auth).json()["token"]
    headers = {"Authorization": f"Bearer {tok}"}
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "post_to_timeline", "arguments": {"project_id": p["id"], "body": "from the assistant"}}}
    result = client.post("/api/mcp", json=call, headers=headers).json()["result"]
    assert not result["isError"], result
    assert json.loads(result["content"][0]["text"])["via"] == "mcp"
    item = next(i for i in _feed(client, auth, p["id"]) if i["post"])
    assert item["via"] == "mcp" and item["post"]["body"] == "from the assistant"


def test_hashtags():
    from castor_server.api.posts import hashtags

    text = "#Recon notes: see #web-app, (#scope). Not ##this, a#b, http://x/#frag, #1, `#code`, [l](http://x/#link)\n```\n#fenced\n```\n#recon again #über-"
    assert hashtags(text) == ["Recon", "web-app", "scope", "über"]


def test_posts_are_tagged_and_filtered_by_hashtag(client, auth):
    p = _project(client, auth)
    a = client.post(f"/api/projects/{p['id']}/posts", json={"body": "#Recon found the VPN #web"}, headers=auth).json()
    client.post(f"/api/projects/{p['id']}/posts", json={"body": "lunch, no tags"}, headers=auth)
    b = client.post(f"/api/projects/{p['id']}/posts", json={"body": "more #recon"}, headers=auth).json()
    assert a["tags"] == ["Recon", "web"] and b["tags"] == ["recon"]

    # Any case, with or without #, and whatever the kinds say.
    for tag in ("recon", "#RECON"):
        items = _feed(client, auth, p["id"], tag=tag, kinds="job")
        assert [i["post"]["id"] for i in items] == [b["id"], a["id"]]
    assert [i["post"]["tags"] for i in _feed(client, auth, p["id"], tag="web")] == [["Recon", "web"]]
    assert client.get(f"/api/projects/{p['id']}/post-tags", headers=auth).json() == [{"tag": "Recon", "count": 2}, {"tag": "web", "count": 1}]
    listed = client.get(f"/api/projects/{p['id']}/posts", params={"tag": "web"}, headers=auth).json()
    assert [x["id"] for x in listed] == [a["id"]]
    found = client.get("/api/search", params={"q": f"in:{p['codename']} tag:web"}, headers=auth).json()
    assert [h["id"] for g in found["groups"] if g["type"] == "post" for h in g["hits"]] == [a["id"]]

    # Editing re-reads them.
    client.patch(f"/api/posts/{a['id']}", json={"body": "found the VPN"}, headers=auth)
    assert [i["post"]["id"] for i in _feed(client, auth, p["id"], tag="recon")] == [b["id"]]


def test_timeline_entries_link_to_what_they_are_about(client, auth):
    p = _project(client, auth)
    card = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Linked"}, headers=auth).json()
    comment = client.post(f"/api/tasks/{card['id']}/comments", json={"body": "look"}, headers=auth).json()
    post = client.post(f"/api/projects/{p['id']}/posts", json={"body": "a note"}, headers=auth).json()
    client.post(f"/api/projects/{p['id']}/columns", json={"name": "Parked"}, headers=auth)
    links = {i["type"]: i["link"] for i in _feed(client, auth, p["id"], kinds="comment,card,board")}
    card_link = f"/projects/{p['id']}?task={card['id']}"
    assert links["comment.created"] == f"{card_link}&comment={comment['id']}"
    assert links["task.created"] == card_link
    assert links["post.created"] == f"/projects/{p['id']}/dashboard?post={post['id']}"
    assert links["column.created"] == f"/projects/{p['id']}"
    # Once the card is gone, its entries don't lead anywhere.
    client.delete(f"/api/tasks/{card['id']}", headers=auth)
    assert all(i["link"] is None for i in _feed(client, auth, p["id"], kinds="comment") if i["type"] == "comment.created")


def test_moving_comments_and_posts(client, auth):
    p = _project(client, auth)
    bob, bh = _user(client, auth)
    client.put(f"/api/projects/{p['id']}/members", json=[bob["id"]], headers=auth)
    a = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Card A"}, headers=auth).json()
    b = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Card B"}, headers=auth).json()
    c = client.post(f"/api/tasks/{a['id']}/comments", json={"body": f"belongs on B, @{bob['username']}"}, headers=auth).json()

    # Card to card: the same comment (id, author, time), now on B; its entry follows it.
    r = client.post(f"/api/tasks/{a['id']}/comments/{c['id']}/move", json={"to_task_id": b["id"]}, headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["comment"]["id"] == c["id"] and r.json()["comment"]["created_at"] == c["created_at"]
    assert not client.get(f"/api/tasks/{a['id']}/comments", headers=auth).json()
    assert [x["id"] for x in client.get(f"/api/tasks/{b['id']}/comments", headers=auth).json()] == [c["id"]]
    feed = _feed(client, auth, p["id"], kinds="comment")
    entry = next(i for i in feed if i["type"] == "comment.created")
    assert entry["task"]["id"] == b["id"] and entry["link"].endswith(f"&comment={c['id']}")
    assert any(i["type"] == "comment.moved" and "Card A" in i["message"] and "Card B" in i["message"] for i in feed)

    # Out to the timeline: a post with the same writing and time; the comment's entry goes.
    r = client.post(f"/api/tasks/{b['id']}/comments/{c['id']}/move", json={"to_task_id": None}, headers=auth).json()
    post = r["post"]
    assert r["kind"] == "post" and post["body"] == c["body"] and post["created_at"] == c["created_at"] and post["mentions"] == [bob["id"]]
    assert not client.get(f"/api/tasks/{b['id']}/comments", headers=auth).json()
    feed = _feed(client, auth, p["id"], kinds="comment")
    assert not [i for i in feed if i["type"] == "comment.created"]
    assert [i["post"]["id"] for i in feed if i["post"]] == [post["id"]]

    # And back onto a card: a comment again.
    assert client.post(f"/api/posts/{post['id']}/move", json={"to_task_id": a["id"]}, headers=bh).status_code == 403  # not Bob's
    back = client.post(f"/api/posts/{post['id']}/move", json={"to_task_id": a["id"]}, headers=auth).json()["comment"]
    assert back["body"] == c["body"] and back["created_at"] == c["created_at"]
    assert client.get(f"/api/projects/{p['id']}/posts", headers=auth).json() == []
    assert [x["id"] for x in client.get(f"/api/tasks/{a['id']}/comments", headers=auth).json()] == [back["id"]]

    # Not into another project, and not by someone else.
    other = _project(client, auth)
    elsewhere = client.post(f"/api/projects/{other['id']}/tasks", json={"title": "Far"}, headers=auth).json()
    assert client.post(f"/api/tasks/{a['id']}/comments/{back['id']}/move", json={"to_task_id": elsewhere["id"]}, headers=auth).status_code == 422
    assert client.post(f"/api/tasks/{a['id']}/comments/{back['id']}/move", json={"to_task_id": b["id"]}, headers=bh).status_code == 403
    assert client.post(f"/api/tasks/{a['id']}/comments/{back['id']}/move", json={"to_task_id": a["id"]}, headers=auth).status_code == 422


def test_the_companion_is_off_until_turned_on(client, auth):
    user, h = _user(client, auth)
    assert client.get("/api/auth/me", headers=h).json()["companion"] is False
    assert client.patch("/api/auth/me", json={"companion": True}, headers=h).json()["companion"] is True
    assert client.get("/api/auth/me", headers=h).json()["companion"] is True
    assert client.get("/api/auth/me", headers=auth).json()["companion"] is False  # only theirs


def test_an_admin_deletes_an_account(client, auth):
    p = _project(client, auth)
    gone, gh = _user(client, auth)
    client.put(f"/api/projects/{p['id']}/members", json=[gone["id"]], headers=auth)
    card = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Theirs", "assignees": [gone["id"]]}, headers=auth).json()
    client.post(f"/api/tasks/{card['id']}/comments", json={"body": "my two cents"}, headers=gh)
    other_admin, ah = _user(client, auth, role="admin")

    assert client.delete(f"/api/users/{gone['id']}", headers=gh).status_code == 403  # not an admin
    assert client.delete(f"/api/users/{other_admin['id']}", headers=ah).status_code == 409  # not yourself
    assert client.delete(f"/api/users/{gone['id']}", headers=auth).status_code == 204

    assert client.get("/api/auth/me", headers=gh).status_code == 401  # signed out for good
    assert client.get(f"/api/users/{gone['id']}", headers=auth).status_code in (404, 405)
    after = client.get(f"/api/tasks/{card['id']}", headers=auth).json()
    assert after["assignees"] == []
    comment = client.get(f"/api/tasks/{card['id']}/comments", headers=auth).json()[0]
    assert comment["body"] == "my two cents" and comment["author_name"] == "Deleted user" and comment["author_id"] is None
    assert any(a["type"] == "user.deleted" for a in client.get("/api/activity?limit=20", headers=auth).json())
    client.delete(f"/api/users/{other_admin['id']}", headers=auth)
