"""Cards with subtasks (subtasks.py): an activity card holding cards of its own, hidden from the
board; consolidating cards into one (REST and MCP), adding, releasing and ordering, progress, the
activity following its subtasks, and archive, restore, delete, duplicate and import/export."""

import json
import uuid


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
