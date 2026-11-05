"""People pages: a person's projects, assigned cards, mentions and numbers, scoped to what
the viewer can see; and the team overview."""

import uuid


def _project(client, auth):
    code = f"PE-{uuid.uuid4().hex[:6]}"
    return client.post("/api/projects", json={"codename": code, "name": code, "kind": "internal"}, headers=auth).json()


def _user(client, auth, role="operator"):
    name = f"pe{role[:2]}{uuid.uuid4().hex[:6]}"
    user = client.post("/api/users", json={"username": name, "display_name": f"Person {name}", "role": role,
                                           "password": "a password"}, headers=auth).json()
    token = client.post("/api/auth/login", json={"username": name, "password": "a password"}).json()["token"]
    client.cookies.clear()
    return user, {"Authorization": f"Bearer {token}"}


def test_person_page(client, auth):
    shared, private = _project(client, auth), _project(client, auth)
    bob, bh = _user(client, auth)
    viewer, vh = _user(client, auth, "viewer")
    client.put(f"/api/projects/{shared['id']}/members", json=[bob["id"], viewer["id"]], headers=auth)
    client.put(f"/api/projects/{private['id']}/members", json=[bob["id"]], headers=auth)

    a = client.post(f"/api/projects/{shared['id']}/tasks", json={"title": "Shared job", "assignees": [bob["id"]], "priority": 2}, headers=auth).json()
    client.post(f"/api/projects/{private['id']}/tasks", json={"title": "Private job", "assignees": [bob["id"]]}, headers=auth)
    client.post(f"/api/projects/{shared['id']}/tasks", json={"title": "Bob made this"}, headers=bh)
    client.post(f"/api/tasks/{a['id']}/comments", json={"body": f"@{bob['username']} please look"}, headers=auth)

    # An admin sees all of it.
    page = client.get(f"/api/people/{bob['username'].upper()}", headers=auth).json()
    assert page["display_name"] == bob["display_name"] and page["role"] == "operator"
    assert {p["codename"] for p in page["projects"]} == {shared["codename"], private["codename"]}
    assert {c["title"] for c in page["assigned"]} == {"Shared job", "Private job"}
    assert page["assigned"][0]["title"] == "Shared job"  # higher priority first
    assert page["mentions"][0]["link"].endswith(f"&comment={page['mentions'][0]['id']}")
    assert page["counts"]["cards_30d"] == 1 and page["counts"]["open_assigned"] == 2
    assert page["last_active"] is not None

    # The viewer only sees the shared project's part.
    seen = client.get(f"/api/people/{bob['username']}", headers=vh).json()
    assert [p["codename"] for p in seen["projects"]] == [shared["codename"]]
    assert [c["title"] for c in seen["assigned"]] == ["Shared job"]
    assert client.get("/api/people/nobody-at-all", headers=vh).status_code == 404

    team = {p["username"]: p for p in client.get("/api/people", headers=vh).json()}
    assert team[bob["username"]]["open_assigned"] == 1  # counted in what the viewer sees
    assert team["admin"]["role"] == "admin"
