"""Previews and retrying what failed (command_spec.PreviewSpec, RetryFailedSpec; services.after_preview,
retry_failed): a dry run a person approves the real run after, and running again only what failed, on a
card and as a playbook step's `retry_failed`. A fake agent speaks the protocol (see test_flow)."""

import json
import time
import uuid

COMMAND = """
id: test-deploy-{n}
name: Deploy
args:
  - {{name: check, type: boolean, flag: --check}}
  - {{name: limit, type: string}}
command: deploy {{{{ check }}}} --limit {{{{ limit }}}}
outputs:
  - {{name: results, parser: json-records}}
preview:
  label: Check run
  args: {{check: true}}
retry_failed:
  output: results
  field: host
  where: {{status: [failed, unreachable]}}
  arg: limit
"""

PLAYBOOK = """
id: test-deploy-pb-{n}
name: Deploy all
steps:
  - id: deploy
    name: Deploy
    command_ref: test-deploy-{n}
    args: {{limit: all}}
    preview: false
    retry_failed: 1
"""


def _project(client, auth):
    code = f"PV-{uuid.uuid4().hex[:6]}"
    return client.post("/api/projects", json={"codename": code, "name": code, "kind": "internal"}, headers=auth).json()


def _agent(client, name, tag):
    ws = client.websocket_connect("/ws/agent")
    agent = ws.__enter__()
    agent.send_text(json.dumps({"type": "hello", "secret": "test-agent-secret", "name": name, "hostname": "h", "os": "Linux",
                                "username": "root", "version": "1.8.0", "capabilities": {"runtimes": ["host"]},
                                "tags": [tag], "max_concurrency": 1}))
    assert json.loads(agent.receive_text())["type"] == "hello.ok"
    return ws, agent


def _next(agent, kind):
    while True:
        msg = json.loads(agent.receive_text())
        if msg["type"] == kind:
            return msg


def _until(fn, seconds=20.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        out = fn()
        if out:
            return out
        time.sleep(0.05)
    raise AssertionError("timed out")


def _finish(client, auth, agent, job_id, rows, exit_code):
    agent.send_text(json.dumps({"type": "job.started", "job_id": job_id}))
    agent.send_text(json.dumps({"type": "job.log", "job_id": job_id, "seq": 0, "stream": "stdout", "data": json.dumps(rows) + "\n"}))
    agent.send_text(json.dumps({"type": "job.finished", "job_id": job_id, "exit_code": exit_code}))
    _until(lambda: client.get(f"/api/jobs/{job_id}/outputs", headers=auth).json())


ROWS = [{"host": "web1", "status": "ok"}, {"host": "web2", "status": "failed"}, {"host": "db1", "status": "unreachable"},
        {"host": "web2", "status": "failed"}]


def test_spec_checks():
    from castor_server.command_spec import parse_spec

    good = parse_spec(COMMAND.format(n="x"))
    assert good.preview.args == {"check": True} and good.retry_failed.values(ROWS) == ["web2", "db1"]
    for bad, says in [
        ("  args: {check: true}", "  args: {nope: true}"),
        ("  arg: limit", "  arg: check"),
        ("  output: results", "  output: other"),
    ]:
        try:
            parse_spec(COMMAND.format(n="x").replace(bad, says))
        except ValueError:
            continue
        raise AssertionError(f"accepted {says}")


def test_a_preview_then_the_approved_run_then_retrying_what_failed(client, auth):
    n = uuid.uuid4().hex[:6]
    tag = f"pv-{n}"
    assert client.post("/api/commands", json={"yaml": COMMAND.format(n=n)}, headers=auth).status_code == 201
    project = _project(client, auth)
    card = client.post(f"/api/projects/{project['id']}/tasks", json={
        "title": "Deploy", "kind": "command", "command_ref": f"test-deploy-{n}", "command_args": {"limit": "all"},
        "agent_selector": {"tags": [tag]}}, headers=auth).json()
    assert card["command"] == "deploy --limit all"
    ws, agent = _agent(client, f"pv-{n}", tag)
    try:
        # The preview runs first, with its own argument values; the card keeps its own.
        job = client.post(f"/api/tasks/{card['id']}/run", headers=auth).json()
        running_in = client.get(f"/api/tasks/{card['id']}", headers=auth).json()["column_id"]
        run = _next(agent, "job.run")["job"]
        assert run["id"] == job["id"] and run["command"] == "deploy --check --limit all"
        got = client.get(f"/api/jobs/{job['id']}", headers=auth).json()
        assert got["purpose"] == "preview" and got["args_override"] == {"check": True}
        _finish(client, auth, agent, run["id"], ROWS[:1], 0)

        # Then the real run waits for a person, pointing at the preview.
        real = _until(lambda: (j := client.get(f"/api/tasks/{card['id']}", headers=auth).json().get("latest_job"))
                      and j["id"] != job["id"] and j)
        assert real["status"] == "approval" and real["follows_job_id"] == job["id"] and real["purpose"] is None
        assert client.get(f"/api/tasks/{card['id']}", headers=auth).json()["column_id"] == running_in  # not moved on
        feed = client.get(f"/api/projects/{project['id']}/activity", headers=auth).json()
        assert any(a["type"] == "job.approval_requested" and "check run is ready" in a["message"] for a in feed)
        assert client.post(f"/api/jobs/{real['id']}/approve", headers=auth).status_code == 200
        run2 = _next(agent, "job.run")["job"]
        assert run2["id"] == real["id"] and run2["command"] == "deploy --limit all"
        _finish(client, auth, agent, run2["id"], ROWS, 2)
        _until(lambda: client.get(f"/api/jobs/{real['id']}", headers=auth).json()["status"] == "failed")

        # What failed, and running only that again (no preview, the card's arguments as they were).
        plan = client.get(f"/api/jobs/{real['id']}/retry-failed", headers=auth)
        assert plan.status_code == 200 and plan.json() == {"arg": "limit", "values": ["web2", "db1"]}
        assert client.get(f"/api/jobs/{job['id']}/retry-failed", headers=auth).status_code == 404  # a preview
        retry = client.post(f"/api/jobs/{real['id']}/retry-failed", headers=auth)
        assert retry.status_code == 201, retry.text
        assert retry.json()["purpose"] == "retry" and retry.json()["follows_job_id"] == real["id"]
        run3 = _next(agent, "job.run")["job"]
        assert run3["command"] == "deploy --limit web2,db1"
        assert client.get(f"/api/tasks/{card['id']}", headers=auth).json()["command_args"] == {"limit": "all"}
        _finish(client, auth, agent, run3["id"], [{"host": "web2", "status": "ok"}, {"host": "db1", "status": "ok"}], 0)
        _until(lambda: client.get(f"/api/jobs/{run3['id']}", headers=auth).json()["status"] == "succeeded")
        assert client.post(f"/api/jobs/{run3['id']}/retry-failed", headers=auth).status_code == 409  # nothing failed

        # A card can turn its preview off; a failed preview stops there.
        assert client.patch(f"/api/tasks/{card['id']}", json={"preview": False}, headers=auth).json()["preview"] is False
        client.post(f"/api/tasks/{card['id']}/run", headers=auth)
        run4 = _next(agent, "job.run")["job"]
        assert run4["command"] == "deploy --limit all"
        _finish(client, auth, agent, run4["id"], ROWS[:1], 0)
        client.patch(f"/api/tasks/{card['id']}", json={"preview": None}, headers=auth)
        _until(lambda: not any(j["status"] in ("queued", "running") for j in client.get("/api/jobs?limit=20", headers=auth).json()
                               if j["task_id"] == card["id"]))
        client.post(f"/api/tasks/{card['id']}/run", headers=auth)
        run5 = _next(agent, "job.run")["job"]
        assert "--check" in run5["command"]
        _finish(client, auth, agent, run5["id"], ROWS[:1], 1)
        time.sleep(0.5)
        latest = client.get(f"/api/tasks/{card['id']}", headers=auth).json()["latest_job"]
        assert latest["id"] == run5["id"] and latest["status"] == "failed"
        feed = client.get(f"/api/projects/{project['id']}/activity", headers=auth).json()
        assert any(a["type"] == "job.preview_failed" for a in feed)
    finally:
        ws.__exit__(None, None, None)
        client.delete(f"/api/projects/{project['id']}", headers=auth)
        client.delete(f"/api/commands/test-deploy-{n}", headers=auth)


def test_a_playbook_step_retries_what_failed(client, auth):
    n = uuid.uuid4().hex[:6]
    tag = f"pvb-{n}"
    assert client.post("/api/commands", json={"yaml": COMMAND.format(n=n)}, headers=auth).status_code == 201
    r = client.post("/api/playbooks", json={"yaml": PLAYBOOK.format(n=n)}, headers=auth)
    assert r.status_code == 201, r.text
    bad = client.post("/api/playbooks/validate", json={"yaml": PLAYBOOK.format(n=n).replace(f"test-deploy-{n}\n", "demo-seed-hosts\n")}, headers=auth)
    project = _project(client, auth)
    ws, agent = _agent(client, f"pvb-{n}", tag)
    try:
        run = client.post(f"/api/playbooks/test-deploy-pb-{n}/run", json={"project_id": project["id"], "agent_selector": {"tags": [tag]}},
                          headers=auth)
        assert run.status_code in (200, 201), run.text
        first = _next(agent, "job.run")["job"]
        assert first["command"] == "deploy --limit all"  # preview: false on the step
        _finish(client, auth, agent, first["id"], ROWS, 2)
        again = _next(agent, "job.run")["job"]
        assert again["command"] == "deploy --limit web2,db1"
        _finish(client, auth, agent, again["id"], [{"host": "web2", "status": "failed"}], 2)
        # Only one retry: then on_failure (stop) applies.
        got = _until(lambda: (x := client.get(f"/api/playbook-runs/{run.json()['id']}", headers=auth).json())["status"] == "failed" and x)
        assert got["step_results"]["deploy"]["retries"] == 1
    finally:
        ws.__exit__(None, None, None)
        client.delete(f"/api/projects/{project['id']}", headers=auth)
        client.delete(f"/api/playbooks/test-deploy-pb-{n}", headers=auth)
        client.delete(f"/api/commands/test-deploy-{n}", headers=auth)
    assert bad.status_code == 200 and any("retry_failed" in p for p in str(bad.json()).split("'")), bad.json()
