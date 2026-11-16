"""MCP tools from plugins (manifest `mcp_tools`, `ctx.mcp.on`): approved at install and again when an
update adds one, listed by Castor's MCP server as `<plugin>__<tool>` in the plugin's projects, and
called with the caller's own checks (read-only tools need read access, others write)."""

from __future__ import annotations

import json
import uuid

from test_plugins import install, preview, project, running, zip_files  # also skips without Deno

MANIFEST = """
id: tools-{n}
name: Tooly
version: {version}
publisher: tests
main: main.ts
mcp_tools:
  - name: lookup
    description: Look a hostname up in the plugin's notes.
    read_only: true
    input_schema:
      type: object
      properties: {{host: {{type: string, description: A hostname}}}}
      required: [host]
  - name: remember
    description: Remember a note about a host.
    input_schema:
      type: object
      properties: {{host: {{type: string}}, note: {{type: string}}}}
      required: [host, note]
{extra}"""

MAIN = """
import { CastorError, definePlugin } from "@castor/plugin";
definePlugin({
  activate(ctx) {
    ctx.mcp.on("lookup", async (call) => {
      const notes = (await ctx.storage.get<Record<string, string>>("notes", { project: call.project.id })) ?? {};
      return { host: call.args.host, note: notes[String(call.args.host)] ?? null, by: call.user.username, project: call.project.codename };
    });
    ctx.mcp.on("remember", async (call) => {
      if (!call.args.note) throw new CastorError("note: say something");
      const notes = (await ctx.storage.get<Record<string, string>>("notes", { project: call.project.id })) ?? {};
      notes[String(call.args.host)] = String(call.args.note);
      await ctx.storage.set("notes", notes, { project: call.project.id });
      return `Noted ${call.args.host}`;
    });
  },
});
"""


def _package(n: str, version: str = "1.0.0", extra: str = "") -> bytes:
    return zip_files({"castor-plugin.yaml": MANIFEST.format(n=n, version=version, extra=extra), "main.ts": MAIN})


def _rpc(client, headers, method, params=None):
    r = client.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["result"]


def _call(client, headers, name, **args):
    return _rpc(client, headers, "tools/call", {"name": name, "arguments": args})


def _token(client, auth, scopes):
    r = client.post("/api/tokens", json={"name": "assistant", "scopes": scopes}, headers=auth)
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_plugins_add_mcp_tools(client, auth):
    n = uuid.uuid4().hex[:6]
    pid = f"tools-{n}"
    mine, other = project(client, auth), project(client, auth)
    p = preview(client, auth, _package(n)).json()
    assert [t["name"] for t in p["manifest"]["mcp_tools"]] == ["lookup", "remember"]
    plugin = install(client, auth, _package(n), project_ids=[mine["id"]])
    assert plugin["granted"]["mcp_tools"] == ["lookup", "remember"]
    running(client, auth, pid)

    tools = {t["name"]: t for t in _rpc(client, auth, "tools/list")["tools"]}
    lookup, remember = tools[f"{pid}__lookup"], tools[f"{pid}__remember"]
    assert lookup["inputSchema"]["required"] == ["project_id", "host"] and "project_id" in lookup["inputSchema"]["properties"]
    assert lookup["annotations"]["readOnlyHint"] and not remember["annotations"]["readOnlyHint"]
    assert "Tooly plugin" in lookup["description"]

    out = _call(client, auth, f"{pid}__remember", project_id=mine["id"], host="db1", note="the old one")
    assert not out["isError"] and "Noted db1" in out["content"][0]["text"]
    out = _call(client, auth, f"{pid}__lookup", project_id=mine["id"], host="db1")
    text = out["content"][0]["text"]
    assert not out["isError"] and "a plugin's answer" in text  # marked untrusted
    assert json.loads(text.split("\n", 1)[1]) == {"host": "db1", "note": "the old one", "by": "admin", "project": mine["codename"]}
    out = _call(client, auth, f"{pid}__remember", project_id=mine["id"], host="db1", note="")
    assert out["isError"] and "say something" in out["content"][0]["text"]
    out = _call(client, auth, f"{pid}__lookup", project_id=other["id"], host="db1")
    assert out["isError"] and "isn't on in this project" in out["content"][0]["text"]
    feed = client.get("/api/dashboard/feed", params={"project_id": mine["id"]}, headers=auth).json()["items"]
    assert any(i["type"] == "plugin.tool.called" and "remember" in i["message"] for i in feed)

    # A token that only reads sees (and may call) the read-only tool, not the other.
    reader = _token(client, auth, ["projects:read"])
    names = {t["name"] for t in _rpc(client, reader, "tools/list")["tools"]}
    assert f"{pid}__lookup" in names and f"{pid}__remember" not in names
    assert not _call(client, reader, f"{pid}__lookup", project_id=mine["id"], host="db1")["isError"]
    refused = client.post(f"/api/plugin-tools/{pid}/remember", json={"project_id": mine["id"], "arguments": {"host": "x", "note": "y"}},
                          headers=reader)
    assert refused.status_code == 403

    # An update adding a tool asks for it again; until it's approved the new tool isn't offered.
    extra = "  - name: forget\n    description: Forget a host.\n    input_schema: {type: object, properties: {host: {type: string}}}\n"
    up = preview(client, auth, _package(n, "1.1.0", extra)).json()
    assert up["new_permissions"] == {"mcp_tools": ["forget"]}
    client.patch(f"/api/plugins/{pid}", json={"enabled": False}, headers=auth)
    assert f"{pid}__lookup" not in {t["name"] for t in _rpc(client, auth, "tools/list")["tools"]}  # off: gone
    client.delete(f"/api/plugins/{pid}", headers=auth)
    for pr in (mine, other):
        client.delete(f"/api/projects/{pr['id']}", headers=auth)


def test_tool_names_and_schemas_are_checked(client, auth):
    bad = zip_files({"castor-plugin.yaml": MANIFEST.format(n="bad1", version="1.0.0", extra="").replace(
        "properties: {host: {type: string, description: A hostname}}", "properties: {project_id: {type: string}}"), "main.ts": MAIN})
    assert "project_id is Castor's" in preview(client, auth, bad).json()["detail"]
    twice = zip_files({"castor-plugin.yaml": MANIFEST.format(n="bad2", version="1.0.0", extra="  - name: lookup\n    description: Again.\n"),
                       "main.ts": MAIN})
    assert "each name once" in preview(client, auth, twice).json()["detail"]


def test_castor_tool_names_never_look_like_a_plugins():
    from castor_server import mcp

    assert not [t.name for t in mcp.TOOLS if "__" in t.name]  # `<plugin>__<tool>` is plugins' alone
