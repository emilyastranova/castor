"""Agents limited to projects: they run only those projects' jobs, cards can't name them
elsewhere, people see only the agents of their projects, and a deleted project leaves them."""

import json
import uuid

from castor_server.orchestrator.dispatcher import project_allowed
from castor_server.orchestrator.gateway import AgentConnection


def _project(client, auth, tag):
    code = f"AP{tag}-{uuid.uuid4().hex[:5]}"
    p = client.post("/api/projects", json={"codename": code, "name": code, "kind": "internal"}, headers=auth).json()
    return p


def _user(client, auth, role="operator"):
    name = f"ap{uuid.uuid4().hex[:8]}"
    user = client.post("/api/users", json={"username": name, "role": role, "password": "a password"}, headers=auth).json()
    token = client.post("/api/auth/login", json={"username": name, "password": "a password"}).json()["token"]
    client.cookies.clear()
    return user, {"Authorization": f"Bearer {token}"}


def test_project_allowed():
    conn = AgentConnection(agent_id=uuid.uuid4(), name="a", ws=None, capabilities={}, tags=[], max_concurrency=1)
    pid = uuid.uuid4()
    assert project_allowed(conn, pid)  # no limit
    conn.project_ids = [str(pid)]
    assert project_allowed(conn, pid) and not project_allowed(conn, uuid.uuid4())
    conn.project_ids = []
    assert not project_allowed(conn, pid)  # limited to nothing: runs nothing


def test_limited_agent_runs_only_its_projects_jobs(client, auth):
    a, b = _project(client, auth, "A"), _project(client, auth, "B")
    tok = client.post("/api/agent-tokens", json={"label": "lab-a", "project_ids": [a["id"]]}, headers=auth).json()
    assert tok["project_ids"] == [a["id"]]
    name = f"proj-agent-{uuid.uuid4().hex[:6]}"
    card_b = client.post(f"/api/projects/{b['id']}/tasks", json={"title": "B's scan", "kind": "command", "command": "echo b"}, headers=auth).json()
    card_a = client.post(f"/api/projects/{a['id']}/tasks", json={"title": "A's scan", "kind": "command", "command": "echo a"}, headers=auth).json()

    with client.websocket_connect("/ws/agent") as ws:
        ws.send_text(json.dumps({"type": "hello", "token": tok["token"], "name": name, "hostname": "box", "os": "Linux",
                                 "username": "u", "version": "1", "capabilities": {"runtimes": ["host"]}, "tags": [], "max_concurrency": 2}))
        assert json.loads(ws.receive_text())["type"] == "hello.ok"
        agent = next(x for x in client.get("/api/agents", headers=auth).json() if x["name"] == name)
        assert agent["project_ids"] == [a["id"]]  # from its token

        # A card can't be pinned to it in another project.
        pinned = client.patch(f"/api/tasks/{card_b['id']}", json={"agent_selector": {"agent_id": agent["id"]}}, headers=auth)
        assert pinned.status_code == 422 and "doesn't run this project's jobs" in pinned.json()["detail"]
        assert client.patch(f"/api/tasks/{card_a['id']}", json={"agent_selector": {"agent_id": agent["id"]}}, headers=auth).status_code == 200

        job_b = client.post(f"/api/tasks/{card_b['id']}/run", headers=auth).json()
        client.post(f"/api/tasks/{card_a['id']}/run", headers=auth)
        run = json.loads(ws.receive_text())
        assert run["type"] == "job.run" and run["job"]["command"].startswith("echo a")  # A's, not B's
        assert client.get(f"/api/jobs/{job_b['id']}", headers=auth).json()["status"] == "queued"

        # Lifting the limit lets it take B's job.
        r = client.patch(f"/api/agents/{agent['id']}", json={"project_ids": None}, headers=auth)
        assert r.status_code == 200 and r.json()["project_ids"] is None
        run = json.loads(ws.receive_text())
        assert run["type"] == "job.run" and run["job"]["id"] == job_b["id"]
        for job in (run["job"]["id"],):
            client.post(f"/api/jobs/{job}/cancel", headers=auth)
    for p in (a, b):
        client.delete(f"/api/projects/{p['id']}", headers=auth)


def test_who_sees_limited_agents_and_deleted_projects(client, auth):
    a, b = _project(client, auth, "C"), _project(client, auth, "D")

    async def make():
        from castor_server import models as m
        from castor_server.db import SessionLocal

        async with SessionLocal() as db:
            agent = m.Agent(name=f"limited-{uuid.uuid4().hex[:6]}", project_ids=[a["id"]])
            db.add(agent)
            await db.commit()
            return str(agent.id), agent.name

    import asyncio

    agent_id, name = asyncio.run(make())
    member, member_h = _user(client, auth)
    _outsider, outsider_h = _user(client, auth)
    client.put(f"/api/projects/{a['id']}/members", json=[member["id"]], headers=auth)
    names = lambda h: {x["name"] for x in client.get("/api/agents", headers=h).json()}
    assert name in names(member_h) and name not in names(outsider_h) and name in names(auth)

    # Only admins limit agents, to projects that exist.
    assert client.patch(f"/api/agents/{agent_id}", json={"project_ids": [b["id"]]}, headers=member_h).status_code == 403
    bad = client.patch(f"/api/agents/{agent_id}", json={"project_ids": [str(uuid.uuid4())]}, headers=auth)
    assert bad.status_code == 422
    r = client.patch(f"/api/agents/{agent_id}", json={"project_ids": [a["id"], b["id"]]}, headers=auth)
    assert r.json()["project_ids"] == [a["id"], b["id"]]
    assert client.patch(f"/api/agents/{agent_id}", json={"tags": ["x"]}, headers=auth).json()["project_ids"] == [a["id"], b["id"]]  # left as it was

    # Deleting projects takes them out; with none left it runs nothing, rather than anything.
    client.delete(f"/api/projects/{b['id']}", headers=auth)
    assert next(x for x in client.get("/api/agents", headers=auth).json() if x["id"] == agent_id)["project_ids"] == [a["id"]]
    client.delete(f"/api/projects/{a['id']}", headers=auth)
    assert next(x for x in client.get("/api/agents", headers=auth).json() if x["id"] == agent_id)["project_ids"] == []
    client.delete(f"/api/agents/{agent_id}", headers=auth)
