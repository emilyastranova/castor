"""The agent's half of stdio MCP servers: start the program, relay lines both ways, stop it
when idle, and say when it exits."""

import asyncio
import json
import sys
import textwrap

from castor_agent.mcp_bridge import McpBridge

SERVER = textwrap.dedent("""
    import json, os, sys
    for line in sys.stdin:
        msg = json.loads(line)
        if "id" not in msg:
            continue
        if msg["method"] == "crash":
            print("dying", file=sys.stderr)
            sys.exit(3)
        result = {"echo": msg["method"], "token": os.environ.get("API_TOKEN")}
        print("not json, ignored")
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
""")


class NoTools:
    async def prepare(self, tool, sink, cwd):
        return "no tools here"


def test_a_stdio_program(tmp_path):
    script = tmp_path / "server.py"
    script.write_text(SERVER)
    sent = []

    async def send(msg):
        sent.append(msg)

    async def until(kind, n=1):
        for _ in range(200):
            if sum(1 for m in sent if m["type"] == kind) >= n:
                return [m for m in sent if m["type"] == kind]
            await asyncio.sleep(0.02)
        raise AssertionError(sent)

    async def go():
        bridge = McpBridge(send, None, True, "host", NoTools(), str(tmp_path / "mcp"))
        await bridge.handle({"type": "mcp.start", "session_id": "s1", "command": [sys.executable, str(script)],
                             "env": {"API_TOKEN": "t0k"}, "idle_s": 60})
        assert (await until("mcp.started"))[0] == {"type": "mcp.started", "session_id": "s1"}
        await bridge.handle({"type": "mcp.send", "session_id": "s1", "message": {"jsonrpc": "2.0", "id": 1, "method": "ping"}})
        reply = (await until("mcp.message"))[0]["message"]
        assert reply == {"jsonrpc": "2.0", "id": 1, "result": {"echo": "ping", "token": "t0k"}}
        # Starting it again while it runs is a no-op.
        await bridge.handle({"type": "mcp.start", "session_id": "s1", "command": ["nope"]})
        assert len(await until("mcp.started", 2)) == 2
        await bridge.handle({"type": "mcp.send", "session_id": "s1", "message": {"jsonrpc": "2.0", "id": 2, "method": "crash"}})
        exited = (await until("mcp.exited"))[0]
        assert exited["session_id"] == "s1" and "exit 3" in exited["error"] and "dying" in exited["error"]
        assert "s1" not in bridge.programs

        # Idle programs stop by themselves; host commands need --allow-host; a tool must prepare.
        await bridge.handle({"type": "mcp.start", "session_id": "s2", "command": [sys.executable, str(script)], "idle_s": 0.2})
        assert (await until("mcp.exited", 2))[-1]["session_id"] == "s2"
        strict = McpBridge(send, None, False, "host", NoTools(), str(tmp_path / "mcp"))
        await strict.handle({"type": "mcp.start", "session_id": "s3", "command": ["x"]})
        await bridge.handle({"type": "mcp.start", "session_id": "s4", "tool": {"id": "t", "kind": "image", "ref": "x"}})
        errors = {m["session_id"]: m.get("error") for m in sent if m["type"] == "mcp.started" and m.get("error")}
        assert "--allow-host" in errors["s3"] and errors["s4"] == "no tools here"

    asyncio.run(go())


def test_container_argv_passes_env_by_name(tmp_path):
    bridge = McpBridge(None, "podman", False, "bridge", NoTools(), str(tmp_path))
    argv = bridge.argv({"session_id": "abcdef1234567890", "command": ["--stdio"], "env": {"API_TOKEN": "secret"}}, "ghcr.io/x/mcp:1")
    assert argv == ["podman", "run", "-i", "--rm", "--name", "castor-mcp-abcdef123456", "--network", "bridge",
                    "-e", "API_TOKEN", "ghcr.io/x/mcp:1", "--stdio"]
    assert "secret" not in " ".join(argv)
