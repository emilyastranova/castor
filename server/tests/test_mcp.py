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


def test_tools_cover_the_api_and_their_inputs_come_from_it(client, auth):
    """Every tool is a real endpoint; generated inputs are plain JSON schema (no $refs)."""
    openapi = client.get("/api/openapi.json").json()
    for tool in mcp.TOOLS:
        assert tool.method.lower() in openapi["paths"].get(tool.path, {}), tool.name
    listed = _rpc(client, auth, "tools/list")["result"]["tools"]
    assert "$ref" not in json.dumps(listed)
    by_name = {t["name"]: t for t in listed}
    card = by_name["update_card"]["inputSchema"]
    assert {"title", "command", "tags", "agent_selector", "assignees"} <= set(card["properties"]) and card["required"] == ["task_id"]
    assert "all" in card["properties"]["agent_selector"]["description"]
    assert by_name["set_project_members"]["inputSchema"]["properties"]["user_ids"]["type"] == "array"


def test_an_assistant_does_what_a_person_does_with_cards(client, auth):
    writer = _token(client, auth, scopes=["projects:write", "jobs:write", "variables:write"])

    def call(tool, **args):
        out = _rpc(client, writer, "tools/call", {"name": tool, "arguments": args})["result"]
        assert not out["isError"], out
        text = out["content"][0]["text"]
        return None if text == "Done." else json.loads(text)

    p = call("create_project", codename=f"MX-{uuid.uuid4().hex[:6]}", name="Parity", kind="internal")
    col = call("add_column", project_id=p["id"], name="Waiting")
    card = call("create_card", project_id=p["id"], title="Check", kind="command", command="id",
                agent_selector={"tags": ["lab"], "all": True})
    assert card["agent_selector"] == {"tags": ["lab"], "all": True}
    changed = call("update_card", task_id=card["id"], title="Check twice", tags=["smb"], agent_selector={"tags": ["lab"]})
    assert changed["title"] == "Check twice" and changed["tags"] == ["smb"] and changed["agent_selector"] == {"tags": ["lab"]}
    assert call("move_card", task_id=card["id"], column_id=col["id"])["column_id"] == col["id"]
    copy = call("duplicate_card", task_id=card["id"])
    assert copy["title"].startswith("Check twice") and copy["id"] != card["id"]
    comment = call("comment", task_id=card["id"], body="first")
    assert call("edit_comment", task_id=card["id"], comment_id=comment["id"], body="second")["body"] == "second"
    call("delete_comment", task_id=card["id"], comment_id=comment["id"])
    note = call("create_note", project_id=p["id"], title="Scope", body="10.0.0.0/24 only")
    assert call("update_note", note_id=note["id"], body="10.0.1.0/24 too")["body"] == "10.0.1.0/24 too"
    me = next(u for u in call("list_people", project_id=p["id"]) if u["username"] == "admin")
    assert [m["id"] for m in call("set_project_members", project_id=p["id"], user_ids=[me["id"]])] == [me["id"]]
    assert any(a["type"] == "task.created" for a in call("card_history", task_id=card["id"]))

    s = call("create_variable_set", kind_id="ssh-key", name=f"key-{uuid.uuid4().hex[:6]}", values={"username": "root"})
    import base64
    up = call("upload_variable_file", set_id=s["id"], field="private_key", filename="id_ed25519",
              content_base64=base64.b64encode(b"KEY").decode())
    assert "private_key" in up["files"] and "KEY" not in json.dumps(up)
    call("delete_variable_set", set_id=s["id"])

    call("delete_card", task_id=copy["id"])
    assert _rpc(client, writer, "tools/call", {"name": "get_card", "arguments": {"task_id": copy["id"]}})["result"]["isError"]
    # A token without the scope doesn't even see the tools that change things.
    reader = _token(client, auth, scopes=["projects:read"])
    names = {t["name"] for t in _rpc(client, reader, "tools/list")["result"]["tools"]}
    assert "get_project" in names and not names & {"update_card", "delete_card", "create_project", "delete_variable_set"}
    call("delete_project", project_id=p["id"])


def test_mcp_work_shows_in_the_feed(client, auth):
    """Library changes, card edits and comment edits through MCP are in the feed, marked."""
    p = client.post("/api/projects", json={"codename": f"MF-{uuid.uuid4().hex[:6]}", "name": "Feed", "kind": "internal"}, headers=auth).json()
    tok = _token(client, auth, scopes=["projects:write", "library:write"], project_ids=[p["id"]])
    cid = f"feed-{uuid.uuid4().hex[:6]}"
    made = _call(client, tok, "create_command", yaml=f"id: {cid}\nname: Feed probe\ncommand: echo hi\n")
    assert not made["isError"], made
    card = json.loads(_call(client, tok, "create_card", project_id=p["id"], title="Watched")["content"][0]["text"])
    assert not _call(client, tok, "update_card", task_id=card["id"], title="Watched closely", priority=2)["isError"]
    comment = json.loads(_call(client, tok, "comment", task_id=card["id"], body="first")["content"][0]["text"])
    assert comment["via"] == "mcp"
    assert not _call(client, tok, "edit_comment", task_id=card["id"], comment_id=comment["id"], body="second")["isError"]
    by_hand = client.post(f"/api/tasks/{card['id']}/comments", json={"body": "by hand"}, headers=auth).json()
    assert by_hand["via"] is None
    client.delete(f"/api/tasks/{card['id']}/comments/{by_hand['id']}", headers=auth)
    listed = {c["body"]: c["via"] for c in client.get(f"/api/tasks/{card['id']}/comments", headers=auth).json()}
    assert listed == {"second": "mcp"}

    items = client.get(f"/api/dashboard/feed?project_id={p['id']}", headers=auth).json()["items"]
    seen = {(i["type"], i["via"]) for i in items}
    assert {("library.command.created", "mcp"), ("task.updated", "mcp"), ("comment.edited", "mcp"), ("comment.deleted", None)} <= seen
    lib = next(i for i in items if i["type"] == "library.command.created" and cid in i["message"])
    assert lib["kind"] == "library" and lib["link"] == f"/library?c={cid}"
    edit = next(i for i in items if i["type"] == "task.updated")
    assert "title" in edit["message"] and "priority" in edit["message"]
    client.delete(f"/api/commands/{cid}", headers=auth)
