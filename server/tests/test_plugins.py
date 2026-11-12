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
                       + "hooks: [{kind: asset-mapper}]\nruntime: unrestricted\n", "main.ts": PROBE_MAIN})
    p = preview(client, auth, later).json()
    assert any("unrestricted" in x for x in p["problems"]) and any(x.startswith("hooks:") for x in p["problems"])
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


# ---------- phase B: events, declared network, rate limits ----------

SHODAN = ROOT / "plugins" / "shodan-enrich"


class FakeShodan:
    """A local stand-in for api.shodan.io: the sample for 203.0.113.10, 404 for anything else."""

    def __init__(self) -> None:
        import http.server
        import json
        import threading

        sample = (SHODAN / "samples" / "host.json").read_bytes()
        seen: list[str] = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                from urllib.parse import parse_qs, urlsplit

                parts = urlsplit(self.path)
                seen.append(parts.path)
                key = parse_qs(parts.query).get("key", [""])[0]
                if key != "good-key":
                    self.send_response(401)
                    body = json.dumps({"error": "bad key"}).encode()
                elif parts.path == "/shodan/host/203.0.113.10":
                    self.send_response(200)
                    body = sample
                else:
                    self.send_response(404)
                    body = json.dumps({"error": "No information available for that IP."}).encode()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.seen = seen
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()


def shodan_zip(port: int, extra: str = "", version: str = "1.0.0") -> bytes:
    """The real plugin, its network pointed at the fake (exactly what an admin would approve)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path in sorted(SHODAN.iterdir()):
            if path.is_file() and not path.name.endswith("_test.ts") and path.name != "deno.json":
                text = path.read_text()
                if path.name == "castor-plugin.yaml":
                    text = text.replace("  - api.shodan.io", f"  - 127.0.0.1:{port}{extra}").replace("version: 1.0.0", f"version: {version}")
                zf.writestr(path.name, text)
    return buf.getvalue()


def test_shodan_enrichment_on_new_hosts(client, auth):
    fake = FakeShodan()
    try:
        p = project(client, auth)
        client.put(f"/api/projects/{p['id']}/scope", json={"include": ["203.0.113.0/24", "10.0.0.0/8"]}, headers=auth)
        preview_out = preview(client, auth, shodan_zip(fake.port)).json()
        assert preview_out["problems"] == [] and preview_out["manifest"]["network"] == [f"127.0.0.1:{fake.port}"]
        assert preview_out["manifest"]["events"] == ["asset.created"]
        plugin = install(client, auth, shodan_zip(fake.port), project_ids=[p["id"]],
                         settings={"api_url": f"http://127.0.0.1:{fake.port}"}, secrets={"api_key": "good-key"})
        assert plugin["granted"]["network"] == [f"127.0.0.1:{fake.port}"] and plugin["granted"]["events"] == ["asset.created"]
        running(client, auth, "shodan-enrich")

        # A private host and an out-of-scope one aren't looked up; the in-scope public one is.
        for ip in ("10.0.0.5", "198.51.100.7", "203.0.113.10"):
            client.post(f"/api/projects/{p['id']}/assets", json={"type": "host", "fields": {"ip": ip}}, headers=auth)

        def enriched():
            items = client.get(f"/api/projects/{p['id']}/assets", params={"type": "service"}, headers=auth).json()["items"]
            return items if len(items) == 3 else None
        services = wait(enriched, "Shodan's services")
        assert {s["fields"]["port"] for s in services} == {22, 443, 8080}
        assert fake.seen == ["/shodan/host/203.0.113.10"]
        vulns = client.get(f"/api/projects/{p['id']}/assets", params={"type": "vulnerability"}, headers=auth).json()["items"]
        assert {v["fields"]["id"] for v in vulns} == {"CVE-2023-38408", "CVE-2021-41773"}
        assert next(v for v in vulns if v["fields"]["id"] == "CVE-2023-38408")["fields"]["severity"] == "critical"
        host = client.get(f"/api/projects/{p['id']}/assets", params={"type": "host", "where": "203.0.113.10"}, headers=auth).json()["items"][0]
        assert host["fields"]["org"] == "Example Hosting" and host["fields"]["os"] == "Ubuntu"
        detail = client.get(f"/api/assets/{host['id']}", headers=auth).json()
        assert any(h["source"]["kind"] == "plugin" and h["source"]["parser"] == "shodan-enrich" for h in detail["history"])

        # The action, on demand (even though it was just looked up); a bad key says so.
        out = run(client, auth, "shodan-enrich", "lookup", p["id"], asset_id=host["id"])
        assert out["status"] == "succeeded" and out["message"] == "203.0.113.10: 3 services, 2 known CVEs"
        client.patch("/api/plugins/shodan-enrich", json={"secrets": {"api_key": "bad"}}, headers=auth)
        running(client, auth, "shodan-enrich")
        out = run(client, auth, "shodan-enrich", "lookup", p["id"], asset_id=host["id"])
        assert out["status"] == "failed" and "refused the API key" in out["message"]

        # Its network is only what it declared: pointing it elsewhere fails in the sandbox.
        client.patch("/api/plugins/shodan-enrich", json={"settings": {"api_url": f"http://127.0.0.1:{fake.port + 1}"}, "secrets": {"api_key": "good-key"}},
                     headers=auth)
        running(client, auth, "shodan-enrich")
        out = run(client, auth, "shodan-enrich", "lookup", p["id"], asset_id=host["id"])
        assert out["status"] == "failed" and ("NotCapable" in out["message"] or "net access" in out["message"]), out["message"]

        # An update that reaches somewhere new needs approving again.
        newer = shodan_zip(fake.port, extra="\n  - api.censys.io", version="1.1.0")
        assert preview(client, auth, newer).json()["new_permissions"] == {"network": ["api.censys.io"]}
    finally:
        client.delete("/api/plugins/shodan-enrich", headers=auth)
        fake.close()


EVENTS_MAIN = """
import { definePlugin } from "@castor/plugin";
definePlugin({
  activate(ctx) {
    for (const name of ["asset.created", "asset.linked", "task.moved", "comment.created"] as const) {
      ctx.events.on(name, async (e) => {
        const seen = (await ctx.storage.get<string[]>("seen", { project: e.project.id })) ?? [];
        const what = name === "asset.created" ? (e.data as any).asset.label : name === "asset.linked" ? (e.data as any).link.forward
          : name === "task.moved" ? (e.data as any).to.name : (e.data as any).comment.body;
        await ctx.storage.set("seen", [...seen, `${name}:${what}`], { project: e.project.id });
        if (name === "asset.created" && (e.data as any).asset.label === "10.80.0.1") {
          // Its own change: never told back to it.
          await ctx.api.post(`/api/projects/${e.project.id}/assets`, { type: "host", fields: { ip: "10.80.0.99" } });
        }
      });
    }
    ctx.actions.on("seen", async (run) => JSON.stringify(await ctx.storage.get("seen", { project: run.project!.id })));
    ctx.actions.on("burst", async (run) => {
      try { for (let i = 0; i < 10; i++) await ctx.api.get(`/api/projects/${run.project!.id}`); return "ok"; }
      catch (e) { return (e as Error).message; }
    });
  },
});
"""


def test_events_reach_the_plugins_that_asked(client, auth):
    from castor_server import plugins as P

    p, other = project(client, auth), project(client, auth)
    n = uuid.uuid4().hex[:6]
    manifest = f"""
id: events-{n}
name: Events
version: 1.0.0
main: main.ts
permissions: [projects:read, assets:write]
events: [asset.created, asset.linked, task.moved, comment.created]
actions:
  - {{id: seen, label: Seen}}
  - {{id: burst, label: Burst}}
"""
    pid = install(client, auth, zip_files({"castor-plugin.yaml": manifest, "main.ts": EVENTS_MAIN}), project_ids=[p["id"]])["id"]
    running(client, auth, pid)
    assert "not an event" in preview(client, auth, zip_files({"castor-plugin.yaml": manifest.replace("comment.created", "job.started"),
                                                               "main.ts": ""})).json()["detail"]

    host = client.post(f"/api/projects/{p['id']}/assets", json={"type": "host", "fields": {"ip": "10.80.0.1"}}, headers=auth).json()
    cred = client.post(f"/api/projects/{p['id']}/assets", json={"type": "credential", "fields": {"username": "x"}}, headers=auth).json()
    client.post(f"/api/assets/{cred['id']}/links", json={"kind": "valid_on", "to_id": host["id"]}, headers=auth)
    board = client.get(f"/api/projects/{p['id']}/board", headers=auth).json()
    card = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Moving"}, headers=auth).json()
    done = next(c for c in board["columns"] if c["role"] == "done")
    client.post(f"/api/tasks/{card['id']}/move", json={"column_id": done["id"], "position": 1}, headers=auth)
    client.post(f"/api/tasks/{card['id']}/comments", json={"body": "hello plugins"}, headers=auth)
    client.post(f"/api/projects/{other['id']}/assets", json={"type": "host", "fields": {"ip": "10.80.0.2"}}, headers=auth)  # not its project

    def seen():
        got = __import__("json").loads(run(client, auth, pid, "seen", p["id"])["message"] or "null") or []
        return got if len(got) >= 5 else None
    got = wait(seen, "the events")
    assert sorted(got) == sorted(["asset.created:10.80.0.1", "asset.created:x (password)", "asset.linked:works on",
                                  f"task.moved:{done['name']}", "comment.created:hello plugins"]), got
    # The host it added itself isn't told back to it.
    assert client.get(f"/api/projects/{p['id']}/assets", params={"where": "10.80.0.99"}, headers=auth).json()["total"] == 1
    assert not any("10.80.0.99" in g for g in got)

    # Rate limit on calls to Castor.
    was = P.API_PER_MINUTE
    P.API_PER_MINUTE = 3
    try:
        P._calls.clear()
        assert "calls to Castor in a minute" in run(client, auth, pid, "burst", p["id"])["message"]
    finally:
        P.API_PER_MINUTE = was
        P._calls.clear()
    client.delete(f"/api/plugins/{pid}", headers=auth)


def test_settings_each_person_sets(client, auth):
    fake = FakeShodan()
    try:
        p = project(client, auth)
        client.put(f"/api/projects/{p['id']}/scope", json={"include": ["203.0.113.0/24"]}, headers=auth)
        never = preview(client, auth, shodan_zip(fake.port)).json()
        bad = client.post("/api/plugins", json={"token": never["token"], "approve": True, "project_ids": [p["id"]], "per_user": ["api_url"]},
                          headers=auth)
        assert bad.status_code == 422 and "one value for everyone" in bad.json()["detail"]
        plugin = install(client, auth, shodan_zip(fake.port), project_ids=[p["id"]], per_user=["api_key"],
                         settings={"api_url": f"http://127.0.0.1:{fake.port}"})
        assert plugin["per_user"] == ["api_key"] and plugin["missing_settings"] == []  # each person's: it runs without one
        running(client, auth, "shodan-enrich")
        # A shared key can't be set for a per-person setting.
        r = client.patch("/api/plugins/shodan-enrich", json={"secrets": {"api_key": "good-key"}}, headers=auth)
        assert r.status_code == 422

        # Someone in the project without a key of their own: told to set one; then it works.
        name = f"shodan-{uuid.uuid4().hex[:6]}"
        member = client.post("/api/users", json={"username": name, "role": "operator", "password": "correct horse"}, headers=auth).json()
        client.put(f"/api/projects/{p['id']}/members", json=[member["id"]], headers=auth)
        session = client.post("/api/auth/login", json={"username": name, "password": "correct horse"}).json()["token"]
        client.cookies.clear()
        mh = {"Authorization": f"Bearer {session}"}
        mine = client.get("/api/auth/me/plugins", headers=mh).json()
        assert [x["id"] for x in mine] == ["shodan-enrich"] and mine[0]["missing"] == ["Shodan API key"]
        assert [s["name"] for s in mine[0]["settings_spec"]] == ["api_key"]
        host = client.post(f"/api/projects/{p['id']}/assets", json={"type": "host", "fields": {"ip": "203.0.113.10"}}, headers=mh).json()
        r = client.post("/api/plugins/shodan-enrich/actions/lookup", data={"project_id": p["id"], "asset_id": host["id"]}, headers=mh)
        assert r.status_code == 422 and "needs your own Shodan API key" in r.json()["detail"]
        assert fake.seen == []  # the event for the host they added found no key of theirs
        logs = wait(lambda: [entry for entry in client.get("/api/plugins/shodan-enrich/logs", headers=auth).json()
                             if "no Shodan key" in entry["message"]], "the plugin to skip it")
        assert name in logs[0]["message"]

        saved = client.put("/api/auth/me/plugins/shodan-enrich", json={"secrets": {"api_key": "good-key"}}, headers=mh).json()
        assert saved["missing"] == [] and saved["secrets_set"] == ["api_key"] and "good-key" not in str(saved)
        assert client.put("/api/auth/me/plugins/shodan-enrich", json={"settings": {"api_url": "http://evil"}}, headers=mh).status_code == 422
        out = run(client, auth=mh, plugin_id="shodan-enrich", action="lookup", project_id=p["id"], asset_id=host["id"])
        assert out["status"] == "succeeded" and out["message"].startswith("203.0.113.10: 3 services"), out
        # Their key is theirs: the admin still has none.
        r = client.post("/api/plugins/shodan-enrich/actions/lookup", data={"project_id": p["id"], "asset_id": host["id"]}, headers=auth)
        assert r.status_code == 422
        assert client.get("/api/plugins/shodan-enrich", headers=auth).json()["people_set"] == 1

        # A new host they add is looked up with their key.
        client.post(f"/api/projects/{p['id']}/assets", json={"type": "host", "fields": {"ip": "203.0.113.11"}}, headers=mh)
        wait(lambda: "/shodan/host/203.0.113.11" in fake.seen, "their host to be looked up")

        # Personal API tokens and plugins can't read anyone's own settings.
        pat = client.post("/api/tokens", json={"name": "t", "scopes": ["projects:read"]}, headers=mh).json()["token"]
        assert client.get("/api/auth/me/plugins", headers={"Authorization": f"Bearer {pat}"}).status_code == 403
    finally:
        client.delete("/api/plugins/shodan-enrich", headers=auth)
        fake.close()


def test_plugin_icons(client, auth):
    data = zip_folder(NESSUS)
    p = preview(client, auth, data).json()
    assert p["icon"].startswith("data:image/svg+xml;base64,")
    plugin = install(client, auth, data, all_projects=True)
    assert plugin["icon_url"].startswith("/api/plugins/nessus-import/icon?v=")
    r = client.get(plugin["icon_url"], headers=auth)
    assert r.status_code == 200 and r.headers["content-type"] == "image/svg+xml" and b"<svg" in r.content
    assert "sandbox" in r.headers["content-security-policy"] and r.headers["x-content-type-options"] == "nosniff"
    client.delete("/api/plugins/nessus-import", headers=auth)
    evil = zip_files({"castor-plugin.yaml": PROBE_MANIFEST.format(n="icon", version="1.0.0", perms="") + "icon: icon.svg\n",
                      "main.ts": PROBE_MAIN, "icon.svg": '<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>'})
    assert "can only draw" in preview(client, auth, evil).json()["detail"]
    missing = zip_files({"castor-plugin.yaml": PROBE_MANIFEST.format(n="icon", version="1.0.0", perms="") + "icon: logo.png\n",
                         "main.ts": PROBE_MAIN})
    assert "isn't in the .zip" in preview(client, auth, missing).json()["detail"]


PANEL_MANIFEST = """
id: panel-{n}
name: Panel probe
version: 1.0.0
publisher: tests
main: main.ts
permissions: [projects:read, assets:write]
ui:
  - {{id: page, slot: project.page, title: Overview, path: ui/page.html}}
  - {{id: side, slot: asset.panel, title: Side, path: ui/page.html, asset_types: [host]}}
"""
PANEL_MAIN = """
import { definePlugin } from "@castor/plugin";
definePlugin({
  activate(ctx) {
    ctx.ui.on("hello", (call) => ({ to: call.user.username, writes: call.user.can_write, asset: call.asset?.label ?? null, args: call.args }),
              { readOnly: true });
    ctx.ui.on("change", async (call) => {
      await ctx.storage.set("changed", true, { project: call.project.id });
      return "changed";
    });
  },
});
"""


def test_plugin_panels(client, auth):
    n = uuid.uuid4().hex[:6]
    pid = f"panel-{n}"
    files = {"castor-plugin.yaml": PANEL_MANIFEST.format(n=n), "main.ts": PANEL_MAIN,
             "ui/page.html": "<!doctype html><html><head><title>x</title></head><body>hi<script src='page.js'></script></body></html>",
             "ui/page.js": "document.body.append('!')", "notes.ts": "export {}"}
    missing = {k: v for k, v in files.items() if k != "ui/page.html"}
    assert "ui/page.html isn't in the .zip" in preview(client, auth, zip_files(missing)).json()["detail"]

    p = project(client, auth)
    other = project(client, auth)
    install(client, auth, zip_files(files), project_ids=[p["id"]])
    try:
        running(client, auth, pid)
        panels = client.get(f"/api/projects/{p['id']}/plugin-panels", headers=auth).json()
        mine = [x for x in panels if x["plugin"]["id"] == pid]
        assert [(x["id"], x["slot"]) for x in mine] == [("page", "project.page"), ("side", "asset.panel")]
        assert mine[1]["asset_types"] == ["host"]
        assert not [x for x in client.get(f"/api/projects/{other['id']}/plugin-panels", headers=auth).json() if x["plugin"]["id"] == pid]

        # The page: sandboxed, no network, the SDK inline; only the plugin's own static files.
        client.cookies.clear()
        url = mine[0]["url"]
        page = client.get(url)  # no cookie, no token: the signed address is enough
        assert page.status_code == 200, page.text
        csp = page.headers["content-security-policy"]
        assert csp.startswith("sandbox allow-scripts;") and "connect-src 'none'" in csp and "frame-ancestors 'self'" in csp
        # A sandboxed page's 'self' matches nothing: its server is named (the host asked, or a proxy's X-Forwarded-Host).
        assert "script-src 'self' https://testserver http://testserver 'unsafe-inline'" in csp
        fwd = client.get(url, headers={"X-Forwarded-Host": "castor.example:8443", "Host": "bad host"}).headers["content-security-policy"]
        assert "https://castor.example:8443" in fwd and "bad host" not in fwd
        assert page.headers["x-content-type-options"] == "nosniff"
        assert page.text.index("window.Castor") < page.text.index("<title>")
        base = url.rsplit("/", 2)[0]
        assert client.get(f"{base}/ui/page.js").headers["content-type"].startswith("text/javascript")
        assert client.get(f"{base}/main.ts").status_code == 404  # not a page's kind of file
        assert client.get(f"{base}/castor-plugin.yaml").status_code == 404
        assert client.get(f"{base}/..%2F..%2Fsecret.html").status_code == 404
        wrong = url.replace(url.split("/")[4], "0" * 32)
        assert client.get(wrong).status_code == 404

        # Calls: its handlers, as the person; a viewer only reaches read-only ones.
        host = client.post(f"/api/projects/{p['id']}/assets", json={"type": "host", "fields": {"ip": "10.9.9.9"}}, headers=auth).json()
        call = f"/api/plugins/{pid}/panels/side/call"
        r = client.post(call, json={"project_id": p["id"], "asset_id": host["id"], "kind": "call", "name": "hello", "args": {"x": 1}}, headers=auth)
        assert r.status_code == 200, r.text
        assert r.json()["result"] == {"to": "admin", "writes": True, "asset": "10.9.9.9", "args": {"x": 1}}
        assert client.post(call, json={"project_id": p["id"], "kind": "call", "name": "change"}, headers=auth).json()["result"] == "changed"
        assert client.post(call, json={"project_id": p["id"], "kind": "call", "name": "nope"}, headers=auth).status_code == 404
        # Its API, as the plugin: reads for anyone who sees the project; refused paths stay refused.
        r = client.post(call, json={"project_id": p["id"], "kind": "api", "method": "GET", "path": f"/api/projects/{p['id']}/assets"}, headers=auth)
        assert r.json()["status"] == 200 and r.json()["result"]["total"] == 1
        assert client.post(call, json={"project_id": p["id"], "kind": "api", "path": "/api/auth/me"}, headers=auth).status_code == 422
        # Not this project's asset, or a project the plugin isn't in.
        oh = client.post(f"/api/projects/{other['id']}/assets", json={"type": "host", "fields": {"ip": "10.9.9.8"}}, headers=auth).json()
        assert client.post(call, json={"project_id": p["id"], "asset_id": oh["id"], "kind": "call", "name": "hello"}, headers=auth).status_code == 404
        assert client.post(call, json={"project_id": other["id"], "kind": "call", "name": "hello"}, headers=auth).status_code == 404

        name = f"pview-{uuid.uuid4().hex[:6]}"
        viewer = client.post("/api/users", json={"username": name, "role": "viewer", "password": "correct horse"}, headers=auth).json()
        client.put(f"/api/projects/{p['id']}/members", json=[viewer["id"]], headers=auth)
        token = client.post("/api/auth/login", json={"username": name, "password": "correct horse"}).json()["token"]
        client.cookies.clear()
        vh = {"Authorization": f"Bearer {token}"}
        r = client.post(call, json={"project_id": p["id"], "kind": "call", "name": "hello"}, headers=vh)
        assert r.status_code == 200 and r.json()["result"]["writes"] is False
        assert client.post(call, json={"project_id": p["id"], "kind": "call", "name": "change"}, headers=vh).status_code == 403
        assert client.post(call, json={"project_id": p["id"], "kind": "api", "method": "POST", "path": f"/api/projects/{p['id']}/assets",
                                       "body": {"type": "host", "fields": {"ip": "10.9.9.7"}}}, headers=vh).status_code == 403
        assert client.post(f"/api/plugins/{pid}/panels/page/call", json={"project_id": other["id"], "kind": "call", "name": "hello"},
                           headers=vh).status_code == 404

        # Turned off: no pages, no calls.
        client.patch(f"/api/plugins/{pid}", json={"enabled": False}, headers=auth)
        assert client.get(url).status_code == 404
    finally:
        client.delete(f"/api/plugins/{pid}", headers=auth)


BLOODHOUND = ROOT / "plugins" / "bloodhound"


def test_bloodhound_bridge(client, auth):
    p = project(client, auth)
    install(client, auth, zip_folder(BLOODHOUND), project_ids=[p["id"]])
    try:
        running(client, auth, "bloodhound")
        call = "/api/plugins/bloodhound/panels/paths/call"
        assert client.post(call, json={"project_id": p["id"], "kind": "call", "name": "summary"}, headers=auth).json()["result"] == {"imported": False}
        out = run(client, auth, "bloodhound", "import", p["id"], file=("corp.zip", (BLOODHOUND / "samples" / "corp-sharphound.zip").read_bytes()))
        assert out["status"] == "succeeded", out
        assert "7 users, 3 computers, 2 domains" in out["message"] and "5 enabled users have a path" in out["message"], out["message"]

        assets = client.get(f"/api/projects/{p['id']}/assets", params={"limit": 100}, headers=auth).json()
        assert assets["counts"]["user"] == 7 and assets["counts"]["host"] == 3 and assets["counts"]["domain"] == 2
        bob = client.get(f"/api/projects/{p['id']}/assets", params={"type": "user", "where": "username:bob"}, headers=auth).json()["items"][0]
        assert bob["label"] == "corp\\bob"
        links = client.get(f"/api/projects/{p['id']}/asset-links", params={"limit": 200}, headers=auth).json()["items"]
        kinds = {link["kind"] for link in links}
        assert {"in_domain", "joined_to", "dc_of", "session_on", "admin_on", "trusts"} <= kinds, kinds

        summary = client.post(call, json={"project_id": p["id"], "kind": "call", "name": "summary"}, headers=auth).json()["result"]
        assert summary["counts"]["groups"] == 4 and summary["admins"][0]["members"] == ["CAROL.DA@CORP.LOCAL"]
        assert summary["reach"][0]["name"] == "BACKUP_SVC@CORP.LOCAL" and summary["reach"][0]["hops"] == 1
        side = "/api/plugins/bloodhound/panels/asset-path/call"
        path = client.post(side, json={"project_id": p["id"], "asset_id": bob["id"], "kind": "call", "name": "path"}, headers=auth).json()["result"]
        assert [s["rel"] for s in path["path"][:-1]] == ["MemberOf", "AdminTo", "HasSession", "MemberOf"]
        found = client.post(call, json={"project_id": p["id"], "kind": "call", "name": "asset", "args": {"sid": path["path"][2]["sid"]}},
                            headers=auth).json()["result"]
        assert client.get(f"/api/assets/{found}", headers=auth).json()["label"] == "srv01.corp.local"

        # Again: nothing doubles.
        run(client, auth, "bloodhound", "import", p["id"], file=("corp.zip", (BLOODHOUND / "samples" / "corp-sharphound.zip").read_bytes()))
        assert client.get(f"/api/projects/{p['id']}/assets", params={"type": "user"}, headers=auth).json()["total"] == 7
        assert run(client, auth, "bloodhound", "forget", p["id"])["message"].startswith("Forgot")
        assert client.post(call, json={"project_id": p["id"], "kind": "call", "name": "summary"}, headers=auth).json()["result"] == {"imported": False}
    finally:
        client.delete("/api/plugins/bloodhound", headers=auth)
