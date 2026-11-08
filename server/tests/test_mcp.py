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
