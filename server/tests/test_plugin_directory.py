"""Plugins, phase E (docs/PLUGINS.md): packs (library content a plugin ships) and the plugin
directory (browsing it, installing a release checked against its SHA-256, update checks), against a
local stand-in for GitHub. Pack-only plugins have no code, so none of this needs Deno."""

from __future__ import annotations

import hashlib
import io
import json
import threading
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from castor_server import plugin_directory as D
from castor_server.config import settings
from castor_server.plugins import castor_ok

TOOL = "id: pk-echo\nname: Pack echo\ndescription: An image with echo\nimage: docker.io/library/alpine:3.20\n"
PARSER = "id: pk.words\nname: Pack words\ntype: list<string>\nformat: lines\n"
COMMAND = """id: pk-hello
name: Pack hello
schema_version: 1
description: Says hello
tool: pk-echo
args:
  - name: who
    type: string
    required: true
command: echo hello {{ who }}
outputs:
  - name: words
    parser: pk.words
"""
COMMAND_2 = COMMAND.replace("pk-hello", "pk-bye").replace("Pack hello", "Pack bye").replace("echo hello", "echo bye")
PLAYBOOK = """id: pk-flow
name: Pack flow
schema_version: 1
steps:
  - id: hi
    name: Hi
    command_ref: pk-hello
    args: {who: world}
"""
THEME = "id: pk-dense\nname: Pack dense\nmodes: both\ncolour: palette\nsize: {spacing: 0.2rem}\n"
KIND = "id: pk-login\nname: Pack login\nfields:\n  - name: user\n"

MANIFEST = """id: {id}
name: Pack test {id}
version: {version}
publisher: {publisher}
description: Library content only
contents: [{contents}]
"""


def pack(pid: str = "pk-pack", version: str = "1.0.0", publisher: str = "tester", library: dict[str, str] | None = None,
         extra: dict[str, bytes] | None = None) -> bytes:
    library = library if library is not None else {
        "tools/echo.yaml": TOOL, "parsers/words.yaml": PARSER, "commands/hello.yaml": COMMAND, "commands/bye.yaml": COMMAND_2,
        "playbooks/flow.yaml": PLAYBOOK, "themes/dense.yaml": THEME, "variable_kinds/login.yaml": KIND}
    kinds = sorted({k.split("/")[0] for k in library})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("castor-plugin.yaml", MANIFEST.format(id=pid, version=version, publisher=publisher, contents=", ".join(kinds)))
        for path, text in library.items():
            zf.writestr(f"library/{path}", text)
        for path, data in (extra or {}).items():
            zf.writestr(path, data)
    return buf.getvalue()


def preview(client, auth, data: bytes):
    return client.post("/api/plugins/preview", files={"file": ("p.zip", data, "application/zip")}, headers=auth)


def install(client, auth, data: bytes):
    pv = preview(client, auth, data)
    assert pv.status_code == 200, pv.text
    r = client.post("/api/plugins", json={"token": pv.json()["token"], "approve": True, "all_projects": True}, headers=auth)
    return pv.json(), r


def test_castor_versions():
    assert castor_ok(">=0.1", "0.1.0") and castor_ok(">=0.1, <0.3", "0.2.5") and castor_ok("0.1.0", "0.1.0")
    assert not castor_ok(">=0.2", "0.1.0") and not castor_ok("<0.1", "0.1.0")
    assert castor_ok("whatever", "0.1.0")  # unreadable: doesn't block


def test_packs(client, auth):
    pv, r = install(client, auth, pack())
    assert r.status_code == 201, r.text
    assert {(i["kind"], i["id"]) for i in pv["library"]} >= {("commands", "pk-hello"), ("tools", "pk-echo"), ("themes", "pk-dense")}
    assert pv["library_warnings"] == [] and pv["problems"] == []
    p = r.json()
    assert p["has_code"] is False and len(p["library"]) == 7
    assert client.get("/api/plugins/pk-pack", headers=auth).json()["status"] == "library"

    cmd = client.get("/api/commands/pk-hello", headers=auth).json()
    assert cmd["origin"] == "plugin:pk-pack"
    assert client.get("/api/tools/pk-echo", headers=auth).json()["origin"] == "plugin:pk-pack"
    assert any(x["name"] == "pk.words" and x["origin"] == "plugin:pk-pack" for x in client.get("/api/parsers", headers=auth).json())
    # Read-only, like built-ins.
    changed = client.put("/api/commands/pk-hello", json={"yaml": COMMAND.replace("Says hello", "Changed")}, headers=auth)
    assert changed.status_code == 409 and "plugin pk-pack" in changed.json()["detail"]
    assert client.delete("/api/commands/pk-hello", headers=auth).status_code == 409
    assert client.delete("/api/parsers/pk.words", headers=auth).status_code == 409

    # Someone else's id stops another install, from a person or another plugin.
    taken = preview(client, auth, pack("pk-other", library={"commands/hello.yaml": COMMAND})).json()
    assert any("pk-hello" in x and "plugin pk-pack" in x for x in taken["problems"])
    mine = client.post("/api/commands", json={"yaml": COMMAND.replace("pk-hello", "pk-mine").replace("tool: pk-echo", "tool: pk-echo")},
                       headers=auth)
    assert mine.status_code == 201, mine.text
    clash = preview(client, auth, pack("pk-other", library={"commands/mine.yaml": COMMAND.replace("pk-hello", "pk-mine")})).json()
    assert any("someone here" in x for x in clash["problems"])
    bad = preview(client, auth, pack("pk-bad", library={"commands/x.yaml": "id: [nope"}))
    assert bad.status_code == 422 and "library/commands/x.yaml" in bad.json()["detail"]
    undeclared = io.BytesIO()
    with zipfile.ZipFile(undeclared, "w") as zf:
        zf.writestr("castor-plugin.yaml", MANIFEST.format(id="pk-und", version="1.0.0", publisher="t", contents="tools"))
        zf.writestr("library/tools/echo.yaml", TOOL.replace("pk-echo", "pk-echo2"))
        zf.writestr("library/commands/hello.yaml", COMMAND)
    assert "don't list commands" in preview(client, auth, undeclared.getvalue()).json()["detail"]
    warned = preview(client, auth, pack("pk-warn", library={"commands/w.yaml": COMMAND.replace("pk-hello", "pk-w").replace("tool: pk-echo", "tool: nope")})).json()
    assert any("no tool nope" in w for w in warned["library_warnings"])

    # An update that drops a command retires it; a card that used it keeps its command line.
    code = f"PK-{uuid.uuid4().hex[:6]}"
    project = client.post("/api/projects", json={"codename": code, "name": code, "kind": "internal"}, headers=auth).json()
    card = client.post(f"/api/projects/{project['id']}/tasks", json={"title": "bye", "kind": "command", "command_ref": "pk-bye",
                       "command_args": {"who": "you"}}, headers=auth)
    assert card.status_code == 201, card.text
    library = {"tools/echo.yaml": TOOL, "parsers/words.yaml": PARSER, "commands/hello.yaml": COMMAND.replace("Says hello", "Says hi")}
    _, r = install(client, auth, pack(version="1.1.0", library=library))
    assert r.status_code == 201, r.text
    assert client.get("/api/commands/pk-bye", headers=auth).status_code == 404
    assert client.get("/api/commands/pk-hello", headers=auth).json()["description"] == "Says hi"
    assert client.get("/api/playbooks/pk-flow", headers=auth).status_code == 404
    left = client.get(f"/api/tasks/{card.json()['id']}", headers=auth).json()
    assert left["command_ref"] is None and "echo bye" in left["command"]

    # Removing it takes its items, except a tool someone's own command still uses.
    assert client.delete("/api/plugins/pk-pack", headers=auth).status_code == 204
    assert client.get("/api/commands/pk-hello", headers=auth).status_code == 404
    assert client.get("/api/tools/pk-echo", headers=auth).json()["origin"] == "user"  # pk-mine uses it
    assert any(x["name"] == "pk.words" and x["origin"] == "user" for x in client.get("/api/parsers", headers=auth).json())  # this too
    assert not any(k["id"] == "pk-login" for k in client.get("/api/variables/kinds", headers=auth).json())
    assert not any(x["id"] == "pk-dense" for x in client.get("/api/themes", headers=auth).json())
    client.delete("/api/commands/pk-mine", headers=auth)
    client.delete("/api/tools/pk-echo", headers=auth)
    client.delete("/api/parsers/pk.words", headers=auth)
    client.delete(f"/api/projects/{project['id']}", headers=auth)


# ---------- the directory, against a stand-in for GitHub ----------


class FakeGitHub:
    """Serves /directory.json, /repos/<repo>/releases and /dl/<file>."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.releases: dict[str, list[dict]] = {}
        self.directory: dict = {}
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # quiet
                pass

            def do_GET(self):  # noqa: N802
                path = self.path.split("?")[0]
                if path == "/directory.json":
                    body = json.dumps(fake.directory).encode()
                elif path.startswith("/repos/") and path.endswith("/releases"):
                    body = json.dumps(fake.releases.get(path[7:-9], [])).encode()
                elif path.startswith("/dl/") and path[4:] in fake.files:
                    body = fake.files[path[4:]]
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def release(self, repo: str, pid: str, version: str, data: bytes, *, sha: str | None = None, prefix: str = "") -> None:
        name = f"{pid}-{version}.zip"
        manifest = zipfile.ZipFile(io.BytesIO(data)).read("castor-plugin.yaml")
        assets = {name: data, f"{name}.sha256": f"{sha or hashlib.sha256(data).hexdigest()}  {name}\n".encode(),
                  "castor-plugin.yaml": manifest, "README.md": f"# {pid}\n\nRead me.".encode(),
                  "icon.svg": b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8"><rect width="8" height="8"/></svg>'}
        out = []
        for asset, body in assets.items():
            key = f"{pid}/{version}/{asset}"
            self.files[key] = body
            out.append({"name": asset, "browser_download_url": f"{self.base}/dl/{key}"})
        self.releases.setdefault(repo, []).insert(0, {
            "tag_name": f"{prefix}v{version}", "draft": False, "prerelease": False, "published_at": "2026-11-12T00:00:00Z",
            "body": f"Release {version}", "html_url": f"https://github.com/{repo}/releases/tag/{prefix}v{version}", "assets": out})

    def close(self) -> None:
        self.server.shutdown()


@pytest.fixture
def github(monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setattr(settings, "plugin_directory_url", f"{fake.base}/directory.json")
    monkeypatch.setattr(settings, "github_api_url", fake.base)
    D.clear_cache()
    yield fake
    D.clear_cache()
    fake.close()


def directory_doc(**more) -> dict:
    return {"version": 1,
            "publishers": [{"id": "tester", "name": "Tester", "github": ["tester-gh"], "description": "Tests things"}],
            "plugins": [{"id": "pk-dir", "name": "Directory pack", "publisher": "tester", "description": "From the directory",
                         "repo": "tester-gh/plugins", "path": "plugins/pk-dir", "tag_prefix": "pk-dir-", "tags": ["pack"]}],
            **more}


def test_directory_must_be_consistent():
    doc = directory_doc()
    doc["plugins"][0]["repo"] = "someone-else/plugins"
    with pytest.raises(ValueError, match="isn't one of Tester's GitHub accounts"):
        D.Directory.model_validate(doc)
    doc = directory_doc()
    doc["plugins"][0]["publisher"] = "nobody"
    with pytest.raises(ValueError, match="no publisher nobody"):
        D.Directory.model_validate(doc)
    entry = D.Entry.model_validate(directory_doc()["plugins"][0])
    raw = {"tag_name": "pk-dir-v1.2.0", "assets": [{"name": "pk-dir-1.2.0.zip", "browser_download_url": "https://x/z"}]}
    assert D.release_of(entry, raw).version == "1.2.0"
    assert D.release_of(entry, {**raw, "prerelease": True}) is None
    assert D.release_of(entry, {**raw, "tag_name": "other-v1.2.0"}) is None
    assert D.release_of(entry, {**raw, "assets": []}) is None


def test_the_directory(client, auth, github):
    github.directory = directory_doc()
    data = pack("pk-dir", publisher="tester", library={"commands/hello.yaml": COMMAND.replace("pk-hello", "pk-dir-hello")
                                                      .replace("tool: pk-echo\n", "").replace("    parser: pk.words\n", "    parser: lines\n")})
    github.release("tester-gh/plugins", "pk-dir", "1.0.0", data, prefix="pk-dir-")

    listing = client.get("/api/plugin-directory", headers=auth).json()
    assert listing["enabled"] and listing["error"] is None
    (entry,) = listing["plugins"]
    assert entry["latest"]["version"] == "1.0.0" and entry["installed"] is None and entry["publisher_name"] == "Tester"
    page = client.get("/api/plugin-directory/plugins/pk-dir", headers=auth).json()
    assert page["release"]["notes"] == "Release 1.0.0" and page["manifest"]["contents"] == ["commands"]
    assert page["readme"].startswith("# pk-dir") and page["problems"] == []
    icon = client.get("/api/plugin-directory/plugins/pk-dir/icon", headers=auth)
    assert icon.status_code == 200 and icon.headers["content-type"] == "image/svg+xml" and "sandbox" in icon.headers["content-security-policy"]
    pub = client.get("/api/plugin-directory/publishers/tester", headers=auth).json()
    assert pub["name"] == "Tester" and [p["id"] for p in pub["plugins"]] == ["pk-dir"]
    assert client.get("/api/plugin-directory/plugins/nope", headers=auth).status_code == 404

    pv = client.post("/api/plugins/preview", data={"directory": "pk-dir"}, headers=auth)
    assert pv.status_code == 200, pv.text
    assert pv.json()["source"] == "directory:pk-dir" and pv.json()["release"]["version"] == "1.0.0"
    r = client.post("/api/plugins", json={"token": pv.json()["token"], "source": pv.json()["source"], "approve": True,
                                          "all_projects": True}, headers=auth)
    assert r.status_code == 201, r.text
    assert client.get("/api/plugin-directory", headers=auth).json()["plugins"][0]["installed"] == "1.0.0"

    # A newer release: the check finds it and tells admins.
    github.release("tester-gh/plugins", "pk-dir", "1.1.0", pack("pk-dir", "1.1.0", "tester", {"commands/hello.yaml": COMMAND
                   .replace("pk-hello", "pk-dir-hello").replace("tool: pk-echo\n", "").replace("    parser: pk.words\n", "    parser: lines\n")}),
                   prefix="pk-dir-")
    found = client.post("/api/plugin-directory/check", headers=auth).json()
    assert found["updates"] == [{"id": "pk-dir", "name": "Pack test pk-dir", "from": "1.0.0", "to": "1.1.0"}]
    assert client.get("/api/plugins/pk-dir", headers=auth).json()["latest_version"] == "1.1.0"
    notes = client.get("/api/notifications", headers=auth).json()
    assert any(n["kind"] == "plugin.update" and "1.1.0" in n["title"] for n in (notes.get("items", notes) if isinstance(notes, dict) else notes))

    # A download that doesn't match its checksum, or a release that isn't what the directory says, is refused.
    github.release("tester-gh/plugins", "pk-dir", "1.2.0", pack("pk-dir", "1.2.0", "tester"), sha="0" * 64, prefix="pk-dir-")
    bad = client.post("/api/plugins/preview", data={"directory": "pk-dir"}, headers=auth)
    assert bad.status_code == 422 and "SHA-256" in bad.json()["detail"]
    github.release("tester-gh/plugins", "pk-dir", "1.3.0", pack("pk-dir", "1.3.0", "mallory"), prefix="pk-dir-")
    wrong = client.post("/api/plugins/preview", data={"directory": "pk-dir"}, headers=auth)
    assert wrong.status_code == 422 and "doesn't match the directory" in wrong.json()["detail"]

    # Updating through the directory clears the notice.
    github.releases["tester-gh/plugins"] = [r for r in github.releases["tester-gh/plugins"] if r["tag_name"] == "pk-dir-v1.1.0"]
    pv = client.post("/api/plugins/preview", data={"directory": "pk-dir"}, headers=auth).json()
    assert pv["existing"]["version"] == "1.0.0"
    client.post("/api/plugins", json={"token": pv["token"], "source": pv["source"], "approve": True}, headers=auth)
    after = client.get("/api/plugins/pk-dir", headers=auth).json()
    assert after["version"] == "1.1.0" and after["latest_version"] is None

    # A token needs the admin scope, like the rest of plugin administration.
    assert client.delete("/api/plugins/pk-dir", headers=auth).status_code == 204


def test_directory_off(client, auth, monkeypatch):
    monkeypatch.setattr(settings, "plugin_directory_url", "")
    D.clear_cache()
    out = client.get("/api/plugin-directory", headers=auth).json()
    assert out["enabled"] is False and out["plugins"] == []
    assert client.post("/api/plugins/preview", data={"directory": "x"}, headers=auth).status_code == 422
