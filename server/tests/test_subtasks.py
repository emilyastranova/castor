"""Cards with subtasks (subtasks.py): an activity card holding cards of its own, hidden from the
board; consolidating cards into one (REST and MCP), adding, releasing and ordering, progress, the
activity following its subtasks, and archive, restore, delete, duplicate and import/export."""

import json
import time
import uuid

import yaml


def _project(client, auth):
    code = f"ST-{uuid.uuid4().hex[:6]}"
    return client.post("/api/projects", json={"codename": code, "name": code, "kind": "internal"}, headers=auth).json()


def _columns(client, auth, project):
    board = client.get(f"/api/projects/{project['id']}/board", headers=auth).json()
    return {c["role"]: c["id"] for c in board["columns"] if c.get("role")}


def _card(client, auth, project, title, **body):
    r = client.post(f"/api/projects/{project['id']}/tasks", json={"title": title, **body}, headers=auth)
    assert r.status_code == 201, r.text
    return r.json()


def _board_titles(client, auth, project):
    return {t["title"] for t in client.get(f"/api/projects/{project['id']}/board", headers=auth).json()["tasks"]}


def _get(client, auth, card):
    return client.get(f"/api/tasks/{card['id']}", headers=auth).json()


def test_consolidate_hides_subtasks_and_tracks_progress(client, auth):
    project = _project(client, auth)
    cols = _columns(client, auth, project)
    a, b, c = (_card(client, auth, project, f"Update LXC {n}") for n in (1, 2, 3))
    r = client.post(f"/api/projects/{project['id']}/consolidate",
                    json={"title": "Update homelab LXCs", "task_ids": [a["id"], b["id"], c["id"]]}, headers=auth)
    assert r.status_code == 201, r.text
    parent = r.json()
    assert parent["subtasks"]["total"] == 3 and parent["subtasks"]["done"] == 0
    assert _board_titles(client, auth, project) == {"Update homelab LXCs"}
    kids = client.get(f"/api/tasks/{parent['id']}/subtasks", headers=auth).json()
    assert [k["title"] for k in kids] == ["Update LXC 1", "Update LXC 2", "Update LXC 3"]
    assert all(k["parent_id"] == parent["id"] for k in kids)

    # Starting one moves the activity to In Progress; finishing every one moves it to Review.
    client.post(f"/api/tasks/{a['id']}/move", json={"column_id": cols["active"]}, headers=auth)
    parent = _get(client, auth, parent)
    assert parent["column_id"] == cols["active"] and parent["subtasks"]["running"] == 1
    for kid in (a, b):
        client.post(f"/api/tasks/{kid['id']}/move", json={"column_id": cols["done"]}, headers=auth)
    parent = _get(client, auth, parent)
    assert parent["subtasks"]["done"] == 2 and parent["column_id"] == cols["active"]
    client.post(f"/api/tasks/{c['id']}/move", json={"column_id": cols["review"]}, headers=auth)
    parent = _get(client, auth, parent)
    assert parent["subtasks"]["done"] == 3 and parent["column_id"] == cols["review"]

    # Reorder, then release one back onto the board.
    order = client.post(f"/api/tasks/{parent['id']}/subtasks/order", json={"task_ids": [c["id"], a["id"]]}, headers=auth).json()
    assert [k["title"] for k in order] == ["Update LXC 3", "Update LXC 1", "Update LXC 2"]
    released = client.post(f"/api/tasks/{b['id']}/release", headers=auth).json()
    assert released["parent_id"] is None and "Update LXC 2" in _board_titles(client, auth, project)
    assert _get(client, auth, parent)["subtasks"]["total"] == 2
    assert client.post(f"/api/tasks/{b['id']}/release", headers=auth).status_code == 422  # not a subtask now


def test_follow_can_be_turned_off(client, auth):
    project = _project(client, auth)
    client.patch(f"/api/projects/{project['id']}", json={"settings": {"subtasks_move_card": False}}, headers=auth)
    cols = _columns(client, auth, project)
    parent = _card(client, auth, project, "Activity")
    kid = _card(client, auth, project, "Step", parent_id=parent["id"])
    assert kid["parent_id"] == parent["id"]
    client.post(f"/api/tasks/{kid['id']}/move", json={"column_id": cols["done"]}, headers=auth)
    after = _get(client, auth, parent)
    assert after["column_id"] == parent["column_id"] and after["subtasks"]["done"] == 1


def test_one_level_deep_and_other_refusals(client, auth):
    project, other = _project(client, auth), _project(client, auth)
    parent = _card(client, auth, project, "Parent")
    kid = _card(client, auth, project, "Kid", parent_id=parent["id"])
    loose = _card(client, auth, project, "Loose")
    elsewhere = _card(client, auth, other, "Elsewhere")

    def add(to, *cards):
        return client.post(f"/api/tasks/{to['id']}/subtasks", json={"task_ids": [c["id"] for c in cards]}, headers=auth)

    assert add(kid, loose).status_code == 422  # a subtask can't have subtasks
    assert add(loose, parent).status_code == 422  # nor can a card with subtasks become one
    assert add(loose, loose).status_code == 422
    assert add(parent, elsewhere).status_code == 422  # another project
    second = _card(client, auth, project, "Second")
    r = add(second, loose, kid)  # kid belongs to Parent: nothing changes
    assert r.status_code == 422 and "another card" in r.json()["detail"]
    assert _get(client, auth, loose)["parent_id"] is None
    assert add(parent, loose).status_code == 200
    assert _get(client, auth, loose)["parent_id"] == parent["id"]
    r = client.post(f"/api/projects/{project['id']}/tasks", json={"title": "Deep", "parent_id": kid["id"]}, headers=auth)
    assert r.status_code == 422


def test_archive_restore_delete_and_duplicate(client, auth):
    project = _project(client, auth)
    parent = _card(client, auth, project, "Patch round")
    kids = [_card(client, auth, project, f"Host {n}", parent_id=parent["id"]) for n in (1, 2)]
    early = _card(client, auth, project, "Host 0", parent_id=parent["id"])
    client.post(f"/api/tasks/{early['id']}/archive", headers=auth)
    client.post(f"/api/tasks/{parent['id']}/archive", headers=auth)
    assert all(_get(client, auth, k)["archived_at"] for k in kids)
    client.post(f"/api/tasks/{parent['id']}/restore", headers=auth)
    assert not any(_get(client, auth, k)["archived_at"] for k in kids)
    assert _get(client, auth, early)["archived_at"]  # archived on its own before: stays archived

    copy = client.post(f"/api/tasks/{parent['id']}/duplicate", json={}, headers=auth).json()
    copies = client.get(f"/api/tasks/{copy['id']}/subtasks", headers=auth).json()
    assert [k["title"] for k in copies] == ["Host 1", "Host 2"] and {k["id"] for k in copies}.isdisjoint({k["id"] for k in kids})
    kid_copy = client.post(f"/api/tasks/{kids[0]['id']}/duplicate", json={}, headers=auth).json()
    assert kid_copy["parent_id"] == parent["id"]

    assert client.delete(f"/api/tasks/{parent['id']}", headers=auth).status_code in (200, 204)
    assert _get(client, auth, kids[0])["parent_id"] is None
    assert {"Host 1", "Host 2"} <= _board_titles(client, auth, project)


def test_run_subtasks(client, auth):
    project = _project(client, auth)
    parent = _card(client, auth, project, "Checks")
    one = _card(client, auth, project, "One", kind="command", command="echo one", parent_id=parent["id"])
    _card(client, auth, project, "Read the notes", parent_id=parent["id"])  # manual: not run
    gate = _card(client, auth, project, "Gate")
    waits = _card(client, auth, project, "Two", kind="command", command="echo two", parent_id=parent["id"], waits_on=[gate["id"]])
    out = client.post(f"/api/tasks/{parent['id']}/subtasks/run", json={}, headers=auth).json()
    assert out["started"] == [one["id"]]
    assert [s["id"] for s in out["skipped"]] == [waits["id"]] and "waits on" in out["skipped"][0]["reason"]
    again = client.post(f"/api/tasks/{parent['id']}/subtasks/run", json={}, headers=auth).json()
    assert again["started"] == [] and {s["reason"] for s in again["skipped"]} >= {"already running"}
    for card in (one,):
        job = _get(client, auth, card)["latest_job"]
        if job:
            client.post(f"/api/jobs/{job['id']}/cancel", headers=auth)


def test_consolidate_over_mcp(client, auth):
    project = _project(client, auth)
    a, b = _card(client, auth, project, "Old card A"), _card(client, auth, project, "Old card B")

    def call(name, **args):
        r = client.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                          "params": {"name": name, "arguments": args}}, headers=auth)
        result = r.json()["result"]
        assert not result["isError"], result
        return json.loads(result["content"][0]["text"])

    parent = call("consolidate_cards", project_id=project["id"], title="Cleanup", task_ids=[a["id"], b["id"]])
    assert parent["subtasks"]["total"] == 2
    listed = call("list_subtasks", task_id=parent["id"])
    assert [k["title"] for k in listed] == ["Old card A", "Old card B"]
    call("release_subtask", task_id=a["id"])
    assert [k["title"] for k in call("list_subtasks", task_id=parent["id"])] == ["Old card B"]
    call("add_subtasks", task_id=parent["id"], task_ids=[a["id"]])
    new = call("create_card", project_id=project["id"], title="New step", parent_id=parent["id"])
    assert new["parent_id"] == parent["id"]
    assert _board_titles(client, auth, project) == {"Cleanup"}


def test_export_import_keeps_subtasks(client, auth):
    project = _project(client, auth)
    parent = _card(client, auth, project, "Activity")
    _card(client, auth, project, "Inside", parent_id=parent["id"])
    _card(client, auth, project, "Outside")
    data = client.post("/api/export", json={"projects": [project["id"]]}, headers=auth).content
    preview = client.post("/api/import/preview", files={"file": ("x.castor.zip", data, "application/zip")}, headers=auth).json()
    result = client.post("/api/import", json={"token": preview["token"], "filename": preview["filename"]}, headers=auth).json()
    new_id = result["projects"][0]
    board = client.get(f"/api/projects/{new_id}/board", headers=auth).json()
    assert {t["title"] for t in board["tasks"]} == {"Activity", "Outside"}
    copy = next(t for t in board["tasks"] if t["title"] == "Activity")
    assert copy["id"] != parent["id"] and copy["subtasks"]["total"] == 1
    assert [k["title"] for k in client.get(f"/api/tasks/{copy['id']}/subtasks", headers=auth).json()] == ["Inside"]


def test_one_subtask_per_target_and_run_a_few_at_a_time(client, auth):
    project = _project(client, auth)
    parent = _card(client, auth, project, "Sweep the lab")
    r = client.post(f"/api/tasks/{parent['id']}/subtasks/many", json={
        "command_ref": "nmap-ping-sweep", "target_arg": "targets", "targets": "10.9.0.1, 10.9.0.2\n10.9.0.3", "title": "Sweep {{ target }}",
    }, headers=auth)
    assert r.status_code == 201, r.text
    kids = r.json()
    assert [k["title"] for k in kids] == ["Sweep 10.9.0.1", "Sweep 10.9.0.2", "Sweep 10.9.0.3"]
    assert "10.9.0.2" in kids[1]["command"] and all(k["parent_id"] == parent["id"] for k in kids)
    assert client.post(f"/api/tasks/{parent['id']}/subtasks/many", json={
        "command_ref": "nmap-ping-sweep", "target_arg": "nope", "targets": ["x"]}, headers=auth).status_code == 422

    # Two at a time: two start, one waits its turn; cancelling one starts the next.
    out = client.post(f"/api/tasks/{parent['id']}/subtasks/run", json={"at_once": 2}, headers=auth).json()
    assert len(out["started"]) == 2 and out["queued"] == 1
    summary = client.get(f"/api/tasks/{parent['id']}", headers=auth).json()["subtasks"]
    assert summary["queued"] == 1 and summary["at_once"] == 2
    first = client.get(f"/api/tasks/{out['started'][0]}", headers=auth).json()["latest_job"]
    client.post(f"/api/jobs/{first['id']}/cancel", headers=auth)
    waiting = next(k for k in kids if k["id"] not in out["started"])
    deadline = time.time() + 5
    while time.time() < deadline and not client.get(f"/api/tasks/{waiting['id']}", headers=auth).json()["latest_job"]:
        time.sleep(0.1)
    assert client.get(f"/api/tasks/{waiting['id']}", headers=auth).json()["latest_job"]
    assert client.get(f"/api/tasks/{parent['id']}", headers=auth).json()["subtasks"]["queued"] == 0
    for k in kids:
        job = client.get(f"/api/tasks/{k['id']}", headers=auth).json()["latest_job"]
        if job:
            client.post(f"/api/jobs/{job['id']}/cancel", headers=auth)
    client.delete(f"/api/projects/{project['id']}", headers=auth)


def test_to_and_from_playbooks(client, auth):
    project = _project(client, auth)
    parent = _card(client, auth, project, "Monthly checks")
    a = _card(client, auth, project, "Sweep", kind="command", command_ref="nmap-ping-sweep", command_args={"targets": "10.8.0.0/24"},
              parent_id=parent["id"])
    _card(client, auth, project, "Then scan", kind="command", command_ref="nmap-ping-sweep", command_args={"targets": "10.8.1.0/24"},
          parent_id=parent["id"], waits_on=[a["id"]])
    _card(client, auth, project, "Write it up", parent_id=parent["id"])
    draft = client.get(f"/api/tasks/{parent['id']}/subtasks/playbook", headers=auth).json()
    assert draft["left_out"] == ["Write it up"]
    spec = yaml.safe_load(draft["yaml"])
    assert [s["id"] for s in spec["steps"]] == ["sweep", "then-scan"] and spec["steps"][1]["depends_on"] == ["sweep"]
    pb_id = f"monthly-{uuid.uuid4().hex[:6]}"
    r = client.post("/api/playbooks", json={"yaml": draft["yaml"].replace(f"id: {spec['id']}", f"id: {pb_id}", 1)}, headers=auth)
    assert r.status_code == 201, r.text

    other = _project(client, auth)
    r = client.post(f"/api/playbooks/{pb_id}/activity", json={"project_id": other["id"]}, headers=auth)
    assert r.status_code == 201, r.text
    made = r.json()["task"]
    assert made["title"] == "Monthly checks" and made["subtasks"]["total"] == 2 and r.json()["left_out"] == []
    kids = client.get(f"/api/tasks/{made['id']}/subtasks", headers=auth).json()
    assert kids[1]["waits_on"] == [kids[0]["id"]] and not kids[0]["latest_job"]  # nothing started
    client.delete(f"/api/playbooks/{pb_id}", headers=auth)
    for p in (project, other):
        client.delete(f"/api/projects/{p['id']}", headers=auth)


def test_home_and_search_show_the_activity(client, auth):
    project = _project(client, auth)
    # The activity doesn't follow here, so its own moves don't come between the subtasks'.
    client.patch(f"/api/projects/{project['id']}", json={"settings": {"subtasks_move_card": False}}, headers=auth)
    parent = _card(client, auth, project, "Patch round zeta")
    cols = _columns(client, auth, project)
    kids = [_card(client, auth, project, f"Zeta host {n}", parent_id=parent["id"]) for n in (1, 2, 3)]
    summary = client.get("/api/dashboard/summary", params={"project_id": project["id"]}, headers=auth).json()
    assert summary["counts"]["open_cards"] == 1  # the activity, once
    for k in kids:
        client.post(f"/api/tasks/{k['id']}/move", json={"column_id": cols["review"]}, headers=auth)
    feed = client.get("/api/dashboard/feed", params={"project_id": project["id"], "kinds": "move"}, headers=auth).json()["items"]
    rolled = next(i for i in feed if i.get("subtasks"))
    assert rolled["message"].startswith("3 subtasks of “Patch round zeta”") and len(rolled["subtasks"]) == 3
    hits = client.get("/api/search", params={"q": "Zeta host 2"}, headers=auth).json()
    titles = [h["title"] for g in hits["groups"] for h in g["hits"]]
    assert any(t == "Zeta host 2 in “Patch round zeta”" for t in titles), hits
    client.delete(f"/api/projects/{project['id']}", headers=auth)


def test_moving_a_subtask_keeps_its_place(client, auth):
    project = _project(client, auth)
    cols = _columns(client, auth, project)
    parent = _card(client, auth, project, "Order")
    kids = [_card(client, auth, project, f"Step {n}", parent_id=parent["id"]) for n in (1, 2, 3)]
    client.post(f"/api/tasks/{kids[2]['id']}/move", json={"column_id": cols["done"], "position": 0}, headers=auth)
    client.post(f"/api/tasks/{kids[0]['id']}/move", json={"column_id": cols["active"]}, headers=auth)
    order = [k["title"] for k in client.get(f"/api/tasks/{parent['id']}/subtasks", headers=auth).json()]
    assert order == ["Step 1", "Step 2", "Step 3"]
    client.delete(f"/api/projects/{project['id']}", headers=auth)
