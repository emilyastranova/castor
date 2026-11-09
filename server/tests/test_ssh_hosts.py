"""SSH hosts: Castor connects to a host over SSH and runs jobs there as an agent.

A real SSH server (asyncssh, in a thread) stands in for the host: it runs each command with
the local /bin/sh in its own session (as sshd does) and serves SFTP, so these tests cover
the whole path: keys, trust on first use, probing, jobs, variables, artifacts, cancel."""

import asyncio
import threading
import time
import uuid
from pathlib import Path

import asyncssh
import pytest


class FakeHost:
    """An SSH server on 127.0.0.1 that accepts the public keys in `authorized`."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self.authorized: set[str] = set()
        self.host_key = asyncssh.generate_private_key("ssh-ed25519")
        self.loop = asyncio.new_event_loop()
        self.server = None
        self.port = 0
        self.conns: list = []
        self.forwarded: list[tuple[str, int]] = []  # as a jump host: where it forwarded to
        threading.Thread(target=self.loop.run_forever, daemon=True).start()

    def _call(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(10)

    def start(self) -> None:
        host = self

        class Server(asyncssh.SSHServer):
            def connection_made(self, conn):
                host.conns.append(conn)

            def begin_auth(self, username):
                return True

            def public_key_auth_supported(self):
                return True

            def validate_public_key(self, username, key):
                return key.export_public_key().decode().split()[1] in {k.split()[1] for k in host.authorized}

            def connection_requested(self, dest_host, dest_port, orig_host, orig_port):
                host.forwarded.append((dest_host, dest_port))
                return True  # as sshd allows (ssh -J / ProxyJump)

        async def process(proc: asyncssh.SSHServerProcess) -> None:
            local = await asyncio.create_subprocess_exec(
                "/bin/sh", "-c", proc.command or "true", cwd=host.home, start_new_session=True,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )

            async def pump(src, dst):
                while data := await src.read(65536):
                    dst.write(data)

            async def feed():
                try:
                    while data := await proc.stdin.read(65536):
                        local.stdin.write(data)
                except asyncssh.BreakReceived:
                    pass
                local.stdin.close()

            feeding = asyncio.ensure_future(feed())
            await asyncio.gather(pump(local.stdout, proc.stdout), pump(local.stderr, proc.stderr))
            feeding.cancel()
            proc.exit(await local.wait())

        async def go():
            self.server = await asyncssh.create_server(
                Server, "127.0.0.1", self.port, server_host_keys=[self.host_key], process_factory=process,
                sftp_factory=True, encoding=None,
            )
            self.port = self.server.sockets[0].getsockname()[1]

        self._call(go())

    def stop(self) -> None:
        async def go():
            self.server.close()
            for conn in self.conns:
                conn.close()
            self.conns.clear()
            await self.server.wait_closed()

        self._call(go())


@pytest.fixture
def fake_host(tmp_path, client, auth):
    host = FakeHost(tmp_path)
    host.start()
    yield host
    for h in client.get("/api/ssh-hosts", headers=auth).json():  # disconnect before the server goes
        client.delete(f"/api/ssh-hosts/{h['id']}", headers=auth)
    host.stop()


def _wait(fn, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError(f"timed out; last value {value!r}")


def _host(client, auth, host_id):
    return next(h for h in client.get("/api/ssh-hosts", headers=auth).json() if h["id"] == host_id)


def _add(client, auth, fake, tag, **extra):
    body = {"name": f"ssh-{uuid.uuid4().hex[:6]}", "host": "127.0.0.1", "port": fake.port, "username": "castor",
            "workdir": str(fake.home / "work"), "generate_key": True, "tags": [tag], "max_concurrency": 2, **extra}
    r = client.post("/api/ssh-hosts", json=body, headers=auth)
    assert r.status_code == 201, r.text
    return r.json()


def _connected(client, auth, fake, tag):
    host = _add(client, auth, fake, tag)
    assert host["public_key"].startswith("ssh-ed25519 ") and host["key_fingerprint"].startswith("SHA256:")
    # Not authorized yet: it says why.
    _wait(lambda: _host(client, auth, host["id"])["state"] == "error")
    assert "authorized_keys" in _host(client, auth, host["id"])["error"]
    fake.authorized.add(host["public_key"])
    client.post(f"/api/ssh-hosts/{host['id']}/reconnect", headers=auth)
    _wait(lambda: _host(client, auth, host["id"])["state"] == "connected")
    return _host(client, auth, host["id"])


def _project(client, auth):
    return client.post("/api/projects", json={"codename": f"SH-{uuid.uuid4().hex[:6]}", "name": "SSH", "kind": "internal"},
                       headers=auth).json()


def _run(client, auth, project, tag, command, **extra):
    task = client.post(f"/api/projects/{project['id']}/tasks",
                       json={"title": "over ssh", "kind": "command", "command": command, "agent_selector": {"tags": [tag]},
                             **extra}, headers=auth).json()
    assert "id" in task, task
    return task, client.post(f"/api/tasks/{task['id']}/run", headers=auth).json()["id"]


def _finished(client, auth, job_id):
    return _wait(lambda: (d := client.get(f"/api/jobs/{job_id}", headers=auth).json())["status"] not in
                 ("queued", "dispatched", "running") and d)


def test_a_job_runs_over_ssh(client, auth, fake_host):
    tag = uuid.uuid4().hex[:6]
    host = _connected(client, auth, fake_host, tag)
    assert host["host_key_fingerprint"] == fake_host.host_key.get_fingerprint()  # trusted on first use
    agent = next(a for a in client.get("/api/agents", headers=auth).json() if a["id"] == host["agent_id"])
    assert agent["connected"] and agent["ssh_host_id"] == host["id"] and "host" in agent["capabilities"]["runtimes"]
    assert agent["tags"] == [tag] and agent["max_concurrency"] == 2 and agent["hostname"]

    project = _project(client, auth)
    _, job_id = _run(client, auth, project, tag,
                        'echo "hello from $(pwd)"; echo oops >&2; mkdir -p artifacts; echo data > artifacts/out.txt; exit 3')
    job = _finished(client, auth, job_id)
    assert job["status"] == "failed" and job["exit_code"] == 3 and job["agent_id"] == host["agent_id"], job
    out = client.get(f"/api/jobs/{job_id}/output", headers=auth).text
    workdir = fake_host.home / "work" / job_id
    assert f"hello from {workdir}" in out and "oops" in out and "exit code 3" in out
    files = client.get(f"/api/jobs/{job_id}/artifacts", headers=auth).json()
    assert [f["filename"] for f in files] == ["artifacts/out.txt"]
    assert not (workdir / ".castor").exists()  # the job's script and variables are gone


def test_variables_reach_the_job_but_no_command_line(client, auth, fake_host):
    tag = uuid.uuid4().hex[:6]
    _connected(client, auth, fake_host, tag)
    yaml = """
id: ssh-vartest
name: SSH variable test
vars:
  - name: creds
    kind: credential
command: echo "user=${CASTOR_VAR_CREDS_USERNAME} pass=${CASTOR_VAR_CREDS_PASSWORD}"; ls .castor; ps -o args= -u "$(id -u)" | grep -c 'Pa5[5]word' || true
"""
    if client.get("/api/commands/ssh-vartest", headers=auth).status_code == 404:
        assert client.post("/api/commands", json={"yaml": yaml}, headers=auth).status_code == 201
    creds = client.post("/api/variables/sets", json={"kind_id": "credential", "name": f"c-{uuid.uuid4().hex[:6]}",
                                                     "values": {"username": "svc", "password": "Pa55word!x"}}, headers=auth).json()
    project = _project(client, auth)
    task = client.post(f"/api/projects/{project['id']}/tasks",
                       json={"title": "vars", "kind": "command", "command_ref": "ssh-vartest",
                             "var_bindings": {"creds": creds["id"]}, "agent_selector": {"tags": [tag]}}, headers=auth).json()
    job_id = client.post(f"/api/tasks/{task['id']}/run", headers=auth).json()["id"]
    job = _finished(client, auth, job_id)
    assert job["status"] == "succeeded", job
    out = client.get(f"/api/jobs/{job_id}/output", headers=auth).text
    assert "user=svc pass=••••" in out and "Pa55word" not in out  # masked in the log
    lines = out.splitlines()
    assert "env" not in lines and "run.sh" in lines  # env file deleted before the command ran
    assert "0" in lines  # no process has the secret in its arguments


def test_cancel_stops_the_remote_process(client, auth, fake_host):
    tag = uuid.uuid4().hex[:6]
    _connected(client, auth, fake_host, tag)
    project = _project(client, auth)
    marker = fake_host.home / f"still-running-{uuid.uuid4().hex[:6]}"
    _, job_id = _run(client, auth, project, tag, f"echo started; sleep 20; touch {marker}")
    _wait(lambda: "started" in client.get(f"/api/jobs/{job_id}/output", headers=auth).text)
    assert client.post(f"/api/jobs/{job_id}/cancel", headers=auth).status_code == 200
    job = _finished(client, auth, job_id)
    assert job["status"] == "cancelled"
    out = _wait(lambda: (t := client.get(f"/api/jobs/{job_id}/output", headers=auth).text) and "[castor] cancelled" in t and t)
    assert "cancelled" in out
    time.sleep(0.3)
    assert not marker.exists()


def test_host_key_change_needs_an_admin(client, auth, fake_host):
    tag = uuid.uuid4().hex[:6]
    host = _connected(client, auth, fake_host, tag)
    trusted = host["host_key_fingerprint"]
    fake_host.stop()
    fake_host.host_key = asyncssh.generate_private_key("ssh-ed25519")  # a different machine on that address
    fake_host.start()
    _wait(lambda: not _host(client, auth, host["id"])["connected"], timeout=60)
    client.post(f"/api/ssh-hosts/{host['id']}/reconnect", headers=auth)
    changed = _wait(lambda: (h := _host(client, auth, host["id"]))["state"] == "host_key_changed" and h)
    assert changed["host_key_fingerprint"] == trusted
    assert changed["host_key_pending_fingerprint"] == fake_host.host_key.get_fingerprint()
    r = client.post(f"/api/ssh-hosts/{host['id']}/accept-host-key", headers=auth)
    assert r.status_code == 200 and r.json()["host_key_fingerprint"] == fake_host.host_key.get_fingerprint()
    _wait(lambda: _host(client, auth, host["id"])["state"] == "connected")


def test_names_keys_and_removal(client, auth, fake_host):
    tag = uuid.uuid4().hex[:6]
    host = _connected(client, auth, fake_host, tag)
    # A connecting agent can't take an SSH host's name.
    with client.websocket_connect("/ws/agent") as ws:
        ws.send_json({"type": "hello", "secret": "test-agent-secret", "name": host["name"], "capabilities": {"runtimes": ["host"]}})
        reply = ws.receive_json()
    assert reply["type"] == "hello.error" and "SSH host" in reply["reason"]
    assert client.post("/api/ssh-hosts", json={"name": host["name"], "host": "x", "username": "u", "generate_key": True},
                       headers=auth).status_code == 409
    assert client.post("/api/ssh-hosts", json={"name": "nokey", "host": "x", "username": "u"}, headers=auth).status_code == 422
    bad = client.post("/api/ssh-hosts", json={"name": "badkey", "host": "x", "username": "u", "private_key": "nope"}, headers=auth)
    assert bad.status_code == 422 and "private_key" in bad.text

    # A pasted key with a passphrase is kept decrypted in the vault, never shown again.
    pasted = asyncssh.generate_private_key("ssh-ed25519")
    text = pasted.export_private_key(passphrase="pw").decode()
    r = client.post("/api/ssh-hosts", json={"name": f"p-{uuid.uuid4().hex[:6]}", "host": "127.0.0.1", "port": 1, "username": "u",
                                            "private_key": text, "passphrase": "pw", "enabled": False}, headers=auth)
    assert r.status_code == 201, r.text
    assert r.json()["key_fingerprint"] == pasted.get_fingerprint() and "PRIVATE" not in r.text
    assert "PRIVATE" not in client.get("/api/ssh-hosts", headers=auth).text
    client.delete(f"/api/ssh-hosts/{r.json()['id']}", headers=auth)

    # Tags change without reconnecting; removing it forgets the agent.
    body = {**{k: host[k] for k in ("name", "host", "port", "username", "workdir", "network")}, "tags": [tag, "more"], "max_concurrency": 3}
    assert client.put(f"/api/ssh-hosts/{host['id']}", json=body, headers=auth).status_code == 200
    assert _host(client, auth, host["id"])["connected"]
    agent = next(a for a in client.get("/api/agents", headers=auth).json() if a["id"] == host["agent_id"])
    assert agent["tags"] == sorted([tag, "more"]) and agent["max_concurrency"] == 3
    assert client.delete(f"/api/agents/{host['agent_id']}", headers=auth).status_code == 409
    assert client.delete(f"/api/ssh-hosts/{host['id']}", headers=auth).status_code == 204
    assert host["agent_id"] not in {a["id"] for a in client.get("/api/agents", headers=auth).json()}



def test_container_command_line():
    """With podman or docker on the host, an image job runs in it with the job dir at /work;
    variables are passed by name only (their values are in the sourced env file)."""
    from castor_server.orchestrator.ssh import HostLink, JobRun, Probe

    link = HostLink(uuid.uuid4(), uuid.uuid4())
    link.probe = Probe(workdir="/home/u/castor-jobs", hostname="h", os="Linux", username="u", runtime="podman")
    run = JobRun(link, {"id": "1234567890", "command": "nmap -sV 'a b'", "image": "docker.io/instrumentisto/nmap",
                        "container_flags": ["--cap-add=NET_RAW"]})
    line = run._command(["CASTOR_VAR_X"])
    assert line == (
        f"exec podman run --rm --name {run.container_name} --network host -v /home/u/castor-jobs/1234567890:/work:Z "
        "-w /work -e CASTOR_VAR_X --cap-add=NET_RAW --entrypoint /bin/sh docker.io/instrumentisto/nmap -c "
        "'nmap -sV '\"'\"'a b'\"'\"''"
    )
    run.job["container_flags"] = ["--network=bridge"]  # the command's own network wins
    assert "--network host" not in run._command([]) and "--network=bridge" in run._command([])
    host_job = JobRun(link, {"id": "1", "command": "id"})
    assert host_job._command([]) == "exec /bin/sh -c id"


def test_jump_hosts_from_the_ssh_config(client, auth, fake_host, tmp_path_factory):
    bastion = FakeHost(tmp_path_factory.mktemp("bastion"))
    bastion.start()
    try:
        config = (f"Host bastion\n  HostName 127.0.0.1\n  Port {bastion.port}\n  User jumper\n"
                  f"Host lab-target\n  HostName 127.0.0.1\n  Port {fake_host.port}\n  User castor\n  ProxyJump bastion\n"
                  "Host *\n  IdentityFile ~/.ssh/id_ed25519\n")
        bad = client.put("/api/ssh-config", json={"text": config + "  ProxyCommand nc %h %p\n"}, headers=auth)
        assert bad.status_code == 422 and "ProxyCommand" in bad.text
        saved = client.put("/api/ssh-config", json={"text": config}, headers=auth)
        assert saved.status_code == 200, saved.text
        assert saved.json()["warnings"] and "IdentityFile" in saved.json()["warnings"][0]

        tag = uuid.uuid4().hex[:6]
        host = _add(client, auth, fake_host, tag, host="lab-target", port=None, username="")
        assert host["route"] == [f"jumper@127.0.0.1:{bastion.port}", f"castor@127.0.0.1:{fake_host.port}"]
        fake_host.authorized.add(host["public_key"])
        bastion.authorized.add(host["public_key"])  # every hop signs in with the host's key
        client.post(f"/api/ssh-hosts/{host['id']}/reconnect", headers=auth)
        live = _wait(lambda: (h := _host(client, auth, host["id"]))["state"] == "connected" and h)
        assert ("127.0.0.1", fake_host.port) in bastion.forwarded  # it went through the bastion
        assert live["jump_host_keys"] == {f"jumper@127.0.0.1:{bastion.port}": bastion.host_key.get_fingerprint()}
        assert live["host_key_fingerprint"] == fake_host.host_key.get_fingerprint()

        _, job_id = _run(client, auth, _project(client, auth), tag, "echo through the bastion")
        assert _finished(client, auth, job_id)["status"] == "succeeded"
        assert "through the bastion" in client.get(f"/api/jobs/{job_id}/output", headers=auth).text

        # The bastion is replaced: Castor stops at it until an admin accepts its new key.
        bastion.stop()
        bastion.host_key = asyncssh.generate_private_key("ssh-ed25519")
        bastion.start()
        _wait(lambda: not _host(client, auth, host["id"])["connected"], timeout=60)
        client.post(f"/api/ssh-hosts/{host['id']}/reconnect", headers=auth)
        changed = _wait(lambda: (h := _host(client, auth, host["id"]))["state"] == "host_key_changed" and h)
        assert "jump host jumper@127.0.0.1" in changed["error"]
        label = f"jumper@127.0.0.1:{bastion.port}"
        assert changed["jump_host_keys_pending"] == {label: bastion.host_key.get_fingerprint()}
        accepted = client.post(f"/api/ssh-hosts/{host['id']}/accept-host-key", headers=auth).json()
        assert accepted["jump_host_keys"][label] == bastion.host_key.get_fingerprint() and not accepted["jump_host_keys_pending"]
        _wait(lambda: _host(client, auth, host["id"])["state"] == "connected")
    finally:
        client.put("/api/ssh-config", json={"text": ""}, headers=auth)
        for h in client.get("/api/ssh-hosts", headers=auth).json():
            client.delete(f"/api/ssh-hosts/{h['id']}", headers=auth)
        bastion.stop()
