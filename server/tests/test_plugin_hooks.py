"""Plugin hooks (plugin_hooks.py) with a real Deno plugin: job.prepare fills a command's input files
and environment as it's sent; assets.map turns what its parser read into assets. And command input
files on their own (job_inputs.py)."""

from __future__ import annotations

import base64
import json
import uuid

import pytest

from castor_server.plugin_runtime import PluginRuntimeError, deno_bin

from test_plugins import install, running, zip_files

try:
    deno_bin()
except PluginRuntimeError:
    pytest.skip("Deno isn't installed", allow_module_level=True)

MANIFEST = """
id: hooks-{n}
name: Hooks {n}
version: 1.0.0
publisher: tests
main: main.ts
contents: [commands, parsers]
hooks:
  - kind: job.prepare
  - kind: assets.map
"""

COMMAND = """
id: hooked-{n}
name: Hooked {n}
args:
  - name: who
    required: true
inputs:
  - name: inv
    filename: hosts.ini
    content: "[all]\\n{{{{ who }}}}\\n"
  - name: extra
command: cat {{{{ input.inv }}}} {{{{ input.extra }}}}
outputs:
  - name: hosts
    parser: hooked-lines-{n}
"""

PARSER = """
id: hooked-lines-{n}
name: Hooked lines
type: list<string>
format: lines
"""

MAIN = """
import { definePlugin } from "@castor/plugin";
definePlugin({
  activate(ctx) {
    ctx.hooks.on("job.prepare", (call) => ({
      inputs: { extra: `for ${call.args.who} on ${call.agent?.name} in ${call.project.codename}; it had ${call.inputs.inv.trim()}` },
      env: { HOOK_SAYS: "hello" },
    }));
    ctx.hooks.on("assets.map", (call) => ({
      assets: (call.value as string[]).filter((l) => /^\\d/.test(l)).map((ip, i) => ({ ref: `h${i}`, type: "host", fields: { ip } })),
    }));
  },
});
"""


def _agent(client, name: str):
    ws = client.websocket_connect("/ws/agent")
    ws.__enter__()
    ws.send_text(json.dumps({"type": "hello", "secret": "test-agent-secret", "name": name, "hostname": name, "os": "Linux",
                             "username": "u", "version": "1", "capabilities": {"runtimes": ["host"]}, "tags": [], "max_concurrency": 8}))
    assert json.loads(ws.receive_text())["type"] == "hello.ok"
    return ws


def _job_run(client, auth, ws, card_id: str) -> dict:
    """This card's job.run; jobs other tests left queued that come first are finished."""
    while True:
        msg = json.loads(ws.receive_text())
        if msg.get("type") != "job.run":
            continue
        latest = client.get(f"/api/tasks/{card_id}", headers=auth).json()["latest_job"]
        if latest and str(msg["job"]["id"]) == latest["id"]:
            return msg
        ws.send_text(json.dumps({"type": "job.finished", "job_id": msg["job"]["id"], "exit_code": 0}))


def test_hooks_prepare_a_job_and_map_its_results(client, auth):
    n = uuid.uuid4().hex[:6]
    plugin = install(client, auth, zip_files({
        "castor-plugin.yaml": MANIFEST.format(n=n), "main.ts": MAIN,
        f"library/commands/hooked-{n}.yaml": COMMAND.format(n=n), f"library/parsers/hooked-lines-{n}.yaml": PARSER.format(n=n),
    }), all_projects=True)
    running(client, auth, plugin["id"])
    code = f"HK-{n}"
    project = client.post("/api/projects", json={"codename": code, "name": code, "kind": "internal"}, headers=auth).json()
    name = f"hook-agent-{n}"
    ws = _agent(client, name)
    try:
        agent = next(a for a in client.get("/api/agents", headers=auth).json() if a["name"] == name)
        card = client.post(f"/api/projects/{project['id']}/tasks", json={
            "title": "Hooked", "kind": "command", "command_ref": f"hooked-{n}", "command_args": {"who": "web01"},
            "agent_selector": {"agent_id": agent["id"]}}, headers=auth).json()
        assert card["command"] == 'cat "${CASTOR_INPUT_INV}" "${CASTOR_INPUT_EXTRA}"'
        client.post(f"/api/tasks/{card['id']}/run", headers=auth)
        msg = _job_run(client, auth, ws, card["id"])

        # The definition's input, filled from the arguments; the plugin's, and its environment.
        files = {f["path"]: base64.b64decode(f["data"]).decode() for f in msg["job"]["files"]}
        assert files[".castor/inputs/hosts.ini"] == "[all]\nweb01\n"
        assert files[".castor/inputs/extra"] == f"for web01 on {name} in {code}; it had [all]\nweb01"
        assert msg["job"]["env"]["HOOK_SAYS"] == "hello"
        job_id = msg["job"]["id"]
        kept = client.get(f"/api/jobs/{job_id}", headers=auth).json()["inputs"]
        assert kept["inv"] == {"file": "hosts.ini", "content": "[all]\nweb01\n"}

        # What its parser read becomes assets through assets.map, credited to the run.
        ws.send_text(json.dumps({"type": "job.started", "job_id": job_id}))
        ws.send_text(json.dumps({"type": "job.log", "job_id": job_id, "seq": 0, "stream": "stdout", "data": "10.77.0.1\n10.77.0.2\nnot a host\n"}))
        ws.send_text(json.dumps({"type": "job.finished", "job_id": job_id, "exit_code": 0}))
        from test_plugins import wait

        hosts = wait(lambda: (h := client.get(f"/api/projects/{project['id']}/assets?type=host", headers=auth).json()["items"])
                     and len(h) >= 2 and h, "the hook's hosts")
        assert {"10.77.0.1", "10.77.0.2"} <= {h["fields"].get("ip") for h in hosts}
    finally:
        ws.__exit__(None, None, None)
        client.delete(f"/api/plugins/{plugin['id']}", headers=auth)
        client.delete(f"/api/projects/{project['id']}", headers=auth)


def test_a_plugin_that_cant_prepare_a_job_fails_it(client, auth):
    """Off in the project: the job fails with why, rather than running without what it should get."""
    n = uuid.uuid4().hex[:6]
    plugin = install(client, auth, zip_files({
        "castor-plugin.yaml": MANIFEST.format(n=n), "main.ts": MAIN,
        f"library/commands/hooked-{n}.yaml": COMMAND.format(n=n), f"library/parsers/hooked-lines-{n}.yaml": PARSER.format(n=n),
    }), all_projects=False)
    code = f"HN-{n}"
    project = client.post("/api/projects", json={"codename": code, "name": code, "kind": "internal"}, headers=auth).json()
    name = f"hook-agent-{n}"
    ws = _agent(client, name)
    try:
        agent = next(a for a in client.get("/api/agents", headers=auth).json() if a["name"] == name)
        card = client.post(f"/api/projects/{project['id']}/tasks", json={
            "title": "Hooked", "kind": "command", "command_ref": f"hooked-{n}", "command_args": {"who": "x"},
            "agent_selector": {"agent_id": agent["id"]}}, headers=auth).json()
        job = client.post(f"/api/tasks/{card['id']}/run", headers=auth).json()
        from test_plugins import wait

        failed = wait(lambda: (j := client.get(f"/api/jobs/{job['id']}", headers=auth).json())["status"] == "failed" and j, "the job to fail")
        assert "isn't on in this project" in failed["error"]
    finally:
        ws.__exit__(None, None, None)
        client.delete(f"/api/plugins/{plugin['id']}", headers=auth)
        client.delete(f"/api/projects/{project['id']}", headers=auth)


# ---------- the official Ansible plugin (castor-plugins/plugins/ansible) ----------

def test_the_ansible_plugin_prepares_an_inventory_and_reads_its_results(client, auth, monkeypatch):
    from test_plugins import OFFICIAL, wait, zip_folder

    from castor_server import plugins

    folder = OFFICIAL / "ansible"
    if not folder.is_dir():
        pytest.skip(f"no castor-plugins checkout with the ansible plugin at {folder}")
    # It needs the Castor that has hooks: whatever version this checkout's metadata says, it's that one.
    monkeypatch.setattr(plugins, "CASTOR_VERSION", "99.0.0")
    plugin = install(client, auth, zip_folder(folder), all_projects=True)
    running(client, auth, plugin["id"])
    n = uuid.uuid4().hex[:6]
    code = f"AN-{n}"
    project = client.post("/api/projects", json={"codename": code, "name": code, "kind": "internal"}, headers=auth).json()
    pid = project["id"]
    # What Castor knows: a web server (a role, a service) and a host nothing selects.
    client.post(f"/api/projects/{pid}/assets", json={"type": "host", "fields": {"ip": "10.88.0.5", "hostname": "web01.lab", "roles": ["web"]}}, headers=auth)
    client.post(f"/api/projects/{pid}/assets", json={"type": "service", "fields": {"host": "10.88.0.5", "port": 443, "name": "https"}}, headers=auth)
    client.post(f"/api/projects/{pid}/assets", json={"type": "host", "fields": {"ip": "10.88.0.9", "hostname": "db01.lab"}}, headers=auth)
    name = f"ansible-agent-{n}"
    ws = client.websocket_connect("/ws/agent")
    ws.__enter__()
    try:
        ws.send_text(json.dumps({"type": "hello", "secret": "test-agent-secret", "name": name, "hostname": name, "os": "Linux", "username": "u",
                                 "version": "1", "capabilities": {"runtimes": ["docker"]}, "tags": [], "max_concurrency": 8}))
        assert json.loads(ws.receive_text())["type"] == "hello.ok"
        agent = next(a for a in client.get("/api/agents", headers=auth).json() if a["name"] == name)
        playbook = "- hosts: all\n  tasks:\n    - ansible.builtin.ping:\n"
        card = client.post(f"/api/projects/{pid}/tasks", json={
            "title": "Ping the web servers", "kind": "command", "command_ref": "ansible-playbook-inline",
            "command_args": {"playbook": playbook, "asset_filter": "roles:web"}, "agent_selector": {"agent_id": agent["id"]}}, headers=auth).json()
        assert "id" in card, card
        client.post(f"/api/tasks/{card['id']}/run", headers=auth)
        msg = _job_run(client, auth, ws, card["id"])
        job = msg["job"]
        assert job["tool"]["id"] == "ansible"
        files = {f["path"]: base64.b64decode(f["data"]).decode() for f in job["files"]}
        inventory = json.loads(files[".castor/inputs/inventory.yml"])
        assert list(inventory["all"]["hosts"]) == ["web01.lab"]  # by the filter: not db01
        web = inventory["all"]["hosts"]["web01.lab"]
        assert web["ansible_host"] == "10.88.0.5" and web["castor_ports"] == [443] and "web01.lab" in inventory["all"]["children"]["web"]["hosts"]
        assert "class CallbackModule" in files[".castor/inputs/castor_results.py"]
        assert files[".castor/inputs/extra-vars.yml"] == "{}" and files[".castor/inputs/playbook.yml"] == playbook

        # The callback's file comes back as an artifact; the parsers read it, and the hook makes facts hosts.
        results = {"version": 1, "results": [
            {"host": "web01.lab", "play": "all", "task": "Gathering Facts", "action": "gather_facts", "status": "ok", "changed": False, "msg": "", "rc": None, "duration": 1.1},
            {"host": "web01.lab", "play": "all", "task": "ansible.builtin.ping", "action": "ping", "status": "ok", "changed": False, "msg": "", "rc": None, "duration": 0.2}],
            "hosts": {"web01.lab": {"ok": 2, "changed": 0, "failed": 0, "unreachable": 0, "skipped": 0, "facts": {
                "distribution": "Debian", "distribution_version": "12", "fqdn": "web01.lab", "domain": "lab",
                "default_ipv4": {"address": "10.88.0.5", "macaddress": "52:54:00:aa:bb:cc", "gateway": "10.88.0.1"}}}}}
        r = client.post(f"/api/jobs/{job['id']}/artifacts", files={"file": ("ansible-results.json", json.dumps(results).encode(), "application/json")},
                        data={"path": "artifacts/ansible-results.json"}, headers={"X-Castor-Job-Token": job["artifacts"]["token"]})
        assert r.status_code == 201, r.text
        ws.send_text(json.dumps({"type": "job.started", "job_id": job["id"]}))
        ws.send_text(json.dumps({"type": "job.finished", "job_id": job["id"], "exit_code": 0}))
        outputs = wait(lambda: (o := client.get(f"/api/jobs/{job['id']}/outputs", headers=auth).json()) and len(o) == 2 and o, "the results")
        by = {o["name"]: o for o in outputs}
        assert [r["task"] for r in by["results"]["value"]] == ["Gathering Facts", "ansible.builtin.ping"]
        assert by["hosts"]["value"][0]["os"] == "Debian 12"
        web01 = wait(lambda: next((h for h in client.get(f"/api/projects/{pid}/assets?type=host", headers=auth).json()["items"]
                                   if h["fields"].get("ip") == "10.88.0.5" and h["fields"].get("os")), None), "the facts on the host")
        assert web01["fields"]["os"] == "Debian 12" and web01["fields"]["mac"] == "52:54:00:aa:bb:cc"
    finally:
        ws.__exit__(None, None, None)
        client.delete(f"/api/plugins/{plugin['id']}", headers=auth)
        client.delete(f"/api/projects/{pid}", headers=auth)
