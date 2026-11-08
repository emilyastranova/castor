"""castor-mcp forwards stdio JSON-RPC lines to /api/mcp and turns failures into errors."""

import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from castor_agent import mcp_stdio


class _Castor(BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Castor.seen.append((self.path, self.headers["Authorization"], body))
        if "id" not in body:
            self.send_response(202)
            self.end_headers()
            return
        reply = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": {"ok": body["method"]}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def log_message(self, *args):
        pass


def test_forwards_lines(monkeypatch, capsys):
    server = HTTPServer(("127.0.0.1", 0), _Castor)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("CASTOR_TOKEN", "castor_pat_test")
    monkeypatch.setattr("sys.stdin", io.StringIO(
        '{"jsonrpc":"2.0","id":1,"method":"initialize"}\n\n{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
        '{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n'))
    try:
        assert mcp_stdio.main(["--server", f"http://127.0.0.1:{server.server_port}/"]) == 0
    finally:
        server.shutdown()
    replies = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [r["result"]["ok"] for r in replies] == ["initialize", "tools/list"]
    assert {(path, auth) for path, auth, _ in _Castor.seen} == {("/api/mcp", "Bearer castor_pat_test")}


def test_unreachable_server_answers_with_an_error():
    reply = json.loads(mcp_stdio.forward("http://127.0.0.1:9/api/mcp", "t", '{"jsonrpc":"2.0","id":7,"method":"ping"}', timeout=2))
    assert reply["id"] == 7 and "Can't reach Castor" in reply["error"]["message"]
    assert mcp_stdio.forward("http://127.0.0.1:9/api/mcp", "t", '{"jsonrpc":"2.0","method":"x"}', timeout=2) is None
