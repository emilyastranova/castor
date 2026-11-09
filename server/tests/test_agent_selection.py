"""Choosing where work runs: a card or a playbook run on one agent, any agent with some tags,
or every connected agent with them (a card: one job per agent, as a batch; a playbook: one
run per agent)."""

import json
import uuid


def _agent(client, name, tags):
    ws = client.websocket_connect("/ws/agent")
    agent = ws.__enter__()
    agent.send_text(json.dumps({
        "type": "hello", "secret": "test-agent-secret", "name": name, "hostname": "h", "os": "Linux",
        "username": "root", "version": "0.1.0", "capabilities": {"runtimes": ["host"]}, "tags": tags,
        "max_concurrency": 8,
    }))
    assert json.loads(agent.receive_text())["type"] == "hello.ok"
    return ws, agent


def _take(agent, wanted):
    """The job.run for one of `wanted`, finishing anything else the agent is sent meanwhile."""
    while True:
        msg = json.loads(agent.receive_text())
        if msg["type"] != "job.run":
            continue
        if msg["job"]["id"] in wanted:
            return msg["job"]["id"]
        agent.send_text(json.dumps({"type": "job.finished", "job_id": msg["job"]["id"], "exit_code": 0}))


def _finish(agent, job_id, code=0):
    agent.send_text(json.dumps({"type": "job.started", "job_id": job_id}))
    agent.send_text(json.dumps({"type": "job.finished", "job_id": job_id, "exit_code": code}))


def _project(client, auth):
    return client.post("/api/projects", json={"codename": f"AS-{uuid.uuid4().hex[:6]}", "name": "Agents", "kind": "internal"},
                       headers=auth).json()


def _wait(fn, timeout=5.0):
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        if value := fn():
            return value
        time.sleep(0.05)
    raise AssertionError("timed out")


def test_a_card_runs_on_every_agent_with_its_tags(client, auth):
    tag = f"fleet-{uuid.uuid4().hex[:5]}"
    a_ws, a = _agent(client, f"{tag}-a", [tag, "extra"])
    b_ws, b = _agent(client, f"{tag}-b", [tag])
    c_ws, _c = _agent(client, f"{tag}-c", ["elsewhere"])
    try:
        p = _project(client, auth)
        board = client.get(f"/api/projects/{p['id']}/board", headers=auth).json()
        review = next(c for c in board["columns"] if c["role"] == "review")
        task = client.post(f"/api/projects/{p['id']}/tasks", json={
            "title": "Everywhere", "kind": "command", "command": "hostname",
            "agent_selector": {"tags": [tag, tag, " "], "all": True},
        }, headers=auth).json()
        assert task["agent_selector"] == {"tags": [tag], "all": True}

        client.post(f"/api/tasks/{task['id']}/run", headers=auth)
        jobs = client.get(f"/api/tasks/{task['id']}/jobs", headers=auth).json()
        assert len(jobs) == 2  # a and b, not c
        ids = {j["id"] for j in jobs}
        on_a, on_b = _take(a, ids), _take(b, ids)
        assert on_a != on_b
        card = client.get(f"/api/tasks/{task['id']}", headers=auth).json()
        assert card["batch"]["total"] == 2 and card["batch"]["status"] == "running"

        _finish(a, on_a)
        card = _wait(lambda: (t := client.get(f"/api/tasks/{task['id']}", headers=auth).json())["batch"]["succeeded"] == 1 and t)
        assert card["batch"]["status"] == "running" and card["column_id"] != review["id"]  # b is still going
        _finish(b, on_b)
        card = _wait(lambda: (t := client.get(f"/api/tasks/{task['id']}", headers=auth).json())["batch"]["active"] == 0 and t)
        assert card["batch"]["status"] == "succeeded" and card["column_id"] == review["id"]

        # Again, with one failing: the batch fails and the card stays put.
        client.post(f"/api/tasks/{task['id']}/move", json={"column_id": board["columns"][0]["id"]}, headers=auth)
        client.post(f"/api/tasks/{task['id']}/run", headers=auth)
        ids = {j["id"] for j in client.get(f"/api/tasks/{task['id']}/jobs", headers=auth).json()} - {on_a, on_b}
        on_a, on_b = _take(a, ids), _take(b, ids)
        _finish(a, on_a)
        _finish(b, on_b, code=1)
        card = _wait(lambda: (t := client.get(f"/api/tasks/{task['id']}", headers=auth).json())["batch"]["active"] == 0 and t)
        assert card["batch"]["status"] == "failed" and card["batch"]["failed"] == 1 and card["column_id"] != review["id"]
    finally:
        for ws in (a_ws, b_ws, c_ws):
            ws.__exit__(None, None, None)


def test_with_one_or_no_agent_connected_it_is_an_ordinary_job(client, auth):
    tag = f"lone-{uuid.uuid4().hex[:5]}"
    p = _project(client, auth)
    task = client.post(f"/api/projects/{p['id']}/tasks", json={
        "title": "Later", "kind": "command", "command": "id", "agent_selector": {"tags": [tag], "all": True},
    }, headers=auth).json()
    job = client.post(f"/api/tasks/{task['id']}/run", headers=auth).json()
    assert job["status"] == "queued"  # waits for the first agent with the tag
    ws, agent = _agent(client, f"{tag}-x", [tag])
    try:
        _finish(agent, _take(agent, {job["id"]}))
        assert _wait(lambda: client.get(f"/api/jobs/{job['id']}", headers=auth).json()["status"] == "succeeded")
        assert client.get(f"/api/tasks/{task['id']}", headers=auth).json()["batch"] is None
    finally:
        ws.__exit__(None, None, None)


def test_selectors_are_checked(client, auth):
    p = _project(client, auth)
    url = f"/api/projects/{p['id']}/tasks"
    base = {"title": "x", "kind": "command", "command": "id"}
    assert client.post(url, json={**base, "agent_selector": {"all": True}}, headers=auth).status_code == 422
    assert client.post(url, json={**base, "agent_selector": {"agent_id": str(uuid.uuid4())}}, headers=auth).status_code == 422
    assert client.post(url, json={**base, "agent_selector": {"tags": [], "all": False}}, headers=auth).json()["agent_selector"] == {}
    run = client.post("/api/playbooks/pipeline-demo/run", json={"project_id": p["id"], "agent_selector": {"tags": ["nobody-has-this"], "all": True}},
                      headers=auth)
    assert run.status_code == 409 and "nobody-has-this" in run.text


def test_a_playbook_runs_on_a_chosen_agent_or_once_per_agent(client, auth):
    tag = f"pb-{uuid.uuid4().hex[:5]}"
    a_ws, _a = _agent(client, f"{tag}-a", [tag])
    b_ws, _b = _agent(client, f"{tag}-b", [tag])
    runs = []
    try:
        p = _project(client, auth)
        agents = {a["name"]: a["id"] for a in client.get("/api/agents", headers=auth).json()}

        one = client.post("/api/playbooks/pipeline-demo/run", json={"project_id": p["id"], "agent_selector": {"agent_id": agents[f"{tag}-b"]}},
                          headers=auth).json()
        runs.append(one["id"])
        assert one["agent_selector"] == {"agent_id": agents[f"{tag}-b"]} and one["sibling_run_ids"] == []
        assert all(t["agent_selector"] == {"agent_id": agents[f"{tag}-b"]} for t in one["tasks"])

        each = client.post("/api/playbooks/pipeline-demo/run", json={"project_id": p["id"], "agent_selector": {"tags": [tag], "all": True}},
                           headers=auth)
        assert each.status_code == 201, each.text
        first = each.json()
        assert len(first["sibling_run_ids"]) == 1
        both = [first, client.get(f"/api/playbook-runs/{first['sibling_run_ids'][0]}", headers=auth).json()]
        runs += [r["id"] for r in both]
        assert {r["agent_selector"]["agent_id"] for r in both} == {agents[f"{tag}-a"], agents[f"{tag}-b"]}
        cards = {client.get(f"/api/tasks/{r['card_id']}", headers=auth).json()["title"] for r in both}
        assert cards == {f"Pipeline Demo · {tag}-a", f"Pipeline Demo · {tag}-b"}
        for r in both:  # every step card goes to the run's own agent
            assert {t["agent_selector"]["agent_id"] for t in r["tasks"]} == {r["agent_selector"]["agent_id"]}
    finally:
        for run_id in runs:
            client.post(f"/api/playbook-runs/{run_id}/cancel", headers=auth)
        for ws in (a_ws, b_ws):
            ws.__exit__(None, None, None)


def test_run_card_over_mcp_chooses_the_agents(client, auth):
    tag = f"mcp-{uuid.uuid4().hex[:5]}"
    a_ws, a = _agent(client, f"{tag}-a", [tag])
    b_ws, b = _agent(client, f"{tag}-b", [tag])
    try:
        p = _project(client, auth)
        made = client.post("/api/tokens", json={"name": "runner", "scopes": ["projects:write", "jobs:write"]}, headers=auth).json()
        bearer = {"Authorization": f"Bearer {made['token']}"}
        task = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Both", "kind": "command", "command": "id"}, headers=auth).json()

        def run_card(**args):
            r = client.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                              "params": {"name": "run_card", "arguments": {"task_id": task["id"], **args}}}, headers=bearer)
            return r.json()["result"]

        bad = run_card(agent_selector={"all": True})
        assert bad["isError"] and "needs tags" in bad["content"][0]["text"]
        out = run_card(agent_selector={"tags": [tag], "all": True})
        assert not out["isError"], out
        assert client.get(f"/api/tasks/{task['id']}", headers=auth).json()["agent_selector"] == {"tags": [tag], "all": True}
        ids = {j["id"] for j in client.get(f"/api/tasks/{task['id']}/jobs", headers=auth).json()}
        assert len(ids) == 2
        for agent in (a, b):
            _finish(agent, _take(agent, ids))
        card = _wait(lambda: (t := client.get(f"/api/tasks/{task['id']}", headers=auth).json())["batch"]["active"] == 0 and t)
        assert card["batch"]["status"] == "succeeded"

        # Without agent_selector it runs as the card says (here: both again).
        assert not run_card()["isError"]
        assert len(client.get(f"/api/tasks/{task['id']}/jobs", headers=auth).json()) == 4
    finally:
        for ws in (a_ws, b_ws):
            ws.__exit__(None, None, None)


def test_container_jobs_get_their_work_directory(client, auth):
    """{{ work }} renders as "${CASTOR_WORK}": the server sets it to /work for a container job
    (so any agent passes it in), and leaves it to the agent for a host job."""
    tag = f"work-{uuid.uuid4().hex[:5]}"
    ws = client.websocket_connect("/ws/agent")
    agent = ws.__enter__()
    agent.send_text(json.dumps({
        "type": "hello", "secret": "test-agent-secret", "name": tag, "hostname": "h", "os": "Linux", "username": "root",
        "version": "0.1.0", "capabilities": {"runtimes": ["docker", "host"]}, "tags": [tag], "max_concurrency": 8,
    }))
    assert json.loads(agent.receive_text())["type"] == "hello.ok"
    try:
        p = _project(client, auth)
        sent = {}
        for title, image in (("in a container", "docker.io/library/alpine"), ("in the host shell", None)):
            task = client.post(f"/api/projects/{p['id']}/tasks", json={
                "title": title, "kind": "command", "command": 'echo hi > "${CASTOR_WORK}"/out.txt', "image": image,
                "agent_selector": {"tags": [tag]},
            }, headers=auth).json()
            job_id = client.post(f"/api/tasks/{task['id']}/run", headers=auth).json()["id"]
            while True:
                msg = json.loads(agent.receive_text())
                if msg["type"] == "job.run" and msg["job"]["id"] == job_id:
                    sent[title] = msg["job"]
                    break
            _finish(agent, job_id)
        assert sent["in a container"]["env"]["CASTOR_WORK"] == "/work"
        assert sent["in a container"]["display_command"] == "echo hi > /work/out.txt"
        assert "CASTOR_WORK" not in sent["in the host shell"]["env"]
        assert sent["in the host shell"]["display_command"] == 'echo hi > "${CASTOR_WORK}"/out.txt'
    finally:
        ws.__exit__(None, None, None)
