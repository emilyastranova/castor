"""Network routes: the rules (routing.py), the API, and what agents get and report."""

from __future__ import annotations

import json
import time
import uuid

import pytest

from castor_server import routing
from castor_server.routing import RouteCaps, RouteError, plan

# ---------- the rules ----------

NMAP = RouteCaps(hook=True, flags="-sT -Pn", only=["native", "hooked"])


def methods(caps: RouteCaps, command: str, *, in_container: bool = True, strict: bool = False, agent=None) -> list[str]:
    return [w["method"] for w in plan(caps, command, in_container=in_container, strict=strict, agent=agent or {})]


def test_ways_best_first():
    httpx = RouteCaps(proxy="-http-proxy {url}", scheme="http")
    assert methods(httpx, "httpx ${CASTOR_ROUTE_ARGS} -l t.txt") == ["native", "transparent"]
    assert methods(httpx, "httpx -l t.txt") == ["transparent"]  # no {{ route }}: no flag to fill
    assert methods(httpx, "httpx ${CASTOR_ROUTE_ARGS} -l t.txt", in_container=False) == ["native"]
    ways = plan(httpx, "httpx ${CASTOR_ROUTE_ARGS}", in_container=True, strict=True, agent={})
    assert ways[0] == {"method": "native", "scheme": "http", "args": "-http-proxy {url}", "flags": ""}
    assert methods(routing.SHELL, "curl https://x", in_container=False, agent={"routes": {"proxychains": True}}) == ["hooked", "env"]


def test_scanners_and_raw_packets():
    # nmap: a connect scan without pings, and only by proxychains (transparent would make every port open).
    assert methods(NMAP, "nmap ${CASTOR_ROUTE_ARGS} -sV -p 22 10.0.0.1") == ["hooked"]
    assert plan(NMAP, "nmap ${CASTOR_ROUTE_ARGS} -p 22 h", in_container=True, strict=True, agent={})[0]["flags"] == "-sT -Pn"
    with pytest.raises(RouteError, match="add -sT"):
        plan(NMAP, "nmap -p 22 10.0.0.1", in_container=True, strict=True, agent={})
    with pytest.raises(RouteError, match="SYN scan"):
        plan(NMAP, "nmap ${CASTOR_ROUTE_ARGS} -sS -p 22 h", in_container=True, strict=True, agent={})
    with pytest.raises(RouteError, match="ping sweep"):
        plan(RouteCaps(), "nmap -sn 10.0.0.0/24", in_container=True, strict=True, agent={})
    with pytest.raises(RouteError, match="ICMP"):
        plan(routing.SHELL, "ping -c1 10.0.0.1", in_container=True, strict=True, agent={})
    with pytest.raises(RouteError, match="proxychains"):  # a host without proxychains can't scan through it
        plan(NMAP, "nmap ${CASTOR_ROUTE_ARGS} -p 22 h", in_container=False, strict=False, agent={})
    with pytest.raises(RouteError, match="UDP"):
        plan(RouteCaps(unroutable="UDP can't go through"), "nmap -sU h", in_container=True, strict=True, agent={})


def test_strict_routes_need_a_container():
    with pytest.raises(RouteError, match="container"):
        plan(routing.SHELL, "curl https://x", in_container=False, strict=True, agent={})


def test_caps_validation():
    with pytest.raises(ValueError, match="url"):
        RouteCaps(proxy="--proxy")
    with pytest.raises(ValueError, match="options only"):
        RouteCaps(flags="-sT; rm -rf /")
    merged = routing.merge_caps(RouteCaps(proxy="-x {url}", env=True), RouteCaps.model_validate({"env": False}))
    assert merged.proxy == "-x {url}" and merged.env is False


def test_chain_label_and_fingerprint():
    hops = [{"kind": "ssh", "host": "jump01", "port": 22, "username": "ops"}, {"kind": "socks5", "host": "10.0.0.5", "port": 1080}]
    assert routing.chain_label(hops) == "agent → SSH ops@jump01:22 → SOCKS5 10.0.0.5:1080 → targets"
    assert routing.fingerprint("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl").startswith("SHA256:")


# ---------- the API and agents ----------

def _project(client, auth, code: str) -> dict:
    r = client.post("/api/projects", json={"codename": code, "name": code, "kind": "internal"}, headers=auth)
    assert r.status_code == 201, r.text
    return r.json()


def _route(client, auth, **over) -> dict:
    body = {"name": f"route-{uuid.uuid4().hex[:6]}", "hops": [
        {"kind": "ssh", "host": "jump01.lab", "username": "ops", "password": "hunter2-jump"},
        {"kind": "socks5", "host": "10.0.0.5", "username": "u", "password": "s0cks-pass"},
    ], **over}
    r = client.post("/api/routes", json=body, headers=auth)
    assert r.status_code == 201, r.text
    return r.json()


def test_route_crud_keeps_secrets_hidden(client, auth):
    route = _route(client, auth, strict=False, segment="DMZ")
    assert route["label"] == "agent → SSH ops@jump01.lab:22 → SOCKS5 10.0.0.5:1080 → targets"
    assert all(h["has_secret"] for h in route["hops"])
    text = json.dumps(client.get(f"/api/routes/{route['id']}", headers=auth).json())
    assert "hunter2-jump" not in text and "s0cks-pass" not in text and "\"secret\"" not in text

    # Kept hops (by id) keep their secrets; a generated key shows its public half; a hop moved
    # to another host forgets its trusted key.
    hops = route["hops"]
    r = client.patch(f"/api/routes/{route['id']}", json={"hops": [
        {"id": hops[0]["id"], "kind": "ssh", "host": "jump01.lab", "username": "ops", "generate_key": True},
        {"id": hops[1]["id"], "kind": "socks5", "host": "10.0.0.5", "username": "u"},
        {"kind": "http", "host": "proxy.corp", "port": 3128},
    ]}, headers=auth)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["hops"][0]["auth"] == "key" and out["hops"][0]["public_key"].startswith("ssh-ed25519 ")
    assert out["hops"][1]["has_secret"] and out["hops"][1]["auth"] == "password"
    assert out["hops"][2]["port"] == 3128 and out["hops"][2]["auth"] == "none"

    bad = client.post("/api/routes", json={"name": "x", "hops": [{"kind": "ssh", "host": "-oProxyCommand=sh", "username": "a",
                                                                   "password": "p"}]}, headers=auth)
    assert bad.status_code == 422
    bad = client.post("/api/routes", json={"name": "y", "hops": [{"kind": "ssh", "host": "a", "username": "a"}]}, headers=auth)
    assert bad.status_code == 422 and "key or a password" in bad.text
    assert client.post("/api/routes", json={"name": route["name"], "hops": [{"kind": "socks5", "host": "a"}]},
                       headers=auth).status_code == 409

    usage = client.get(f"/api/routes/{route['id']}/usage", headers=auth).json()
    assert usage["card_count"] == 0
    assert client.delete(f"/api/routes/{route['id']}", headers=auth).status_code == 204


def _agent(client, name: str, *, box: bool = False, runtimes=("host",), tags=()):
    ws = client.websocket_connect("/ws/agent")
    ws.__enter__()
    ws.send_text(json.dumps({
        "type": "hello", "secret": "test-agent-secret", "name": name, "hostname": name, "os": "Linux", "username": "root",
        "version": "1.13.0", "tags": [name, *tags], "max_concurrency": 8,
        "capabilities": {"runtimes": list(runtimes), "routes": {"box": box, "ssh": True, "proxychains": False}},
    }))
    hello = json.loads(ws.receive_text())
    assert hello["type"] == "hello.ok", hello
    return ws, hello["agent_id"]


def _job_run(client, auth, ws, task_id: str) -> dict:
    """The job.run for this card's latest job; other tests' leftover jobs that come first are finished."""
    for _ in range(20):
        msg = json.loads(ws.receive_text())
        if msg.get("type") != "job.run":
            continue
        latest = client.get(f"/api/tasks/{task_id}", headers=auth).json()["latest_job"]
        if latest and msg["job"]["id"] == latest["id"]:
            return msg
        ws.send_text(json.dumps({"type": "job.finished", "job_id": msg["job"]["id"], "exit_code": 0}))
    raise AssertionError("no job.run for the card")


def _wait(fn, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        value = fn()
        if value:
            return value
        time.sleep(0.05)
    return fn()


def test_jobs_get_their_route_and_report_back(client, auth):
    project = _project(client, auth, "ROUTE-26-001")
    route = _route(client, auth, strict=False, project_ids=[project["id"]])
    ws, agent_id = _agent(client, "route-agent-1")
    try:
        r = client.post(f"/api/projects/{project['id']}/tasks", json={
            "title": "Through the jumpbox", "kind": "command", "command": "curl -sS https://10.1.1.1/",
            "agent_selector": {"agent_id": agent_id}, "route": route["id"]}, headers=auth)
        assert r.status_code == 201, r.text
        task = r.json()
        assert task["route"] == route["id"]
        plan_out = client.post("/api/routes/plan", json={"task_id": task["id"]}, headers=auth).json()
        assert plan_out["host"]["ways"] == ["env"] and plan_out["container"]["ways"][-1] == "transparent"

        assert client.post(f"/api/tasks/{task['id']}/run", headers=auth).status_code in (200, 201)
        msg = _job_run(client, auth, ws, task["id"])
        job = msg["job"]
        got = job["route"]
        assert [h["kind"] for h in got["hops"]] == ["ssh", "socks5"]
        assert got["hops"][0]["password"] == "hunter2-jump" and got["hops"][1]["password"] == "s0cks-pass"
        assert got["plan"][0]["method"] == "env" and got["strict"] is False and len(got["key"]) == 64
        assert "${CASTOR" not in job["command"]  # nothing about the route on the command line

        # First host key seen: trusted. How it went: recorded on the job. Passwords: masked in logs.
        ws.send_text(json.dumps({"type": "route.host_key", "route_id": route["id"], "hop_id": got["hops"][0]["id"],
                                 "key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl",
                                 "changed": False}))
        ws.send_text(json.dumps({"type": "route.status", "route_id": route["id"], "key": got["key"], "state": "up",
                                 "mode": "local", "jobs": 1}))
        ws.send_text(json.dumps({"type": "job.started", "job_id": job["id"]}))
        ws.send_text(json.dumps({"type": "job.route", "job_id": job["id"], "method": "env"}))
        ws.send_text(json.dumps({"type": "job.log", "job_id": job["id"], "seq": 0, "stream": "stderr",
                                 "data": "proxy said: bad password s0cks-pass\n"}))
        ws.send_text(json.dumps({"type": "job.finished", "job_id": job["id"], "exit_code": 0}))
        detail = _wait(lambda: (d := client.get(f"/api/jobs/{job['id']}", headers=auth).json())["status"] == "succeeded" and d)
        assert detail["route_info"]["name"] == route["name"] and detail["route_info"]["method"] == "env"
        assert "s0cks-pass" not in json.dumps(detail["logs"])
        live = _wait(lambda: client.get(f"/api/routes/{route['id']}", headers=auth).json())
        assert live["hops"][0]["host_key_fingerprint"].startswith("SHA256:")
        assert any(x["agent_id"] == agent_id and x["state"] == "up" for x in live["live"])

        # A different key later: held for a person, and jobs on the route stop at it.
        ws.send_text(json.dumps({"type": "route.host_key", "route_id": route["id"], "hop_id": got["hops"][0]["id"],
                                 "key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBhz0vNhfp6E1jMdcZ8D4IUZXyN4wAR1pnaJuZH2u2gO",
                                 "changed": True}))
        pending = _wait(lambda: (r := client.get(f"/api/routes/{route['id']}", headers=auth).json())["pending_keys"] and r)
        assert pending["hops"][0]["host_key_pending_fingerprint"]
        client.post(f"/api/tasks/{task['id']}/run", headers=auth)
        failed = _wait(lambda: (j := client.get(f"/api/tasks/{task['id']}", headers=auth).json()["latest_job"])["status"] == "failed" and j)
        assert "host key changed" in failed["error"]
        ok = client.post(f"/api/routes/{route['id']}/hops/{got['hops'][0]['id']}/accept-host-key", headers=auth)
        assert ok.status_code == 200 and ok.json()["pending_keys"] == 0

        # Test from the agent: it answers route.test hop by hop.
        import threading

        def answer():
            m = json.loads(ws.receive_text())
            while m["type"] != "route.test":
                if m["type"] == "job.run":
                    ws.send_text(json.dumps({"type": "job.finished", "job_id": m["job"]["id"], "exit_code": 0}))
                m = json.loads(ws.receive_text())
            assert m["target"] == ["10.1.1.1", 443]
            ws.send_text(json.dumps({"type": "route.tested", "request_id": m["request_id"], "hops": [
                {"index": 0, "ok": True, "ms": 12}, {"index": 1, "ok": False, "error": "it refused the username and password"}]}))

        t = threading.Thread(target=answer)
        t.start()
        tested = client.post(f"/api/routes/{route['id']}/test", json={"agent_id": agent_id, "target": "10.1.1.1:443"}, headers=auth)
        t.join(5)
        assert tested.status_code == 200, tested.text
        res = tested.json()
        assert res["ok"] is False and res["hops"][1]["error"].startswith("it refused") and res["hops"][0]["hop_id"]
        assert client.get(f"/api/routes/{route['id']}", headers=auth).json()["last_test"]["ok"] is False
    finally:
        ws.__exit__(None, None, None)


def test_strict_routes_scope_and_agent_routes(client, auth):
    project = _project(client, auth, "ROUTE-26-002")
    strict = _route(client, auth, project_ids=[project["id"]])
    ws, agent_id = _agent(client, "route-agent-2")
    try:
        # Strict: a host shell command is refused (it couldn't be held to the route).
        r = client.post(f"/api/projects/{project['id']}/tasks", json={
            "title": "Host only", "kind": "command", "command": "curl https://x", "agent_selector": {"agent_id": agent_id},
            "route": strict["id"]}, headers=auth)
        task = r.json()
        client.post(f"/api/tasks/{task['id']}/run", headers=auth)
        job = _wait(lambda: (j := client.get(f"/api/tasks/{task['id']}", headers=auth).json()["latest_job"])["status"] == "failed" and j)
        assert "container" in job["error"]

        # The project's default route applies to cards without one; "none" opts a card out.
        r = client.patch(f"/api/projects/{project['id']}", json={"settings": {"route": strict["id"]}}, headers=auth)
        assert r.status_code == 200, r.text
        loose = _route(client, auth, strict=False, project_ids=[project["id"]])
        r = client.post(f"/api/projects/{project['id']}/tasks", json={
            "title": "Default", "kind": "command", "command": "echo hi", "agent_selector": {"agent_id": agent_id}}, headers=auth)
        default_card = r.json()
        assert client.post("/api/routes/plan", json={"task_id": default_card["id"]}, headers=auth).json()["route"]["id"] == strict["id"]
        client.patch(f"/api/tasks/{default_card['id']}", json={"route": "none"}, headers=auth)
        assert client.post("/api/routes/plan", json={"task_id": default_card["id"]}, headers=auth).json()["route"] is None

        # Outside the scope through a route: a person approves it first.
        client.patch(f"/api/projects/{project['id']}", json={"settings": {"scope": {"groups": [{"name": "Lab", "include": ["10.0.0.0/24"]}]}}},
                     headers=auth)
        r = client.post(f"/api/projects/{project['id']}/tasks", json={
            "title": "Scan", "kind": "command", "command_ref": "nmap-service-scan",
            "command_args": {"targets": "192.168.9.9", "ports": "22"}, "agent_selector": {"agent_id": agent_id},
            "route": loose["id"]}, headers=auth)
        assert r.status_code == 201, r.text
        scan = r.json()
        assert "${CASTOR_ROUTE_ARGS}" in scan["command"]
        run = client.post(f"/api/tasks/{scan['id']}/run", headers=auth)
        assert run.status_code in (200, 201), run.text
        latest = client.get(f"/api/tasks/{scan['id']}", headers=auth).json()["latest_job"]
        assert latest["status"] == "approval"

        # An agent's own route goes first; the card's is chained after it.
        assert client.patch(f"/api/agents/{agent_id}", json={"route": loose["id"]}, headers=auth).status_code == 200
        r = client.post(f"/api/projects/{project['id']}/tasks", json={
            "title": "Chained", "kind": "command", "command": "curl https://x", "agent_selector": {"agent_id": agent_id},
            "route": strict["id"], "image": "docker.io/curlimages/curl"}, headers=auth)
        chained = r.json()
        client.post(f"/api/tasks/{chained['id']}/run", headers=auth)
        # This agent has no route box: a container job can't take a route here, so it waits.
        time.sleep(0.3)
        assert client.get(f"/api/tasks/{chained['id']}", headers=auth).json()["latest_job"]["status"] == "queued"
        client.patch(f"/api/agents/{agent_id}", json={"route": ""}, headers=auth)
        client.post(f"/api/jobs/{client.get(f'/api/tasks/{chained['id']}', headers=auth).json()['latest_job']['id']}/cancel", headers=auth)
    finally:
        ws.__exit__(None, None, None)
    # Deleting a route in use needs force, and its cards then refuse to run rather than going direct.
    assert client.delete(f"/api/routes/{strict['id']}", headers=auth).status_code == 409
    assert client.delete(f"/api/routes/{strict['id']}?force=true", headers=auth).status_code == 204


def test_agent_route_chains_in_front(client, auth):
    project = _project(client, auth, "ROUTE-26-003")
    agent_route = _route(client, auth, strict=False, name=f"agent-way-{uuid.uuid4().hex[:4]}")
    card_route = _route(client, auth, strict=False, project_ids=[project["id"]],
                        hops=[{"kind": "http", "host": "inner.proxy", "port": 3128}])
    ws, agent_id = _agent(client, "route-agent-3")
    try:
        assert client.patch(f"/api/agents/{agent_id}", json={"route": agent_route["id"]}, headers=auth).status_code == 200
        r = client.post(f"/api/projects/{project['id']}/tasks", json={
            "title": "Chained", "kind": "command", "command": "curl https://x", "agent_selector": {"agent_id": agent_id},
            "route": card_route["id"]}, headers=auth)
        client.post(f"/api/tasks/{r.json()['id']}/run", headers=auth)
        msg = _job_run(client, auth, ws, r.json()["id"])
        assert [h["kind"] for h in msg["job"]["route"]["hops"]] == ["ssh", "socks5", "http"]
        client.post(f"/api/jobs/{msg['job']['id']}/cancel", headers=auth)
        client.patch(f"/api/agents/{agent_id}", json={"route": ""}, headers=auth)
    finally:
        ws.__exit__(None, None, None)


def test_a_route_only_goes_to_the_agents_it_works_from(client, auth):
    project = _project(client, auth, "ROUTE-26-004")
    pair = f"pair-{uuid.uuid4().hex[:4]}"
    vpn = f"vpn-{uuid.uuid4().hex[:4]}"
    ws_a, a = _agent(client, f"on-vpn-{uuid.uuid4().hex[:4]}", tags=(pair, vpn))
    ws_b, b = _agent(client, f"off-vpn-{uuid.uuid4().hex[:4]}", tags=(pair,))
    try:
        # Unknown agents and an empty limit are refused; the limit is kept tidy.
        assert client.post("/api/routes", json={"name": "x", "hops": [{"kind": "socks5", "host": "10.9.0.1"}],
                                                "agents": {"ids": [str(uuid.uuid4())]}}, headers=auth).status_code == 422
        assert client.post("/api/routes", json={"name": "x", "hops": [{"kind": "socks5", "host": "10.9.0.1"}],
                                                "agents": {"ids": [], "tags": []}}, headers=auth).status_code == 422
        route = _route(client, auth, strict=False, project_ids=[project["id"]],
                       hops=[{"kind": "socks5", "host": "10.9.0.1", "port": 9050}], agents={"tags": [vpn, f" {vpn} "]})
        assert route["agents"] == {"ids": [], "tags": [vpn]}

        # Either agent has the card's tag; only the one on the VPN gets the job.
        r = client.post(f"/api/projects/{project['id']}/tasks", json={
            "title": "Through the VPN", "kind": "command", "command": "curl http://intranet", "agent_selector": {"tags": [pair]},
            "route": route["id"]}, headers=auth)
        card = r.json()
        client.post(f"/api/tasks/{card['id']}/run", headers=auth)
        msg = _job_run(client, auth, ws_a, card["id"])
        assert msg["job"]["route"]["hops"][0]["port"] == 9050
        assert client.get(f"/api/tasks/{card['id']}", headers=auth).json()["latest_job"]["agent_id"] == a
        ws_a.send_text(json.dumps({"type": "job.finished", "job_id": msg["job"]["id"], "exit_code": 0}))

        # A card pinned to an agent the route doesn't work from waits, until the route takes it in.
        r = client.post(f"/api/projects/{project['id']}/tasks", json={
            "title": "Pinned elsewhere", "kind": "command", "command": "curl http://intranet", "agent_selector": {"agent_id": b},
            "route": route["id"]}, headers=auth)
        pinned = r.json()
        client.post(f"/api/tasks/{pinned['id']}/run", headers=auth)
        time.sleep(0.3)
        assert client.get(f"/api/tasks/{pinned['id']}", headers=auth).json()["latest_job"]["status"] == "queued"
        r = client.patch(f"/api/routes/{route['id']}", json={"agents": {"ids": [b], "tags": [vpn]}}, headers=auth)
        assert r.status_code == 200 and r.json()["agents"] == {"ids": [b], "tags": [vpn]}
        msg = _job_run(client, auth, ws_b, pinned["id"])
        ws_b.send_text(json.dumps({"type": "job.finished", "job_id": msg["job"]["id"], "exit_code": 0}))

        # Testing from, or giving it as its own route to, an agent it doesn't work from is refused.
        client.patch(f"/api/routes/{route['id']}", json={"agents": {"tags": [vpn]}}, headers=auth)
        r = client.post(f"/api/routes/{route['id']}/test", json={"agent_id": b}, headers=auth)
        assert r.status_code == 409 and "only works from" in r.text
        r = client.patch(f"/api/agents/{b}", json={"route": route["id"]}, headers=auth)
        assert r.status_code == 422 and "only works from" in r.text

        # Every agent with the tag: one job, on the agent the route works from.
        r = client.post(f"/api/projects/{project['id']}/tasks", json={
            "title": "Everywhere", "kind": "command", "command": "curl http://intranet", "agent_selector": {"tags": [pair], "all": True},
            "route": route["id"]}, headers=auth)
        every = r.json()
        client.post(f"/api/tasks/{every['id']}/run", headers=auth)
        msg = _job_run(client, auth, ws_a, every["id"])
        assert not msg["job"].get("target_agent_id") or msg["job"]["target_agent_id"] == a
        assert len(client.get(f"/api/tasks/{every['id']}/jobs", headers=auth).json()) == 1
        ws_a.send_text(json.dumps({"type": "job.finished", "job_id": msg["job"]["id"], "exit_code": 0}))

        # Renaming the agent tag renames it on the route; null opens it to every agent again.
        renamed = f"{vpn}-new"
        r = client.post("/api/tags/agents/rename", json={"from": [vpn], "to": renamed}, headers=auth)
        assert r.status_code == 200, r.text
        assert client.get(f"/api/routes/{route['id']}", headers=auth).json()["agents"]["tags"] == [renamed]
        r = client.patch(f"/api/routes/{route['id']}", json={"agents": None}, headers=auth)
        assert r.json()["agents"] is None
        assert client.patch(f"/api/agents/{b}", json={"route": route["id"]}, headers=auth).status_code == 200
        client.patch(f"/api/agents/{b}", json={"route": ""}, headers=auth)
    finally:
        ws_a.__exit__(None, None, None)
        ws_b.__exit__(None, None, None)


def test_stand_in_addresses_become_names_again():
    names = routing.stand_ins({"198.18.0.1": "Intranet.Lab.", "198.18.0.12": "dc1.corp.local", "10.0.0.1": "not-a-stand-in",
                               "198.18.0.3": "bad name; rm -rf"})
    assert names == {"198.18.0.1": "intranet.lab", "198.18.0.12": "dc1.corp.local"}
    rows = [{"host": "198.18.0.1", "port": 445, "url": "https://198.18.0.12:8443/", "other": "198.18.0.123"}]
    assert routing.unfake(rows, names) == [{"host": "intranet.lab", "port": 445, "url": "https://dc1.corp.local:8443/",
                                            "other": "198.18.0.123"}]
