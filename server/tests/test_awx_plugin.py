"""The official AWX plugin (castor-plugins plugins/awx) against a fake AWX API on loopback: a job
template launched from a card, its check run approved before the real run, per-task and per-host
results, the AWX job's failure failing the card with its results kept, "Retry what failed" with only
the failed hosts as the limit, and an inventory's hosts read for Castor's SSH hosts."""

from __future__ import annotations

import http.server
import json
import threading
from urllib.parse import parse_qs, urlparse

from test_plugins import OFFICIAL, install, official, project, wait, zip_folder  # also skips without Deno

PROMPTS = {k: True for k in ("ask_inventory_on_launch", "ask_limit_on_launch", "ask_variables_on_launch", "ask_tags_on_launch",
                             "ask_skip_tags_on_launch", "ask_job_type_on_launch", "ask_diff_mode_on_launch")}


def _events(limit: str) -> list[dict]:
    """A run on the hosts of the limit: web1 changes, web2 fails, db1 can't be reached."""
    hosts = ["web1", "web2", "db1"] if limit in ("", "all") else limit.split(",")
    out = [{"counter": 1, "event": "playbook_on_start", "stdout": ""},
           {"counter": 2, "event": "playbook_on_task_start", "stdout": "TASK [copy motd] ****"}]
    stats: dict[str, dict[str, int]] = {"ok": {}, "changed": {}, "failures": {}, "dark": {}}
    for i, h in enumerate(hosts):
        if h == "db1":
            ev = {"event": "runner_on_unreachable", "event_data": {"res": {"msg": "timed out"}}}
            stats["dark"][h] = 1
        elif h == "web2":
            ev = {"event": "runner_on_failed", "event_data": {"res": {"msg": "permission denied", "rc": 1}}}
            stats["failures"][h] = 1
        else:
            ev = {"event": "runner_on_ok", "event_data": {"res": {"changed": True}}}
            stats["ok"][h], stats["changed"][h] = 1, 1
        ev["event_data"].update({"play": "site", "task": "copy motd", "task_action": "copy", "duration": 0.2})
        out.append({"counter": 3 + i, "host_name": h, "stdout": f"{ev['event']}: [{h}]", **ev})
    out.append({"counter": 10, "event": "playbook_on_stats", "stdout": "PLAY RECAP", "event_data": stats})
    return out


class FakeAwx:
    def __init__(self) -> None:
        self.launches: list[dict] = []
        jobs: dict[int, dict] = {}
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _send(self, body, status=200):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _ok(self) -> bool:
                if self.headers.get("Authorization") != "Bearer awx-token":
                    self._send({"detail": "no"}, 401)
                    return False
                return True

            def do_POST(self):
                if not self._ok():
                    return
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                if self.path == "/api/v2/job_templates/5/launch/":
                    job_id = 100 + len(jobs)
                    jobs[job_id] = {"body": body, "polls": 0}
                    fake.launches.append(body)
                    return self._send({"job": job_id, "id": job_id, "ignored_fields": {}}, 201)
                if self.path.endswith("/cancel/"):
                    return self._send({}, 202)
                return self._send({}, 404)

            def do_GET(self):
                if not self._ok():
                    return
                url = urlparse(self.path)
                q = parse_qs(url.query)
                if url.path == "/api/v2/job_templates/" and q.get("name") == ["Deploy web"]:
                    return self._send({"results": [{"id": 5, "name": "Deploy web"}]})
                if url.path == "/api/v2/job_templates/5/launch/":
                    return self._send(PROMPTS)
                if url.path == "/api/v2/inventories/" and q.get("name") == ["Lab"]:
                    return self._send({"results": [{"id": 3, "name": "Lab"}]})
                if url.path == "/api/v2/inventories/3/script/":
                    return self._send({"_meta": {"hostvars": {"web1": {"ansible_host": "10.0.0.1", "ansible_user": "deploy"}}},
                                       "all": {"children": ["web"]}, "web": {"hosts": ["web1"]}})
                parts = url.path.strip("/").split("/")
                if len(parts) >= 4 and parts[2] == "jobs" and int(parts[3]) in jobs:
                    job = jobs[int(parts[3])]
                    body = job["body"]
                    events = _events(body.get("limit", ""))
                    failed = body.get("job_type") != "check" and any(e["event"] in ("runner_on_failed", "runner_on_unreachable") for e in events)
                    if len(parts) == 5 and parts[4] == "job_events":
                        after = int(q.get("counter__gt", ["0"])[0])
                        return self._send({"results": [e for e in events if e["counter"] > after]})
                    job["polls"] += 1
                    return self._send({"id": int(parts[3]), "status": "running" if job["polls"] < 2 else "failed" if failed else "successful"})
                return self._send({}, 404)

            def log_message(self, *_args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()


def _latest(client, auth, card_id):
    return client.get(f"/api/tasks/{card_id}", headers=auth).json().get("latest_job")


@official
def test_awx_job_templates_previews_and_retries(client, auth, monkeypatch):
    from castor_server import plugins as P

    monkeypatch.setattr(P, "CASTOR_VERSION", "99.0.0")  # it needs the release with previews (this checkout)
    fake = FakeAwx()
    p = project(client, auth)
    try:
        plugin = install(client, auth, zip_folder(OFFICIAL / "awx"), project_ids=[p["id"]],
                         settings={"url": f"http://127.0.0.1:{fake.port}", "poll_seconds": 1}, secrets={"token": "awx-token"})
        assert plugin["granted"]["network_settings"] == ["url"]
        spec = client.get("/api/commands/awx-launch", headers=auth).json()["spec"]
        assert spec["preview"]["label"] == "Check run" and spec["retry_failed"]["arg"] == "limit"

        card = client.post(f"/api/projects/{p['id']}/tasks", json={
            "title": "Deploy", "kind": "command", "command_ref": "awx-launch", "command_args": {"template": "Deploy web"},
            "preview": True}, headers=auth).json()
        preview = client.post(f"/api/tasks/{card['id']}/run", headers=auth).json()
        assert preview["purpose"] == "preview"
        done = wait(lambda: (j := client.get(f"/api/jobs/{preview['id']}", headers=auth).json())["status"] in ("succeeded", "failed") and j,
                    "the check run", seconds=60)
        assert done["status"] == "succeeded", done
        assert fake.launches[0] == {"job_type": "check", "diff_mode": True}
        assert any("TASK [copy motd]" in c["data"] for c in done["logs"])

        real = wait(lambda: (j := _latest(client, auth, card["id"])) and j["id"] != preview["id"] and j, "the real run to wait")
        assert real["status"] == "approval" and real["follows_job_id"] == preview["id"]
        assert client.post(f"/api/jobs/{real['id']}/approve", headers=auth).status_code == 200
        ran = wait(lambda: (j := client.get(f"/api/jobs/{real['id']}", headers=auth).json())["status"] in ("succeeded", "failed") and j,
                   "the real run", seconds=60)
        assert ran["status"] == "failed" and "AWX job 101 failed" in (ran["error"] or ""), ran
        assert fake.launches[1] == {}
        outputs = wait(lambda: (o := client.get(f"/api/jobs/{real['id']}/outputs", headers=auth).json()) and len(o) == 2 and o, "its results")
        by = {o["name"]: o["value"] for o in outputs}
        assert [(r["host"], r["status"]) for r in by["results"]] == [("web1", "changed"), ("web2", "failed"), ("db1", "unreachable")]
        assert [(h["host"], h["status"]) for h in by["hosts"]] == [("db1", "unreachable"), ("web1", "changed"), ("web2", "failed")]

        plan = client.get(f"/api/jobs/{real['id']}/retry-failed", headers=auth).json()
        assert plan == {"arg": "limit", "values": ["web2", "db1"]}
        retry = client.post(f"/api/jobs/{real['id']}/retry-failed", headers=auth)
        assert retry.status_code == 201, retry.text
        wait(lambda: len(fake.launches) == 3, "the retry to launch", seconds=60)
        assert fake.launches[2] == {"limit": "web2,db1"}
        wait(lambda: client.get(f"/api/jobs/{retry.json()['id']}", headers=auth).json()["status"] in ("succeeded", "failed"), "the retry", seconds=60)

        # An inventory's hosts, for Castor's SSH hosts.
        inv = client.post(f"/api/projects/{p['id']}/tasks", json={
            "title": "Lab hosts", "kind": "command", "command_ref": "awx-inventory", "command_args": {"inventory": "Lab"}}, headers=auth).json()
        job = client.post(f"/api/tasks/{inv['id']}/run", headers=auth).json()
        wait(lambda: client.get(f"/api/jobs/{job['id']}/outputs", headers=auth).json(), "the inventory", seconds=60)
        hosts = client.post("/api/ssh-hosts/inventory", json={"job_id": job["id"], "output": "inventory"}, headers=auth)
        assert hosts.status_code == 200, hosts.text
        assert [(h["name"], h["host"], h["user"], h["groups"]) for h in hosts.json()] == [("web1", "10.0.0.1", "deploy", ["web"])]
    finally:
        client.delete("/api/plugins/awx", headers=auth)
        client.delete(f"/api/projects/{p['id']}", headers=auth)
        fake.close()
