"""HTTP actions (docs/ACTIONS.md phase 1): the format, rendering, the outbound guard, and jobs
the Castor server runs against a fake API on loopback (paging, batches, retries, rate limits,
waiting, cancel)."""

import ipaddress
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from castor_server.command_spec import CommandSpec, parse_spec
from castor_server.http_render import RenderError, preview_line, render_request
from castor_server.http_spec import parse_rate
from castor_server.outbound import OutboundRefused, Policy

# ---------- a fake API ----------


class Api:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.plan: dict[str, list] = {}  # path -> responses to give in turn (status, headers, body)
        self.lock = threading.Lock()

    def answer(self, method: str, path: str, query: dict, headers: dict, body: bytes):
        with self.lock:
            self.requests.append({"method": method, "path": path, "query": query, "headers": headers, "body": body})
            queue = self.plan.get(path)
            if queue:
                item = queue.pop(0) if len(queue) > 1 else queue[0]
                return item(query, body) if callable(item) else item
        return 404, {}, b'{"error": "no such thing"}'


@pytest.fixture(scope="module")
def api():
    state = Api()

    class Handler(BaseHTTPRequestHandler):
        def _go(self):
            parts = urlsplit(self.path)
            length = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(length) if length else b""
            status, headers, data = state.answer(self.command, unquote(parts.path), parse_qs(parts.query), dict(self.headers), body)
            self.send_response(status)
            headers = {"Content-Type": "application/json", **headers}
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _go

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state.base = f"http://127.0.0.1:{server.server_address[1]}"
    yield state
    server.shutdown()


def _json(status, doc, **headers):
    return status, headers, json.dumps(doc).encode()


# ---------- helpers ----------


def _kind(client, auth):
    kind_id = "test-api"
    if kind_id not in {k["id"] for k in client.get("/api/variables/kinds", headers=auth).json()}:
        r = client.post("/api/variables/kinds", json={"id": kind_id, "name": "Test API", "fields": [
            {"name": "base_url", "type": "text", "required": True}, {"name": "token", "type": "secret", "required": True},
        ]}, headers=auth)
        assert r.status_code == 201, r.text
    return kind_id


def _set(client, auth, api, token="tok-Sekrit-123456", rate=None):
    body = {"kind_id": _kind(client, auth), "name": f"api-{uuid.uuid4().hex[:6]}",
            "values": {"base_url": api.base, "token": token}}
    if rate:
        body["rate_limit"] = rate
    r = client.post("/api/variables/sets", json=body, headers=auth)
    assert r.status_code == 201, r.text
    return r.json()


def _definition(client, auth, yaml_text):
    spec = parse_spec(yaml_text)
    _kind(client, auth)
    client.delete(f"/api/commands/{spec.id}", headers=auth)
    r = client.post("/api/commands", json={"yaml": yaml_text}, headers=auth)
    assert r.status_code == 201, r.text
    return spec.id


def _project(client, auth):
    code = f"HTTP-{uuid.uuid4().hex[:6]}"
    return client.post("/api/projects", json={"codename": code, "name": code, "kind": "internal"}, headers=auth).json()


def _card(client, auth, project, ref, args, api_set, **extra):
    r = client.post(f"/api/projects/{project['id']}/tasks", json={
        "title": f"call {uuid.uuid4().hex[:4]}", "kind": "command", "command_ref": ref, "command_args": args,
        "var_bindings": {"api": api_set["id"]}, **extra,
    }, headers=auth)
    assert r.status_code == 201, r.text
    return r.json()


def _run(client, auth, card):
    r = client.post(f"/api/tasks/{card['id']}/run", headers=auth)
    assert r.status_code == 201, r.text
    return r.json()["id"]


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


CREATE = """
id: test-create-item
name: Create an item
kind: http
args:
  - {name: summary, type: string, required: true}
  - {name: labels, type: list}
  - {name: count, type: number, default: 2}
vars:
  - {name: api, kind: test-api}
request:
  method: POST
  url: "{{ api.base_url }}/items/{{ summary }}"
  query: {label: "{{ labels }}", q: "x {{ summary }}"}
  headers: {X-Trace: "t-{{ count }}"}
  auth: {bearer: "{{ api.token }}"}
  json:
    summary: "{{ summary }}"
    labels: "{{ labels }}"
    count: "{{ count }}"
    note: "about {{ summary }}"
expect: {status: [201]}
outputs:
  - {name: key, parser: json-value, options: {path: key}}
"""


# ---------- the format and rendering ----------


def test_the_format():
    spec = parse_spec(CREATE)
    assert spec.kind == "http" and spec.request.method == "POST" and spec.command == ""
    bad = [
        ("kind: http\nrequest: {url: 'https://x'}\ncommand: echo hi", "no command"),
        ("kind: http", "needs its request"),
        ("request: {url: 'https://x'}\ncommand: echo", "only for kind: http"),
        ("kind: http\nrequest: {url: 'https://x/{{ nope }}'}", "undefined arguments: nope"),
        ("kind: http\nrequest: {url: '{{ s.base }}/x'}", "undeclared vars slots: s"),
        ("kind: http\nrequest: {method: GET, url: 'https://x', json: {a: 1}}", "has no body"),
        ("kind: http\nrequest: {url: 'https://x', headers: {'Bad Header': x}}", "isn't a header name"),
        ("kind: http\nargs: [{name: a}]\nbatch: {arg: a, size: 2}\nrequest: {url: 'https://x'}", "isn't a list argument"),
        ("kind: http\nrequest: {url: 'https://x'}\npaginate: {style: cursor}", "needs cursor"),
        ("kind: http\nrequest: {url: 'https://x'}\nrate_limit: lots", "rate limit looks like"),
    ]
    for body, message in bad:
        with pytest.raises(ValueError, match=message):
            parse_spec(f"id: x\nname: x\n{body}\n")
    assert str(parse_rate("60 / minutes")) == "60/minute"
    assert str(parse_rate("5/second, 2 at once")) == "5/second, 2 at once"


def test_values_are_escaped_where_they_land():
    spec = parse_spec(CREATE)
    args = {"summary": "a/b c&d {{ api.token }}", "labels": ["x y", "z&w"], "count": 3}
    lookup = {("api", "base_url"): "https://api.example.com/", ("api", "token"): "T0K"}.get
    out = render_request(spec, args, lambda s, f: lookup((s, f)))
    # The base URL goes in as it is; the summary stays inside its path segment.
    assert out.url == "https://api.example.com/items/a%2Fb%20c%26d%20%7B%7B%20api.token%20%7D%7D"
    assert out.params == [("label", "x y"), ("label", "z&w"), ("q", "x a/b c&d {{ api.token }}")]
    body = json.loads(out.content)
    # A whole-placeholder value keeps its type; text around one stays text. The token text in an
    # argument is never filled in.
    assert body == {"summary": "a/b c&d {{ api.token }}", "labels": ["x y", "z&w"], "count": 3, "note": "about a/b c&d {{ api.token }}"}
    assert out.headers["Authorization"] == "Bearer T0K" and out.headers["X-Trace"] == "t-3"
    with pytest.raises(RenderError, match="line break"):
        render_request(spec, {**args, "count": "1\r\nX-Evil: 1"}, lambda s, f: lookup((s, f)))
    with pytest.raises(RenderError, match="http:// or https://"):
        render_request(spec, args, lambda s, f: "file:///etc/passwd" if f == "base_url" else "t")
    assert preview_line(spec, {"summary": "s", "labels": [], "count": 2}) == "POST {{ api.base_url }}/items/s?q=x+s"


def test_a_text_body_is_escaped_for_its_type():
    spec = CommandSpec.model_validate({
        "id": "x", "name": "x", "kind": "http", "args": [{"name": "v"}],
        "request": {"method": "POST", "url": "https://x", "body": '{"v": "{{ v }}"}', "content_type": "application/json"},
    })
    out = render_request(spec, {"v": 'he said "hi"\n'}, lambda s, f: None)
    assert json.loads(out.content) == {"v": 'he said "hi"\n'}


def test_the_outbound_guard():
    rules = Policy()
    for bad in ("127.0.0.1", "169.254.169.254", "::1", "::ffff:127.0.0.1", "0.0.0.0", "fe80::1", "100.100.100.200"):
        with pytest.raises(OutboundRefused):
            rules.check_ip(ipaddress.ip_address(bad))
    for fine in ("10.1.2.3", "192.168.1.5", "8.8.8.8", "100.101.102.103"):
        rules.check_ip(ipaddress.ip_address(fine))
    with pytest.raises(OutboundRefused, match="private"):
        Policy(block_private=True).check_ip(ipaddress.ip_address("10.1.2.3"))
    Policy(allow=[ipaddress.ip_network("127.0.0.0/8")]).check_ip(ipaddress.ip_address("127.0.0.1"))
    hosts = Policy(hosts=["api.example.com", "*.atlassian.net"])
    hosts.check_host("api.example.com")
    hosts.check_host("acme.atlassian.net")
    for bad in ("example.com", "atlassian.net", "evil.com"):
        with pytest.raises(OutboundRefused):
            hosts.check_host(bad)


# ---------- jobs on the server ----------


def test_a_request_runs_on_the_server(client, auth, api):
    api.plan["/items/first item"] = [lambda q, body: _json(201, {"key": "SEC-1", "got": json.loads(body)})]
    ref = _definition(client, auth, CREATE)
    project = _project(client, auth)
    creds = _set(client, auth, api)
    card = _card(client, auth, project, ref, {"summary": "first item", "labels": "a, b"}, creds)
    assert card["command"].startswith("POST {{ api.base_url }}/items/first%20item?label=a&label=b")
    preview = client.post(f"/api/commands/{ref}/render", json={"args": {"summary": "s"}}, headers=auth).json()
    assert preview["kind"] == "http" and preview["request"]["headers"]["Authorization"] == "••••"
    job_id = _run(client, auth, card)
    job = _wait(client, auth, job_id)
    assert job["status"] == "succeeded", _log(client, auth, job_id)
    assert job["kind"] == "http" and job["runner"] == "server" and job["agent_id"] is None
    sent = next(r for r in api.requests if r["path"] == "/items/first item")
    assert sent["headers"]["Authorization"] == "Bearer tok-Sekrit-123456"
    assert sent["query"] == {"label": ["a", "b"], "q": ["x first item"]}
    log = _log(client, auth, job_id)
    assert "> POST http://127.0.0.1" in log and "Authorization: ••••" in log and "< 201" in log
    assert "tok-Sekrit-123456" not in log
    assert json.loads(_log(client, auth, job_id, "stdout"))["key"] == "SEC-1"
    deadline = time.time() + 5
    while time.time() < deadline and not client.get(f"/api/jobs/{job_id}/outputs", headers=auth).json():
        time.sleep(0.05)
    outputs = {o["name"]: o for o in client.get(f"/api/jobs/{job_id}/outputs", headers=auth).json()}
    assert outputs["key"]["value"] == "SEC-1", outputs

    # The card can't be detached or have its request edited by hand.
    r = client.patch(f"/api/tasks/{card['id']}", json={"command_ref": None}, headers=auth)
    assert r.status_code == 422
    r = client.patch(f"/api/tasks/{card['id']}", json={"command": "curl evil"}, headers=auth)
    assert r.status_code == 422

    # Not the status it expects: a failure that says why.
    api.plan["/items/second"] = [_json(200, {"ok": True})]
    job = _wait(client, auth, _run(client, auth, _card(client, auth, project, ref, {"summary": "second"}, creds)))
    assert job["status"] == "failed" and "status 200, expected 201" in job["error"]
    client.delete(f"/api/projects/{project['id']}", headers=auth)


PAGED = """
id: test-list-hosts
name: List hosts
kind: http
vars: [{name: api, kind: test-api}]
request:
  url: "{{ api.base_url }}/hosts"
  auth: {bearer: "{{ api.token }}"}
paginate:
  style: cursor
  items: data
  cursor: {from: meta.next, param: cursor}
outputs:
  - {name: hosts, parser: json-records}
"""


def test_paging_joins_every_page(client, auth, api):
    def page(query, _body):
        cursor = (query.get("cursor") or ["0"])[0]
        n = int(cursor)
        nxt = str(n + 2) if n < 4 else None
        return _json(200, {"data": [{"name": f"h{n}"}, {"name": f"h{n + 1}"}], "meta": {"next": nxt}})

    api.plan["/hosts"] = [page]
    ref = _definition(client, auth, PAGED)
    project = _project(client, auth)
    job_id = _run(client, auth, _card(client, auth, project, ref, {}, _set(client, auth, api)))
    job = _wait(client, auth, job_id)
    assert job["status"] == "succeeded", _log(client, auth, job_id)
    records = json.loads(_log(client, auth, job_id, "stdout"))
    assert [r["name"] for r in records] == ["h0", "h1", "h2", "h3", "h4", "h5"]
    log = _log(client, auth, job_id)
    assert "page 3: 2 items" in log and "6 records in all" in log
    client.delete(f"/api/projects/{project['id']}", headers=auth)


BATCHED = """
id: test-lookup-batch
name: Look up in batches
kind: http
args: [{name: ips, type: list, required: true}]
vars: [{name: api, kind: test-api}]
request:
  method: POST
  url: "{{ api.base_url }}/lookup"
  json: {ips: "{{ ips }}"}
batch: {arg: ips, size: 2}
"""


def test_batches_and_retries(client, auth, api):
    calls = {"n": 0}

    def lookup(_q, body):
        calls["n"] += 1
        if calls["n"] == 2:
            return _json(503, {"error": "busy"}, **{"Retry-After": "1"})
        if calls["n"] == 3:
            return _json(500, {"error": "oops"})
        return _json(200, [{"ip": ip} for ip in json.loads(body)["ips"]])

    api.plan["/lookup"] = [lookup]
    ref = _definition(client, auth, BATCHED)
    project = _project(client, auth)
    card = _card(client, auth, project, ref, {"ips": ["1.1.1.1", "2.2.2.2", "3.3.3.3", "4.4.4.4", "5.5.5.5"]},
                 _set(client, auth, api), timeout_s=60)
    job_id = _run(client, auth, card)
    job = _wait(client, auth, job_id, timeout=20)
    assert job["status"] == "succeeded", _log(client, auth, job_id)
    assert [r["ip"] for r in json.loads(_log(client, auth, job_id, "stdout"))] == \
        ["1.1.1.1", "2.2.2.2", "3.3.3.3", "4.4.4.4", "5.5.5.5"]
    log = _log(client, auth, job_id)
    assert "Batch 3 of 3: 1 values" in log and "answered 503" in log and "trying again (1 of 3)" in log
    client.delete(f"/api/projects/{project['id']}", headers=auth)


SIMPLE = """
id: test-ping
name: Ping
kind: http
vars: [{name: api, kind: test-api}]
request: {url: "{{ api.base_url }}/ping"}
"""


def test_rate_limits_wait_and_long_waits_park(client, auth, api):
    api.plan["/ping"] = [_json(200, {"pong": True})]
    ref = _definition(client, auth, SIMPLE)
    project = _project(client, auth)
    creds = _set(client, auth, api, rate="1/second")
    # Two cards with the same credential share its limit.
    cards = [_card(client, auth, project, ref, {}, creds) for _ in range(3)]
    started = time.time()
    jobs = [_run(client, auth, card) for card in cards]
    for job_id in jobs:
        assert _wait(client, auth, job_id)["status"] == "succeeded"
    assert time.time() - started >= 1.6  # the second and third each waited a second
    assert any(f"the {creds['name']} rate limit, 1/second" in _log(client, auth, j) for j in jobs)

    # An API that says to come back in a while: the job waits without a slot, and can be cancelled.
    api.plan["/ping"] = [_json(429, {"error": "slow down"}, **{"Retry-After": "120"})]
    job_id = _run(client, auth, _card(client, auth, project, ref, {}, _set(client, auth, api)))
    job = _wait(client, auth, job_id, statuses=("waiting",))
    assert job["resume_at"] and "Waiting 2 min" in _log(client, auth, job_id)
    assert client.post(f"/api/jobs/{job_id}/cancel", headers=auth).json()["status"] == "cancelled"
    api.plan["/ping"] = [_json(200, {"pong": True})]
    client.delete(f"/api/projects/{project['id']}", headers=auth)


def test_card_rate_limits_and_a_refused_address(client, auth, api):
    project = _project(client, auth)
    ref = _definition(client, auth, SIMPLE)
    creds = _set(client, auth, api)
    card = _card(client, auth, project, ref, {}, creds, rate_limit="30 / minute")
    assert card["rate_limit"] == "30/minute"
    r = client.patch(f"/api/tasks/{card['id']}", json={"rate_limit": "often"}, headers=auth)
    assert r.status_code == 422
    # Metadata addresses are refused whatever the URL says.
    meta = _set(client, auth, api)
    client.patch(f"/api/variables/sets/{meta['id']}", json={"values": {"base_url": "http://169.254.169.254"}}, headers=auth)
    job = _wait(client, auth, _run(client, auth, _card(client, auth, project, ref, {}, meta)))
    assert job["status"] == "failed" and "won't send this request" in job["error"]
    client.delete(f"/api/projects/{project['id']}", headers=auth)


def test_a_parked_job_carries_on_from_its_page(client, auth, api):
    import asyncio

    from castor_server import models as m
    from castor_server.db import SessionLocal

    seen = []

    def page(query, _body):
        cursor = (query.get("cursor") or ["0"])[0]
        seen.append(cursor)
        if cursor == "2" and seen.count("2") == 1:
            return _json(429, {"error": "later"}, **{"Retry-After": "90"})
        return _json(200, {"data": [{"name": f"h{cursor}"}], "meta": {"next": "2" if cursor == "0" else None}})

    api.plan["/hosts"] = [page]
    ref = _definition(client, auth, PAGED)
    project = _project(client, auth)
    job_id = _run(client, auth, _card(client, auth, project, ref, {}, _set(client, auth, api)))
    job = _wait(client, auth, job_id, statuses=("waiting",))
    assert job["progress"]["pages"] == 1

    async def due_now():
        async with SessionLocal() as db:
            row = await db.get(m.Job, uuid.UUID(job_id))
            row.resume_at = row.queued_at
            await db.commit()
        from castor_server.orchestrator.dispatcher import dispatcher

        dispatcher.wake()

    asyncio.run(due_now())
    job = _wait(client, auth, job_id)
    assert job["status"] == "succeeded", _log(client, auth, job_id)
    assert seen == ["0", "2", "2"]  # page 1 wasn't asked for again
    assert [r["name"] for r in json.loads(_log(client, auth, job_id, "stdout"))] == ["h0", "h2"]
    assert "Carrying on from page 2" in _log(client, auth, job_id)
    client.delete(f"/api/projects/{project['id']}", headers=auth)
