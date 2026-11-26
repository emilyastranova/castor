"""Plugin repositories (plugin_repos.py): real git against real servers. A local repository over
file://, a smart-HTTP server (git http-backend) that wants a username and token, and an SSH server
(asyncssh) that wants a key, whose host key is trusted on first use and refused once it changes."""

from __future__ import annotations

import asyncio
import base64
import io
import subprocess
import threading
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import asyncssh
import pytest
from test_ssh_hosts import FakeHost

from castor_server import models as m
from castor_server import plugin_repos
from castor_server.db import SessionLocal

TOKEN = "glpat-s3cr3t-t0ken"


def sh(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env={"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin"}).stdout


def manifest(pid: str, version: str, extra: str = "") -> str:
    return f"id: {pid}\nname: {pid.title()}\nversion: {version}\npublisher: tests\ndescription: From git\n{extra}"


def make_repo(root: Path, n: str) -> Path:
    """A repository with two plugins in plugins/, one broken manifest, and what packing leaves out."""
    work = root / f"work-{n}"
    work.mkdir()
    sh("init", "-q", "-b", "main", cwd=work)
    files = {
        f"plugins/alpha-{n}/castor-plugin.yaml": manifest(f"alpha-{n}", "1.0.0", "icon: icon.svg\n"),
        f"plugins/alpha-{n}/icon.svg": '<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0h1"/></svg>',
        f"plugins/alpha-{n}/README.md": "# Alpha",
        f"plugins/alpha-{n}/lib/thing.ts": "export const x = 1",
        f"plugins/alpha-{n}/main_test.ts": "test",
        f"plugins/alpha-{n}/samples/big.json": "{}",
        f"plugins/alpha-{n}/deno.json": "{}",
        f"plugins/beta-{n}/castor-plugin.yaml": manifest(f"beta-{n}", "0.3.0"),
        "plugins/broken/castor-plugin.yaml": "id: [not, valid",
        "README.md": "plugins",
    }
    for name, text in files.items():
        (work / name).parent.mkdir(parents=True, exist_ok=True)
        (work / name).write_text(text)
    (work / f"plugins/alpha-{n}/link").symlink_to("/etc/passwd")
    sh("add", "-A", cwd=work)
    sh("commit", "-qm", "first", cwd=work)
    bare = root / f"repo-{n}.git"
    sh("clone", "-q", "--bare", str(work), str(bare), cwd=root)
    return work


def push(work: Path, path: str, text: str) -> None:
    (work / path).write_text(text)
    sh("commit", "-qam", "change", cwd=work)
    sh("push", "-q", str(work.parent / f"repo-{work.name.removeprefix('work-')}.git"), "main", cwd=work)


def add(client, headers, **body):
    r = client.post("/api/plugin-repositories", json={"name": "Repo", **body}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def install_from(client, auth, repo_id: str, path: str):
    p = client.post("/api/plugins/preview", data={"repository": repo_id, "path": path}, headers=auth)
    assert p.status_code == 200, p.text
    r = client.post("/api/plugins", json={"token": p.json()["token"], "source": p.json()["source"], "approve": True}, headers=auth)
    assert r.status_code == 201, r.text
    return p.json(), r.json()


def test_checks():
    with pytest.raises(plugin_repos.RepoError, match="out of the URL"):
        plugin_repos.check_url("https://me:hunter2@git.example/x.git")
    plugin_repos.check_url("https://oauth2@git.example/x.git")  # a username alone is fine
    for bad in ("-uhttps://x", "https://x/y z", "ssh://x\ny"):
        with pytest.raises(plugin_repos.RepoError):
            plugin_repos.check_url(bad)
    for bad in ("--upload-pack=sh", "a..b", "main:refs/heads/evil", "x y"):
        with pytest.raises(plugin_repos.RepoError):
            plugin_repos.check_ref(bad)
    assert plugin_repos.check_ref(" release/2.x ") == "release/2.x"
    assert plugin_repos.is_ssh("git@gitlab.example:team/plugins.git") and plugin_repos.is_ssh("ssh://git@h/x")
    assert not plugin_repos.is_ssh("https://h/x") and not plugin_repos.is_ssh("/srv/x.git") and not plugin_repos.is_ssh("ext::sh")


def test_file_repository_browse_install_and_update(client, auth, tmp_path):
    n = uuid.uuid4().hex[:6]
    work = make_repo(tmp_path, n)
    repo = add(client, auth, url=f"file://{tmp_path}/repo-{n}.git")
    assert repo["error"] is None and len(repo["commit"]) == 40 and repo["plugins"] == 2
    assert repo["auth"] == "none" and not repo["has_secret"]

    listed = client.get("/api/plugin-repositories/plugins", headers=auth).json()
    mine = {p["path"]: p for p in listed["plugins"] if p["repository"]["id"] == repo["id"]}
    assert set(mine) == {f"plugins/alpha-{n}", f"plugins/beta-{n}", "plugins/broken"}
    assert mine["plugins/broken"]["problem"] and not mine["plugins/broken"]["id"]
    alpha = mine[f"plugins/alpha-{n}"]
    assert (alpha["id"], alpha["version"], alpha["installed"]) == (f"alpha-{n}", "1.0.0", None)
    icon = client.get(alpha["icon_url"], headers=auth)
    assert icon.status_code == 200 and icon.headers["content-type"] == "image/svg+xml" and "sandbox" in icon.headers["content-security-policy"]

    # Installing packs the folder as pack.py does: no tests, samples, deno.json or symlinks.
    preview, plugin = install_from(client, auth, repo["id"], f"plugins/alpha-{n}")
    assert preview["source"] == f"repo:{repo['id']}:plugins/alpha-{n}" and plugin["source"] == preview["source"]
    assert preview["manifest"]["id"] == f"alpha-{n}" and preview["files"] == 4  # manifest, icon, README, lib/thing.ts
    listed = client.get("/api/plugin-repositories/plugins", headers=auth).json()
    alpha = next(p for p in listed["plugins"] if p["path"] == f"plugins/alpha-{n}" and p["repository"]["id"] == repo["id"])
    assert alpha["installed"] == "1.0.0" and alpha["installed_here"] and not alpha["update"]

    # A newer version pushed: the update check fetches and finds it.
    push(work, f"plugins/alpha-{n}/castor-plugin.yaml", manifest(f"alpha-{n}", "1.1.0", "icon: icon.svg\n"))
    found = client.post("/api/plugin-directory/check", headers=auth).json()["updates"]
    assert {"id": f"alpha-{n}", "name": f"Alpha-{n}".title(), "from": "1.0.0", "to": "1.1.0"} in found
    assert client.get(f"/api/plugins/alpha-{n}", headers=auth).json()["latest_version"] == "1.1.0"
    _, updated = install_from(client, auth, repo["id"], f"plugins/alpha-{n}")
    assert updated["version"] == "1.1.0"

    # A branch or tag of its own.
    sh("tag", "v1", cwd=work)
    sh("push", "-q", str(tmp_path / f"repo-{n}.git"), "v1", cwd=work)
    r = client.patch(f"/api/plugin-repositories/{repo['id']}", json={"ref": "v1"}, headers=auth).json()
    assert r["ref"] == "v1" and r["error"] is None and r["plugins"] == 2
    r = client.patch(f"/api/plugin-repositories/{repo['id']}", json={"ref": "no-such-branch"}, headers=auth).json()
    assert r["error"] and r["commit"] is None

    assert client.delete(f"/api/plugin-repositories/{repo['id']}", headers=auth).status_code == 204
    assert not plugin_repos.repo_dir(repo["id"]).exists()
    assert client.get(f"/api/plugins/alpha-{n}", headers=auth).status_code == 200  # stays installed
    client.delete(f"/api/plugins/alpha-{n}", headers=auth)


def test_transports_git_refuses(client, auth, tmp_path):
    marker = tmp_path / "ran"
    script = tmp_path / "x.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\n")
    script.chmod(0o755)
    r = add(client, auth, url=f"ext::{script}")  # git's ext:: runs a command: never allowed
    assert r["error"] and "transport" in r["error"] and not marker.exists()
    client.delete(f"/api/plugin-repositories/{r['id']}", headers=auth)


class GitHttp(BaseHTTPRequestHandler):
    """git's smart HTTP protocol (git http-backend), behind Basic auth."""

    root: Path
    seen: ClassVar[list[str]] = []

    def log_message(self, *a):
        pass

    def _serve(self):
        got = self.headers.get("Authorization", "")
        GitHttp.seen.append(got)
        if got != "Basic " + base64.b64encode(f"oauth2:{TOKEN}".encode()).decode():
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="git"')
            self.end_headers()
            return
        path, _, query = self.path.partition("?")
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        env = {"GIT_PROJECT_ROOT": str(self.root), "GIT_HTTP_EXPORT_ALL": "1", "PATH_INFO": path, "QUERY_STRING": query,
               "REQUEST_METHOD": self.command, "CONTENT_TYPE": self.headers.get("Content-Type", ""),
               "CONTENT_LENGTH": str(len(body)), "REMOTE_USER": "oauth2", "GIT_CONFIG_GLOBAL": "/dev/null", "PATH": "/usr/bin:/bin"}
        out = subprocess.run(["git", "http-backend"], check=False, input=body, env=env, capture_output=True).stdout
        head, _, rest = out.partition(b"\r\n\r\n")
        status = 200
        headers = []
        for line in head.decode().split("\r\n"):
            k, _, v = line.partition(": ")
            if k.lower() == "status":
                status = int(v.split()[0])
            elif k:
                headers.append((k, v))
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(rest)))
        self.end_headers()
        self.wfile.write(rest)

    do_GET = do_POST = _serve


@pytest.fixture
def git_http(tmp_path):
    GitHttp.root = tmp_path
    GitHttp.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), GitHttp)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_private_https_repository(client, auth, tmp_path, git_http):
    n = uuid.uuid4().hex[:6]
    make_repo(tmp_path, n)
    url = f"{git_http}/repo-{n}.git"
    anon = add(client, auth, url=url)
    assert anon["error"] and anon["commit"] is None  # it wants a sign-in, and git never prompts

    r = client.patch(f"/api/plugin-repositories/{anon['id']}", json={"auth": "https", "username": "oauth2"}, headers=auth)
    assert r.status_code == 422  # no token yet
    r = client.patch(f"/api/plugin-repositories/{anon['id']}", json={"auth": "https", "username": "oauth2", "secret": "wrong"},
                     headers=auth).json()
    assert r["error"] and "wrong" not in r["error"]
    r = client.patch(f"/api/plugin-repositories/{anon['id']}", json={"secret": TOKEN}, headers=auth).json()
    assert r["error"] is None and r["plugins"] == 2 and r["has_secret"]
    assert TOKEN not in str(client.get("/api/plugin-repositories", headers=auth).json())
    _, plugin = install_from(client, auth, anon["id"], f"plugins/beta-{n}")
    assert plugin["version"] == "0.3.0"
    assert plugin_repos.repo_dir(anon["id"]).exists() and TOKEN not in (plugin_repos.repo_dir(anon["id"]) / "config").read_text()
    client.delete(f"/api/plugins/beta-{n}", headers=auth)
    client.delete(f"/api/plugin-repositories/{anon['id']}", headers=auth)


def test_private_ssh_repository(client, auth, tmp_path):
    n = uuid.uuid4().hex[:6]
    make_repo(tmp_path, n)
    host = FakeHost(tmp_path)
    host.start()
    try:
        key = asyncssh.generate_private_key("ssh-ed25519")
        host.authorized.add(key.export_public_key().decode())
        url = f"ssh://git@127.0.0.1:{host.port}{tmp_path}/repo-{n}.git"
        locked = key.export_private_key("openssh", passphrase="pass phrase").decode()

        r = client.post("/api/plugin-repositories", json={"name": "SSH", "url": url, "auth": "ssh", "secret": locked,
                                                          "passphrase": "nope"}, headers=auth)
        assert r.status_code == 422 and "can't be read" in r.json()["detail"]
        repo = add(client, auth, url=url, auth="ssh", secret=locked, passphrase="pass phrase")
        assert repo["error"] is None, repo["error"]
        assert repo["plugins"] == 2 and repo["ssh_url"] and len(repo["host_keys"]) == 1
        assert repo["host_keys"][0]["fingerprint"] == host.host_key.get_fingerprint()
        assert "PRIVATE KEY" not in str(client.get("/api/plugin-repositories", headers=auth).json())

        assert repo["public_key"] == key.export_public_key().decode().strip()
        switched = client.patch(f"/api/plugin-repositories/{repo['id']}", json={"auth": "https"}, headers=auth)
        assert switched.status_code == 422  # the SSH key never becomes a password

        # A key made by Castor: its public half is the deploy key to add; until then, no way in.
        made = add(client, auth, url=url, generate_key=True)
        assert made["auth"] == "ssh" and made["public_key"].startswith("ssh-ed25519 ") and made["error"]
        host.authorized.add(made["public_key"])
        assert client.post(f"/api/plugin-repositories/{made['id']}/fetch", headers=auth).json()["error"] is None
        client.delete(f"/api/plugin-repositories/{made['id']}", headers=auth)

        # Another machine on that address: refused until an admin forgets the old key.
        port = host.port
        host.stop()
        host.host_key = asyncssh.generate_private_key("ssh-ed25519")
        host.start()
        assert host.port == port
        r = client.post(f"/api/plugin-repositories/{repo['id']}/fetch", headers=auth).json()
        assert r["error"] and "isn't the one trusted" in r["error"]
        r = client.post(f"/api/plugin-repositories/{repo['id']}/forget-host-keys", headers=auth).json()
        assert r["host_keys"] == []
        r = client.post(f"/api/plugin-repositories/{repo['id']}/fetch", headers=auth).json()
        assert r["error"] is None and r["host_keys"][0]["fingerprint"] == host.host_key.get_fingerprint()
        client.delete(f"/api/plugin-repositories/{repo['id']}", headers=auth)
    finally:
        host.stop()


def test_packing_matches_pack_py(client, auth, tmp_path):
    n = uuid.uuid4().hex[:6]
    make_repo(tmp_path, n)
    repo = add(client, auth, url=str(tmp_path / f"repo-{n}.git"))  # a plain path works too
    assert repo["error"] is None

    async def pack():
        async with SessionLocal() as db:
            row = await db.get(m.PluginRepository, uuid.UUID(repo["id"]))
            return await plugin_repos.package(row, f"plugins/alpha-{n}")
    data = asyncio.run(pack())
    names = sorted(zipfile.ZipFile(io.BytesIO(data)).namelist())
    assert names == ["README.md", "castor-plugin.yaml", "icon.svg", "lib/thing.ts"]
    assert asyncio.run(pack()) == data  # the same files, the same .zip
    client.delete(f"/api/plugin-repositories/{repo['id']}", headers=auth)
