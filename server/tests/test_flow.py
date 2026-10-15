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
            "tags": ["recon"],
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
        agents = client.get("/api/agents", headers=auth).json()
        assert agents[0]["name"] == "test-agent" and agents[0]["connected"] is True

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
        agents = client.get("/api/agents", headers=auth).json()
        if agents[0]["status"] == "offline":
            break
        time.sleep(0.05)
    assert agents[0]["status"] == "offline" and agents[0]["connected"] is False


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
