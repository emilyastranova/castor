"""Archived cards: off the board until restored, a column at a time, never while running."""

import asyncio
import uuid


def _project(client, auth):
    code = f"AR-{uuid.uuid4().hex[:6]}"
    p = client.post("/api/projects", json={"codename": code, "name": code, "kind": "internal"}, headers=auth).json()
    board = client.get(f"/api/projects/{p['id']}/board", headers=auth).json()
    return p, {c["role"]: c["id"] for c in board["columns"] if c["role"]}


def _card(client, auth, pid, title, column=None, **kw):
    body = {"title": title, "kind": "manual", **({"column_id": column} if column else {}), **kw}
    r = client.post(f"/api/projects/{pid}/tasks", json=body, headers=auth)
    assert r.status_code == 201, r.text
    return r.json()


def _board_ids(client, auth, pid):
    return {t["id"] for t in client.get(f"/api/projects/{pid}/board", headers=auth).json()["tasks"]}


def _queue_job(task_id):
    async def go():
        from castor_server import models as m
        from castor_server.db import SessionLocal

        async with SessionLocal() as db:
            db.add(m.Job(task_id=uuid.UUID(task_id), command="sleep 1", status=m.JobStatus.queued))
            await db.commit()

    asyncio.run(go())


def test_archive_and_restore(client, auth):
    p, cols = _project(client, auth)
    a = _card(client, auth, p["id"], f"Kickoff notes {uuid.uuid4().hex[:4]}", tags=["meeting"])
    b = _card(client, auth, p["id"], "Still on the board")

    r = client.post(f"/api/tasks/{a['id']}/archive", headers=auth)
    assert r.status_code == 200 and r.json()["archived_at"] and r.json()["archived_by"]
    assert _board_ids(client, auth, p["id"]) == {b["id"]}
    assert client.get(f"/api/tasks/{a['id']}", headers=auth).json()["archived_at"]  # still reachable by link
    listed = client.get(f"/api/projects/{p['id']}/archived", headers=auth).json()
    assert listed["total"] == 1 and listed["items"][0]["id"] == a["id"]
    assert client.get(f"/api/projects/{p['id']}/archived", params={"q": "meeting"}, headers=auth).json()["total"] == 1
    assert client.get(f"/api/projects/{p['id']}/archived", params={"q": "nothing-like-it"}, headers=auth).json()["total"] == 0
    # Archiving twice changes nothing; an archived card can't move or run until restored.
    assert client.post(f"/api/tasks/{a['id']}/archive", headers=auth).status_code == 200
    moved = client.post(f"/api/tasks/{a['id']}/move", json={"column_id": cols["active"]}, headers=auth)
    assert moved.status_code == 409 and "restore it first" in moved.json()["detail"]
    hits = client.get("/api/search", params={"q": a["title"]}, headers=auth)
    assert f"{a['title']} (archived)" in hits.text  # search still finds it, marked
    # A copy of an archived card is on the board.
    copy = client.post(f"/api/tasks/{a['id']}/duplicate", json={}, headers=auth).json()
    assert copy["archived_at"] is None and copy["id"] in _board_ids(client, auth, p["id"])

    restored = client.post(f"/api/tasks/{a['id']}/restore", headers=auth).json()
    assert restored["archived_at"] is None and restored["column_id"] == a["column_id"]
    assert a["id"] in _board_ids(client, auth, p["id"])
    types = [e["type"] for e in client.get(f"/api/projects/{p['id']}/activity", headers=auth).json()]
    assert "task.archived" in types and "task.restored" in types
    client.delete(f"/api/projects/{p['id']}", headers=auth)


def test_running_cards_stay_and_columns_archive_together(client, auth):
    p, cols = _project(client, auth)
    done = [_card(client, auth, p["id"], f"Done {i}", column=cols["done"]) for i in range(3)]
    busy = _card(client, auth, p["id"], "Busy", column=cols["done"], kind="command", command="sleep 60")
    _queue_job(busy["id"])
    other = _card(client, auth, p["id"], "In To Do")

    refused = client.post(f"/api/tasks/{busy['id']}/archive", headers=auth)
    assert refused.status_code == 409 and "running" in refused.json()["detail"]
    r = client.post(f"/api/projects/{p['id']}/columns/{cols['done']}/archive", headers=auth).json()
    assert r["archived"] == 3 and len(r["skipped"]) == 1 and "Busy" in r["skipped"][0]
    assert _board_ids(client, auth, p["id"]) == {busy["id"], other["id"]}
    assert {t["id"] for t in client.get(f"/api/projects/{p['id']}/archived", headers=auth).json()["items"]} == {d["id"] for d in done}
    # Only people who can change the project archive; a column of another project is not found.
    q, qcols = _project(client, auth)
    assert client.post(f"/api/projects/{p['id']}/columns/{qcols['done']}/archive", headers=auth).status_code == 404
    for pid in (p["id"], q["id"]):
        client.delete(f"/api/projects/{pid}", headers=auth)


def test_mcp_has_archive_tools():
    from castor_server.mcp import TOOLS

    names = {t.name for t in TOOLS}
    assert {"archive_card", "restore_card", "archive_column", "list_archived_cards"} <= names
