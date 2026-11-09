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
