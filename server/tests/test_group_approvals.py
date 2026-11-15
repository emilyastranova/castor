"""More for groups: approver groups (the project's, a card's, a trigger's) decide who approves
waiting runs and trigger fires and who is asked; groups told when a card finishes; and people who
see a project through a group get its notifications."""

import uuid


def _user(client, auth, prefix):
    name = f"{prefix}{uuid.uuid4().hex[:6]}"
    user = client.post("/api/users", json={"username": name, "role": "operator", "password": "a password"}, headers=auth).json()
    token = client.post("/api/auth/login", json={"username": name, "password": "a password"}).json()["token"]
    client.cookies.clear()
    return user, {"Authorization": f"Bearer {token}"}


def _group(client, auth, members):
    slug = f"g{uuid.uuid4().hex[:6]}"
    group = client.post("/api/groups", json={"slug": slug, "name": slug}, headers=auth).json()
    client.put(f"/api/groups/{group['id']}/members", json=[u["id"] for u in members], headers=auth)
    return group


def _setup(client, auth):
    """A project shared with @ops (alice, not a direct member) and with bob directly."""
    code = f"GA-{uuid.uuid4().hex[:6]}"
    project = client.post("/api/projects", json={"codename": code, "name": code, "kind": "internal"}, headers=auth).json()
    alice, alice_h = _user(client, auth, "alice")
    bob, bob_h = _user(client, auth, "bob")
    ops = _group(client, auth, [alice])
    bobs = _group(client, auth, [bob])
    client.put(f"/api/projects/{project['id']}/members", json=[bob["id"]], headers=auth)
    client.put(f"/api/projects/{project['id']}/groups", json=[ops["id"]], headers=auth)
    return project, (alice, alice_h), (bob, bob_h), ops, bobs


def _notes(client, headers, kind):
    return [n for n in client.get("/api/notifications", headers=headers).json()["items"] if n["kind"] == kind and not n.get("read_at")]


def test_project_approvers_decide_runs(client, auth):
    project, (alice, alice_h), (bob, bob_h), ops, bobs = _setup(client, auth)
    r = client.patch(f"/api/projects/{project['id']}", json={"settings": {"approvers": [f"@{ops['slug']}"]}}, headers=auth)
    assert r.status_code == 200 and r.json()["settings"]["approvers"] == [ops["id"]]

    card = client.post(f"/api/projects/{project['id']}/tasks", json={"title": "Careful", "kind": "command", "command": "echo hi",
                                                                    "confirm": True}, headers=bob_h).json()
    job = client.post(f"/api/tasks/{card['id']}/run", headers=bob_h).json()
    assert job["status"] == "approval"
    # Only the approvers are asked: alice (through her group, not a member) and not bob.
    assert [n for n in _notes(client, alice_h, "job.approval") if job["id"] in str(n.get("payload"))]
    assert not [n for n in _notes(client, bob_h, "job.approval") if job["id"] in str(n.get("payload"))]

    r = client.post(f"/api/jobs/{job['id']}/approve", headers=bob_h)
    assert r.status_code == 403 and f"@{ops['slug']}" in r.json()["detail"]
    assert client.post(f"/api/jobs/{job['id']}/reject", json={}, headers=bob_h).status_code == 403
    assert client.post(f"/api/jobs/{job['id']}/approve", headers=alice_h).json()["status"] == "queued"
    client.post(f"/api/jobs/{job['id']}/cancel", headers=auth)

    # Who approves is changed only by someone who may approve now.
    r = client.patch(f"/api/tasks/{card['id']}", json={"approvers": [bobs["id"]]}, headers=bob_h)
    assert r.status_code == 403
    r = client.patch(f"/api/projects/{project['id']}", json={"settings": {}}, headers=bob_h)
    assert r.status_code == 403
    assert client.patch(f"/api/tasks/{card['id']}", json={"approvers": [bobs["id"]]}, headers=alice_h).json()["approvers"] == [bobs["id"]]

    # The card's own approvers win over the project's.
    job = client.post(f"/api/tasks/{card['id']}/run", headers=alice_h).json()
    assert client.post(f"/api/jobs/{job['id']}/approve", headers=alice_h).status_code == 403
    assert client.post(f"/api/jobs/{job['id']}/approve", headers=bob_h).json()["status"] == "queued"
    client.post(f"/api/jobs/{job['id']}/cancel", headers=auth)
    assert client.patch(f"/api/tasks/{card['id']}", json={"approvers": ["@nobody-here"]}, headers=auth).status_code == 422
    client.delete(f"/api/projects/{project['id']}", headers=auth)


def test_trigger_approvers(client, auth):
    project, (alice, alice_h), (bob, bob_h), ops, _ = _setup(client, auth)
    client.put(f"/api/projects/{project['id']}/scope", json={"include": ["10.60.0.0/16"]}, headers=auth)
    trig = client.post(f"/api/projects/{project['id']}/asset-triggers", json={
        "name": "Sweep", "asset_type": "host", "on_event": "created", "action": "command", "target": "nmap-ping-sweep",
        "args": {"targets": "{{ asset.ip }}"}, "approvers": [ops["id"]],
    }, headers=auth)
    assert trig.status_code == 201 and trig.json()["approvers"] == [ops["id"]]
    client.post(f"/api/projects/{project['id']}/assets/import", json={"type": "ip", "text": "192.168.60.1"}, headers=auth)
    fire = client.get(f"/api/projects/{project['id']}/asset-trigger-fires", headers=auth).json()[0]
    assert fire["status"] == "waiting"
    assert _notes(client, alice_h, "asset_trigger.approval") and not _notes(client, bob_h, "asset_trigger.approval")
    assert client.post(f"/api/asset-trigger-fires/{fire['id']}/skip", headers=bob_h).status_code == 403
    body = {**trig.json(), "approvers": []}
    assert client.put(f"/api/asset-triggers/{trig.json()['id']}", json=body, headers=bob_h).status_code == 403
    assert client.post(f"/api/asset-trigger-fires/{fire['id']}/approve", headers=alice_h).json()["status"] == "started"
    for f in client.get(f"/api/projects/{project['id']}/asset-trigger-fires", headers=auth).json():
        if f["task_id"]:
            job = client.get(f"/api/tasks/{f['task_id']}", headers=auth).json()["latest_job"]
            if job:
                client.post(f"/api/jobs/{job['id']}/cancel", headers=auth)
    client.delete(f"/api/projects/{project['id']}", headers=auth)


def test_groups_hear_when_a_card_finishes(client, auth):
    project, (alice, alice_h), (bob, bob_h), ops, _ = _setup(client, auth)
    done = next(c for c in client.get(f"/api/projects/{project['id']}/board", headers=auth).json()["columns"] if c["role"] == "done")
    card = client.post(f"/api/projects/{project['id']}/tasks", json={"title": "Rotate keys"}, headers=auth).json()
    r = client.patch(f"/api/tasks/{card['id']}", json={"alerts": {"channels": [], "groups": [ops["id"]]}}, headers=auth)
    assert r.status_code == 200 and r.json()["alerts"]["groups"] == [ops["id"]]
    client.post(f"/api/tasks/{card['id']}/move", json={"column_id": done["id"]}, headers=bob_h)
    assert [n for n in _notes(client, alice_h, "task.finished") if "Rotate keys" in n["title"]]
    assert not [n for n in _notes(client, bob_h, "task.finished") if "Rotate keys" in n["title"]]
    client.delete(f"/api/projects/{project['id']}", headers=auth)
