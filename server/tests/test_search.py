"""Global search: what it finds, the filters, and what it must never show."""

import asyncio
import json
import uuid

from castor_server.search import parse


def _project(client, auth):
    code = f"SR-{uuid.uuid4().hex[:6]}"
    return client.post("/api/projects", json={"codename": code, "name": f"Search {code}", "kind": "internal"}, headers=auth).json()


def _login(client, auth, role="operator"):
    name = f"sr{uuid.uuid4().hex[:6]}"
    user = client.post("/api/users", json={"username": name, "role": role, "password": "a password"}, headers=auth).json()
    token = client.post("/api/auth/login", json={"username": name, "password": "a password"}).json()["token"]
    client.cookies.clear()
    return user, {"Authorization": f"Bearer {token}"}


def _search(client, headers, q):
    r = client.get("/api/search", params={"q": q}, headers=headers)
    assert r.status_code == 200, r.text
    return {g["type"]: g["hits"] for g in r.json()["groups"]}


def test_parse():
    q = parse('in:NWL26 type:comment,cards by:@Sam tag:smb is:mine status:failed relay "two words"')
    assert q.project == "NWL26" and q.types == {"comment", "card"} and q.by == "sam"
    assert q.tags == ["smb"] and q.mine and q.status == "failed" and q.terms == ["relay", "two words"]
    assert parse("   ").empty


def test_finds_cards_comments_files_runs_and_logs(client, auth):
    word = f"zebra{uuid.uuid4().hex[:5]}"
    p = _project(client, auth)
    task = client.post(f"/api/projects/{p['id']}/tasks", json={"title": f"Check {word} host", "tags": ["smb"],
                       "kind": "command", "command": "echo hi"}, headers=auth).json()
    client.post(f"/api/tasks/{task['id']}/comments", json={"body": f"the {word} box answers on 445"}, headers=auth)
    client.post(f"/api/tasks/{task['id']}/attachments", files={"file": (f"{word}-shot.png", b"png", "image/png")}, headers=auth)
    client.post(f"/api/projects/{p['id']}/notes", json={"title": "Scope", "body": f"only {word} subnets"}, headers=auth)

    # A run with a log line, and a parsed output on it.
    agent = f"sr-agent-{uuid.uuid4().hex[:5]}"
    client.patch(f"/api/tasks/{task['id']}", json={"agent_selector": {"tags": [agent]}}, headers=auth)
    with client.websocket_connect("/ws/agent") as ws:
        ws.send_text(json.dumps({"type": "hello", "secret": "test-agent-secret", "name": agent, "hostname": "h", "os": "Linux",
                                 "username": "u", "version": "0", "tags": [agent], "capabilities": {"runtimes": ["host"]},
                                 "max_concurrency": 1}))
        assert json.loads(ws.receive_text())["type"] == "hello.ok"
        client.post(f"/api/tasks/{task['id']}/run", headers=auth)
        while True:
            msg = json.loads(ws.receive_text())
            if msg["type"] == "job.run":
                job = msg["job"]
                if job["command"] == "echo hi":
                    break
                ws.send_text(json.dumps({"type": "job.finished", "job_id": job["id"], "exit_code": 0}))
        ws.send_text(json.dumps({"type": "job.log", "job_id": job["id"], "seq": 0, "stream": "stdout",
                                 "data": f"scanning...\n445/tcp open {word}-smb\ndone\n"}))
        ws.send_text(json.dumps({"type": "job.finished", "job_id": job["id"], "exit_code": 0}))
        for _ in range(50):
            if client.get(f"/api/jobs/{job['id']}", headers=auth).json()["status"] == "succeeded":
                break

    async def add_output():
        from castor_server import models as m
        from castor_server.db import SessionLocal

        async with SessionLocal() as db:
            db.add(m.JobOutput(job_id=uuid.UUID(job["id"]), name="hosts", type="list<host>", parser="nmap-hosts",
                               value=[{"ip": "10.9.9.9", "hostname": f"{word}.corp"}]))
            await db.commit()

    asyncio.run(add_output())

    found = _search(client, auth, word)
    assert found["card"][0]["title"] == f"Check {word} host" and found["card"][0]["link"].endswith(f"?task={task['id']}")
    assert word in found["comment"][0]["snippet"] and "&comment=" in found["comment"][0]["link"]
    assert found["file"][0]["title"] == f"{word}-shot.png" and "tab=attachments" in found["file"][0]["link"]
    assert found["note"][0]["title"] == "Scope"
    assert found["log"][0]["snippet"] == f"445/tcp open {word}-smb" and f"job={job['id']}" in found["log"][0]["link"]
    assert f"{word}.corp" in found["output"][0]["snippet"] and "view=results" in found["output"][0]["link"]

    # Filters.
    assert set(_search(client, auth, f"type:comment {word}")) == {"comment"}
    assert set(_search(client, auth, f"tag:smb {word}")) == {"card"}
    assert _search(client, auth, f"in:{p['codename']} {word}")["card"]
    assert not _search(client, auth, f"in:NOPE-NOPE {word}")
    assert _search(client, auth, f"by:admin {word}")["comment"]
    assert _search(client, auth, f"status:succeeded {word}")["card"]
    assert not _search(client, auth, f"status:failed {word}").get("card")

    # Someone who can't see the project finds nothing from it.
    _, other = _login(client, auth)
    assert not _search(client, other, word)


def test_mine_and_libraries(client, auth):
    word = f"okapi{uuid.uuid4().hex[:5]}"
    p = _project(client, auth)
    bob, bh = _login(client, auth)
    client.put(f"/api/projects/{p['id']}/members", json=[bob["id"]], headers=auth)
    mine = client.post(f"/api/projects/{p['id']}/tasks", json={"title": f"{word} for bob", "assignees": [bob["id"]]}, headers=auth).json()
    client.post(f"/api/projects/{p['id']}/tasks", json={"title": f"{word} for nobody"}, headers=auth)
    hits = _search(client, bh, f"is:mine {word}")
    assert [h["title"] for h in hits["card"]] == [mine["title"]]

    # Library things and people are found by name.
    assert _search(client, auth, "nmap")["command"]
    assert any(h["title"] == "Pipeline Demo" for h in _search(client, auth, "pipeline demo")["playbook"])
    assert _search(client, auth, bob["username"])["person"][0]["snippet"].startswith(f"@{bob['username']}")


def test_never_finds_secrets(client, auth):
    secret = f"Hunter2-{uuid.uuid4().hex[:6]}"
    r = client.post("/api/variables/sets", json={"kind_id": "credential", "name": f"creds-{uuid.uuid4().hex[:5]}",
                                                 "values": {"username": "svc_backup", "password": secret}}, headers=auth)
    assert r.status_code == 201, r.text
    assert not _search(client, auth, secret)
    assert _search(client, auth, r.json()["name"])["variable"]  # the set itself is findable by name
