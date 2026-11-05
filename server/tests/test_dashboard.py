"""The home dashboard: feed (enriched, filtered, paged, scoped) and summary."""

import uuid


def _project(client, auth):
    code = f"DB-{uuid.uuid4().hex[:6]}"
    return client.post("/api/projects", json={"codename": code, "name": code, "kind": "internal"}, headers=auth).json()


def test_feed_shows_comments_cards_and_jobs(client, auth):
    project = _project(client, auth)
    task = client.post(f"/api/projects/{project['id']}/tasks", json={"title": "Recon notes"}, headers=auth).json()
    client.post(f"/api/tasks/{task['id']}/comments", json={"body": "Found **two** open shares"}, headers=auth)

    feed = client.get(f"/api/dashboard/feed?project_id={project['id']}", headers=auth).json()
    kinds = [i["kind"] for i in feed["items"]]
    assert kinds[0] == "comment" and "card" in kinds
    first = feed["items"][0]
    assert first["comment"]["body"] == "Found **two** open shares"
    assert first["task"] == {"id": task["id"], "name": "Recon notes"}
    assert first["project"]["codename"] == project["codename"]
    assert first["actor"]["name"] == "admin" and first["actor"]["type"] == "user"

    only = client.get(f"/api/dashboard/feed?project_id={project['id']}&kinds=card", headers=auth).json()
    assert {i["kind"] for i in only["items"]} == {"card"}


def test_feed_pages(client, auth):
    project = _project(client, auth)
    for n in range(5):
        client.post(f"/api/projects/{project['id']}/tasks", json={"title": f"Card {n}"}, headers=auth)
    first = client.get(f"/api/dashboard/feed?project_id={project['id']}&kinds=card&limit=3", headers=auth).json()
    assert len(first["items"]) == 3 and first["next_before"]
    rest = client.get(f"/api/dashboard/feed?project_id={project['id']}&kinds=card&limit=3&before={first['next_before']}", headers=auth).json()
    assert [i["task"]["name"] for i in first["items"] + rest["items"]][:5] == [f"Card {n}" for n in range(4, -1, -1)]


def test_feed_and_summary_follow_membership(client, auth):
    hidden = _project(client, auth)
    client.post(f"/api/projects/{hidden['id']}/tasks", json={"title": "Secret"}, headers=auth)
    name = f"dash-{uuid.uuid4().hex[:6]}"
    client.post("/api/users", json={"username": name, "role": "viewer", "password": "a password"}, headers=auth)
    token = client.post("/api/auth/login", json={"username": name, "password": "a password"}).json()["token"]
    client.cookies.clear()
    vh = {"Authorization": f"Bearer {token}"}
    items = client.get("/api/dashboard/feed?limit=100", headers=vh).json()["items"]
    assert all(i["project"] is None or i["project"]["id"] != hidden["id"] for i in items)
    assert all(i["kind"] != "admin" for i in items)  # user/project admin events are for admins
    assert client.get(f"/api/dashboard/feed?project_id={hidden['id']}", headers=vh).status_code == 404
    assert client.get(f"/api/dashboard/summary?project_id={hidden['id']}", headers=vh).status_code == 404


def test_summary(client, auth):
    project = _project(client, auth)
    task = client.post(f"/api/projects/{project['id']}/tasks", json={"title": "Scan", "kind": "command", "command": "true"}, headers=auth).json()
    client.post(f"/api/tasks/{task['id']}/comments", json={"body": "go"}, headers=auth)
    job = client.post(f"/api/tasks/{task['id']}/run", headers=auth).json()  # no agent: stays queued
    s = client.get(f"/api/dashboard/summary?project_id={project['id']}", headers=auth).json()
    assert s["counts"]["open_cards"] == 1 and s["counts"]["comments_24h"] == 1 and s["counts"]["cards_24h"] == 1
    assert [j["task"]["name"] for j in s["live_jobs"]] == ["Scan"]
    assert set(s["agents"]) == {"online", "slots", "busy"}
    assert s["mine"] == {"assigned": 0, "opened": 1}
    client.patch(f"/api/tasks/{task['id']}", json={"assignees": ["admin"]}, headers=auth)
    s = client.get(f"/api/dashboard/summary?project_id={project['id']}", headers=auth).json()
    assert s["mine"]["assigned"] == 1
    assert any("comment" in f for f in s["facts"])  # facts only for what has data: 1 comment, no finished job
    assert not any("jobs have run" in f for f in s["facts"])
    assert client.get("/api/dashboard/summary", headers=auth).status_code == 200  # every project
    client.post(f"/api/jobs/{job['id']}/cancel", headers=auth)  # don't leave it for other tests' agents
