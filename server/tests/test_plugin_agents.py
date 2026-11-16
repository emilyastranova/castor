"""Agent types from plugins (plugin_agents.py): a plugin lists the machines its system reaches, an
admin brings some in as agents, and jobs run on them through the plugin (logs in order, artifacts,
cancel), with the system's tags on them; the machines go offline when the plugin stops."""

from __future__ import annotations

import uuid

from test_plugins import OFFICIAL, install, official, preview, project, wait, zip_files, zip_folder  # also skips without Deno

from castor_server.orchestrator.dispatcher import tool_way

MANIFEST = """
id: fleet-{n}
name: Fleet
version: 1.0.0
publisher: tests
main: main.ts
agent_types:
  - name: fleet
    label: Fake fleet
    description: Machines a pretend config-management system reaches.
    icon: lan
    max_concurrency: 2
"""

MAIN = """
import { definePlugin } from "@castor/plugin";
definePlugin({
  activate(ctx) {
    const id = ctx.plugin.id;
    ctx.agents.on("fleet", {
      list: () => [
        { id: "m1", name: `${id}-web01`, hostname: "web01.lab", os: "Ubuntu 24.04", tags: ["role:web", "os:ubuntu"] },
        { id: "m2", name: `${id}-db01`, hostname: "db01.lab", os: "Debian 12", tags: ["role:db"] },
      ],
      async run(job, io) {
        io.log(`on ${io.machine.hostname}\\n`);
        for (let i = 1; i <= 5; i++) io.log(`line ${i}\\n`);
        io.log(`env ${Object.keys(job.env).sort().join(",")}\\n`, "stderr");
        if (job.command.includes("sleep")) {
          await new Promise((resolve) => io.signal.addEventListener("abort", resolve));
          io.log("stopped\\n");
          return { exitCode: null, error: "cancelled" };
        }
        await io.artifact("report.txt", `ran: ${job.command}`);
        return { exitCode: job.command.includes("fail") ? 3 : 0 };
      },
    });
  },
});
"""


def _package(n: str) -> bytes:
    return zip_files({"castor-plugin.yaml": MANIFEST.format(n=n), "main.ts": MAIN})


def _agent(client, auth, name):
    return next((a for a in client.get("/api/agents", headers=auth).json() if a["name"] == name), None)


def _run(client, auth, p, agent_id, command):
    card = client.post(f"/api/projects/{p['id']}/tasks", json={"title": command, "kind": "command", "command": command,
                                                               "agent_selector": {"agent_id": agent_id}}, headers=auth).json()
    return card, client.post(f"/api/tasks/{card['id']}/run", headers=auth).json()


def test_machines_from_a_plugin_run_jobs(client, auth):
    n = uuid.uuid4().hex[:6]
    pid = f"fleet-{n}"
    p = project(client, auth)
    assert preview(client, auth, _package(n)).json()["manifest"]["agent_types"][0]["name"] == "fleet"
    install(client, auth, _package(n), project_ids=[p["id"]])

    types = [t for t in client.get("/api/plugin-agent-types", headers=auth).json() if t["plugin"]["id"] == pid]
    assert [(t["name"], t["label"], t["agents"]) for t in types] == [("fleet", "Fake fleet", 0)]
    machines = client.get(f"/api/plugin-agent-types/{pid}/fleet/machines", headers=auth)
    assert machines.status_code == 200, machines.text
    assert [(x["id"], x["agent_id"]) for x in machines.json()["machines"]] == [("m1", None), ("m2", None)]
    assert client.post(f"/api/plugin-agent-types/{pid}/fleet/import", json={"machines": ["nope"]}, headers=auth).status_code == 422

    made = client.post(f"/api/plugin-agent-types/{pid}/fleet/import", json={"machines": ["m1"], "tags": ["lab"],
                                                                             "project_ids": [p["id"]]}, headers=auth)
    assert made.status_code == 201, made.text
    agent = made.json()[0]
    assert agent["name"] == f"{pid}-web01" and agent["plugin"] == {"id": pid, "type": "fleet", "machine": "m1"}
    assert sorted(agent["tags"]) == ["lab", "os:ubuntu", "role:web"] and agent["max_concurrency"] == 2
    agent = wait(lambda: (a := _agent(client, auth, agent["name"])) and a["connected"] and a, "the machine to connect")
    assert agent["hostname"] == "web01.lab" and agent["plugin_status"]["ok"] is True
    again = client.post(f"/api/plugin-agent-types/{pid}/fleet/import", json={"machines": ["m1"]}, headers=auth).json()
    assert again == []  # already in

    # A job runs through the plugin: its lines in order, Castor's around them, an artifact.
    card, job = _run(client, auth, p, agent["id"], "uptime")
    done = wait(lambda: (j := client.get(f"/api/jobs/{job['id']}", headers=auth).json())["status"] in ("succeeded", "failed") and j,
                "the job to finish")
    assert done["status"] == "succeeded" and done["exit_code"] == 0, done
    text = "".join(c["data"] for c in done["logs"])
    assert text.index("through the") < text.index("on web01.lab") < text.index("line 1") < text.index("line 5") < text.index("exit code 0")
    arts = client.get(f"/api/jobs/{job['id']}/artifacts", headers=auth).json()
    assert [a["filename"] for a in arts] == ["report.txt"]
    _card, failing = _run(client, auth, p, agent["id"], "fail now")
    failed = wait(lambda: (j := client.get(f"/api/jobs/{failing['id']}", headers=auth).json())["status"] in ("succeeded", "failed") and j,
                  "the failing job")
    assert failed["status"] == "failed" and failed["exit_code"] == 3

    # Cancelling reaches the plugin's handler.
    _card, slow = _run(client, auth, p, agent["id"], "sleep 600")
    wait(lambda: client.get(f"/api/jobs/{slow['id']}", headers=auth).json()["status"] == "running", "the slow job to start")
    assert client.post(f"/api/jobs/{slow['id']}/cancel", headers=auth).status_code == 200
    wait(lambda: "stopped" in "".join(c["data"] for c in client.get(f"/api/jobs/{slow['id']}", headers=auth).json()["logs"]),
         "the plugin to stop it")

    # Turned off: its machines go offline. Forgetting one works while it is.
    client.patch(f"/api/plugins/{pid}", json={"enabled": False}, headers=auth)
    wait(lambda: not _agent(client, auth, agent["name"])["connected"], "the machine to go offline")
    assert client.delete(f"/api/agents/{agent['id']}", headers=auth).status_code == 204
    client.delete(f"/api/plugins/{pid}", headers=auth)
    client.delete(f"/api/projects/{p['id']}", headers=auth)


def test_a_plugin_machine_gets_the_whole_tool():
    from types import SimpleNamespace

    from castor_server.tool_spec import parse_tool_spec

    tool = parse_tool_spec({"id": "nmap", "name": "nmap", "image": "docker.io/instrumentisto/nmap",
                            "host": {"check": "nmap --version", "install": "apt-get install -y nmap"}})
    plain = SimpleNamespace(runtimes={"host"}, capabilities={"runtimes": ["host"]})
    machine = SimpleNamespace(runtimes={"host"}, capabilities={"runtimes": ["host"], "plugin": True})
    assert tool_way(plain, tool)["kind"] == "host"
    way = tool_way(machine, tool)
    assert way["kind"] == "plugin" and way["image"] == "docker.io/instrumentisto/nmap" and way["install"] == "apt-get install -y nmap"


# ---------- the official SaltStack plugin, against a pretend salt-api ----------


class FakeSaltApi:
    """salt-api (rest_cherrypy) on localhost: one minion that answers, jobs that finish on the
    second poll, and the calls it got (to check nothing secret went into a command line)."""

    def __init__(self) -> None:
        import http.server
        import json
        import threading

        calls: list[dict] = []
        jobs: dict[str, dict] = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def _send(self, body, status=200):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"null")
                if self.path == "/login":
                    return self._send({"return": [{"token": "tok"}]} if body.get("password") == "pw" else {}, 200 if body.get("password") == "pw" else 401)
                if self.headers.get("X-Auth-Token") != "tok":
                    return self._send({}, 401)
                low = body[0]
                calls.append(low)
                if low["fun"] == "grains.items":
                    return self._send({"return": [{"lab-web01": {"fqdn": "web01.lab", "os": "Rocky", "osfinger": "Rocky Linux-9", "roles": ["web"]}}]})
                if low["client"] == "local_async":
                    jid = str(20261124000000 + len(jobs))
                    jobs[jid] = {"low": low, "polls": 0}
                    return self._send({"return": [{"jid": jid, "minions": ["lab-web01"]}]})
                return self._send({"return": [{"lab-web01": {"retcode": 0, "stdout": "", "stderr": ""}}]})

            def do_GET(self):
                jid = self.path.rsplit("/", 1)[-1]
                job = jobs.get(jid)
                if job is None:
                    return self._send({}, 404)
                job["polls"] += 1
                if job["polls"] < 2:
                    return self._send({"info": [{"Result": {}}]})
                env = job["low"]["kwarg"].get("env") or {}
                out = f"hello from the minion, CASTOR_WORK={env.get('CASTOR_WORK')}\n"
                return self._send({"info": [{"Result": {"lab-web01": {"return": {"retcode": 0, "stdout": out, "stderr": ""}}}}]})

            def log_message(self, *_args):
                pass

        self.calls = calls
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()


@official
def test_saltstack_minions_run_jobs(client, auth, monkeypatch):
    from castor_server import plugins as P

    monkeypatch.setattr(P, "CASTOR_VERSION", "1.12.0")  # it needs the release with agent types

    assert P.network_host("https://salt.example.com:8000/x") == "salt.example.com:8000"
    assert P.network_host("salt.example.com") == "salt.example.com" and P.network_host("https://*.example.com") is None
    fake = FakeSaltApi()
    try:
        p = project(client, auth)
        package = zip_folder(OFFICIAL / "saltstack")
        out = preview(client, auth, package).json()
        assert out["problems"] == [] and out["manifest"]["network"] == []
        assert [s["name"] for s in out["manifest"]["settings"] if s.get("network")] == ["api_url"]
        plugin = install(client, auth, package, project_ids=[p["id"]], settings={"api_url": f"http://127.0.0.1:{fake.port}",
                                                                                  "username": "castor"}, secrets={"password": "pw"})
        assert plugin["granted"]["network_settings"] == ["api_url"] and plugin["granted"]["agent_types"] == ["minion"]
        listed = client.get("/api/plugin-agent-types/saltstack/minion/machines", headers=auth)
        assert listed.status_code == 200, listed.text
        machine = listed.json()["machines"][0]
        assert machine["id"] == "lab-web01" and "roles:web" in machine["tags"] and machine["os"] == "Rocky Linux-9"
        agent = client.post("/api/plugin-agent-types/saltstack/minion/import", json={"machines": ["lab-web01"], "project_ids": [p["id"]]},
                            headers=auth).json()[0]
        wait(lambda: _agent(client, auth, agent["name"])["connected"], "the minion to connect")
        _card, job = _run(client, auth, p, agent["id"], "uptime")
        done = wait(lambda: (j := client.get(f"/api/jobs/{job['id']}", headers=auth).json())["status"] in ("succeeded", "failed") and j,
                    "the Salt job", seconds=60)
        assert done["status"] == "succeeded", done
        assert "hello from the minion, CASTOR_WORK=/var/tmp/castor/" in "".join(c["data"] for c in done["logs"])
        started = next(c for c in fake.calls if c["client"] == "local_async")
        assert started["arg"] == ["uptime"] and started["kwarg"]["cwd"].endswith(job["id"])
        client.delete(f"/api/agents/{agent['id']}", headers=auth)
        client.delete("/api/plugins/saltstack", headers=auth)
        client.delete(f"/api/projects/{p['id']}", headers=auth)
    finally:
        fake.close()
