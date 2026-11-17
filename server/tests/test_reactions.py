"""Emoji reactions on comments and posts, and custom emoji."""

from __future__ import annotations

import base64
import uuid

from castor_server.reactions import is_unicode_emoji

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def test_what_counts_as_an_emoji():
    for ok in ("👍", "🎉", "❤️", "👍🏽", "🏳️‍🌈", "🇳🇴", "1️⃣", "👩‍💻", "©️"):
        assert is_unicode_emoji(ok), ok
    for bad in ("", "a", ":)", "👍 👍", "<script>", "🎉x", "ab", "1", "—", "x" * 20):
        assert not is_unicode_emoji(bad), bad


def _card_with_comment(client, auth):
    p = client.post("/api/projects", json={"codename": f"RX-{uuid.uuid4().hex[:6]}", "name": "Reactions", "kind": "internal"},
                    headers=auth).json()
    t = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Card"}, headers=auth).json()
    c = client.post(f"/api/tasks/{t['id']}/comments", json={"body": "Found the admin share"}, headers=auth).json()
    return p, t, c


def test_reacting_to_comments_and_posts(client, auth):
    p, t, c = _card_with_comment(client, auth)
    r = client.post(f"/api/comments/{c['id']}/reactions", json={"emoji": "🎉"}, headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()[0]["emoji"] == "🎉" and r.json()[0]["count"] == 1 and r.json()[0]["names"]
    client.post(f"/api/comments/{c['id']}/reactions", json={"emoji": "🎉"}, headers=auth)  # again: still one
    client.post(f"/api/comments/{c['id']}/reactions", json={"emoji": "👀"}, headers=auth)
    listed = client.get(f"/api/tasks/{t['id']}/comments", headers=auth).json()[0]["reactions"]
    assert [(x["emoji"], x["count"]) for x in listed] == [("🎉", 1), ("👀", 1)]
    assert client.post(f"/api/comments/{c['id']}/reactions", json={"emoji": "lol"}, headers=auth).status_code == 422
    assert client.post(f"/api/comments/{c['id']}/reactions", json={"emoji": ":nope:"}, headers=auth).status_code == 422
    r = client.request("DELETE", f"/api/comments/{c['id']}/reactions", params={"emoji": "👀"}, headers=auth)
    assert [x["emoji"] for x in r.json()] == ["🎉"]
    # Editing the comment keeps them; the timeline shows them.
    edited = client.patch(f"/api/tasks/{t['id']}/comments/{c['id']}", json={"body": "Found the admin share!"}, headers=auth).json()
    assert edited["reactions"][0]["emoji"] == "🎉"
    feed = client.get(f"/api/projects/{p['id']}/dashboard/feed", headers=auth)
    if feed.status_code == 404:
        feed = client.get("/api/dashboard/feed", params={"project_id": p["id"]}, headers=auth)
    items = [i for i in feed.json()["items"] if i.get("comment") and i["comment"]["id"] == c["id"]]
    assert items and items[0]["comment"]["reactions"][0]["emoji"] == "🎉"

    # Moving the comment to the timeline takes its reactions along.
    moved = client.post(f"/api/tasks/{t['id']}/comments/{c['id']}/move", json={"to_task_id": None}, headers=auth).json()
    assert moved["kind"] == "post" and moved["post"]["reactions"][0]["emoji"] == "🎉"
    post_id = moved["post"]["id"]
    r = client.post(f"/api/posts/{post_id}/reactions", json={"emoji": "👍"}, headers=auth)
    assert [x["emoji"] for x in r.json()] == ["🎉", "👍"]
    posts = client.get(f"/api/projects/{p['id']}/posts", headers=auth).json()
    assert [x["emoji"] for x in posts[0]["reactions"]] == ["🎉", "👍"]


def test_custom_emoji(client, auth):
    _, _, c = _card_with_comment(client, auth)
    name = f"party_{uuid.uuid4().hex[:4]}"
    r = client.post("/api/emojis", data={"name": name}, files={"file": ("p.png", PNG, "image/png")}, headers=auth)
    assert r.status_code == 201, r.text
    assert client.post("/api/emojis", data={"name": name}, files={"file": ("p.png", PNG, "image/png")}, headers=auth).status_code == 409
    assert client.post("/api/emojis", data={"name": "Bad Name"}, files={"file": ("p.png", PNG, "image/png")}, headers=auth).status_code == 422
    assert client.post("/api/emojis", data={"name": "svgish"}, files={"file": ("p.svg", b"<svg/>", "image/svg+xml")},
                       headers=auth).status_code == 422
    listed = {e["name"]: e for e in client.get("/api/emojis", headers=auth).json()}
    img = client.get(listed[name]["url"], headers=auth)
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    r = client.post(f"/api/comments/{c['id']}/reactions", json={"emoji": f":{name}:"}, headers=auth)
    assert r.status_code == 200 and r.json()[0]["emoji"] == f":{name}:"
    # Removing the emoji removes reactions made with it.
    assert client.delete(f"/api/emojis/{name}", headers=auth).status_code == 204
    assert client.get(f"/api/tasks/{c['task_id']}/comments", headers=auth).json()[0]["reactions"] == []


def _user(client, auth, name_prefix="rx"):
    name = f"{name_prefix}{uuid.uuid4().hex[:6]}"
    user = client.post("/api/users", json={"username": name, "display_name": name.title(), "role": "operator",
                                           "password": "a password"}, headers=auth).json()
    token = client.post("/api/auth/login", json={"username": name, "password": "a password"}).json()["token"]
    client.cookies.clear()
    return user, {"Authorization": f"Bearer {token}"}


def test_authors_hear_about_reactions_in_one_notification(client, auth):
    p, t, c = _card_with_comment(client, auth)  # written by the admin
    sam, sh = _user(client, auth, "sam")
    ana, ah = _user(client, auth, "ana")
    client.put(f"/api/projects/{p['id']}/members", json=[sam["id"], ana["id"]], headers=auth)
    me = client.get("/api/auth/me", headers=auth).json()

    def unread():
        items = client.get("/api/notifications", params={"unread_only": True}, headers=auth).json()["items"]
        return [n for n in items if n["kind"] == "reaction" and n["subject"] == f"reaction:{c['id']}"]

    client.post(f"/api/comments/{c['id']}/reactions", json={"emoji": "👀"}, headers=auth)  # your own: nothing
    assert unread() == []
    client.post(f"/api/comments/{c['id']}/reactions", json={"emoji": "🎉"}, headers=sh)
    first = unread()
    assert len(first) == 1 and first[0]["title"].startswith(sam["display_name"]) and "your comment on “Card”" in first[0]["title"]
    assert first[0]["link"].endswith(f"comment={c['id']}") and "🎉 1" in first[0]["body"]
    client.post(f"/api/comments/{c['id']}/reactions", json={"emoji": "🎉"}, headers=ah)
    folded = unread()
    assert len(folded) == 1 and f"{sam['display_name']} and {ana['display_name']}" in folded[0]["title"] and "🎉 2" in folded[0]["body"]
    # Taking one back tells nobody.
    client.request("DELETE", f"/api/comments/{c['id']}/reactions", params={"emoji": "🎉"}, headers=ah)
    assert unread()[0]["id"] == folded[0]["id"]
    assert me["id"] not in [sam["id"], ana["id"]]


def test_custom_emoji_show_in_exports(client, auth):
    p, t, c = _card_with_comment(client, auth)
    name = f"pwned_{uuid.uuid4().hex[:4]}"
    client.post("/api/emojis", data={"name": name}, files={"file": ("p.png", PNG, "image/png")}, headers=auth)
    client.post(f"/api/tasks/{t['id']}/comments", json={"body": f"Domain admin :{name}: and `:{name}:` in code"}, headers=auth)
    client.post(f"/api/comments/{c['id']}/reactions", json={"emoji": f":{name}:"}, headers=auth)
    page = client.get(f"/api/projects/{p['id']}/timeline/export", params={"format": "html"}, headers=auth).text
    assert f'alt=":{name}:"' in page and "data:image/png;base64," in page
    assert f"<code>:{name}:</code>" in page  # not in code
    assert 'class="reactions"' in page
    client.delete(f"/api/emojis/{name}", headers=auth)
