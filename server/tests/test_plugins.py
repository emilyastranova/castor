"""Plugins, phase A (docs/PLUGINS.md): installing from a .zip, the sandbox, a plugin's own account
and token, actions with files, storage, updates, and the Nessus import plugin end to end."""

from __future__ import annotations

import io
import time
import uuid
import zipfile
from pathlib import Path

import pytest

from castor_server.plugin_runtime import PluginRuntimeError, deno_bin

ROOT = Path(__file__).resolve().parents[2]
NESSUS = ROOT / "plugins" / "nessus-import"

try:
    deno_bin()
except PluginRuntimeError:
    pytest.skip("Deno isn't installed", allow_module_level=True)


def zip_folder(folder: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path in sorted(folder.rglob("*")):
            rel = path.relative_to(folder)
            if path.is_file() and rel.parts[0] != "samples" and not path.name.endswith("_test.ts") and path.name != "deno.json":
                zf.write(path, str(rel))
    return buf.getvalue()


def zip_files(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buf.getvalue()


def preview(client, auth, data: bytes, name="plugin.zip"):
    return client.post("/api/plugins/preview", files={"file": (name, data, "application/zip")}, headers=auth)


def install(client, auth, data: bytes, **body):
    p = preview(client, auth, data)
    assert p.status_code == 200, p.text
    r = client.post("/api/plugins", json={"token": p.json()["token"], "source": p.json()["source"], "approve": True, **body}, headers=auth)
    assert r.status_code == 201, r.text
    return r.json()


def wait(fn, what: str, seconds: float = 30):
    end = time.time() + seconds
    while time.time() < end:
        out = fn()
        if out:
            return out
        time.sleep(0.2)
    raise AssertionError(f"timed out waiting for {what}")


def running(client, auth, plugin_id: str):
    def check():
        p = client.get(f"/api/plugins/{plugin_id}", headers=auth).json()
        assert p["status"] not in ("error", "crashed"), p["status_message"]
        return p if p["status"] == "running" else None
    return wait(check, f"{plugin_id} to start")


def run(client, auth, plugin_id: str, action: str, project_id: str, file: tuple[str, bytes] | None = None, **form):
    r = client.post(f"/api/plugins/{plugin_id}/actions/{action}", data={"project_id": project_id, **form},
                    files={"file": (file[0], file[1], "application/octet-stream")} if file else None, headers=auth)
    assert r.status_code == 202, r.text
    rid = r.json()["id"]

    def done():
        out = client.get(f"/api/plugin-runs/{rid}", headers=auth).json()
        return out if out["status"] != "running" else None
    return wait(done, f"{plugin_id}/{action}", 60)


def project(client, auth):
    code = f"PL-{uuid.uuid4().hex[:6]}"
    return client.post("/api/projects", json={"codename": code, "name": code, "kind": "internal"}, headers=auth).json()


PROBE_MANIFEST = """
id: probe-{n}
name: Probe
version: {version}
publisher: tests
main: main.ts
permissions: [{perms}]
actions:
  - id: escape
    label: Try to get out
  - id: store
    label: Use storage
  - id: whoami
    label: Who am I
    on: [project, card]
  - id: echo
    label: Echo a file
    file: {{accept: [".txt"], max_mb: 1}}
settings:
  - {{name: greeting, label: Greeting, type: text, default: hello}}
  - {{name: key, label: Key, type: secret}}
"""

PROBE_MAIN = """
import { definePlugin } from "@castor/plugin";
definePlugin({
  activate(ctx) {
    ctx.actions.on("escape", async () => {
      const out: Record<string, string> = {};
      const tries: Record<string, () => unknown> = {
        read: () => Deno.readTextFileSync("/etc/hostname"),
        write: () => Deno.writeTextFileSync("/tmp/castor-probe", "x"),
        env: () => Deno.env.get("HOME"),
        net: () => fetch("https://example.com"),
        run: () => new Deno.Command("id").outputSync(),
        imp: () => import(["file://", "etc", "passwd"].join("/").replace("//etc", "///etc")),
      };
      for (const [k, fn] of Object.entries(tries)) {
        try { await fn(); out[k] = "ALLOWED"; } catch (e) { out[k] = (e as Error).name; }
      }
      for (const path of ["/api/users", "/api/auth/me", "/api/tokens", "/api/plugins", "/api/mcp"]) {
        try { await ctx.api.get(path); out[path] = "ALLOWED"; } catch (e) { out[path] = String((e as Error).message).slice(0, 60); }
      }
      return JSON.stringify(out);
    });
    ctx.actions.on("store", async (run) => {
      await ctx.storage.set("count", ((await ctx.storage.get<number>("count")) ?? 0) + 1);
      await ctx.storage.set("here", { project: run.project!.codename }, { project: run.project!.id });
      return JSON.stringify({ count: await ctx.storage.get("count"), here: await ctx.storage.get("here", { project: run.project!.id }),
                              keys: await ctx.storage.list(), greeting: ctx.settings.greeting, key: ctx.settings.key ?? null });
    });
    ctx.actions.on("whoami", async (run) => {
      const projects = await ctx.api.get<{ id: string }[]>("/api/projects");
      console.log("listing", projects.length, "projects");
      return JSON.stringify({ user: run.user?.username, task: run.task?.title ?? null, projects: projects.map((p) => p.id) });
    });
    ctx.actions.on("echo", async (run) => (await run.file!.text()).toUpperCase());
  },
});
"""


def probe(n: str, version="1.0.0", perms="projects:read", main=PROBE_MAIN) -> bytes:
    return zip_files({"castor-plugin.yaml": PROBE_MANIFEST.format(n=n, version=version, perms=perms), "main.ts": main})


def test_manifests_and_packages_are_checked(client, auth):
    assert preview(client, auth, b"not a zip").json()["detail"] == "That isn't a .zip file"
    assert "No castor-plugin.yaml" in preview(client, auth, zip_files({"main.ts": ""})).json()["detail"]
    bad = preview(client, auth, zip_files({"castor-plugin.yaml": "id: X\nname: x\nversion: 1\n", "main.ts": ""})).json()["detail"]
    assert "id:" in bad and "version:" in bad
    escape = zip_files({"castor-plugin.yaml": PROBE_MANIFEST.format(n="zz", version="1.0.0", perms=""), "main.ts": "", "../evil.ts": ""})
    assert "outside the plugin" in preview(client, auth, escape).json()["detail"]
    admin_scope = preview(client, auth, probe("adm", perms="admin")).json()["detail"]
    assert "plugins can't have it" in admin_scope

    # A manifest asking for what this Castor can't do yet: previewed with its problems, not installed.
    later = zip_files({"castor-plugin.yaml": PROBE_MANIFEST.format(n="later", version="1.0.0", perms="")
                       + "network: [api.shodan.io]\nruntime: unrestricted\n", "main.ts": PROBE_MAIN})
    p = preview(client, auth, later).json()
    assert any("unrestricted" in x for x in p["problems"]) and any(x.startswith("network:") for x in p["problems"])
    r = client.post("/api/plugins", json={"token": p["token"], "approve": True}, headers=auth)
    assert r.status_code == 422 and "unrestricted" in r.json()["detail"]
    # Approving is required, and only admins see any of it.
    ok = preview(client, auth, probe("appr")).json()
    assert client.post("/api/plugins", json={"token": ok["token"]}, headers=auth).status_code == 422
    name = f"plug-{uuid.uuid4().hex[:6]}"
    client.post("/api/users", json={"username": name, "role": "operator", "password": "correct horse"}, headers=auth)
    token = client.post("/api/auth/login", json={"username": name, "password": "correct horse"}).json()["token"]
    client.cookies.clear()
    op = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/plugins", headers=op).status_code == 403
    assert preview(client, op, probe("op")).status_code == 403


def test_the_sandbox_and_the_plugins_account(client, auth):
    mine, other = project(client, auth), project(client, auth)
    n = uuid.uuid4().hex[:6]
    plugin = install(client, auth, probe(n), project_ids=[mine["id"]], secrets={"key": "s3cret"})
    pid = plugin["id"]
    assert plugin["account"]["username"] == f"plugin-{pid}" and plugin["settings"] == {"greeting": "hello"}
    assert plugin["secrets_set"] == ["key"] and "s3cret" not in str(plugin)
    running(client, auth, pid)

    # Nothing outside the sandbox: files, network, environment, programs, and people's own endpoints.
    out = run(client, auth, pid, "escape", mine["id"])
    assert out["status"] == "succeeded", out
    got = __import__("json").loads(out["message"])
    for k in ("read", "write", "env", "net", "run"):
        assert got[k] == "NotCapable", (k, got)
    assert got["imp"] != "ALLOWED"
    assert "403" in got["/api/users"]  # its token has projects:read only
    for path in ("/api/auth/me", "/api/tokens", "/api/plugins", "/api/mcp"):
        assert "Plugins can't call" in got[path], (path, got)

    # It acts as its own account, sees only the projects it's enabled in, and isn't a person.
    who = __import__("json").loads(run(client, auth, pid, "whoami", mine["id"])["message"])
    assert who["user"] == "admin" and who["projects"] == [mine["id"]]
    assert all(u["username"] != f"plugin-{pid}" for u in client.get("/api/users", headers=auth).json())
    assert client.post("/api/auth/login", json={"username": f"plugin-{pid}", "password": ""}).status_code in (401, 422)
    logs = client.get(f"/api/plugins/{pid}/logs", headers=auth).json()
    assert any(entry["message"] == "listing 1 projects" for entry in logs)
    # Not enabled in the other project: its actions aren't offered or run there.
    assert all(a["plugin"]["id"] != pid for a in client.get(f"/api/projects/{other['id']}/plugin-actions", headers=auth).json())
    r = client.post(f"/api/plugins/{pid}/actions/whoami", data={"project_id": other["id"]}, headers=auth)
    assert r.status_code == 404
    assert {a["id"] for a in client.get(f"/api/projects/{mine['id']}/plugin-actions", headers=auth).json() if a["plugin"]["id"] == pid} \
        == {"escape", "store", "whoami", "echo"}

    # Storage, instance-wide and per project; settings and secrets reach it.
    first = __import__("json").loads(run(client, auth, pid, "store", mine["id"])["message"])
    second = __import__("json").loads(run(client, auth, pid, "store", mine["id"])["message"])
    assert first["count"] == 1 and second["count"] == 2 and second["here"] == {"project": mine["codename"]}
    assert second["keys"] == ["count"] and second["greeting"] == "hello" and second["key"] == "s3cret"

    # Files: given to the action, checked against what it accepts.
    assert run(client, auth, pid, "echo", mine["id"], file=("a.txt", b"shout"))["message"] == "SHOUT"
    bad = client.post(f"/api/plugins/{pid}/actions/echo", data={"project_id": mine["id"]}, files={"file": ("a.exe", b"x")}, headers=auth)
    assert bad.status_code == 422 and ".txt" in bad.json()["detail"]
    assert client.post(f"/api/plugins/{pid}/actions/echo", data={"project_id": mine["id"]}, headers=auth).status_code == 422

    # Settings change restarts it; turning it off stops actions.
    changed = client.patch(f"/api/plugins/{pid}", json={"settings": {"greeting": "hi"}, "secrets": {"key": ""}}, headers=auth).json()
    assert changed["settings"]["greeting"] == "hi" and changed["secrets_set"] == []
    running(client, auth, pid)
    got = __import__("json").loads(run(client, auth, pid, "store", mine["id"])["message"])
    assert got["greeting"] == "hi" and got["key"] is None
    client.patch(f"/api/plugins/{pid}", json={"enabled": False}, headers=auth)
    off = client.post(f"/api/plugins/{pid}/actions/store", data={"project_id": mine["id"]}, headers=auth)
    assert off.status_code == 422 and "turned off" in off.json()["detail"]

    # Removing it: gone, its account disabled, its files removed.
    assert client.delete(f"/api/plugins/{pid}", headers=auth).status_code == 204
    assert client.get(f"/api/plugins/{pid}", headers=auth).status_code == 404


def test_updates_ask_again_for_new_permissions(client, auth):
    mine = project(client, auth)
    n = uuid.uuid4().hex[:6]
    pid = install(client, auth, probe(n), project_ids=[mine["id"]])["id"]
    p = preview(client, auth, probe(n, version="1.1.0", perms="projects:read, assets:write")).json()
    assert p["existing"] == {"version": "1.0.0", "same": False} and p["new_permissions"] == {"permissions": ["assets:write"]}
    updated = client.post("/api/plugins", json={"token": p["token"], "approve": True}, headers=auth).json()
    assert updated["version"] == "1.1.0" and updated["granted"]["permissions"] == ["assets:write", "projects:read"]
    assert updated["project_ids"] == [mine["id"]]  # kept
    same = preview(client, auth, probe(n, version="1.1.0", perms="projects:read, assets:write")).json()
    assert same["existing"]["same"]
    client.delete(f"/api/plugins/{pid}", headers=auth)


def test_a_plugin_that_imports_from_outside_is_refused(client, auth):
    n = uuid.uuid4().hex[:6]
    sneaky = PROBE_MAIN.replace('import { definePlugin } from "@castor/plugin";',
                                'import { definePlugin } from "@castor/plugin";\nimport secrets from "../../.cache/probe-' + n + '/import_map.json" with { type: "json" };')
    pid = install(client, auth, probe(n, main=sneaky), all_projects=True)["id"]

    def failed():
        p = client.get(f"/api/plugins/{pid}", headers=auth).json()
        return p if p["status"] == "error" else None
    p = wait(failed, "the start to be refused")
    assert "outside its own folder" in p["status_message"], p["status_message"]
    client.delete(f"/api/plugins/{pid}", headers=auth)


def test_nessus_import(client, auth):
    mine = project(client, auth)
    card = client.post(f"/api/projects/{mine['id']}/tasks", json={"title": "External scan"}, headers=auth).json()
    plugin = install(client, auth, zip_folder(NESSUS), project_ids=[mine["id"]])
    assert plugin["id"] == "nessus-import" and plugin["granted"]["permissions"] == ["assets:write", "projects:read"]
    running(client, auth, "nessus-import")

    out = run(client, auth, "nessus-import", "import", mine["id"],
              file=("scan.nessus", (NESSUS / "samples" / "cerno-demo-expanded.nessus").read_bytes()), task_id=card["id"])
    assert out["status"] == "succeeded", out
    assert out["message"].startswith("Imported 42 hosts") and "125 findings" not in out["message"]
    assets = client.get(f"/api/projects/{mine['id']}/assets", params={"limit": 1000}, headers=auth).json()
    assert assets["counts"]["host"] == 42 and assets["counts"]["vulnerability"] > 50
    signing = client.get(f"/api/projects/{mine['id']}/assets", params={"type": "host", "where": "smb_signing:false"}, headers=auth).json()
    assert signing["total"] == 11  # the hosts with "SMB Signing not required"
    links = client.get(f"/api/projects/{mine['id']}/asset-links", params={"kind": "affects"}, headers=auth).json()
    assert links["total"] == assets["counts"]["vulnerability"]
    web = client.get(f"/api/projects/{mine['id']}/assets", params={"type": "host", "where": "web01"}, headers=auth).json()["items"][0]
    detail = client.get(f"/api/assets/{web['id']}", headers=auth).json()
    assert detail["fields"]["hostname"] == "web01.demo.example"
    assert detail["history"][0]["source"]["kind"] == "plugin" and detail["history"][0]["source"]["task"]["id"] == card["id"]
    feed = client.get(f"/api/dashboard/feed?project_id={mine['id']}", headers=auth).json()["items"]
    assert any(i["type"] == "plugin.action.succeeded" and "Import .nessus file scan.nessus" in i["message"] for i in feed)

    # Again: the same things, no duplicates. The real export adds one detailed host.
    again = run(client, auth, "nessus-import", "import", mine["id"], file=("scan.nessus", (NESSUS / "samples" / "cerno-demo-expanded.nessus").read_bytes()))
    assert again["status"] == "succeeded"
    assert client.get(f"/api/projects/{mine['id']}/assets", params={"type": "host"}, headers=auth).json()["total"] == 42
    real = run(client, auth, "nessus-import", "import", mine["id"], file=("one.nessus", (NESSUS / "samples" / "ruby-nessus-example-v3.nessus").read_bytes()))
    assert real["message"].startswith("Imported 1 host") and "37 findings" in real["message"], real["message"]

    wrong = run(client, auth, "nessus-import", "import", mine["id"], file=("x.nessus", b"<html>not a scan</html>"))
    assert wrong["status"] == "failed" and "isn't a .nessus v2 file" in wrong["message"]
    client.delete("/api/plugins/nessus-import", headers=auth)


def test_plugins_through_mcp(client, auth):
    import base64
    import json

    mine = project(client, auth)
    n = uuid.uuid4().hex[:6]
    pid = install(client, auth, probe(n), project_ids=[mine["id"]])["id"]
    running(client, auth, pid)
    token = client.post("/api/tokens", json={"name": "assistant", "scopes": ["projects:write", "assets:write"]}, headers=auth).json()["token"]
    bearer = {"Authorization": f"Bearer {token}"}

    def call(tool, **args):
        r = client.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}},
                        headers=bearer).json()["result"]
        assert not r["isError"], r
        text = r["content"][0]["text"]
        from castor_server import mcp

        return json.loads(text.removeprefix(mcp.UNTRUSTED))

    names = {t["name"] for t in client.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=bearer).json()["result"]["tools"]}
    assert {"list_plugin_actions", "run_plugin_action", "get_plugin_run", "bulk_assets"} <= names
    assert "list_plugins" not in names  # admins only
    actions = call("list_plugin_actions", project_id=mine["id"])
    assert any(a["id"] == "echo" and a["file"]["accept"] == [".txt"] for a in actions)
    started = call("run_plugin_action", plugin_id=pid, action="echo", project_id=mine["id"], filename="a.txt",
                   content_base64=base64.b64encode(b"quiet").decode())
    done = wait(lambda: (r := call("get_plugin_run", run_id=started["id"]))["status"] != "running" and r, "the MCP run")
    assert done["message"] == "QUIET"
    got = call("bulk_assets", project_id=mine["id"], assets=[{"ref": "h", "type": "host", "fields": {"ip": "10.70.0.1"}},
                                                          {"ref": "c", "type": "credential", "fields": {"username": "x"}}],
               links=[{"from": "c", "to": "h", "kind": "valid_on"}, {"from": "h", "to": "c", "kind": "valid_on"}])
    assert got["created"] == 2 and got["links"] == 1 and "doesn't go host → credential" in got["skipped"][0]["reason"]
    client.delete(f"/api/plugins/{pid}", headers=auth)
