"""Output people bring in to be parsed (manual runs), and shell cards."""

import asyncio
import uuid
from pathlib import Path

from castor_server import manual_runs
from castor_server.shells import SHELL_COMMAND

SAMPLES = Path(__file__).resolve().parents[1] / "castor_server" / "parser_samples"
NETEXEC = (SAMPLES / "netexec-smb.txt").read_text()


def _project(client, auth):
    code = f"MAN-{uuid.uuid4().hex[:5]}"
    return client.post("/api/projects", json={"codename": code, "name": code, "kind": "internal"}, headers=auth).json()


def _card(client, auth, project, **extra):
    r = client.post(f"/api/projects/{project['id']}/tasks", json={"title": "Poke at SMB", **extra}, headers=auth)
    assert r.status_code == 201, r.text
    return r.json()


def test_the_text_of_a_comment():
    body = "Ran this by hand:\n\n```\nline one\nline two\n```\n\nand ![shot](/api/x)\n\n~~~text\nmore\n~~~\n"
    assert manual_runs.text_of_writing(body) == "line one\nline two\nmore\n"  # its code blocks
    assert manual_runs.text_of_writing("10.0.0.5 is up ![x](/a.png)") == "10.0.0.5 is up\n"  # else the text


def test_parsers_that_know_the_format_come_first():
    found = asyncio.run(manual_runs.rank(NETEXEC))
    assert found[0]["parser"] == "netexec.smb" and found[0]["count"] == 4 and not found[0]["generic"]
    generic = [f["parser"] for f in found if f["generic"]]
    assert "lines" in generic and found.index(next(f for f in found if f["generic"])) > 0
    assert asyncio.run(manual_runs.rank("")) == []


def test_output_from_a_comment_becomes_a_run_and_assets(client, auth):
    project = _project(client, auth)
    card = _card(client, auth, project)
    comment = client.post(f"/api/tasks/{card['id']}/comments", json={"body": f"From my box:\n\n```\n{NETEXEC}```\n"}, headers=auth).json()

    tried = client.post("/api/parsers/try", json={"comment_id": comment["id"]}, headers=auth)
    assert tried.status_code == 200, tried.text
    tried = tried.json()
    assert tried["task_id"] == card["id"] and tried["origin"] == {"from": "comment", "id": comment["id"]}
    assert tried["text"].startswith("SMB ") and tried["candidates"][0]["parser"] == "netexec.smb"

    r = client.post(f"/api/tasks/{card['id']}/manual-runs", json={"comment_id": comment["id"], "parser": "netexec.smb"}, headers=auth)
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["runner"] == "manual" and job["status"] == "succeeded" and job["origin"]["from"] == "comment"
    outputs = client.get(f"/api/jobs/{job['id']}/outputs", headers=auth).json()
    assert outputs[0]["name"] == "results" and outputs[0]["parser"] == "netexec.smb" and len(outputs[0]["value"]) == 4
    hosts = client.get(f"/api/projects/{project['id']}/assets?type=host", headers=auth).json()["items"]
    assert {"192.168.1.10", "192.168.1.30"} <= {h["fields"].get("ip") for h in hosts}

    # Without a parser it's kept unread (the AI can draft one for it); pasted text works too.
    r = client.post(f"/api/tasks/{card['id']}/manual-runs", json={"text": "whatever this is\n"}, headers=auth)
    assert r.status_code == 201, r.text
    unread = client.get(f"/api/jobs/{r.json()['id']}/outputs", headers=auth).json()[0]
    assert unread["error"] == manual_runs.UNREAD and r.json()["origin"] == {"from": "paste"}

    # A run's output (a shell session, say) can be brought in too, or some of its lines.
    tried = client.post("/api/parsers/try", json={"job_id": job["id"], "lines": [0, 2]}, headers=auth).json()
    assert tried["text"].count("\n") == 1 and tried["candidates"][0]["parser"] == "netexec.smb"

    # Not into another project's card; not an unknown parser.
    other = _card(client, auth, _project(client, auth))
    assert client.post(f"/api/tasks/{other['id']}/manual-runs", json={"comment_id": comment["id"], "parser": "netexec.smb"},
                       headers=auth).status_code == 422
    assert client.post(f"/api/tasks/{card['id']}/manual-runs", json={"text": "x", "parser": "nope"}, headers=auth).status_code == 422


def test_shell_cards(client, auth):
    project = _project(client, auth)
    card = _card(client, auth, project, shell=True)
    assert card["shell"] and card["kind"] == "command" and card["tty"] and card["command"] == SHELL_COMMAND

    # Back to an ordinary command card: nothing to run until it's given a command.
    r = client.patch(f"/api/tasks/{card['id']}", json={"shell": False}, headers=auth).json()
    assert not r["shell"] and r["command"] == "" and not r["tty"]
    r = client.patch(f"/api/tasks/{card['id']}", json={"shell": True}, headers=auth).json()
    assert r["shell"] and r["command"] == SHELL_COMMAND

    # It sits at its prompt all the time: that's never a "waiting for input" alert.
    from castor_server import interactive
    from castor_server import models as m
    from castor_server.db import SessionLocal

    async def prompt() -> str | None:
        async with SessionLocal() as db:
            job = m.Job(task_id=uuid.UUID(card["id"]), command=SHELL_COMMAND, tty=True, status=m.JobStatus.running, env={},
                        var_bindings={}, container_flags=[])
            db.add(job)
            await db.commit()
            await interactive.on_prompt(db, job, "user@box:~$")
            await db.refresh(job)
            return job.waiting_input

    assert asyncio.run(prompt()) is None


def test_a_drafted_parser_reads_unread_output(client, auth):
    """What the AI drafts for output brought in without a parser reads it, and its results become assets."""
    from castor_server import models as m
    from castor_server.db import SessionLocal
    from castor_server.parser_spec import parse_parser_spec

    project = _project(client, auth)
    card = _card(client, auth, project)
    job = client.post(f"/api/tasks/{card['id']}/manual-runs", json={"text": "10.9.8.1\n10.9.8.2\n"}, headers=auth).json()
    yaml = "id: plain-hosts-manual\nname: Plain hosts\ntype: list<host>\nformat: lines\nfields: {ip: line}\n"

    async def drafted() -> str:
        async with SessionLocal() as db:
            p = m.ParserProposal(job_id=uuid.UUID(job["id"]), project_id=uuid.UUID(project["id"]), output_name="results",
                                 status="ready", yaml=yaml, spec=parse_parser_spec(yaml).model_dump(mode="json"), count=2)
            db.add(p)
            await db.commit()
            return str(p.id)

    r = client.post(f"/api/parser-proposals/{asyncio.run(drafted())}/apply", json={}, headers=auth)
    assert r.status_code == 200, r.text
    out = client.get(f"/api/jobs/{job['id']}/outputs", headers=auth).json()[0]
    assert out["error"] is None and len(out["value"]) == 2
    hosts = client.get(f"/api/projects/{project['id']}/assets?type=host", headers=auth).json()["items"]
    assert {"10.9.8.1", "10.9.8.2"} <= {h["fields"].get("ip") for h in hosts}


def test_a_shell_runs_in_a_terminal_and_leaving_it_isnt_a_failure(client, auth):
    import json

    project = _project(client, auth)
    name = f"shell-agent-{uuid.uuid4().hex[:6]}"
    with client.websocket_connect("/ws/agent") as ws:
        ws.send_text(json.dumps({"type": "hello", "secret": "test-agent-secret", "name": name, "hostname": "box", "os": "Linux",
                                 "username": "u", "version": "1", "capabilities": {"runtimes": ["host"], "tty": True}, "tags": [],
                                 "max_concurrency": 2}))
        assert json.loads(ws.receive_text())["type"] == "hello.ok"
        agent = next(a for a in client.get("/api/agents", headers=auth).json() if a["name"] == name)
        card = _card(client, auth, project, shell=True, agent_selector={"agent_id": agent["id"]})
        client.post(f"/api/tasks/{card['id']}/run", headers=auth)
        while (msg := json.loads(ws.receive_text()))["type"] != "job.run":
            pass
        assert msg["job"]["command"] == SHELL_COMMAND and msg["job"]["tty"] and not msg["job"].get("timeout_s")
        ws.send_text(json.dumps({"type": "job.started", "job_id": msg["job"]["id"]}))
        ws.send_text(json.dumps({"type": "job.finished", "job_id": msg["job"]["id"], "exit_code": 127}))
        import time

        for _ in range(50):
            job = client.get(f"/api/jobs/{msg['job']['id']}", headers=auth).json()
            if job["status"] not in ("dispatched", "running"):
                break
            time.sleep(0.05)
        assert job["status"] == "succeeded" and job["exit_code"] == 127
