"""Cards that wait on other cards, and due date reminders."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta


def _project(client, auth):
    code = f"DD-{uuid.uuid4().hex[:6]}"
    p = client.post("/api/projects", json={"codename": code, "name": code, "kind": "internal"}, headers=auth).json()
    cols = {c["role"]: c["id"] for c in client.get(f"/api/projects/{p['id']}/board", headers=auth).json()["columns"] if c["role"]}
    return p, cols


def _card(client, auth, pid, title, **kw):
    r = client.post(f"/api/projects/{pid}/tasks", json={"title": title, "kind": "manual", **kw}, headers=auth)
    assert r.status_code == 201, r.text
    return r.json()


def _user(client, auth):
    name = f"dd{uuid.uuid4().hex[:8]}"
    user = client.post("/api/users", json={"username": name, "role": "operator", "password": "a password"}, headers=auth).json()
    token = client.post("/api/auth/login", json={"username": name, "password": "a password"}).json()["token"]
    client.cookies.clear()
    return user, {"Authorization": f"Bearer {token}"}


def _notes(client, headers, kind):
    return [n for n in client.get("/api/notifications", headers=headers).json()["items"] if n["kind"] == kind]


def test_a_card_waits_on_others(client, auth):
    p, cols = _project(client, auth)
    design = _card(client, auth, p["id"], "Design")
    review = _card(client, auth, p["id"], "Review the design")
    member, member_h = _user(client, auth)
    client.put(f"/api/projects/{p['id']}/members", json=[member["id"]], headers=auth)
    build = _card(client, auth, p["id"], "Build it", waits_on=[design["id"], review["id"]], assignees=[member["id"]])
    assert build["waits_on"] == [design["id"], review["id"]]
    assert [b["title"] for b in build["blocked_by"]] == ["Design", "Review the design"]
    board = {t["id"]: t for t in client.get(f"/api/projects/{p['id']}/board", headers=auth).json()["tasks"]}
    assert len(board[build["id"]]["blocked_by"]) == 2

    # Starting it is refused (with what it waits on), unless forced.
    r = client.post(f"/api/tasks/{build['id']}/move", json={"column_id": cols["active"]}, headers=auth)
    assert r.status_code == 409 and [b["id"] for b in r.json()["detail"]["blocked_by"]] == [design["id"], review["id"]]
    # Other columns are fine.
    assert client.post(f"/api/tasks/{build['id']}/move", json={"column_id": cols["backlog"]}, headers=auth).status_code == 200

    deps = client.get(f"/api/tasks/{design['id']}/dependencies", headers=auth).json()
    assert [b["title"] for b in deps["blocks"]] == ["Build it"] and deps["waits_on"] == []
    deps = client.get(f"/api/tasks/{build['id']}/dependencies", headers=auth).json()
    assert [(w["title"], w["finished"]) for w in deps["waits_on"]] == [("Design", False), ("Review the design", False)]

    # Finishing one isn't enough; archiving the other unblocks it, and its people hear.
    client.post(f"/api/tasks/{design['id']}/move", json={"column_id": cols["done"]}, headers=auth)
    assert [b["title"] for b in client.get(f"/api/tasks/{build['id']}", headers=auth).json()["blocked_by"]] == ["Review the design"]
    assert not _notes(client, member_h, "task.unblocked")
    client.post(f"/api/tasks/{review['id']}/archive", headers=auth)
    assert client.get(f"/api/tasks/{build['id']}", headers=auth).json()["blocked_by"] == []
    unblocked = _notes(client, member_h, "task.unblocked")
    assert len(unblocked) == 1 and "Build it" in unblocked[0]["title"]
    assert client.post(f"/api/tasks/{build['id']}/move", json={"column_id": cols["active"]}, headers=auth).status_code == 200

    # A forced start of a blocked card.
    later = _card(client, auth, p["id"], "Later", waits_on=[build["id"]])
    assert client.post(f"/api/tasks/{later['id']}/move", json={"column_id": cols["active"], "force": True}, headers=auth).status_code == 200
    client.delete(f"/api/projects/{p['id']}", headers=auth)


def test_what_a_card_can_wait_on(client, auth):
    p, _ = _project(client, auth)
    q, _ = _project(client, auth)
    a = _card(client, auth, p["id"], "A")
    b = _card(client, auth, p["id"], "B", waits_on=[a["id"]])
    elsewhere = _card(client, auth, q["id"], "Elsewhere")
    patch = lambda card, ids: client.patch(f"/api/tasks/{card['id']}", json={"waits_on": ids}, headers=auth)
    assert patch(a, [a["id"]]).status_code == 422  # itself
    assert "circle" in patch(a, [b["id"]]).json()["detail"]  # A → B → A
    c = _card(client, auth, p["id"], "C", waits_on=[b["id"]])
    assert "circle" in patch(a, [c["id"]]).json()["detail"]  # A → C → B → A
    assert patch(a, [elsewhere["id"]]).status_code == 422  # another project
    assert patch(a, [str(uuid.uuid4())]).status_code == 422
    assert patch(c, [b["id"], b["id"], a["id"]]).json()["waits_on"] == [b["id"], a["id"]]  # repeats dropped
    assert patch(c, []).json()["waits_on"] == []
    # A deleted card leaves the lists that named it.
    client.delete(f"/api/tasks/{a['id']}", headers=auth)
    assert client.get(f"/api/tasks/{b['id']}", headers=auth).json()["waits_on"] == []
    for pid in (p["id"], q["id"]):
        client.delete(f"/api/projects/{pid}", headers=auth)


def _sweep(now):
    async def go():
        from castor_server.db import SessionLocal
        from castor_server.due import check_due

        async with SessionLocal() as db:
            return await check_due(db, now)

    return asyncio.run(go())


def test_due_date_reminders(client, auth):
    p, cols = _project(client, auth)
    member, member_h = _user(client, auth)
    client.put(f"/api/projects/{p['id']}/members", json=[member["id"]], headers=auth)
    now = datetime.now(UTC)
    due = (now + timedelta(days=3)).isoformat()
    card = _card(client, auth, p["id"], f"Send the report {uuid.uuid4().hex[:4]}", due_at=due, assignees=[member["id"]])
    assert card["due_at"]
    done = _card(client, auth, p["id"], "Already done", due_at=(now - timedelta(hours=1)).isoformat(), column_id=cols["done"])

    def mine(kind):
        return [n for n in _notes(client, member_h, kind) if card["title"] in n["title"]]

    _sweep(now)
    assert not mine("task.due_soon")  # three days out: nothing yet
    _sweep(now + timedelta(days=2, hours=2))
    soon = mine("task.due_soon")
    assert len(soon) == 1 and "is due in" in soon[0]["title"]
    _sweep(now + timedelta(days=2, hours=3))
    assert len(mine("task.due_soon")) == 1  # once
    _sweep(now + timedelta(days=3, hours=1))
    overdue = mine("task.overdue")
    assert len(overdue) == 1 and overdue[0]["severity"] == "warning"
    _sweep(now + timedelta(days=3, hours=5))
    assert len(mine("task.overdue")) == 1  # once

    # A new date is reminded again; a finished card never is.
    client.patch(f"/api/tasks/{card['id']}", json={"due_at": (now + timedelta(days=10)).isoformat()}, headers=auth)
    _sweep(now + timedelta(days=9, hours=12))
    assert len(mine("task.due_soon")) == 2
    assert not [n for n in client.get("/api/notifications", headers=auth).json()["items"] if done["title"] in n["title"]]
    # Clearing the date.
    assert client.patch(f"/api/tasks/{card['id']}", json={"due_at": None}, headers=auth).json()["due_at"] is None
    client.delete(f"/api/projects/{p['id']}", headers=auth)
