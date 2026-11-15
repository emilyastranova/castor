"""Runs that wait for a person to approve them (docs/ACTIONS.md phase 5): a definition's
confirm: true or a card that asks before each run, approving and rejecting, the two-person
setting, Home and notifications, and playbook steps that wait."""

import time
import uuid

CONFIRMED = """
id: test-confirmed-{n}
name: Post to the customer
confirm: true
args: [{{name: text, type: string}}]
command: echo {{{{ text }}}}
"""


def _project(client, auth):
    code = f"AP-{uuid.uuid4().hex[:6]}"
    p = client.post("/api/projects", json={"codename": code, "name": code, "kind": "internal"}, headers=auth).json()
    return p


def _operator(client, auth, project):
    name = f"ap{uuid.uuid4().hex[:8]}"
    user = client.post("/api/users", json={"username": name, "role": "operator", "password": "a password"}, headers=auth).json()
    token = client.post("/api/auth/login", json={"username": name, "password": "a password"}).json()["token"]
    client.cookies.clear()
    client.put(f"/api/projects/{project['id']}/members", json=[user["id"]], headers=auth)
    return user, {"Authorization": f"Bearer {token}"}


def test_runs_wait_for_approval(client, auth):
    n = uuid.uuid4().hex[:6]
    assert client.post("/api/commands", json={"yaml": CONFIRMED.format(n=n)}, headers=auth).status_code == 201
    project = _project(client, auth)
    other, other_h = _operator(client, auth, project)
    card = client.post(f"/api/projects/{project['id']}/tasks", json={
        "title": "Tell the customer", "kind": "command", "command_ref": f"test-confirmed-{n}", "command_args": {"text": "hi"},
    }, headers=auth).json()

    job = client.post(f"/api/tasks/{card['id']}/run", headers=auth).json()
    assert job["status"] == "approval"
    assert client.post(f"/api/tasks/{card['id']}/run", headers=auth).status_code == 409  # one at a time, as for any run
    notes = [x for x in client.get("/api/notifications", headers=other_h).json()["items"] if x["kind"] == "job.approval"]
    assert notes and "needs approval to run" in notes[0]["title"]
    summary = client.get("/api/dashboard/summary", params={"project_id": project["id"]}, headers=other_h).json()
    asked = [a for a in summary["attention"] if a["kind"] == "approval"]
    assert asked and asked[0]["job_id"] == job["id"] and asked[0]["for_you"]

    # Rejecting cancels it, with the reason; approving queues it.
    r = client.post(f"/api/jobs/{job['id']}/reject", json={"reason": "not today"}, headers=other_h).json()
    assert r["status"] == "cancelled" and r["error"] == f"Rejected by {other['display_name']}: not today"
    assert client.post(f"/api/jobs/{job['id']}/approve", headers=auth).status_code == 409  # decided already
    job = client.post(f"/api/tasks/{card['id']}/run", headers=auth).json()
    r = client.post(f"/api/jobs/{job['id']}/approve", headers=auth).json()
    assert r["status"] == "queued" and r["approved_by"]
    assert not [x for x in client.get("/api/notifications", headers=other_h).json()["items"]
                if x["kind"] == "job.approval" and not x.get("read_at") and job["id"] in str(x.get("payload") or "")]
    client.post(f"/api/jobs/{job['id']}/cancel", headers=auth)

    # Someone other than who started it, when the setting says so.
    client.patch("/api/settings", json={"approval_by_someone_else": True}, headers=auth)
    try:
        job = client.post(f"/api/tasks/{card['id']}/run", headers=auth).json()
        r = client.post(f"/api/jobs/{job['id']}/approve", headers=auth)
        assert r.status_code == 409 and "someone other" in r.json()["detail"]
        assert client.post(f"/api/jobs/{job['id']}/approve", headers=other_h).json()["status"] == "queued"
        client.post(f"/api/jobs/{job['id']}/cancel", headers=auth)
    finally:
        client.patch("/api/settings", json={"approval_by_someone_else": False}, headers=auth)

    # A plain card can ask too; turning that off doesn't undo a definition's confirm.
    plain = client.post(f"/api/projects/{project['id']}/tasks", json={"title": "Careful", "kind": "command", "command": "echo hi",
                                                                      "confirm": True}, headers=auth).json()
    assert plain["confirm"] is True
    assert client.post(f"/api/tasks/{plain['id']}/run", headers=auth).json()["status"] == "approval"
    client.patch(f"/api/tasks/{card['id']}", json={"confirm": False}, headers=auth)
    assert client.post(f"/api/tasks/{card['id']}/run", headers=auth).json()["status"] == "approval"
    viewer_name = f"apv{uuid.uuid4().hex[:6]}"
    client.post("/api/users", json={"username": viewer_name, "role": "viewer", "password": "a password"}, headers=auth)
    vtoken = client.post("/api/auth/login", json={"username": viewer_name, "password": "a password"}).json()["token"]
    client.cookies.clear()
    pending = client.get(f"/api/tasks/{plain['id']}", headers=auth).json()["latest_job"]
    assert client.post(f"/api/jobs/{pending['id']}/approve", headers={"Authorization": f"Bearer {vtoken}"}).status_code in (403, 404)
    client.delete(f"/api/commands/test-confirmed-{n}", headers=auth)
    client.delete(f"/api/projects/{project['id']}", headers=auth)


def test_a_playbook_step_waits_without_starting_again(client, auth):
    n = uuid.uuid4().hex[:6]
    assert client.post("/api/commands", json={"yaml": CONFIRMED.format(n=n)}, headers=auth).status_code == 201
    pb = f"test-approve-{n}"
    yaml_text = f"id: {pb}\nname: Approve\nsteps:\n  - id: post\n    name: Post\n    command_ref: test-confirmed-{n}\n    args: {{text: hello}}\n"
    assert client.post("/api/playbooks", json={"yaml": yaml_text}, headers=auth).status_code == 201
    project = _project(client, auth)
    run = client.post(f"/api/playbooks/{pb}/run", json={"project_id": project["id"], "inputs": {}}, headers=auth).json()
    time.sleep(1.0)  # several dispatcher passes
    detail = client.get(f"/api/playbook-runs/{run['id']}", headers=auth).json()
    assert detail["step_status"]["post"] == "queued"
    task_id = detail["step_task_ids"]["post"]
    jobs = client.get(f"/api/tasks/{task_id}/jobs", headers=auth).json()
    assert [j["status"] for j in jobs] == ["approval"]  # one job, still waiting: not started again
    client.post(f"/api/jobs/{jobs[0]['id']}/reject", json={"reason": "no"}, headers=auth)
    deadline = time.time() + 5
    while time.time() < deadline and client.get(f"/api/playbook-runs/{run['id']}", headers=auth).json()["status"] == "running":
        time.sleep(0.1)
    detail = client.get(f"/api/playbook-runs/{run['id']}", headers=auth).json()
    assert detail["step_status"]["post"] == "cancelled" and detail["status"] != "running"
    client.delete(f"/api/playbooks/{pb}", headers=auth)
    client.delete(f"/api/commands/test-confirmed-{n}", headers=auth)
    client.delete(f"/api/projects/{project['id']}", headers=auth)
