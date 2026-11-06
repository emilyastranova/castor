"""Chat and webhook alerts (3k): presets and templates, secrets kept server-side, the Test
button, alerts for a card's runs and playbook runs, everything-channels and long runs."""

import asyncio
import json
import threading
import uuid
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from castor_server import alerts as A


@pytest.fixture
def hook():
    """A local endpoint that records what it receives (path, headers, body)."""
    got: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            got.append({"path": self.path, "headers": dict(self.headers), "body": body.decode(), "method": "POST"})
            status = 500 if self.path.startswith("/fail") else 200
            self.send_response(status)
            self.end_headers()
            self.wfile.write(b"nope" if status == 500 else b"ok")

        do_PUT = do_POST  # noqa: N815

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", got
    server.shutdown()


def test_templates_escape_for_their_content_type():
    values = {"title": 'He said "hi"\nnew line', "link": "https://x/?a=1&b=2"}
    body = A.render('{"text": "{{ title }}", "url": "{{ link }}", "x": "{{ unknown }}"}', values, "json")
    assert json.loads(body) == {"text": 'He said "hi"\nnew line', "url": "https://x/?a=1&b=2", "x": ""}
    assert A.render("t={{ title }}", values, "form") == "t=He%20said%20%22hi%22%0Anew%20line"
    assert A.render("{{title}}", values, "text") == values["title"]


def _channel(client, auth, url, kind="webhook", **extra):
    fields = {"url": url} if kind != "webhook" else {"url": url, "method": "POST", "content_type": "json"}
    body = {"name": f"ch-{uuid.uuid4().hex[:6]}", "kind": kind, "fields": {**fields, **extra.pop("fields", {})}, **extra}
    r = client.post("/api/alert-channels", json=body, headers=auth)
    assert r.status_code == 201, r.text
    return r.json()


def test_channels_keep_secrets_and_test_sends(client, auth, hook):
    base, got = hook
    ch = _channel(client, auth, f"{base}/slack/T000/SECRET", kind="slack")
    assert ch["secrets"] == {"url": True} and "SECRET" not in json.dumps(ch)
    assert "SECRET" not in json.dumps(client.get("/api/alert-channels", headers=auth).json())

    r = client.post(f"/api/alert-channels/{ch['id']}/test", headers=auth).json()
    assert r == {"ok": True, "status": 200, "error": ""}
    sent = got[-1]
    assert sent["path"] == "/slack/T000/SECRET" and "Test from Castor" in json.loads(sent["body"])["text"]

    # Saving with a blank secret keeps the stored one.
    upd = client.put(f"/api/alert-channels/{ch['id']}", json={"name": "Renamed", "kind": "slack", "fields": {"url": ""}}, headers=auth)
    assert upd.status_code == 200 and upd.json()["secrets"] == {"url": True}
    client.post(f"/api/alert-channels/{ch['id']}/test", headers=auth)
    assert got[-1]["path"] == "/slack/T000/SECRET"

    # A failing endpoint is reported with its answer, and kept in the history.
    bad = _channel(client, auth, f"{base}/fail", kind="discord")
    r = client.post(f"/api/alert-channels/{bad['id']}/test", headers=auth).json()
    assert not r["ok"] and r["status"] == 500 and "nope" in r["error"]
    history = client.get(f"/api/alert-channels/{bad['id']}/deliveries", headers=auth).json()
    assert history[0]["ok"] is False


def test_custom_webhook_and_presets(client, auth, hook):
    base, got = hook
    body = '{"who": "{{ project_code }}", "what": "{{ title }}", "n": 1}'
    r = client.post("/api/alert-channels/test", json={"name": "x", "kind": "webhook", "fields": {
        "url": f"{base}/custom/{{{{ event }}}}", "method": "PUT", "content_type": "json",
        "headers": "Authorization: Bearer abc\nX-Source: castor", "body": body}}, headers=auth).json()
    assert r["ok"], r
    sent = got[-1]
    assert sent["path"] == "/custom/run.failed" and sent["headers"]["Authorization"] == "Bearer abc"
    assert json.loads(sent["body"])["what"].startswith("Test from Castor")

    broken = client.post("/api/alert-channels/test", json={"name": "x", "kind": "webhook", "fields": {
        "url": f"{base}/x", "content_type": "json", "body": '{"a": {{ title }}}'}}, headers=auth).json()
    assert not broken["ok"] and "valid JSON" in broken["error"]

    ntfy = client.post("/api/alert-channels/test", json={"name": "x", "kind": "ntfy", "fields": {"server": base, "topic": "ops", "token": "tk"}}, headers=auth).json()
    assert ntfy["ok"]
    msg = json.loads(got[-1]["body"])
    assert msg["topic"] == "ops" and msg["priority"] == 4 and got[-1]["headers"]["Authorization"] == "Bearer tk"

    presets = client.get("/api/alert-presets", headers=auth).json()
    kinds = {p["kind"] for p in presets["presets"]}
    assert {"slack", "google_chat", "discord", "teams", "ntfy", "telegram", "gotify", "pushover", "webhook"} <= kinds
    assert "title" in presets["variables"] and "run.failed" in presets["events"]
    assert client.post("/api/alert-channels", json={"name": "x", "kind": "carrier-pigeon"}, headers=auth).status_code == 422


def _finish(task_id, status):
    async def go():
        from castor_server import models as m
        from castor_server.db import SessionLocal

        async with SessionLocal() as db:
            task = await db.get(m.Task, uuid.UUID(task_id))
            now = datetime.now(UTC)
            job = m.Job(task_id=task.id, command="x", status=status, exit_code=0 if status == m.JobStatus.succeeded else 2,
                        started_at=now - timedelta(minutes=3), finished_at=now)
            db.add(job)
            await db.commit()
            await A.job_finished(db, task, job, "agent-x")
            await db.commit()  # as the gateway does: the hook adds in-app notifications
        await A.drain()

    asyncio.run(go())


def test_card_alerts_follow_their_rules(client, auth, hook):
    from castor_server import models as m

    base, got = hook
    ch = _channel(client, auth, f"{base}/card", fields={"body": '{"t": "{{ title }}", "d": "{{ duration }}", "a": "{{ agent }}"}'})
    p = client.post("/api/projects", json={"codename": f"AL-{uuid.uuid4().hex[:6]}", "name": "Alerts", "kind": "internal"}, headers=auth).json()
    task = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Long scan"}, headers=auth).json()
    r = client.patch(f"/api/tasks/{task['id']}", json={"alerts": {"channels": [ch["id"]], "on": "failure"}}, headers=auth)
    assert r.json()["alerts"]["on"] == "failure"

    before = len(got)
    _finish(task["id"], m.JobStatus.succeeded)
    assert len([g for g in got[before:] if g["path"] == "/card"]) == 0  # failures only
    _finish(task["id"], m.JobStatus.failed)
    sent = [json.loads(g["body"]) for g in got[before:] if g["path"] == "/card"]
    assert sent == [{"t": "“Long scan” failed", "d": "3m 0s", "a": "agent-x"}]

    client.patch(f"/api/tasks/{task['id']}", json={"alerts": {"channels": []}}, headers=auth)
    assert client.get(f"/api/tasks/{task['id']}", headers=auth).json()["alerts"] is None


def test_everything_channels_and_long_runs(client, auth, hook):
    from castor_server import models as m
    from castor_server.db import SessionLocal

    base, got = hook
    ch = _channel(client, auth, f"{base}/all", events=["job.succeeded", "long"], long_minutes=30)
    assert client.post("/api/alert-channels", json={"name": "x", "kind": "slack", "fields": {"url": base}, "events": ["long"]}, headers=auth).status_code == 422
    p = client.post("/api/projects", json={"codename": f"AL-{uuid.uuid4().hex[:6]}", "name": "Alerts", "kind": "internal"}, headers=auth).json()
    task = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Any card"}, headers=auth).json()
    before = len(got)
    _finish(task["id"], m.JobStatus.succeeded)
    assert any(g["path"] == "/all" and "Any card" in g["body"] for g in got[before:])

    async def long_running():
        async with SessionLocal() as db:
            job = m.Job(task_id=uuid.UUID(task["id"]), command="sleep", status=m.JobStatus.running,
                        started_at=datetime.now(UTC) - timedelta(minutes=45))
            db.add(job)
            await db.commit()
            first = await A.check_long_running(db)
            await A.drain()
            again = await A.check_long_running(db)
            job.status = m.JobStatus.cancelled
            await db.commit()
            return first, again

    first, again = asyncio.run(long_running())
    assert first >= 1 and again == 0  # once per channel
    assert any(g["path"] == "/all" and "still running" in g["body"] for g in got)
    history = client.get(f"/api/alert-channels/{ch['id']}/deliveries", headers=auth).json()
    assert any(h["event"] == "long" and h["ok"] for h in history)
    client.delete(f"/api/alert-channels/{ch['id']}", headers=auth)  # don't alert other tests' runs


def test_only_admins_manage_channels(client, auth):
    name = f"al{uuid.uuid4().hex[:6]}"
    client.post("/api/users", json={"username": name, "role": "operator", "password": "a password"}, headers=auth)
    token = client.post("/api/auth/login", json={"username": name, "password": "a password"}).json()["token"]
    client.cookies.clear()
    other = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/alert-channels", headers=other).status_code == 403
    assert client.post("/api/alert-channels", json={"name": "x", "kind": "slack", "fields": {"url": "https://x"}}, headers=other).status_code == 403
    assert client.get("/api/alert-channels/brief", headers=other).status_code == 200


def test_notify_me_when_a_card_finishes(client, auth):
    """People on a card hear when it finishes (a run ending, or a manual card moved to Done),
    except whoever finished it."""
    from castor_server import models as m

    me = client.get("/api/auth/me", headers=auth).json()
    name = f"nm{uuid.uuid4().hex[:6]}"
    other = client.post("/api/users", json={"username": name, "role": "operator", "password": "a password"}, headers=auth).json()
    token = client.post("/api/auth/login", json={"username": name, "password": "a password"}).json()["token"]
    client.cookies.clear()
    oh = {"Authorization": f"Bearer {token}"}
    p = client.post("/api/projects", json={"codename": f"NM-{uuid.uuid4().hex[:6]}", "name": "Notify", "kind": "internal"}, headers=auth).json()
    client.put(f"/api/projects/{p['id']}/members", json=[other["id"]], headers=auth)

    cmd = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Scan"}, headers=auth).json()
    r = client.patch(f"/api/tasks/{cmd['id']}", json={"alerts": {"people": [other["id"]]}}, headers=auth)
    assert r.status_code == 200 and r.json()["alerts"]["people"] == [other["id"]]
    _finish(cmd["id"], m.JobStatus.succeeded)
    mine = client.get("/api/notifications", headers=oh).json()
    items = mine["items"] if isinstance(mine, dict) else mine
    assert any(n["kind"] == "task.finished" and "Scan" in n["title"] for n in items)

    # A manual card moved to Done: the person who moved it isn't told, others are.
    manual = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Write report"}, headers=auth).json()
    client.patch(f"/api/tasks/{manual['id']}", json={"alerts": {"people": [me["id"], other["id"]]}}, headers=auth)
    done = next(c for c in client.get(f"/api/projects/{p['id']}/board", headers=auth).json()["columns"] if c["role"] == "done")
    client.post(f"/api/tasks/{manual['id']}/move", json={"column_id": done["id"], "position": 1}, headers=auth)
    theirs = client.get("/api/notifications", headers=oh).json()
    theirs = theirs["items"] if isinstance(theirs, dict) else theirs
    assert any("Write report" in n["title"] for n in theirs)
    admin_n = client.get("/api/notifications", headers=auth).json()
    admin_n = admin_n["items"] if isinstance(admin_n, dict) else admin_n
    assert not any("Write report" in n["title"] for n in admin_n)

    # Only people who can see the project.
    outsider = client.post("/api/users", json={"username": f"no{uuid.uuid4().hex[:6]}", "role": "operator", "password": "a password"}, headers=auth).json()
    assert client.patch(f"/api/tasks/{manual['id']}", json={"alerts": {"people": [outsider["id"]]}}, headers=auth).status_code == 422
