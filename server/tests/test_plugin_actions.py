"""Plugin library actions (docs/ACTIONS.md phase 4): a real Deno plugin whose manifest declares
library_actions, which become read-only kind: plugin definitions, run from cards as jobs."""

from __future__ import annotations

import json
import time
import uuid

import pytest

from castor_server.plugin_runtime import PluginRuntimeError, deno_bin

from test_plugins import install, preview, zip_files

try:
    deno_bin()
except PluginRuntimeError:
    pytest.skip("Deno isn't installed", allow_module_level=True)

MANIFEST = """
id: crack-{n}
name: Hash cracker
version: 1.0.0
publisher: tests
main: main.ts
permissions: [projects:read]
library_actions:
  - id: crack
    name: Crack hashes
    description: Tries a word list against hashes
    tags: [passwords]
    args:
      - {{name: hashes, type: list, required: true}}
      - {{name: rounds, type: number, default: 3}}
    vars:
      - {{name: creds, kind: credential, required: false}}
    outputs:
      - {{name: cracked, parser: json-records, options: {{records: cracked}}}}
      - {{name: summary, parser: json-value, options: {{path: summary}}}}
    timeout_minutes: 2
  - id: slow
    name: Slow
    timeout_minutes: 1
"""

MAIN = """
import { definePlugin } from "@castor/plugin";

definePlugin({
  activate(ctx) {
    ctx.actions.on("crack", async (run) => {
      const hashes = run.args.hashes as string[];
      run.log.info(`trying ${hashes.length} hashes for ${run.user?.username}`);
      run.progress({ done: 1, total: hashes.length });
      const cracked = hashes.filter((h) => h.startsWith("a")).map((h) => ({ hash: h, password: "letmein" }));
      return { message: `${cracked.length} cracked`,
               output: { cracked, summary: `${cracked.length} of ${hashes.length}, rounds ${run.args.rounds}, user ${run.vars.creds?.username ?? "none"}` } };
    });
    ctx.actions.on("slow", async (run) => {
      await new Promise((resolve, reject) => {
        const t = setTimeout(resolve, 30000);
        run.signal.addEventListener("abort", () => { clearTimeout(t); reject(new Error("stopped")); });
      });
      return "finished";
    });
  },
});
"""


def _wait(client, auth, job_id, statuses=("succeeded", "failed", "cancelled"), timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}", headers=auth).json()
        if job["status"] in statuses:
            return job
        time.sleep(0.1)
    raise AssertionError(job)


def _log(client, auth, job_id, stream="both"):
    return client.get(f"/api/jobs/{job_id}/output", params={"stream": stream}, headers=auth).text


def test_library_actions_run_as_jobs(client, auth):
    n = uuid.uuid4().hex[:6]
    bad = preview(client, auth, zip_files({"castor-plugin.yaml": MANIFEST.format(n=n).replace("type: number", "type: nonsense"), "main.ts": MAIN}))
    assert bad.status_code == 422 and "library_actions.crack" in bad.text
    code = f"PA-{n}"
    project = client.post("/api/projects", json={"codename": code, "name": code, "kind": "internal"}, headers=auth).json()
    plugin = install(client, auth, zip_files({"castor-plugin.yaml": MANIFEST.format(n=n), "main.ts": MAIN}), project_ids=[project["id"]])
    pid = plugin["id"]
    ref = f"{pid}-crack"
    definition = client.get(f"/api/commands/{ref}", headers=auth).json()
    assert definition["origin"] == f"plugin:{pid}" and definition["spec"]["kind"] == "plugin"
    assert definition["spec"]["plugin"] == {"id": pid, "action": "crack", "timeout_minutes": 2}
    assert client.put(f"/api/commands/{ref}", json={"yaml": "id: x\nname: x\ncommand: x\n"}, headers=auth).status_code == 409

    creds = client.post("/api/variables/sets", json={"kind_id": "credential", "name": f"pa-{n}", "values": {"username": "svc", "password": "Pa55word-xyz"}}, headers=auth).json()
    card = client.post(f"/api/projects/{project['id']}/tasks", json={
        "title": "Crack them", "kind": "command", "command_ref": ref, "command_args": {"hashes": "aa11, bb22, ab33"},
        "var_bindings": {"creds": creds["id"]},
    }, headers=auth).json()
    assert card["command"] == f'{pid} · crack(hashes=["aa11", "bb22", "ab33"], rounds=3)'
    job_id = client.post(f"/api/tasks/{card['id']}/run", headers=auth).json()["id"]
    job = _wait(client, auth, job_id)
    log = _log(client, auth, job_id)
    assert job["status"] == "succeeded", log
    assert job["kind"] == "plugin" and job["runner"] == "server"
    assert "> Hash cracker: crack" in log and "[info] trying 3 hashes for admin" in log and "progress 1 of 3" in log
    assert "< done" in log and "2 cracked" in log and "Pa55word-xyz" not in log
    deadline = time.time() + 10
    while time.time() < deadline and len(client.get(f"/api/jobs/{job_id}/outputs", headers=auth).json()) < 2:
        time.sleep(0.1)
    outputs = {o["name"]: o["value"] for o in client.get(f"/api/jobs/{job_id}/outputs", headers=auth).json()}
    assert [r["hash"] for r in outputs["cracked"]] == ["aa11", "ab33"]
    assert outputs["summary"] == "2 of 3, rounds 3, user svc"

    # Cancelling tells the handler (run.signal); the plugin's rate limit holds runs back.
    slow = client.post(f"/api/projects/{project['id']}/tasks", json={"title": "Slow", "kind": "command", "command_ref": f"{pid}-slow"}, headers=auth).json()
    job_id = client.post(f"/api/tasks/{slow['id']}/run", headers=auth).json()["id"]
    _wait(client, auth, job_id, statuses=("running",))
    time.sleep(0.5)
    assert client.post(f"/api/jobs/{job_id}/cancel", headers=auth).json()["status"] == "cancelled"
    r = client.patch(f"/api/plugins/{pid}", json={"actions_rate_limit": "often"}, headers=auth)
    assert r.status_code == 422
    assert client.patch(f"/api/plugins/{pid}", json={"actions_rate_limit": "1/minute"}, headers=auth).json()["actions_rate_limit"] == "1/minute"
    first = client.post(f"/api/tasks/{card['id']}/run", headers=auth).json()["id"]
    assert _wait(client, auth, first)["status"] == "succeeded"
    second = client.post(f"/api/tasks/{card['id']}/run", headers=auth).json()["id"]
    job = _wait(client, auth, second, statuses=("waiting",))
    assert "the Hash cracker plugin's rate limit" in _log(client, auth, second)
    client.post(f"/api/jobs/{second}/cancel", headers=auth)

    # Removing the plugin takes its actions out of the library; their cards become manual.
    assert client.delete(f"/api/plugins/{pid}", headers=auth).status_code in (200, 204)
    assert client.get(f"/api/commands/{ref}", headers=auth).status_code == 404
    assert client.get(f"/api/tasks/{card['id']}", headers=auth).json()["kind"] == "manual"
    client.delete(f"/api/variables/sets/{creds['id']}", headers=auth)
    client.delete(f"/api/projects/{project['id']}", headers=auth)
