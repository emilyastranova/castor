"""Built-ins whose file left the library are retired at the next sync: commands and playbooks
go (cards keep their command line, runs their snapshot), a tool goes unless a command uses it."""

import asyncio
import uuid

from castor_server import models as m
from castor_server.command_library import sync_builtin_definitions
from castor_server.command_spec import parse_spec
from castor_server.db import SessionLocal
from castor_server.playbook_library import sync_builtin_playbooks
from castor_server.tool_library import sync_builtin_tools
from castor_server.tool_spec import parse_tool_spec


def test_retired_builtins_are_removed(client, auth):
    p = client.post("/api/projects", json={"codename": f"RT-{uuid.uuid4().hex[:6]}", "name": "Retire", "kind": "internal"}, headers=auth).json()
    gone_cmd, gone_pb, gone_tool, kept_tool = (f"gone-{k}-{uuid.uuid4().hex[:6]}" for k in ("cmd", "pb", "tool", "kept"))

    def command(cid, tool=None):
        text = f"id: {cid}\nname: {cid}\nschema_version: 1\ncommand: echo hi\n" + (f"tool: {tool}\n" if tool else "")
        return parse_spec(text).model_dump(mode="json"), text

    def tool(tid):
        text = f"id: {tid}\nname: {tid}\nimage: docker.io/library/alpine\n"
        return parse_tool_spec(text).model_dump(mode="json"), text

    async def seed():
        async with SessionLocal() as db:
            for cid, origin, uses in ((gone_cmd, "builtin", None), (f"uses-{kept_tool}", "user", kept_tool)):
                spec, text = command(cid, uses)  # the second is a user command still using kept_tool
                db.add(m.CommandDefinition(id=cid, origin=origin, name=cid, spec=spec, source_yaml=text))
            for tid in (gone_tool, kept_tool):
                spec, text = tool(tid)
                db.add(m.ToolDefinition(id=tid, origin="builtin", name=tid, spec=spec, source_yaml=text))
            db.add(m.PlaybookDefinition(id=gone_pb, origin="builtin", name="Old", spec={"id": gone_pb}, source_yaml=""))
            await db.commit()

    asyncio.run(seed())
    task = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Old card", "kind": "command", "command": "echo old"}, headers=auth).json()

    async def link_and_sync():
        async with SessionLocal() as db:
            (await db.get(m.Task, uuid.UUID(task["id"]))).command_ref = gone_cmd
            await db.commit()
            await sync_builtin_tools(db)
            await sync_builtin_definitions(db)
            await sync_builtin_playbooks(db)
            return (await db.get(m.CommandDefinition, gone_cmd), await db.get(m.PlaybookDefinition, gone_pb),
                    await db.get(m.ToolDefinition, gone_tool), await db.get(m.ToolDefinition, kept_tool))

    cmd, pb, tool, kept = asyncio.run(link_and_sync())
    assert cmd is None and pb is None and tool is None
    assert kept is not None and kept.origin == "user"
    card = client.get(f"/api/tasks/{task['id']}", headers=auth).json()
    assert card["command_ref"] is None and card["command"] == "echo old"
    # The real built-ins are untouched.
    assert client.get("/api/commands/nmap-ping-sweep", headers=auth).status_code == 200

    async def clean_up():  # tests share one database
        async with SessionLocal() as db:
            await db.delete(await db.get(m.CommandDefinition, f"uses-{kept_tool}"))
            await db.delete(await db.get(m.ToolDefinition, kept_tool))
            await db.commit()

    asyncio.run(clean_up())


def test_a_broken_tool_row_doesnt_stop_dispatch():
    """tools_by_id skips a stored tool whose spec no longer loads (the dispatcher calls it)."""
    from castor_server.tool_library import tools_by_id

    bad = f"broken-{uuid.uuid4().hex[:6]}"

    async def check():
        async with SessionLocal() as db:
            db.add(m.ToolDefinition(id=bad, origin="user", name=bad, spec={"id": bad}, source_yaml=""))
            await db.commit()
            tools = await tools_by_id(db)
            await db.delete(await db.get(m.ToolDefinition, bad))
            await db.commit()
            return tools

    tools = asyncio.run(check())
    assert bad not in tools and "nmap" in tools
