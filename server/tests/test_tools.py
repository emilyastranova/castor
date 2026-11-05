"""Tool environments: specs, the library API, dispatch with a tool, and preparing ahead."""

import json
import time
import uuid

import pytest

from castor_server.command_spec import parse_spec
from castor_server.tool_spec import parse_tool_spec

RECIPE = """
id: {id}
name: Demo tool
build:
  base: docker.io/library/alpine:3.20
  install: |
    apk add --no-cache curl
    echo 'it''s "quoted"' > /etc/demo
  check: curl --version
host:
  check: command -v curl
  install: apt-get install -y curl
"""


def test_spec_rules():
    with pytest.raises(ValueError, match="at least one of image, build or host"):
        parse_tool_spec("id: x\nname: X\n")
    with pytest.raises(ValueError, match="unknown key"):
        parse_tool_spec("id: x\nname: X\nimage: a\nbuld: {}\n")
    with pytest.raises(ValueError, match="image or tool, not both"):
        parse_spec("id: c\nname: C\nimage: a\ntool: x\ncommand: echo\n")


def test_recipe_tag_and_files():
    spec = parse_tool_spec(RECIPE.format(id="demo"))
    tag = spec.build_tag()
    assert tag.startswith("castor-tools/demo:") and len(tag.split(":")[1]) == 12
    assert parse_tool_spec(RECIPE.format(id="demo")).build_tag() == tag  # stable
    changed = parse_tool_spec(RECIPE.format(id="demo").replace("curl --version", "curl -V"))
    assert changed.build_tag() != tag  # any recipe change is a new build
    files = spec.build_files()
    assert files["Dockerfile"].startswith("FROM docker.io/library/alpine:3.20\n")
    assert 'LABEL castor.tool="demo"' in files["Dockerfile"]
    # Scripts are files, so shell text needs no escaping in the Dockerfile.
    assert files["castor-install.sh"] == "apk add --no-cache curl\necho 'it''s \"quoted\"' > /etc/demo\n"
    assert files["castor-check.sh"] == "curl --version\n"


def test_way_for_each_agent():
    spec = parse_tool_spec(RECIPE.format(id="demo") + "image: docker.io/demo/tool\n")
    assert spec.way_for({"docker"})["kind"] == "image"  # a published image wins
    no_image = parse_tool_spec(RECIPE.format(id="demo"))
    assert no_image.way_for({"podman", "host"})["kind"] == "build"
    host = no_image.way_for({"host"})
    assert host["kind"] == "host" and host["install"] == "" and host["install_hint"] == "apt-get install -y curl"
    assert no_image.way_for({"host"}, host_install=True)["install"] == "apt-get install -y curl"
    assert parse_tool_spec("id: x\nname: X\nimage: a\n").way_for({"host"}) is None


def test_builtins_and_library_api(client, auth):
    tools = {t["id"]: t for t in client.get("/api/tools", headers=auth).json()}
    assert {"nmap", "ffuf", "netexec"} <= set(tools)
    assert "nmap-ping-sweep" in tools["nmap"]["used_by"]
    assert tools["netexec"]["recipe_tag"].startswith("castor-tools/netexec:")
    assert client.delete("/api/tools/nmap", headers=auth).status_code == 409  # built-in

    tid = f"demo-{uuid.uuid4().hex[:6]}"
    r = client.post("/api/tools", json={"yaml": RECIPE.format(id=tid)}, headers=auth)
    assert r.status_code == 201, r.text
    check = client.post("/api/tools/validate", json={"yaml": RECIPE.format(id=tid)}, headers=auth).json()
    assert check["ok"] and check["dockerfile"].startswith("FROM ")

    # A command can use it; one naming a missing tool is refused.
    cmd = f"id: use-{tid}\nname: Use\ntool: {tid}\ncommand: curl --version\n"
    assert client.post("/api/commands", json={"yaml": cmd}, headers=auth).status_code == 201
    bad = "id: use-missing\nname: Use\ntool: nope\ncommand: echo hi\n"
    assert "no tool nope" in client.post("/api/commands", json={"yaml": bad}, headers=auth).json()["detail"]
    assert client.delete(f"/api/tools/{tid}", headers=auth).status_code == 409  # in use


def _hello(ws, name, runtimes, tools=()):
    ws.send_text(json.dumps({
        "type": "hello", "secret": "test-agent-secret", "name": name, "hostname": "box", "os": "Linux",
        "username": "u", "version": "0.1.0", "tags": [name],
        "capabilities": {"runtimes": runtimes, "tools": list(tools)}, "max_concurrency": 1,
    }))
    assert json.loads(ws.receive_text())["type"] == "hello.ok"


def test_dispatch_sends_the_tool_and_prepare(client, auth):
    tid = f"demo-{uuid.uuid4().hex[:6]}"
    client.post("/api/tools", json={"yaml": RECIPE.format(id=tid)}, headers=auth)
    client.post("/api/commands", json={"yaml": f"id: run-{tid}\nname: Run\ntool: {tid}\ncommand: curl --version\n"}, headers=auth)
    code = f"TL-{uuid.uuid4().hex[:6]}"
    project = client.post("/api/projects", json={"codename": code, "name": code, "kind": "internal"}, headers=auth).json()
    agent = f"tooled-{uuid.uuid4().hex[:5]}"
    with client.websocket_connect("/ws/agent") as ws:
        _hello(ws, agent, ["docker"])
        task = client.post(
            f"/api/projects/{project['id']}/tasks",
            json={"title": "Curl", "kind": "command", "command_ref": f"run-{tid}", "agent_selector": {"tags": [agent]}},
            headers=auth,
        ).json()
        assert task["tool"] == tid and task["image"] is None
        client.post(f"/api/tasks/{task['id']}/run", headers=auth)
        while True:  # other tests may have left jobs any agent can take: finish those
            run = json.loads(ws.receive_text())
            if run["type"] != "job.run":
                continue
            job = run["job"]
            if job["command"] == "curl --version":
                break
            ws.send_text(json.dumps({"type": "job.finished", "job_id": job["id"], "exit_code": 0}))
        assert job["tool"]["kind"] == "build" and job["runtime"] == "container"
        assert job["image"] == job["tool"]["ref"] and "Dockerfile" in job["tool"]["files"]
        ws.send_text(json.dumps({"type": "job.finished", "job_id": job["id"], "exit_code": 0}))

        # Prepare ahead: the agent is asked, answers, and the library shows it ready.
        r = client.post(f"/api/tools/{tid}/prepare", json={}, headers=auth).json()
        assert agent in r["started"]
        msg = json.loads(ws.receive_text())
        while msg["type"] != "tool.prepare":
            msg = json.loads(ws.receive_text())
        ref = msg["tool"]["ref"]
        ws.send_text(json.dumps({"type": "tool.prepared", "tool_id": tid, "ref": ref, "ok": True, "tools": [ref]}))
        ws.send_text(json.dumps({"type": "heartbeat", "running": [], "tools": [ref]}))
        for _ in range(50):  # the server handles the agent's messages asynchronously
            mine = next(a for a in client.get(f"/api/tools/{tid}", headers=auth).json()["agents"] if a["agent_name"] == agent)
            if mine["ready"]:
                break
            time.sleep(0.05)
        assert mine["way"] == "build" and mine["ready"] is True and mine["state"] == "ready"


def test_agent_that_cant_run_the_tool_isnt_sent_it(client, auth):
    from castor_server.orchestrator.dispatcher import agent_matches, tool_ready
    from castor_server.orchestrator.gateway import AgentConnection

    spec = parse_tool_spec(RECIPE.format(id="demo"))
    image_only = parse_tool_spec("id: x\nname: X\nimage: a\n")

    def conn(runtimes, tools=()):
        return AgentConnection(agent_id=uuid.uuid4(), name="a", ws=None, capabilities={"runtimes": runtimes, "tools": list(tools)}, tags=[], max_concurrency=1)

    class Job:
        image = None

    assert agent_matches(conn(["docker"]), Job(), {}, spec)
    assert agent_matches(conn(["host"]), Job(), {}, spec)  # its host way
    assert not agent_matches(conn(["host"]), Job(), {}, image_only)
    assert not tool_ready(conn(["docker"]), spec)
    assert tool_ready(conn(["docker"], [spec.build_tag()]), spec)
