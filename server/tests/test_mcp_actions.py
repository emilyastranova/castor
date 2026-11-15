"""MCP actions (docs/ACTIONS.md phase 3): connections over Streamable HTTP (a fake server on
loopback) and stdio programs run by an agent (a fake agent answering for one), checking them,
drafting an action from a tool, and runs with their notifications, results and errors."""

import base64
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import yaml

TOKEN = "mcp-Sekrit-token-9876"
PIXEL = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()
TOOLS = [
    {"name": "host_lookup", "title": "Host lookup", "description": "Look a host up.\nMore text.",
     "inputSchema": {"type": "object", "required": ["ip"], "properties": {
         "ip": {"type": "string", "description": "An address"},
         "ports": {"type": "array", "items": {"type": "integer"}},
         "deep": {"type": "boolean", "default": False},
         "mode": {"type": "string", "enum": ["fast", "full"]},
         "filters": {"type": "object"},
     }},
     "outputSchema": {"type": "object"}},
    {"name": "boom", "description": "Always fails", "inputSchema": {"type": "object", "properties": {}}},
]


def _call_result(name, args):
    if name == "boom":
        return {"content": [{"type": "text", "text": "the upstream API said no"}], "isError": True}
    return {
        "content": [{"type": "text", "text": "ok"}, {"type": "image", "data": PIXEL, "mimeType": "image/png"}],
        "structuredContent": {"ip": args.get("ip"), "open": args.get("ports") or [], "echo": f"token was {TOKEN}"},
    }


@pytest.fixture(scope="module")
def mcp_server():
    state = {"calls": [], "sessions": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                return self._json(401, {"error": "no"})
            msg = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            method = msg.get("method")
            if "id" not in msg:  # a notification
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if method == "initialize":
                state["sessions"] += 1
                return self._json(200, {"jsonrpc": "2.0", "id": msg["id"], "result": {
                    "protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fake-intel", "version": "0.9"}}}, {"Mcp-Session-Id": f"s{state['sessions']}"})
            assert self.headers.get("Mcp-Session-Id", "").startswith("s")
            if method == "tools/list":
                return self._json(200, {"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": TOOLS}})
            if method == "tools/call":
                params = msg["params"]
                state["calls"].append(params)
                # As an event stream: a log message first, then the result.
                events = [
                    {"jsonrpc": "2.0", "method": "notifications/message", "params": {"level": "info", "data": f"looking up {params['arguments'].get('ip')}"}},
                    {"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progressToken": 1, "progress": 1, "total": 2}},
                    {"jsonrpc": "2.0", "id": msg["id"], "result": _call_result(params["name"], params["arguments"])},
                ]
                body = "".join(f"event: message\ndata: {json.dumps(e)}\n\n" for e in events).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return None
            return self._json(200, {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "no such method"}})

        def do_DELETE(self):
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _json(self, status, data, headers=None):
            raw = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state["url"] = f"http://127.0.0.1:{server.server_address[1]}/mcp"
    yield state
    server.shutdown()


def _token_set(client, auth):
    r = client.post("/api/variables/sets", json={"kind_id": "api-token", "name": f"mcp-{uuid.uuid4().hex[:6]}",
                                                 "values": {"token": TOKEN}}, headers=auth)
    assert r.status_code == 201, r.text
    return r.json()


def _project(client, auth):
    code = f"MCP-{uuid.uuid4().hex[:6]}"
    return client.post("/api/projects", json={"codename": code, "name": code, "kind": "internal"}, headers=auth).json()


def _wait(client, auth, job_id, statuses=("succeeded", "failed", "cancelled"), timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}", headers=auth).json()
        if job["status"] in statuses:
            return job
        time.sleep(0.05)
    raise AssertionError(job)


def _log(client, auth, job_id, stream="both"):
    return client.get(f"/api/jobs/{job_id}/output", params={"stream": stream}, headers=auth).text


def test_an_http_mcp_server(client, auth, mcp_server):
    vset = _token_set(client, auth)
    cid = f"intel-{uuid.uuid4().hex[:5]}"
    body = {"id": cid, "name": "Fake intel", "transport": "http", "url": mcp_server["url"],
            "headers": {"Authorization": "Bearer {{ nope }}"}, "variable_set_id": vset["id"]}
    r = client.post("/api/mcp-connections", json=body, headers=auth)
    assert r.status_code == 422 and "nope" in r.text  # not a field of the set's kind
    body["headers"] = {"Authorization": "Bearer {{ token }}"}
    assert client.post("/api/mcp-connections", json=body, headers=auth).status_code == 201

    conn = client.post(f"/api/mcp-connections/{cid}/check", headers=auth).json()
    assert conn["last_error"] is None, conn
    assert conn["server_info"]["name"] == "fake-intel" and [t["name"] for t in conn["tools"]] == ["host_lookup", "boom"]
    assert TOKEN not in json.dumps(conn)

    # A draft action from the tool: its schema as typed arguments.
    draft = client.get(f"/api/mcp-connections/{cid}/tools/host_lookup/draft", headers=auth).json()["yaml"]
    spec = yaml.safe_load(draft)
    args = {a["name"]: a for a in spec["args"]}
    assert spec["kind"] == "mcp" and spec["mcp"] == {"server": cid, "tool": "host_lookup"}
    assert args["ip"]["required"] and args["ports"]["type"] == "list" and args["deep"]["type"] == "boolean"
    assert args["mode"]["choices"] == ["fast", "full"] and "filters" not in args and "filters" in draft
    spec["args"] = [a for a in spec["args"] if a["name"] in ("ip", "ports")]
    spec["mcp"]["arguments"] = {"ip": "{{ ip }}", "ports": "{{ ports }}"}
    spec["outputs"] = [{"name": "result", "parser": "json-value", "options": {"path": "ip"}}]
    client.delete(f"/api/commands/{spec['id']}", headers=auth)
    r = client.post("/api/commands", json={"yaml": yaml.safe_dump(spec)}, headers=auth)
    assert r.status_code == 201, r.text
    assert client.get(f"/api/mcp-connections/{cid}", headers=auth).json()["used_by"] == [spec["id"]]

    project = _project(client, auth)
    card = client.post(f"/api/projects/{project['id']}/tasks", json={
        "title": "Look it up", "kind": "command", "command_ref": spec["id"], "command_args": {"ip": "203.0.113.5", "ports": "22, 80"},
    }, headers=auth).json()
    assert card["command"] == f'{cid} · host_lookup(ip="203.0.113.5", ports=["22", "80"])'
    job_id = client.post(f"/api/tasks/{card['id']}/run", headers=auth).json()["id"]
    job = _wait(client, auth, job_id)
    log = _log(client, auth, job_id)
    assert job["status"] == "succeeded", log
    assert mcp_server["calls"][-1] == {"name": "host_lookup", "arguments": {"ip": "203.0.113.5", "ports": ["22", "80"]}}
    assert "fake-intel 0.9, from the server): host_lookup" in log and "[info] looking up 203.0.113.5" in log
    assert "progress 1 of 2" in log and "Kept the image it returned as host_lookup-2.png" in log
    assert TOKEN not in log  # the server echoed the token; the connection's secrets are masked too
    assert json.loads(_log(client, auth, job_id, "stdout"))["ip"] == "203.0.113.5"
    files = client.get(f"/api/jobs/{job_id}/artifacts", headers=auth).json()
    assert [f["filename"] for f in files] == ["host_lookup-2.png"]

    # A tool that reports an error fails the job with what it said.
    spec2 = {**spec, "id": f"{spec['id']}-boom", "args": [], "mcp": {"server": cid, "tool": "boom"}, "outputs": []}
    client.delete(f"/api/commands/{spec2['id']}", headers=auth)
    assert client.post("/api/commands", json={"yaml": yaml.safe_dump(spec2)}, headers=auth).status_code == 201
    card2 = client.post(f"/api/projects/{project['id']}/tasks", json={"title": "Boom", "kind": "command", "command_ref": spec2["id"]}, headers=auth).json()
    job = _wait(client, auth, client.post(f"/api/tasks/{card2['id']}/run", headers=auth).json()["id"])
    assert job["status"] == "failed" and "the upstream API said no" in job["error"]

    # Limited to other projects: refused.
    client.put(f"/api/mcp-connections/{cid}", json={**body, "project_ids": [str(uuid.uuid4())]}, headers=auth)
    job = _wait(client, auth, client.post(f"/api/tasks/{card['id']}/run", headers=auth).json()["id"])
    assert job["status"] == "failed" and "can't be used in this project" in job["error"]

    for d in (spec["id"], spec2["id"]):
        client.delete(f"/api/commands/{d}", headers=auth)
    client.delete(f"/api/mcp-connections/{cid}", headers=auth)
    client.delete(f"/api/projects/{project['id']}", headers=auth)


def test_a_stdio_server_on_an_agent(client, auth):
    tag = f"mcp-{uuid.uuid4().hex[:5]}"
    ws = client.websocket_connect("/ws/agent")
    agent = ws.__enter__()
    agent.send_text(json.dumps({
        "type": "hello", "secret": "test-agent-secret", "name": tag, "hostname": "h", "os": "Linux", "username": "root",
        "version": "1.2.1", "capabilities": {"runtimes": ["host"], "mcp_stdio": True}, "tags": [tag], "max_concurrency": 2,
    }))
    assert json.loads(agent.receive_text())["type"] == "hello.ok"
    vset = _token_set(client, auth)
    cid = f"local-{uuid.uuid4().hex[:5]}"
    assert client.post("/api/mcp-connections", json={
        "id": cid, "name": "Local tool", "transport": "stdio", "command": ["fake-mcp", "--stdio"],
        "env": {"API_TOKEN": "{{ token }}"}, "variable_set_id": vset["id"], "agent_selector": {"tags": [tag]},
    }, headers=auth).status_code == 201
    yaml_text = f"id: {cid}-echo\nname: Echo\nkind: mcp\nargs: [{{name: text, type: string}}]\nmcp: {{server: {cid}, tool: echo}}\n"
    assert client.post("/api/commands", json={"yaml": yaml_text}, headers=auth).status_code == 201
    project = _project(client, auth)
    card = client.post(f"/api/projects/{project['id']}/tasks", json={"title": "Echo", "kind": "command", "command_ref": f"{cid}-echo",
                                                                      "command_args": {"text": "hi"}}, headers=auth).json()
    try:
        job_id = client.post(f"/api/tasks/{card['id']}/run", headers=auth).json()["id"]
        start = json.loads(agent.receive_text())
        while start["type"] != "mcp.start":
            start = json.loads(agent.receive_text())
        assert start["command"] == ["fake-mcp", "--stdio"] and start["env"] == {"API_TOKEN": TOKEN}
        sid = start["session_id"]
        agent.send_text(json.dumps({"type": "mcp.started", "session_id": sid}))
        seen = []
        while True:
            msg = json.loads(agent.receive_text())
            if msg["type"] != "mcp.send":
                continue
            rpc = msg["message"]
            seen.append(rpc.get("method"))
            if rpc.get("method") == "initialize":
                reply = {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "local", "version": "1"}}
            elif rpc.get("method") == "tools/call":
                agent.send_text(json.dumps({"type": "mcp.message", "session_id": sid, "message": {
                    "jsonrpc": "2.0", "method": "notifications/message", "params": {"level": "debug", "data": "echoing"}}}))
                reply = {"content": [{"type": "text", "text": f"you said {rpc['params']['arguments']['text']}"}]}
            else:
                continue
            agent.send_text(json.dumps({"type": "mcp.message", "session_id": sid, "message": {"jsonrpc": "2.0", "id": rpc["id"], "result": reply}}))
            if rpc.get("method") == "tools/call":
                break
        job = _wait(client, auth, job_id)
        assert job["status"] == "succeeded", _log(client, auth, job_id)
        assert seen == ["initialize", "notifications/initialized", "tools/call"]
        assert _log(client, auth, job_id, "stdout").strip() == "you said hi"
        assert f"from {tag})" in _log(client, auth, job_id) and "[debug] echoing" in _log(client, auth, job_id)

        # The program stays warm: the next call goes straight to tools/call.
        job_id = client.post(f"/api/tasks/{card['id']}/run", headers=auth).json()["id"]
        while True:
            msg = json.loads(agent.receive_text())
            if msg["type"] == "mcp.send" and msg["message"].get("method") == "tools/call":
                break
            assert msg["type"] != "mcp.start"
        # It stops before answering: the call is tried again, and the agent starts it anew.
        agent.send_text(json.dumps({"type": "mcp.exited", "session_id": sid, "error": "exit 1: boom"}))
        restart = json.loads(agent.receive_text())
        while restart["type"] != "mcp.start":
            restart = json.loads(agent.receive_text())
        assert restart["session_id"] != sid
        agent.send_text(json.dumps({"type": "mcp.started", "session_id": restart["session_id"]}))
        while True:
            msg = json.loads(agent.receive_text())
            rpc = msg.get("message") or {}
            if msg["type"] != "mcp.send" or "id" not in rpc:
                continue
            reply = ({"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "local", "version": "1"}}
                     if rpc["method"] == "initialize" else {"content": [{"type": "text", "text": "again"}]})
            agent.send_text(json.dumps({"type": "mcp.message", "session_id": restart["session_id"],
                                        "message": {"jsonrpc": "2.0", "id": rpc["id"], "result": reply}}))
            if rpc["method"] == "tools/call":
                break
        job = _wait(client, auth, job_id, timeout=30)
        assert job["status"] == "succeeded" and "stopped: exit 1: boom; trying again" in _log(client, auth, job_id)
    finally:
        ws.__exit__(None, None, None)
        client.delete(f"/api/commands/{cid}-echo", headers=auth)
        client.delete(f"/api/mcp-connections/{cid}", headers=auth)
        client.delete(f"/api/projects/{project['id']}", headers=auth)
