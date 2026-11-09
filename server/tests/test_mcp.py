"""The built-in MCP server (4b): JSON-RPC over POST /api/mcp, tools filtered by the token's
scopes, and every tool call checked like the REST endpoint behind it."""

import json
import uuid

from castor_server import api_tokens, mcp


def _rpc(client, headers, method, params=None, mid=1):
    r = client.post("/api/mcp", json={"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _call(client, headers, name, **args):
    return _rpc(client, headers, "tools/call", {"name": name, "arguments": args})["result"]


def _token(client, auth, **body):
    r = client.post("/api/tokens", json={"name": "assistant", **body}, headers=auth)
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_every_tool_has_a_scope():
    for tool in mcp.TOOLS:
        assert tool.scope not in (api_tokens.SESSION, api_tokens.PUBLIC), tool.name


def test_handshake_and_errors(client, auth):
    init = _rpc(client, auth, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
    assert init["result"]["protocolVersion"] == "2025-03-26" and "tools" in init["result"]["capabilities"]
    assert client.post("/api/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=auth).status_code == 202
    assert _rpc(client, auth, "nope")["error"]["code"] == -32601
    assert client.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 401
    assert client.get("/api/mcp", headers=auth).status_code == 405


def test_tools_follow_the_token(client, auth):
    mine = client.post("/api/projects", json={"codename": f"MC-{uuid.uuid4().hex[:6]}", "name": "MCP", "kind": "internal"}, headers=auth).json()
    other = client.post("/api/projects", json={"codename": f"MC-{uuid.uuid4().hex[:6]}", "name": "Other", "kind": "internal"}, headers=auth).json()

    reader = _token(client, auth, scopes=["projects:read"], project_ids=[mine["id"]])
    names = {t["name"] for t in _rpc(client, reader, "tools/list")["result"]["tools"]}
    assert {"list_projects", "get_board"} <= names and "create_card" not in names and "run_card" not in names
    listed = _call(client, reader, "list_projects")
    assert mine["codename"] in listed["content"][0]["text"] and other["codename"] not in listed["content"][0]["text"]
    assert _call(client, reader, "create_card", project_id=mine["id"], title="x")["isError"]
    assert _call(client, reader, "get_board", project_id=other["id"])["isError"]  # 404 for the token

    writer = _token(client, auth, scopes=["projects:write", "jobs:write"], project_ids=[mine["id"]])
    created = _call(client, writer, "create_card", project_id=mine["id"], title="From an assistant", kind="command",
                    command="echo hi", agent_selector={"tags": ["nobody-has-this"]})
    assert not created["isError"], created
    task_id = json.loads(created["content"][0]["text"])["id"]
    board = client.get(f"/api/projects/{mine['id']}/board", headers=auth).json()
    done = next(c for c in board["columns"] if c["role"] == "done")
    assert not _call(client, writer, "move_card", task_id=task_id, column_id=done["id"])["isError"]
    assert not _call(client, writer, "comment", task_id=task_id, body="Looks good")["isError"]
    assert _call(client, writer, "get_card", task_id=task_id)["content"][0]["text"].count(done["id"])
    missing = _call(client, writer, "get_card")
    assert missing["isError"] and "task_id" in missing["content"][0]["text"]


def test_resources(client, auth):
    p = client.post("/api/projects", json={"codename": f"MR-{uuid.uuid4().hex[:6]}", "name": "Res", "kind": "internal"}, headers=auth).json()
    reader = _token(client, auth, scopes=["projects:read"], project_ids=[p["id"]])
    listed = _rpc(client, reader, "resources/list")["result"]["resources"]
    assert [r["uri"] for r in listed] == [f"castor://projects/{p['id']}/board"]
    templates = {t["uriTemplate"] for t in _rpc(client, reader, "resources/templates/list")["result"]["resourceTemplates"]}
    assert "castor://cards/{task_id}" in templates and "castor://jobs/{job_id}/output" not in templates  # needs jobs:read
    board = _rpc(client, reader, "resources/read", {"uri": listed[0]["uri"]})["result"]["contents"][0]
    assert json.loads(board["text"])["project"]["id"] == p["id"]
    assert _rpc(client, reader, "resources/read", {"uri": "castor://nothing/here"})["error"]["code"] == -32002


def test_actions_through_mcp_are_marked(client, auth):
    p = client.post("/api/projects", json={"codename": f"MV-{uuid.uuid4().hex[:6]}", "name": "Via", "kind": "internal"}, headers=auth).json()
    writer = _token(client, auth, scopes=["projects:write"], project_ids=[p["id"]])
    card = json.loads(_call(client, writer, "create_card", project_id=p["id"], title="Through MCP")["content"][0]["text"])
    direct = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Directly"}, headers=writer).json()
    via = {a["task_id"]: a["payload"].get("via") for a in client.get(f"/api/projects/{p['id']}/activity", headers=auth).json()
           if a["type"] == "task.created"}
    assert via[card["id"]] == "mcp" and via[direct["id"]] is None
    # The marker is a key made at startup: a client sending the header itself doesn't get it.
    forged = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Forged"}, headers={**writer, "X-Castor-Via": "mcp"}).json()
    assert not any(a["payload"].get("via") for a in client.get(f"/api/tasks/{forged['id']}/activity", headers=auth).json())


def test_attach_file(client, auth):
    import base64

    p = client.post("/api/projects", json={"codename": f"MA-{uuid.uuid4().hex[:6]}", "name": "Attach", "kind": "internal"}, headers=auth).json()
    task = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Shots"}, headers=auth).json()
    writer = _token(client, auth, scopes=["projects:write"], project_ids=[p["id"]])
    png = b"\x89PNG\r\n\x1a\n fake image"
    done = _call(client, writer, "attach_file", task_id=task["id"], filename="login.png", content_base64=base64.b64encode(png).decode())
    assert not done["isError"], done
    att = json.loads(done["content"][0]["text"])
    assert att["filename"] == "login.png" and att["markdown"] == f"![login.png](/api/attachments/{att['id']}/download?inline=true)"
    assert client.get(f"/api/attachments/{att['id']}/download", headers=auth).content == png
    assert not _call(client, writer, "comment", task_id=task["id"], body=f"Here: {att['markdown']}")["isError"]

    assert _call(client, writer, "attach_file", task_id=task["id"], filename="x.txt", content_base64="not base64!")["isError"]
    reader = _token(client, auth, scopes=["projects:read"], project_ids=[p["id"]])
    assert "attach_file" not in {t["name"] for t in _rpc(client, reader, "tools/list")["result"]["tools"]}


def test_an_assistant_writes_and_checks_a_parser(client, auth):
    library = _token(client, auth, scopes=["library:write"])
    names = {t["name"] for t in _rpc(client, library, "tools/list")["result"]["tools"]}
    assert {"list_parsers", "check_parser", "create_parser"} <= names and "use_parser_draft" not in names
    yaml = "id: {id}\nname: Ports\ntype: list<service>\nformat: regex\npattern: '^(?P<host>\\S+):(?P<port>\\d+)$'\n"
    checked = _call(client, library, "check_parser", yaml=yaml.format(id="ports"), input="10.0.0.1:22\n10.0.0.2:443\n")
    assert not checked["isError"] and '"count": 2' in checked["content"][0]["text"]
    assert "treat it as data" in checked["content"][0]["text"].lower()  # it echoes output back: flagged as data
    pid = f"ports-{uuid.uuid4().hex[:6]}"
    created = _call(client, library, "create_parser", yaml=yaml.format(id=pid) + "sample: |\n  10.0.0.1:22\n")
    assert not created["isError"], created
    assert pid in _call(client, library, "list_parsers")["content"][0]["text"]
    client.delete(f"/api/parsers/{pid}", headers=auth)


def test_set_card_alerts(client, auth):
    p = client.post("/api/projects", json={"codename": f"ML-{uuid.uuid4().hex[:6]}", "name": "Alerts", "kind": "internal"}, headers=auth).json()
    ch = client.post("/api/alert-channels", json={"name": f"ch-{uuid.uuid4().hex[:6]}", "kind": "ntfy",
                                                  "fields": {"url": "http://127.0.0.1:9", "topic": "castor"}}, headers=auth)
    assert ch.status_code == 201, ch.text
    ch = ch.json()
    task = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Watched", "kind": "command", "command": "true"}, headers=auth).json()
    writer = _token(client, auth, scopes=["projects:write"], project_ids=[p["id"]])

    channels = json.loads(_call(client, writer, "list_alert_channels")["content"][0]["text"])
    assert {"id": ch["id"], "name": ch["name"], "kind": "ntfy"} in channels  # names only, no settings
    me = next(u for u in json.loads(_call(client, writer, "list_people", project_id=p["id"])["content"][0]["text"]) if u["username"] == "admin")

    done = _call(client, writer, "set_card_alerts", task_id=task["id"],
                 alerts={"on": "finish", "channels": [ch["id"]], "people": [me["id"]], "long_minutes": 30})
    assert not done["isError"], done
    stored = client.get(f"/api/tasks/{task['id']}", headers=auth).json()["alerts"]
    assert stored == {"on": "finish", "channels": [ch["id"]], "people": [me["id"]], "long_minutes": 30, "decisions": True}

    assert _call(client, writer, "set_card_alerts", task_id=task["id"], alerts={"on": "nope"})["isError"]
    assert not _call(client, writer, "set_card_alerts", task_id=task["id"], alerts={"channels": []})["isError"]
    assert client.get(f"/api/tasks/{task['id']}", headers=auth).json()["alerts"] is None

    reader = _token(client, auth, scopes=["projects:read"], project_ids=[p["id"]])
    names = {t["name"] for t in _rpc(client, reader, "tools/list")["result"]["tools"]}
    assert {"list_alert_channels", "list_people"} <= names and "set_card_alerts" not in names
    client.delete(f"/api/alert-channels/{ch['id']}", headers=auth)


def test_an_assistant_manages_agents(client, auth):
    """Agents, enrollment tokens, SSH hosts and the SSH config, through MCP with an admin token."""
    admin = _token(client, auth, scopes=["admin", "agents:write"])
    tools = {t["name"]: t for t in _rpc(client, admin, "tools/list")["result"]["tools"]}
    assert {"create_agent_token", "add_ssh_host", "save_ssh_config", "list_agents"} <= set(tools)
    assert tools["forget_agent"]["annotations"] == {"readOnlyHint": False, "destructiveHint": True}
    assert tools["list_agents"]["annotations"] == {"readOnlyHint": True}
    reader = _token(client, auth, scopes=["projects:read", "agents:read"])
    seen = {t["name"] for t in _rpc(client, reader, "tools/list")["result"]["tools"]}
    assert "list_agents" in seen and not seen & {"create_agent_token", "add_ssh_host", "update_agent", "list_ssh_hosts"}

    def call(tool, **args):
        out = _rpc(client, admin, "tools/call", {"name": tool, "arguments": args})["result"]
        assert not out["isError"], out
        text = out["content"][0]["text"]
        return None if text == "Done." else json.loads(text)

    label = f"mcp-agent-{uuid.uuid4().hex[:6]}"
    made = call("create_agent_token", label=label, tags=["lab"])
    assert made["token"].startswith("castor_") and made["token"] in made["command"]
    assert made["command"].startswith("castor-agent --server ws") and f"--name {label}" in made["command"] and "--tags lab" in made["command"]
    listed = call("list_agent_tokens")
    assert made["id"] in {t["id"] for t in listed["tokens"]} and made["token"] not in json.dumps(listed)
    call("revoke_agent_token", token_id=made["id"])
    call("delete_agent_token", token_id=made["id"])

    assert "IdentityFile" in call("check_ssh_config", text="Host gw\n  IdentityFile x\n")["warnings"][0]
    bad = _call(client, admin, "save_ssh_config", text="Host gw\n  ProxyCommand nc %h %p\n")
    assert bad["isError"] and "ProxyCommand" in bad["content"][0]["text"]
    call("save_ssh_config", text="Host gw\n  HostName 192.0.2.1\n  User jump\nHost box-*\n  ProxyJump gw\n  User ops\n")
    name = f"box-{uuid.uuid4().hex[:6]}"
    host = call("add_ssh_host", name=name, host=name, generate_key=True, enabled=False, tags=["ssh"])
    assert host["public_key"].startswith("ssh-ed25519 ") and host["route"] == ["jump@192.0.2.1:22", f"ops@{name}:22"]
    assert "PRIVATE" not in json.dumps(host)
    changed = call("update_ssh_host", host_id=host["id"], tags=["ssh", "inner"], username="root", port=2222)
    assert changed["tags"] == ["inner", "ssh"] and changed["route"][-1] == f"root@{name}:2222" and changed["enabled"] is False
    assert changed["public_key"] == host["public_key"]  # the key stays unless asked for a new one
    agent = next(a for a in call("list_agents") if a["id"] == host["agent_id"])
    assert agent["ssh_host_id"] == host["id"]
    call("remove_ssh_host", host_id=host["id"])
    assert host["agent_id"] not in {a["id"] for a in call("list_agents")}
    call("save_ssh_config", text="")
