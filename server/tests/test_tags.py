"""Tags in use, suggested as people type them: most used first, this project's first, and only
from what you can see."""

import uuid


def _project(client, auth):
    return client.post("/api/projects", json={"codename": f"TG-{uuid.uuid4().hex[:6]}", "name": "Tags", "kind": "internal"},
                       headers=auth).json()


def test_card_tags_rank_by_use_and_this_project_first(client, auth):
    w = uuid.uuid4().hex[:5]
    here, there, hidden = _project(client, auth), _project(client, auth), _project(client, auth)
    for project, tags in ((here, [f"web-{w}"]), (here, [f"web-{w}", f"smb-{w}"]),
                          (there, [f"smb-{w}"]), (there, [f"smb-{w}"]), (there, [f"smb-{w}"]), (hidden, [f"secret-{w}"])):
        client.post(f"/api/projects/{project['id']}/tasks", json={"title": "t", "tags": tags}, headers=auth)

    mine = {t["tag"]: t for t in client.get("/api/tags/cards", headers=auth).json() if t["tag"].endswith(w)}
    assert mine[f"smb-{w}"]["count"] == 4 and mine[f"web-{w}"]["count"] == 2
    ranked = [t["tag"] for t in client.get(f"/api/tags/cards?project_id={here['id']}", headers=auth).json() if t["tag"].endswith(w)]
    assert ranked[:2] == [f"web-{w}", f"smb-{w}"]  # used twice here beats used four times elsewhere

    # A token limited to two projects only learns their tags.
    made = client.post("/api/tokens", json={"name": "t", "scopes": ["projects:read"], "project_ids": [here["id"], there["id"]]},
                       headers=auth).json()
    seen = {t["tag"] for t in client.get("/api/tags/cards", headers={"Authorization": f"Bearer {made['token']}"}).json()}
    assert f"smb-{w}" in seen and f"secret-{w}" not in seen


def test_agent_and_library_tags(client, auth):
    w = uuid.uuid4().hex[:5]
    client.post("/api/agent-tokens", json={"label": "x", "tags": [f"lab-{w}", f"dmz-{w}"]}, headers=auth)
    agents = {t["tag"]: t["count"] for t in client.get("/api/tags/agents", headers=auth).json()}
    assert agents[f"lab-{w}"] == 1 and agents[f"dmz-{w}"] == 1
    library = {t["tag"] for t in client.get("/api/tags/library", headers=auth).json()}
    assert {"network", "discovery"} <= library  # the built-in commands' tags


def test_rename_and_merge_card_tags_in_one_project(client, auth):
    w = uuid.uuid4().hex[:5]
    p, other = _project(client, auth), _project(client, auth)
    url = f"/api/projects/{p['id']}/tasks"
    a = client.post(url, json={"title": "a", "tags": [f"smbsign-{w}", "keep"]}, headers=auth).json()
    b = client.post(url, json={"title": "b", "tags": [f"smb-signing-{w}", f"smbsign-{w}"]}, headers=auth).json()
    c = client.post(f"/api/projects/{other['id']}/tasks", json={"title": "c", "tags": [f"smbsign-{w}"]}, headers=auth).json()

    r = client.post(f"/api/projects/{p['id']}/tags/rename", json={"from": [f"smbsign-{w}", f"smb-signing-{w}"], "to": f"smb-signing-{w}"},
                    headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["changed"] == {"cards": 2}
    tags = lambda t: client.get(f"/api/tasks/{t['id']}", headers=auth).json()["tags"]  # noqa: E731
    assert tags(a) == [f"smb-signing-{w}", "keep"]  # renamed in place
    assert tags(b) == [f"smb-signing-{w}"]  # merged: one, not two
    assert tags(c) == [f"smbsign-{w}"]  # another project's cards are left alone

    bad = client.post(f"/api/projects/{p['id']}/tags/rename", json={"from": ["x"], "to": "  , "}, headers=auth)
    assert bad.status_code == 422
    assert client.post(f"/api/projects/{p['id']}/tags/rename", json={"from": ["keep"], "to": "keep"}, headers=auth).status_code == 422
    activity = client.get(f"/api/projects/{p['id']}/activity", headers=auth).json()
    assert any(e["type"] == "tags.renamed" for e in activity)

    viewer = client.post("/api/tokens", json={"name": "v", "scopes": ["projects:read"]}, headers=auth).json()
    refused = client.post(f"/api/projects/{p['id']}/tags/rename", json={"from": ["keep"], "to": "kept"},
                          headers={"Authorization": f"Bearer {viewer['token']}"})
    assert refused.status_code in (401, 403)


def test_rename_agent_tags_follows_into_cards_that_target_them(client, auth):
    import json

    w = uuid.uuid4().hex[:5]
    old, new = f"lab-{w}", f"lab-east-{w}"
    client.post("/api/agent-tokens", json={"label": "x", "tags": [old]}, headers=auth)
    with client.websocket_connect("/ws/agent") as ws:
        ws.send_text(json.dumps({"type": "hello", "secret": "test-agent-secret", "name": f"tagged-{w}", "hostname": "h", "os": "L",
                                 "username": "u", "version": "0", "tags": [old, "other"], "capabilities": {"runtimes": ["host"]}}))
        assert json.loads(ws.receive_text())["type"] == "hello.ok"
        p = _project(client, auth)
        card = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "t", "kind": "command", "command": "id",
                                                                    "agent_selector": {"tags": [old], "all": True}}, headers=auth).json()

        r = client.post("/api/tags/agents/rename", json={"from": [old], "to": new}, headers=auth)
        assert r.status_code == 200, r.text
        assert r.json()["changed"]["agents"] == 1 and r.json()["changed"]["tokens"] == 1 and r.json()["changed"]["cards"] >= 1
        agent = next(a for a in client.get("/api/agents", headers=auth).json() if a["name"] == f"tagged-{w}")
        assert agent["tags"] == [new, "other"]
        assert client.get(f"/api/tasks/{card['id']}", headers=auth).json()["agent_selector"] == {"tags": [new], "all": True}

        from castor_server.orchestrator.gateway import gateway

        assert gateway.get(uuid.UUID(agent["id"])).tags == [new, "other"]  # the dispatcher matches the new name at once
    operator = client.post("/api/tokens", json={"name": "o", "scopes": ["projects:write"]}, headers=auth).json()
    assert client.post("/api/tags/agents/rename", json={"from": [new], "to": old},
                       headers={"Authorization": f"Bearer {operator['token']}"}).status_code in (401, 403)
