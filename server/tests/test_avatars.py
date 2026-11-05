"""Profile pictures: upload, URL, Gravatar; your own or (admins) anyone's."""

import hashlib
import uuid

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _user(client, auth, role="operator"):
    name = f"pic-{uuid.uuid4().hex[:6]}"
    user = client.post("/api/users", json={"username": name, "role": role, "password": "a password"}, headers=auth).json()
    token = client.post("/api/auth/login", json={"username": name, "password": "a password"}).json()["token"]
    client.cookies.clear()
    return user, {"Authorization": f"Bearer {token}"}


def test_upload_url_and_gravatar(client, auth):
    user, h = _user(client, auth)
    assert user["avatar_url"] is None  # initials

    r = client.post(f"/api/users/{user['id']}/avatar/upload", files={"file": ("me.png", PNG, "image/png")}, headers=h)
    assert r.status_code == 200, r.text
    url = r.json()["avatar_url"]
    assert url == f"/api/users/{user['id']}/avatar?v=1" and r.json()["has_upload"]
    served = client.get(url, headers=auth)
    assert served.status_code == 200 and served.headers["content-type"] == "image/png"
    assert served.headers["x-content-type-options"] == "nosniff" and served.content == PNG

    r = client.put(f"/api/users/{user['id']}/avatar", json={"kind": "url", "url": "https://example.test/me.jpg"}, headers=h)
    assert r.json()["avatar_url"] == "https://example.test/me.jpg"

    r = client.put(f"/api/users/{user['id']}/avatar", json={"kind": "gravatar", "gravatar_email": " Me@Example.test "}, headers=h)
    digest = hashlib.sha256(b"me@example.test").hexdigest()
    assert r.json()["avatar_url"] == f"https://gravatar.com/avatar/{digest}?s=160&d=identicon"

    # The upload is kept, so you can switch back to it; and back to initials.
    assert client.put(f"/api/users/{user['id']}/avatar", json={"kind": "upload"}, headers=h).json()["avatar_url"] == url
    assert client.put(f"/api/users/{user['id']}/avatar", json={"kind": "initials"}, headers=h).json()["avatar_url"] is None

    # Others see it in the directory (but not the Gravatar email).
    client.put(f"/api/users/{user['id']}/avatar", json={"kind": "upload"}, headers=h)
    entry = next(u for u in client.get("/api/users/directory", headers=auth).json() if u["id"] == user["id"])
    assert entry["avatar_url"] == url and "gravatar_email" not in entry


def test_bad_pictures_are_refused(client, auth):
    user, h = _user(client, auth)
    base = f"/api/users/{user['id']}/avatar"
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    for name, data, mime in (("x.svg", svg, "image/svg+xml"), ("x.png", b"not really a png", "image/png"),
                             ("big.png", PNG + b"\x00" * (2 * 1024 * 1024), "image/png")):
        r = client.post(f"{base}/upload", files={"file": (name, data, mime)}, headers=h)
        assert r.status_code == 422, name
    for body in ({"kind": "url", "url": "http://example.test/x.png"}, {"kind": "url", "url": "javascript:alert(1)"},
                 {"kind": "gravatar", "gravatar_email": "nope"}, {"kind": "upload"}, {"kind": "weird"}):
        assert client.put(base, json=body, headers=h).status_code == 422, body


def test_only_yourself_or_an_admin(client, auth):
    a, ah = _user(client, auth)
    b, _ = _user(client, auth)
    r = client.put(f"/api/users/{b['id']}/avatar", json={"kind": "gravatar", "gravatar_email": "b@example.test"}, headers=ah)
    assert r.status_code == 403
    r = client.post(f"/api/users/{a['id']}/avatar/upload", files={"file": ("a.png", PNG, "image/png")}, headers=auth)
    assert r.status_code == 200  # an admin changing someone else's


def test_comment_shows_the_authors_picture(client, auth):
    me = client.get("/api/auth/me", headers=auth).json()
    client.put(f"/api/users/{me['id']}/avatar", json={"kind": "url", "url": "https://example.test/admin.png"}, headers=auth)
    code = f"PIC-{uuid.uuid4().hex[:6]}"
    project = client.post("/api/projects", json={"codename": code, "name": code, "kind": "internal"}, headers=auth).json()
    task = client.post(f"/api/projects/{project['id']}/tasks", json={"title": "Pic"}, headers=auth).json()
    c = client.post(f"/api/tasks/{task['id']}/comments", json={"body": "hi"}, headers=auth).json()
    assert c["author_avatar"] == "https://example.test/admin.png"
    client.put(f"/api/users/{me['id']}/avatar", json={"kind": "initials"}, headers=auth)
