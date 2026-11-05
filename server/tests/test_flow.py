"""End-to-end: project → board → command card → move to In Progress → fake agent runs it."""

import json
import time


def test_health(client):
    assert client.get("/api/health").json()["ok"] is True


def test_login_required(client):
    assert client.get("/api/projects").status_code == 401


def test_bad_login(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "nope"})
    assert r.status_code == 401


def test_full_job_flow(client, auth):
    # Project with default columns
    r = client.post(
        "/api/projects",
        json={"codename": "ACME-26-001", "name": "ACME external", "kind": "external"},
        headers=auth,
    )
    assert r.status_code == 201, r.text
    project = r.json()
    board = client.get(f"/api/projects/{project['id']}/board", headers=auth).json()
    roles = {c["role"]: c for c in board["columns"]}
    assert set(roles) >= {"todo", "active", "review", "done"}

    # Manual card + command card
    r = client.post(
        f"/api/projects/{project['id']}/tasks",
        json={"title": "Kickoff call", "kind": "manual"},
        headers=auth,
    )
    assert r.status_code == 201
    r = client.post(
        f"/api/projects/{project['id']}/tasks",
        json={
            "title": "Ping scan",
            "kind": "command",
            "command": "echo hello; echo oops >&2; exit 0",
            "tags": ["network"],
        },
        headers=auth,
    )
    assert r.status_code == 201, r.text
    task = r.json()
    assert task["column_id"] == roles["todo"]["id"]
    assert task["latest_job"] is None

    # Comment + custom fields
    r = client.post(f"/api/tasks/{task['id']}/comments", json={"body": "Scope OK"}, headers=auth)
    assert r.status_code == 201
    r = client.patch(
        f"/api/tasks/{task['id']}", json={"custom_fields": {"cvss": 7.5}}, headers=auth
    )
    assert r.json()["custom_fields"] == {"cvss": 7.5}
    assert r.json()["comment_count"] == 1

    # Fake agent connects
    with client.websocket_connect("/ws/agent") as agent_ws:
        agent_ws.send_text(
            json.dumps(
                {
                    "type": "hello",
                    "secret": "test-agent-secret",
                    "name": "test-agent",
                    "hostname": "testbox",
                    "os": "Linux",
                    "username": "root",
                    "version": "0.1.0",
                    "capabilities": {"runtimes": ["host"]},
                    "tags": ["lab"],
                    "max_concurrency": 1,
                }
            )
        )
        hello = json.loads(agent_ws.receive_text())
        assert hello["type"] == "hello.ok", hello
        agents = {a["name"]: a for a in client.get("/api/agents", headers=auth).json()}
        assert agents["test-agent"]["connected"] is True

        # Moving the command card to In Progress queues + dispatches a job
        r = client.post(
            f"/api/tasks/{task['id']}/move",
            json={"column_id": roles["active"]["id"], "position": 10},
            headers=auth,
        )
        assert r.status_code == 200, r.text
        assert r.json()["latest_job"]["status"] in ("queued", "dispatched")

        run = json.loads(agent_ws.receive_text())
        assert run["type"] == "job.run", run
        job = run["job"]
        assert job["command"].startswith("echo hello")
        assert job["runtime"] == "host"

        # Agent reports progress
        agent_ws.send_text(json.dumps({"type": "job.started", "job_id": job["id"]}))
        agent_ws.send_text(
            json.dumps({"type": "job.log", "job_id": job["id"], "seq": 0, "stream": "stdout", "data": "hello\n"})
        )
        agent_ws.send_text(
            json.dumps({"type": "job.log", "job_id": job["id"], "seq": 1, "stream": "stderr", "data": "oops\n"})
        )
        agent_ws.send_text(json.dumps({"type": "job.finished", "job_id": job["id"], "exit_code": 0}))

        # Wait for the server to process
        deadline = time.time() + 5
        while time.time() < deadline:
            detail = client.get(f"/api/jobs/{job['id']}", headers=auth).json()
            if detail["status"] == "succeeded":
                break
            time.sleep(0.05)
        assert detail["status"] == "succeeded", detail
        assert [c["data"] for c in detail["logs"]] == ["hello\n", "oops\n"]
        assert detail["stdout_bytes"] == 6 and detail["stderr_bytes"] == 5
        assert client.get(f"/api/jobs/{job['id']}/output?stream=stdout", headers=auth).text == "hello\n"

        # auto_advance moved the card to Review
        t = client.get(f"/api/tasks/{task['id']}", headers=auth).json()
        assert t["column_id"] == roles["review"]["id"]
        assert t["latest_job"]["exit_code"] == 0

        # Activity recorded
        activity = client.get(f"/api/projects/{project['id']}/activity", headers=auth).json()
        types = [a["type"] for a in activity]
        assert "job.succeeded" in types and "job.dispatched" in types and "task.moved" in types

    # After disconnect the agent is offline
    deadline = time.time() + 5
    while time.time() < deadline:
        agents = {a["name"]: a for a in client.get("/api/agents", headers=auth).json()}
        if agents["test-agent"]["status"] == "offline":
            break
        time.sleep(0.05)
    assert agents["test-agent"]["status"] == "offline" and agents["test-agent"]["connected"] is False


def test_queued_without_agent_then_cancel(client, auth):
    projects = client.get("/api/projects", headers=auth).json()
    project = projects[0]
    board = client.get(f"/api/projects/{project['id']}/board", headers=auth).json()
    active = next(c for c in board["columns"] if c["role"] == "active")
    r = client.post(
        f"/api/projects/{project['id']}/tasks",
        json={"title": "Needs container", "kind": "command", "command": "nmap -sn 10.0.0.0/24",
              "image": "instrumentisto/nmap", "column_id": active["id"]},
        headers=auth,
    )
    assert r.status_code == 201, r.text
    task = r.json()
    assert task["latest_job"]["status"] == "queued"
    # Explicit run while queued is rejected
    assert client.post(f"/api/tasks/{task['id']}/run", headers=auth).status_code == 409
    r = client.post(f"/api/jobs/{task['latest_job']['id']}/cancel", headers=auth)
    assert r.json()["status"] == "cancelled"
    # Now it can run again
    assert client.post(f"/api/tasks/{task['id']}/run", headers=auth).status_code == 201


def test_attachment_roundtrip(client, auth):
    projects = client.get("/api/projects", headers=auth).json()
    tasks = client.get(f"/api/projects/{projects[0]['id']}/board", headers=auth).json()["tasks"]
    task = tasks[0]
    r = client.post(
        f"/api/tasks/{task['id']}/attachments",
        files={"file": ("shot.png", b"\x89PNG fake", "image/png")},
        headers=auth,
    )
    assert r.status_code == 201, r.text
    att = r.json()
    assert att["kind"] == "screenshot" and att["size"] == 9
    r = client.get(f"/api/attachments/{att['id']}/download", headers=auth)
    assert r.content == b"\x89PNG fake"
    assert client.get(f"/api/tasks/{task['id']}", headers=auth).json()["attachment_count"] == 1


def test_run_moves_card_to_in_progress(client, auth):
    project = client.get("/api/projects", headers=auth).json()[0]
    board = client.get(f"/api/projects/{project['id']}/board", headers=auth).json()
    roles = {c["role"]: c["id"] for c in board["columns"]}
    # run_on_move is on (the default): a naive "move then run" would queue two jobs.
    r = client.post(
        f"/api/projects/{project['id']}/tasks",
        json={"title": "Run me", "kind": "command", "command": "id", "column_id": roles["todo"]},
        headers=auth,
    )
    task = r.json()
    assert task["column_id"] == roles["todo"]

    r = client.post(f"/api/tasks/{task['id']}/run", headers=auth)
    assert r.status_code == 201, r.text
    after = client.get(f"/api/tasks/{task['id']}", headers=auth).json()
    assert after["column_id"] == roles["active"]
    jobs = client.get(f"/api/tasks/{task['id']}/jobs", headers=auth).json()
    assert len(jobs) == 1 and jobs[0]["status"] == "queued"
    history = client.get(f"/api/tasks/{task['id']}/activity", headers=auth).json()
    moves = [h for h in history if h["type"] == "task.moved"]
    assert len(moves) == 1 and moves[0]["payload"]["reason"] == "run"

    # Already queued: a second Run is refused and nothing moves.
    assert client.post(f"/api/tasks/{task['id']}/run", headers=auth).status_code == 409
    client.post(f"/api/jobs/{jobs[0]['id']}/cancel", headers=auth)

    # Running a card that is already in In Progress leaves it where it is.
    position = client.get(f"/api/tasks/{task['id']}", headers=auth).json()["position"]
    assert client.post(f"/api/tasks/{task['id']}/run", headers=auth).status_code == 201
    again = client.get(f"/api/tasks/{task['id']}", headers=auth).json()
    assert again["column_id"] == roles["active"] and again["position"] == position
    history = client.get(f"/api/tasks/{task['id']}/activity", headers=auth).json()
    assert len([h for h in history if h["type"] == "task.moved"]) == 1


def test_edit_comment(client, auth):
    project = client.get("/api/projects", headers=auth).json()[0]
    task = client.post(
        f"/api/projects/{project['id']}/tasks", json={"title": "Edit me"}, headers=auth
    ).json()
    c = client.post(f"/api/tasks/{task['id']}/comments", json={"body": "frist"}, headers=auth).json()
    assert c["edited_at"] is None

    # Saving the same text is not an edit.
    r = client.patch(f"/api/tasks/{task['id']}/comments/{c['id']}", json={"body": "frist"}, headers=auth)
    assert r.status_code == 200 and r.json()["edited_at"] is None

    r = client.patch(f"/api/tasks/{task['id']}/comments/{c['id']}", json={"body": "first"}, headers=auth)
    assert r.status_code == 200, r.text
    edited = r.json()
    assert edited["body"] == "first" and edited["edited_at"] is not None
    assert edited["author_name"] == "admin"
    listed = client.get(f"/api/tasks/{task['id']}/comments", headers=auth).json()
    assert listed[0]["body"] == "first" and listed[0]["edited_at"] == edited["edited_at"]

    r = client.patch(f"/api/tasks/{task['id']}/comments/{c['id']}", json={"body": "  "}, headers=auth)
    assert r.status_code == 400


def test_edit_comment_requires_author_or_admin(client, auth):
    import asyncio

    from castor_server import models as m
    from castor_server.db import SessionLocal
    from castor_server.security import create_session_token, hash_password

    async def make_user():
        async with SessionLocal() as db:
            user = m.User(username="viewer", display_name="viewer", password_hash=hash_password("x"), role=m.UserRole.operator)
            db.add(user)
            await db.commit()
            return user.id

    other_id = asyncio.run(make_user())
    other = {"Authorization": f"Bearer {create_session_token(other_id)}"}
    project = client.get("/api/projects", headers=auth).json()[0]
    members = [u["id"] for u in client.get(f"/api/projects/{project['id']}/members", headers=auth).json()]
    client.put(f"/api/projects/{project['id']}/members", json=[*members, str(other_id)], headers=auth)
    task = client.post(f"/api/projects/{project['id']}/tasks", json={"title": "Owned"}, headers=auth).json()
    c = client.post(f"/api/tasks/{task['id']}/comments", json={"body": "mine"}, headers=auth).json()
    r = client.patch(f"/api/tasks/{task['id']}/comments/{c['id']}", json={"body": "theirs"}, headers=other)
    assert r.status_code == 403


async def test_migrates_database_created_before_alembic(tmp_path):
    """A database built with create_all (no alembic_version) is stamped and upgraded."""
    import asyncio
    import sqlite3

    from alembic import command

    from castor_server.db import BASELINE_REVISION, alembic_config, run_migrations

    url = f"sqlite+aiosqlite:///{tmp_path / 'old.db'}"
    # Alembic's env.py runs its own event loop, so drive it from a worker thread.
    await asyncio.to_thread(command.upgrade, alembic_config(url), BASELINE_REVISION)
    con = sqlite3.connect(tmp_path / "old.db")
    con.execute("DROP TABLE alembic_version")
    con.execute(
        "INSERT INTO users (id, username, display_name, password_hash, is_admin, created_at, updated_at)"
        " VALUES ('00000000000000000000000000000001', 'u', 'u', 'x', 1, '2026-01-01', '2026-01-01')"
    )
    con.commit()
    con.close()

    await run_migrations(url)

    con = sqlite3.connect(tmp_path / "old.db")
    columns = {row[1] for row in con.execute("PRAGMA table_info(comments)")}
    version = con.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    users = con.execute("SELECT count(*) FROM users").fetchone()[0]
    con.close()
    assert "edited_at" in columns
    assert version != BASELINE_REVISION
    assert users == 1  # existing data survives


def test_new_projects_end_with_reported(client, auth):
    r = client.post("/api/projects", json={"codename": "RPT-1", "name": "Reported order"}, headers=auth)
    board = client.get(f"/api/projects/{r.json()['id']}/board", headers=auth).json()
    assert [c["role"] for c in board["columns"]] == ["backlog", "todo", "active", "review", "done", "reported"]
    assert board["columns"][-1]["name"] == "Reported"


async def test_reported_migration_adds_column_after_done(tmp_path):
    import asyncio
    import sqlite3
    import uuid

    from alembic import command

    from castor_server.db import alembic_config

    url = f"sqlite+aiosqlite:///{tmp_path / 'r.db'}"
    cfg = alembic_config(url)
    await asyncio.to_thread(command.upgrade, cfg, "50c19d965b5a")  # before the Reported migration
    con = sqlite3.connect(tmp_path / "r.db")
    pid = uuid.uuid4().hex
    con.execute(
        "INSERT INTO projects (id, codename, name, kind, status, description, settings, created_at, updated_at)"
        " VALUES (?, 'P', 'P', 'external', 'active', '', '{}', '2026-01-01', '2026-01-01')",
        (pid,),
    )
    done_id = uuid.uuid4().hex
    for name, role, pos, cid in [
        ("To Do", "todo", 10, uuid.uuid4().hex),
        ("Done", "done", 40, done_id),
        ("Archive", None, 60, uuid.uuid4().hex),  # a custom column after Done
    ]:
        con.execute(
            "INSERT INTO board_columns (id, project_id, name, position, role, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, '2026-01-01', '2026-01-01')",
            (cid, pid, name, pos, role),
        )
    con.commit()
    con.close()

    await asyncio.to_thread(command.upgrade, cfg, "head")
    con = sqlite3.connect(tmp_path / "r.db")
    cols = con.execute("SELECT name, role, position FROM board_columns ORDER BY position").fetchall()
    assert [c[0] for c in cols] == ["To Do", "Done", "Reported", "Archive"]
    assert cols[2][1:] == ("reported", 50.0)

    # Downgrade keeps cards: they move from Reported back to Done.
    reported_id = con.execute("SELECT id FROM board_columns WHERE role='reported'").fetchone()[0]
    con.execute(
        "INSERT INTO tasks (id, project_id, column_id, position, title, description, kind, command,"
        " agent_selector, run_on_move, priority, tags, assignees, custom_fields, created_at, updated_at)"
        " VALUES (?, ?, ?, 10, 'T', '', 'manual', '', '{}', 1, 0, '[]', '[]', '{}', '2026-01-01', '2026-01-01')",
        (uuid.uuid4().hex, pid, reported_id),
    )
    con.commit()
    con.close()
    await asyncio.to_thread(command.downgrade, cfg, "50c19d965b5a")
    con = sqlite3.connect(tmp_path / "r.db")
    assert con.execute("SELECT count(*) FROM board_columns WHERE role='reported'").fetchone()[0] == 0
    assert con.execute("SELECT column_id FROM tasks").fetchone()[0] == done_id
    con.close()
