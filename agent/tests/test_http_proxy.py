"""The agent's half of HTTP actions sent through it: one request, no redirects followed."""

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from castor_agent import http_proxy


@pytest.fixture(scope="module")
def site():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/moved":
                self.send_response(302)
                self.send_header("Location", "/elsewhere")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status = 404 if self.path == "/missing" else 200
            body = json.dumps({"path": self.path, "agent": self.headers.get("User-Agent")}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_POST = do_GET

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_answers_come_back_as_they_are(site):
    head, body = http_proxy.fetch({"method": "GET", "url": f"{site}/hello?x=1"})
    assert head["status"] == 200 and json.loads(body) == {"path": "/hello?x=1", "agent": "castor"}
    assert ["Content-Type", "application/json"] in head["headers"]
    head, _ = http_proxy.fetch({"method": "GET", "url": f"{site}/missing"})
    assert head["status"] == 404  # an answer, not an error
    head, body = http_proxy.fetch({"method": "GET", "url": f"{site}/moved"})
    assert head["status"] == 302 and dict(head["headers"])["Location"] == "/elsewhere"  # the server decides
    head, body = http_proxy.fetch({"method": "POST", "url": f"{site}/post", "body": base64.b64encode(b"{}").decode()})
    assert head["status"] == 200


def test_errors_and_limits(site):
    head, body = http_proxy.fetch({"method": "GET", "url": f"{site}/big", "max_bytes": 5})
    assert head["error_kind"] == "too_big" and body == b""
    head, _ = http_proxy.fetch({"method": "GET", "url": "http://127.0.0.1:9/", "timeout_s": 2})
    assert head["error_kind"] == "connect"
    assert "".join(http_proxy.chunks(b"x" * (http_proxy.CHUNK + 1))) != "" and len(http_proxy.chunks(b"x" * (http_proxy.CHUNK + 1))) == 2
