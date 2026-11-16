"""Services (service_jobs.py): commands that run until stopped. Their own slots on an agent, no
timeout, ready and watch read from their output as it comes, outputs parsed while they run, and a
graceful stop (or restart) that ends them as done. A fake agent speaks the protocol (see test_flow)."""

import json
import time
import uuid

SERVICE = """
id: test-relay-{n}
name: Relay
service:
  stop_signal: INT
  stop_timeout: 7
  ready: listening on
  watch: [{{match: 'connection from (\\S+)', say: A client connected}}]
  parse_every: 5
args: [{{name: port, type: number, default: 9000}}]
command: relay --port {{{{ port }}}}
outputs:
  - {{name: lines, parser: lines}}
"""


def _project(client, auth):
    code = f"SV-{uuid.uuid4().hex[:6]}"
    return client.post("/api/projects", json={"codename": code, "name": code, "kind": "internal"}, headers=auth).json()


def _agent(client, name, tag, services=True):
    ws = client.websocket_connect("/ws/agent")
    agent = ws.__enter__()
    caps = {"runtimes": ["host"], **({"services": True, "max_services": 1} if services else {})}
    agent.send_text(json.dumps({"type": "hello", "secret": "test-agent-secret", "name": name, "hostname": "h", "os": "Linux",
                                "username": "root", "version": "1.8.0", "capabilities": caps, "tags": [tag], "max_concurrency": 1}))
    assert json.loads(agent.receive_text())["type"] == "hello.ok"
    return ws, agent


def _next(agent, kind):
    while True:
        msg = json.loads(agent.receive_text())
        if msg["type"] == kind:
            return msg


def _until(fn, seconds=5.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        out = fn()
        if out:
            return out
        time.sleep(0.05)
    raise AssertionError("timed out")


def test_a_service_runs_until_stopped(client, auth):
    n = uuid.uuid4().hex[:6]
    tag = f"svc-{n}"
    assert client.post("/api/commands", json={"yaml": SERVICE.format(n=n)}, headers=auth).status_code == 201
    project = _project(client, auth)
    card = client.post(f"/api/projects/{project['id']}/tasks", json={
        "title": "Relay", "kind": "command", "command_ref": f"test-relay-{n}", "agent_selector": {"tags": [tag]}}, headers=auth).json()
    scan = client.post(f"/api/projects/{project['id']}/tasks", json={
        "title": "Scan", "kind": "command", "command": "echo scanning", "agent_selector": {"tags": [tag]}}, headers=auth).json()

    # An agent from before services isn't sent one.
    old_ws, _old = _agent(client, f"old-{n}", tag, services=False)
    job = client.post(f"/api/tasks/{card['id']}/run", headers=auth).json()
    time.sleep(0.5)
    assert client.get(f"/api/jobs/{job['id']}", headers=auth).json()["status"] == "queued"
    old_ws.__exit__(None, None, None)

    ws, agent = _agent(client, f"new-{n}", tag)
    try:
        run = _next(agent, "job.run")["job"]
        assert run["id"] == job["id"] and run["service"] == {"stop_signal": "INT", "stop_timeout": 7} and run["timeout_s"] is None
        agent.send_text(json.dumps({"type": "job.started", "job_id": run["id"]}))
        # Its own slot: an ordinary job still gets the agent's one job slot.
        client.post(f"/api/tasks/{scan['id']}/run", headers=auth)
        other = _next(agent, "job.run")["job"]
        assert other["command"] == "echo scanning"
        agent.send_text(json.dumps({"type": "job.finished", "job_id": other["id"], "exit_code": 0}))

        # Ready, then a watched line (once, however often it's printed).
        agent.send_text(json.dumps({"type": "job.log", "job_id": run["id"], "seq": 0, "stream": "stdout", "data": "relay listening on 9000\n"}))
        _until(lambda: (client.get(f"/api/jobs/{run['id']}", headers=auth).json().get("progress") or {}).get("ready_at"))
        for seq in (1, 2):
            agent.send_text(json.dumps({"type": "job.log", "job_id": run["id"], "seq": seq, "stream": "stdout",
                                        "data": "connection from 10.4.4.4\n"}))
        progress = _until(lambda: (p := client.get(f"/api/jobs/{run['id']}", headers=auth).json().get("progress") or {}).get("matches") and p)
        time.sleep(0.3)
        progress = client.get(f"/api/jobs/{run['id']}", headers=auth).json()["progress"]
        assert [x["line"] for x in progress["matches"]] == ["connection from 10.4.4.4"]
        notes = [x for x in client.get("/api/notifications", headers=auth).json()["items"] if x["kind"] == "job.watch"]
        assert notes and notes[0]["title"] == "“Relay”: A client connected"

        # Parsed while it runs.
        from castor_server import service_jobs
        import asyncio

        asyncio.run(service_jobs.parse_running())
        outputs = client.get(f"/api/jobs/{run['id']}/outputs", headers=auth).json()
        assert outputs and outputs[0]["value"] == ["relay listening on 9000", "connection from 10.4.4.4", "connection from 10.4.4.4"]

        # A graceful stop: the agent is asked for SIGINT and 7 s; exiting 130 on it still counts as done.
        assert client.post(f"/api/jobs/{other['id']}/stop", headers=auth).status_code == 409  # not a service
        r = client.post(f"/api/jobs/{run['id']}/stop", headers=auth)
        assert r.status_code == 200, r.text
        stop = _next(agent, "job.stop")
        assert stop["signal"] == "INT" and stop["timeout"] == 7 and stop["job_id"] == run["id"]
        agent.send_text(json.dumps({"type": "job.finished", "job_id": run["id"], "exit_code": 130}))
        done = _until(lambda: (j := client.get(f"/api/jobs/{run['id']}", headers=auth).json())["status"] != "running" and j)
        assert done["status"] == "succeeded" and done["exit_code"] == 130 and not done["error"]
        feed = client.get(f"/api/projects/{project['id']}/activity", headers=auth).json()
        assert any(a["type"] == "job.succeeded" and "was stopped" in a["message"] for a in feed)

        # Restart: stopped, then run again.
        job2 = client.post(f"/api/tasks/{card['id']}/run", headers=auth).json()
        run2 = _next(agent, "job.run")["job"]
        assert run2["id"] == job2["id"]
        agent.send_text(json.dumps({"type": "job.started", "job_id": run2["id"]}))
        _until(lambda: client.get(f"/api/jobs/{run2['id']}", headers=auth).json()["status"] == "running")
        assert client.post(f"/api/jobs/{run2['id']}/restart", headers=auth).status_code == 200
        _next(agent, "job.stop")
        agent.send_text(json.dumps({"type": "job.finished", "job_id": run2["id"], "exit_code": 0}))
        run3 = _next(agent, "job.run")["job"]
        assert run3["id"] not in (run["id"], run2["id"]) and run3["service"]
        client.post(f"/api/jobs/{run3['id']}/cancel", headers=auth)
    finally:
        ws.__exit__(None, None, None)
        client.delete(f"/api/commands/test-relay-{n}", headers=auth)
        client.delete(f"/api/projects/{project['id']}", headers=auth)


def test_a_typed_command_can_be_a_service(client, auth):
    project = _project(client, auth)
    card = client.post(f"/api/projects/{project['id']}/tasks", json={
        "title": "Tail", "kind": "command", "command": "tail -f app.log", "service": {"stop_signal": "term", "stop_timeout": 3}}, headers=auth)
    assert card.status_code == 201, card.text
    assert card.json()["service"]["stop_signal"] == "TERM"
    bad = client.post(f"/api/projects/{project['id']}/tasks", json={"title": "x", "kind": "command", "command": "x",
                                                                    "service": {"stop_signal": "KILL"}}, headers=auth)
    assert bad.status_code == 422
    cleared = client.patch(f"/api/tasks/{card.json()['id']}", json={"service": None}, headers=auth).json()
    assert cleared["service"] is None
    client.delete(f"/api/projects/{project['id']}", headers=auth)
