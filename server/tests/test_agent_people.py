"""Agents only some people may use: work goes to them only for those people (or their groups' members)
and admins; anyone else's job waits; automation counts as whoever set it up."""

import json
import uuid

from castor_server.agent_people import Who, allowed


def _user(client, auth, role="operator"):
    name = f"pp{uuid.uuid4().hex[:8]}"
    user = client.post("/api/users", json={"username": name, "role": role, "password": "a password"}, headers=auth).json()
    token = client.post("/api/auth/login", json={"username": name, "password": "a password"}).json()["token"]
    client.cookies.clear()
    return user, {"Authorization": f"Bearer {token}"}


def test_the_rule():
    ana = Who(user_id="a", admin=False, groups={"red"})
    assert allowed(None, ana) and allowed(None, None)  # no limit: everyone
    assert allowed({"users": ["a"], "groups": []}, ana)
    assert allowed({"users": [], "groups": ["red"]}, ana)  # through a group
    assert not allowed({"users": ["b"], "groups": ["blue"]}, ana)
    assert not allowed({"users": ["a"]}, None)  # nobody to vouch for it
    assert allowed({"users": [], "groups": []}, Who(user_id="z", admin=True))  # admins always


def test_an_agent_runs_only_its_peoples_work(client, auth):
    code = f"PPL-{uuid.uuid4().hex[:5]}"
    project = client.post("/api/projects", json={"codename": code, "name": code, "kind": "internal"}, headers=auth).json()
    ana, ana_auth = _user(client, auth)
    bo, bo_auth = _user(client, auth)
    client.put(f"/api/projects/{project['id']}/members", json=[ana["id"], bo["id"]], headers=auth)
    name = f"people-agent-{uuid.uuid4().hex[:6]}"

    def card(who_auth, title):
        r = client.post(f"/api/projects/{project['id']}/tasks", json={
            "title": title, "kind": "command", "command": f"echo {title}", "agent_selector": {"agent_id": agent["id"]}}, headers=who_auth)
        assert r.status_code == 201, r.text
        return r.json()

    with client.websocket_connect("/ws/agent") as ws:
        ws.send_text(json.dumps({"type": "hello", "secret": "test-agent-secret", "name": name, "hostname": "box", "os": "Linux",
                                 "username": "u", "version": "1", "capabilities": {"runtimes": ["host"]}, "tags": [],
                                 "max_concurrency": 4}))
        assert json.loads(ws.receive_text())["type"] == "hello.ok"
        agent = next(x for x in client.get("/api/agents", headers=auth).json() if x["name"] == name)
        assert agent["people"] is None

        # Unknown people are refused; an operator can't set it.
        assert client.patch(f"/api/agents/{agent['id']}", json={"people": {"users": [str(uuid.uuid4())]}}, headers=auth).status_code == 422
        assert client.patch(f"/api/agents/{agent['id']}", json={"people": {"users": [ana["id"]]}}, headers=ana_auth).status_code == 403
        r = client.patch(f"/api/agents/{agent['id']}", json={"people": {"users": [ana["id"]]}}, headers=auth)
        assert r.status_code == 200 and r.json()["people"] == {"users": [ana["id"]], "groups": []}

        # Bo's run waits; Ana's goes.
        bos = card(bo_auth, "bo")
        job_bo = client.post(f"/api/tasks/{bos['id']}/run", headers=bo_auth).json()
        anas = card(ana_auth, "ana")
        client.post(f"/api/tasks/{anas['id']}/run", headers=ana_auth)
        run = json.loads(ws.receive_text())
        assert run["type"] == "job.run" and run["job"]["command"].startswith("echo ana")
        ws.send_text(json.dumps({"type": "job.finished", "job_id": run["job"]["id"], "exit_code": 0}))
        assert client.get(f"/api/jobs/{job_bo['id']}", headers=auth).json()["status"] == "queued"

        # Admins always may.
        mine = card(auth, "admin")
        client.post(f"/api/tasks/{mine['id']}/run", headers=auth)
        run = json.loads(ws.receive_text())
        assert run["job"]["command"].startswith("echo admin")
        ws.send_text(json.dumps({"type": "job.finished", "job_id": run["job"]["id"], "exit_code": 0}))

        # A group Bo joins lets his waiting job go; /auth/me says which groups he's in.
        slug = f"red{uuid.uuid4().hex[:5]}"
        group = client.post("/api/groups", json={"slug": slug, "name": "Red team"}, headers=auth).json()
        client.put(f"/api/groups/{group['id']}/members", json=[bo["id"]], headers=auth)
        assert group["id"] in client.get("/api/auth/me", headers=bo_auth).json()["group_ids"]
        r = client.patch(f"/api/agents/{agent['id']}", json={"people": {"users": [ana["id"]], "groups": [group["id"]]}}, headers=auth)
        assert r.status_code == 200
        run = json.loads(ws.receive_text())
        assert run["job"]["id"] == job_bo["id"]
        ws.send_text(json.dumps({"type": "job.finished", "job_id": run["job"]["id"], "exit_code": 0}))

        # A job nobody pressed Run for counts as whoever set it up: the card's maker here.
        from castor_server import models as m
        from castor_server.agent_people import run_as_for
        from castor_server.db import SessionLocal

        async def runs_as():
            async with SessionLocal() as db:
                task = await db.get(m.Task, uuid.UUID(anas["id"]))
                return str(await run_as_for(db, task, None))

        import asyncio

        assert asyncio.run(runs_as()) == ana["id"]

        # Everyone again.
        r = client.patch(f"/api/agents/{agent['id']}", json={"people": None}, headers=auth)
        assert r.json()["people"] is None
    client.delete(f"/api/projects/{project['id']}", headers=auth)
