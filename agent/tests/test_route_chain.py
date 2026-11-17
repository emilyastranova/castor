"""Routes: chains of proxies (and SSH jumps, with a real sshd from asyncssh when it's installed)."""

from __future__ import annotations

import asyncio
import shutil
import socket
import struct

import pytest

from castor_agent import route_chain as rc


async def echo_server() -> tuple[asyncio.base_events.Server, int]:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while data := await reader.read(1024):
            writer.write(b"echo:" + data)
            await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def proxy(auth: tuple[str, str] | None = None, tmp=None) -> rc.Endpoint:
    """A plain proxy: an endpoint over a chain with no hops (it connects directly)."""
    chain = rc.Chain([], workdir=tmp or "/tmp/castor-test-chain")
    ep = rc.Endpoint(chain, auth=auth)
    await ep.listen_tcp("127.0.0.1", 0)
    return ep


async def roundtrip(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, text: bytes = b"hi") -> bytes:
    writer.write(text)
    await writer.drain()
    data = await asyncio.wait_for(reader.read(100), 5)
    writer.close()
    return data


def hops(*specs: dict) -> list[rc.Hop]:
    return [rc.Hop.from_dict(i, s) for i, s in enumerate(specs)]


@pytest.mark.asyncio
async def test_http_then_socks5_with_password_then_socks4(tmp_path):
    _, target = await echo_server()
    p1 = await proxy(tmp=tmp_path / "a")
    p2 = await proxy(("alice", "pw"), tmp=tmp_path / "b")
    p3 = await proxy(tmp=tmp_path / "c")
    chain = rc.Chain(hops({"kind": "http", "host": "127.0.0.1", "port": p1.port},
                          {"kind": "socks5", "host": "127.0.0.1", "port": p2.port, "username": "alice", "password": "pw"},
                          {"kind": "socks4", "host": "127.0.0.1", "port": p3.port}), workdir=tmp_path / "chain")
    await chain.start()
    r, w = await chain.connect("127.0.0.1", target)
    assert await roundtrip(r, w) == b"echo:hi"
    result = await chain.test(("127.0.0.1", target))
    assert [h["ok"] for h in result["hops"]] == [True, True, True]
    assert result["target"]["ok"] is True


@pytest.mark.asyncio
async def test_where_it_breaks(tmp_path):
    _, target = await echo_server()
    p1 = await proxy(tmp=tmp_path / "a")
    p2 = await proxy(("alice", "pw"), tmp=tmp_path / "b")
    # Wrong password at hop 2: hop 2 is the one that's wrong.
    chain = rc.Chain(hops({"kind": "socks5", "host": "127.0.0.1", "port": p1.port},
                          {"kind": "socks5", "host": "127.0.0.1", "port": p2.port, "username": "alice", "password": "no"}),
                     workdir=tmp_path / "chain")
    await chain.start()
    result = await chain.test(("127.0.0.1", target))
    assert result["hops"][0]["ok"] is True
    assert result["hops"][1]["ok"] is False and "username and password" in result["hops"][1]["error"]
    assert "target" not in result
    with pytest.raises(rc.ChainError) as err:
        await chain.connect("127.0.0.1", target)
    assert err.value.hop == 1

    # Nothing listening at hop 2's address: hop 1 couldn't reach it.
    dead = rc.free_port()
    chain = rc.Chain(hops({"kind": "socks5", "host": "127.0.0.1", "port": p1.port},
                          {"kind": "http", "host": "127.0.0.1", "port": dead}), workdir=tmp_path / "chain2")
    await chain.start()
    result = await chain.test()
    assert result["hops"][0]["ok"] is True
    assert result["hops"][1]["ok"] is False and "not reachable from hop 1" in result["hops"][1]["error"]

    # The target refuses: the chain is fine, the target isn't.
    chain = rc.Chain(hops({"kind": "socks5", "host": "127.0.0.1", "port": p1.port}), workdir=tmp_path / "chain3")
    await chain.start()
    result = await chain.test(("127.0.0.1", dead))
    assert result["hops"][0]["ok"] is True
    assert result["target"]["ok"] is False and "refused" in result["target"]["error"]


@pytest.mark.asyncio
async def test_endpoint_speaks_socks5_socks4_and_http(tmp_path):
    _, target = await echo_server()
    hop = await proxy(tmp=tmp_path / "hop")
    chain = rc.Chain(hops({"kind": "socks5", "host": "127.0.0.1", "port": hop.port}), workdir=tmp_path / "c")
    await chain.start()
    open_end = rc.Endpoint(chain)
    await open_end.listen_tcp("127.0.0.1", 0)
    locked = rc.Endpoint(chain, auth=("u", "p"))
    await locked.listen_tcp("127.0.0.1", 0)

    r, w = await asyncio.open_connection("127.0.0.1", open_end.port)
    await rc.socks4_connect(r, w, "127.0.0.1", target)
    assert await roundtrip(r, w) == b"echo:hi"

    r, w = await asyncio.open_connection("127.0.0.1", locked.port)
    await rc.http_connect(r, w, "localhost", target, "u", "p")  # a name: passed on as a name
    assert await roundtrip(r, w) == b"echo:hi"

    r, w = await asyncio.open_connection("127.0.0.1", locked.port)
    with pytest.raises(ConnectionError):
        await rc.socks5_connect(r, w, "127.0.0.1", target, "u", "wrong")

    # A plain HTTP request through the proxy reaches the origin with its path.
    seen: list[bytes] = []

    async def origin(reader, writer):
        seen.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(origin, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    r, w = await asyncio.open_connection("127.0.0.1", locked.port)
    import base64
    w.write(f"GET http://127.0.0.1:{port}/a/b?c=1 HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            f"Proxy-Authorization: Basic {base64.b64encode(b'u:p').decode()}\r\n\r\n".encode())
    await w.drain()
    body = await asyncio.wait_for(r.read(), 5)
    assert body.endswith(b"ok")
    assert seen[0].startswith(b"GET /a/b?c=1 HTTP/1.1") and b"Proxy-Authorization" not in seen[0]


def test_fake_dns_maps_names_back():
    dns = rc.FakeDns()
    query = struct.pack("!HHHHHH", 7, 0x0100, 1, 0, 0, 0) + b"\x03dc1\x04corp\x05local\x00" + struct.pack("!HH", 1, 1)
    reply = dns.answer(query)
    assert reply is not None and struct.unpack("!H", reply[6:8])[0] == 1
    ip = socket.inet_ntoa(reply[-4:])
    assert ip.startswith("198.18.") and dns.name_for(ip) == "dc1.corp.local"
    assert dns.answer(query)[-4:] == reply[-4:]  # the same name, the same address
    aaaa = query[:-4] + struct.pack("!HH", 28, 1)
    assert struct.unpack("!H", dns.answer(aaaa)[6:8])[0] == 0  # no IPv6 answer


def test_ssh_errors_read_well():
    hop = rc.Hop.from_dict(1, {"kind": "ssh", "host": "jump", "username": "ops", "private_key": "k"})
    assert rc.ssh_reason("ops@jump: Permission denied (publickey).", hop) == "it refused the key for ops"
    assert "host key" in rc.ssh_reason("Host key verification failed.", hop)
    assert rc.ssh_reason("castor-route: the hop before couldn't reach jump:22 (host unreachable)\nkex...", hop).startswith(
        "the hop before couldn't reach")
    with pytest.raises(ValueError):
        rc.Hop.from_dict(0, {"kind": "ssh", "host": "-oProxyCommand=x"})


# ---------- SSH hops against a real SSH server ----------

asyncssh = pytest.importorskip("asyncssh")


async def sshd(tmp_path, name: str, password: str | None = None):
    """An SSH server that allows port forwarding; returns (server, port, host key line, client key)."""
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    client_key = asyncssh.generate_private_key("ssh-ed25519")
    keys = tmp_path / f"{name}_authorized"
    keys.write_text(client_key.export_public_key().decode())

    class Server(asyncssh.SSHServer):
        def password_auth_supported(self):
            return password is not None

        def validate_password(self, username, pw):
            return pw == password

        def connection_requested(self, dest_host, dest_port, orig_host, orig_port):
            return True  # direct-tcpip: what ssh -D asks for

    server = await asyncssh.create_server(Server, "127.0.0.1", 0, server_host_keys=[host_key],
                                          authorized_client_keys=str(keys))
    port = server.sockets[0].getsockname()[1]
    line = host_key.export_public_key().decode().split()
    return server, port, f"{line[0]} {line[1]}", client_key.export_private_key().decode()


needs_ssh = pytest.mark.skipif(not shutil.which("ssh"), reason="no OpenSSH client")


@needs_ssh
@pytest.mark.asyncio
async def test_ssh_jump_then_socks_then_ssh(tmp_path):
    _, target = await echo_server()
    _, p1, hk1, key1 = await sshd(tmp_path, "one")
    _, p2, hk2, _ = await sshd(tmp_path, "two", password="s3cret")
    mid = await proxy(tmp=tmp_path / "mid")
    events: list[dict] = []
    chain = rc.Chain(hops({"kind": "ssh", "host": "127.0.0.1", "port": p1, "username": "ops", "private_key": key1},
                          {"kind": "socks5", "host": "127.0.0.1", "port": mid.port},
                          {"kind": "ssh", "host": "127.0.0.1", "port": p2, "username": "ops", "password": "s3cret",
                           "host_key": hk2}),
                     workdir=tmp_path / "chain", on_event=events.append)
    await chain.start()
    assert chain.broken is None, chain.broken
    # The first hop's key was learned (trusted on first use) and reported.
    assert {"event": "host_key", "index": 0, "key": hk1, "changed": False} in events
    r, w = await chain.connect("127.0.0.1", target)
    assert await roundtrip(r, w) == b"echo:hi"
    result = await chain.test(("127.0.0.1", target))
    assert [h["ok"] for h in result["hops"]] == [True, True, True] and result["target"]["ok"]
    await chain.close()


@needs_ssh
@pytest.mark.asyncio
async def test_ssh_refusals_and_changed_keys(tmp_path):
    _, p1, hk1, key1 = await sshd(tmp_path, "one")
    other = asyncssh.generate_private_key("ssh-ed25519").export_private_key().decode()
    chain = rc.Chain(hops({"kind": "ssh", "host": "127.0.0.1", "port": p1, "username": "ops", "private_key": other}),
                     workdir=tmp_path / "a")
    await chain.start()
    assert chain.broken is not None and chain.broken.hop == 0 and "refused the key" in str(chain.broken)
    await chain.close()

    wrong = asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode().split()
    events: list[dict] = []
    chain = rc.Chain(hops({"kind": "ssh", "host": "127.0.0.1", "port": p1, "username": "ops", "private_key": key1,
                           "host_key": f"{wrong[0]} {wrong[1]}"}), workdir=tmp_path / "b", on_event=events.append)
    await chain.start()
    assert chain.broken is not None and "host key" in str(chain.broken)
    # What it shows now, found without signing in, for a person to accept.
    assert {"event": "host_key", "index": 0, "key": hk1, "changed": True} in events
    await chain.close()


# ---------- how a job is set up for its route ----------

def test_routed_container_job_joins_the_box(tmp_path):
    from castor_agent.routes import Applied, proxychains_conf, wrap_command
    from castor_agent.runtime import JobRunner

    job = {"id": "j1", "command": "nmap ${CASTOR_ROUTE_ARGS} -p 22 h", "image": "docker.io/instrumentisto/nmap",
           "container_flags": ["--network=bridge", "--cap-add=NET_RAW"], "env": {}}
    applied = Applied(method="hooked", network="container:castor-route-x", image="castor-tools/hooked-abc:1",
                      env={"CASTOR_ROUTE_ARGS": "-sT -Pn"}, files={".castor/route/proxychains.conf": proxychains_conf("127.0.0.1", 1080)},
                      wrap=True)
    runner = JobRunner(job, container_runtime="docker", allow_host=True, default_image="alpine", job_dir=str(tmp_path),
                       network="host", extra_run_args=["--network", "host"], route=applied)
    env = {**runner.job_env(), **runner.write_files(True)}
    argv, is_container = runner.build_argv(list(env))
    assert is_container
    joined = " ".join(argv)
    # The route's network, never the card's or the agent's; the hooked image; proxychains around it.
    assert "--network container:castor-route-x" in joined and "bridge" not in joined and "--network host" not in joined
    assert "--cap-add=NET_RAW" in argv and "castor-tools/hooked-abc:1" in argv
    assert argv[-1].startswith("pc=$(command -v proxychains4") and "nmap ${CASTOR_ROUTE_ARGS}" in argv[-1]
    assert env["CASTOR_ROUTE_PROXYCHAINS"] == "/work/.castor/route/proxychains.conf"
    assert "proxy_dns" in (tmp_path / ".castor/route/proxychains.conf").read_text()
    assert "-e" in argv and "CASTOR_ROUTE_ARGS" in argv  # by name only
    assert wrap_command("echo 'hi'").endswith("/bin/sh -c 'echo '\"'\"'hi'\"'\"''")
    runner.remove_files()
    assert not (tmp_path / ".castor").exists()


@pytest.mark.asyncio
async def test_apply_picks_the_first_way_that_works(tmp_path):
    from castor_agent.routes import Active, RouteError, apply

    active = Active(key="k", route={"dns": "remote"}, mode="box", dir=tmp_path, box_name="box1")
    plan = [{"method": "native", "scheme": "http", "args": "-proxy {url}", "flags": ""}, {"method": "transparent", "scheme": "socks5h"}]

    async def sink(_s, _d):
        pass

    got = await apply(active, plan, in_container=True, image="img", tools=None, sink=sink, have_proxychains=False)
    assert got.method == "native" and got.env["CASTOR_ROUTE_ARGS"] == "-proxy http://127.0.0.1:1080" and got.network == "container:box1"

    class NoHook:
        async def hooked_image(self, base, sink):
            return None, "no package manager"

    got = await apply(active, [{"method": "hooked"}, {"method": "transparent"}], in_container=True, image="img", tools=NoHook(),
                      sink=sink, have_proxychains=False)
    assert got.method == "transparent" and "CASTOR_ROUTE_ARGS" not in got.env
    with pytest.raises(RouteError, match="transparent needs a container"):
        await apply(active, [{"method": "transparent"}], in_container=False, image=None, tools=None, sink=sink, have_proxychains=False)
