"""Parsers people and the AI write (declarative definitions), the library API, and what
happens when a job's output isn't in the format its parser reads."""

import uuid

import pytest
from sqlalchemy import select

from castor_server.ai import try_parser
from castor_server.outputs import is_mismatch
from castor_server.parser_spec import parse_parser_spec, run

MASSCAN = r"""id: {id}
name: masscan list
description: Open ports from masscan -oL
type: list<service>
format: regex
pattern: '^open (?P<proto>\w+) (?P<port>\d+) (?P<host>\S+)'
sample: |
  #masscan
  open tcp 80 10.0.0.1 1700000000
  open tcp 443 10.0.0.2 1700000000
"""


def test_formats_read_into_the_output_types():
    services = run(parse_parser_spec(MASSCAN.format(id="m")), b"open tcp 80 10.0.0.1 1\nopen udp 53 10.0.0.2 1\nopen tcp 80 10.0.0.1 1\n")
    assert services == [
        {"host": "10.0.0.1", "port": 80, "proto": "tcp", "state": "open", "name": "", "product": "", "version": ""},
        {"host": "10.0.0.2", "port": 53, "proto": "udp", "state": "open", "name": "", "product": "", "version": ""},
    ]  # typed, defaults filled, duplicates dropped
    urls = run(parse_parser_spec("id: j\nname: j\ntype: list<url>\nformat: json\nrecords: data.items\nfields: {url: link, status: code}\n"),
               b'{"data": {"items": [{"link": "http://a", "code": "200"}, {"code": 1}]}}')
    assert urls[0]["url"] == "http://a" and urls[0]["status"] == 200 and len(urls) == 1  # the record without a url is left out
    hosts = run(parse_parser_spec("id: x\nname: x\ntype: list<host>\nformat: xml\nrecords: .//h\nfields: {ip: '@a', hostnames: n}\n"),
                b'<r><h a="10.1.1.1"><n>web.local</n></h></r>')
    assert hosts == [{"ip": "10.1.1.1", "hostnames": ["web.local"], "status": "up"}]
    ips = run(parse_parser_spec("id: c\nname: c\ntype: list<string>\nformat: csv\nfields: {value: ip}\n"), b"ip,port\n1.1.1.1,80\n2.2.2.2,22\n")
    assert ips == ["1.1.1.1", "2.2.2.2"]
    signing = run(parse_parser_spec("id: k\nname: k\ntype: list<smb_host>\nformat: jsonl\n"), b'{"host": "10.0.0.9", "signing": "false"}\n')
    assert signing[0]["signing"] is False and signing[0]["port"] == 445


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("id: nmap.hosts\nname: x\ntype: list<host>\nformat: lines\n", "built-in"),
        ("id: a\nname: a\ntype: list<host>\nformat: lines\nfields: {bogus: x}\n", "not a field of host"),
        ("id: a\nname: a\ntype: list<host>\nformat: regex\n", "needs a pattern"),
        ("id: a\nname: a\ntype: list<frogs>\nformat: lines\n", "type must be one of"),
        ("id: a\nname: a\ntype: list<host>\nformat: python\n", "format"),
        ("id: a\nname: a\ntype: string\nformat: lines\nimport: os\n", "Extra inputs"),
    ],
)
def test_definitions_are_checked(text, message):
    with pytest.raises(ValueError, match=message):
        parse_parser_spec(text)


def test_a_draft_has_to_read_its_sample():
    _, _, problems = try_parser("id: a\nname: a\ntype: list<host>\nformat: lines\n", "10.0.0.1\n")
    assert problems and "none had a ip" in problems[0]  # no `ip` field: every record left out
    spec, value, problems = try_parser("id: a\nname: a\ntype: list<host>\nformat: lines\nfields: {ip: line}\n", "10.0.0.1\n")
    assert not problems and value == [{"ip": "10.0.0.1", "hostnames": [], "status": "up"}]
    assert is_mismatch("not valid nmap XML (was the scan interrupted?)")
    assert not is_mismatch("no file matched /work/x.xml (the tool may have stopped before writing it)")
    assert not is_mismatch("the tool wrote nothing to read") and not is_mismatch(None)


def test_library_parsers_are_used_like_built_ins(client, auth):
    pid = f"masscan-{uuid.uuid4().hex[:6]}"
    r = client.post("/api/parsers", json={"yaml": MASSCAN.format(id=pid)}, headers=auth)
    assert r.status_code == 201, r.text
    assert r.json()["origin"] == "user" and r.json()["type"] == "list<service>" and "open tcp 80" in r.json()["sample"]
    assert client.post("/api/parsers", json={"yaml": MASSCAN.format(id=pid)}, headers=auth).status_code == 409
    listed = {p["name"]: p for p in client.get("/api/parsers", headers=auth).json()}
    assert listed[pid]["origin"] == "user" and listed["nmap.hosts"]["origin"] == "builtin"
    tried = client.post(f"/api/parsers/{pid}/test", json={"input": "open tcp 22 10.9.9.9 1\n"}, headers=auth).json()
    assert tried["value"][0]["host"] == "10.9.9.9" and tried["value"][0]["port"] == 22

    check = client.post("/api/parsers/validate", json={"yaml": MASSCAN.format(id="x"), "input": "nothing useful\n"}, headers=auth).json()
    assert not check["ok"] and "reads nothing" in check["errors"][0]
    check = client.post("/api/parsers/validate", json={"yaml": MASSCAN.format(id="x")}, headers=auth).json()
    assert check["ok"] and check["count"] == 2  # its own sample

    # A command can name it, and then it can't be deleted or change type.
    cid = f"masscan-cmd-{uuid.uuid4().hex[:6]}"
    yaml_cmd = f"id: {cid}\nname: masscan\nargs: [{{name: t}}]\ncommand: masscan {{{{ t }}}} -oL -\noutputs:\n  - name: services\n    parser: {pid}\n"
    r = client.post("/api/commands", json={"yaml": yaml_cmd}, headers=auth)
    assert r.status_code == 201, r.text
    assert r.json()["spec"]["outputs"][0]["type"] == "list<service>"
    assert client.delete(f"/api/parsers/{pid}", headers=auth).status_code == 409
    retyped = MASSCAN.format(id=pid).replace("type: list<service>", "type: list<string>")
    assert client.put(f"/api/parsers/{pid}", json={"yaml": retyped}, headers=auth).status_code == 409
    assert client.put(f"/api/parsers/{pid}", json={"yaml": MASSCAN.format(id=pid).replace("masscan list", "masscan -oL")}, headers=auth).status_code == 200
    assert client.delete("/api/parsers/nmap.hosts", headers=auth).status_code == 409  # built-ins stay
    client.delete(f"/api/commands/{cid}", headers=auth)
    assert client.delete(f"/api/parsers/{pid}", headers=auth).status_code == 204
    assert client.get(f"/api/parsers/{pid}", headers=auth).status_code == 404


def _start(client, auth):
    p = client.post("/api/projects", json={"codename": f"PX-{uuid.uuid4().hex[:6]}", "name": "Parsers", "kind": "internal"}, headers=auth).json()
    r = client.post("/api/playbooks/branching-demo/run", json={"project_id": p["id"], "inputs": {}}, headers=auth)
    assert r.status_code == 201, r.text
    return uuid.UUID(r.json()["id"])


@pytest.mark.asyncio
async def test_unreadable_output_pauses_the_step_and_a_draft_continues_it(client, auth):
    """seed_hosts (on_failure: ask) prints plain lines instead of the nmap XML its parser
    reads: the step waits, and using a parser drafted for that output carries the run on."""
    from castor_server import models as m
    from castor_server.db import SessionLocal
    from castor_server.services import advance_playbook_run, playbook_run_out

    run_id = _start(client, auth)
    async with SessionLocal() as db:
        run = await db.get(m.PlaybookRun, run_id)
        tid = uuid.UUID(run.step_task_ids["seed_hosts"])
        job = (await db.execute(select(m.Job).where(m.Job.task_id == tid))).scalars().first()
        job.status = m.JobStatus.succeeded
        job.exit_code = 0
        job.output_specs = [{"name": "hosts", "type": "list<host>", "parser": "nmap.hosts", "source": None, "options": {}}]
        db.add(m.JobLogChunk(job_id=job.id, seq=0, stream="stdout", data="10.30.0.3\n10.30.0.4\n"))
        db.add(m.JobOutput(job_id=job.id, name="hosts", type="list<host>", parser="nmap.hosts", error="not valid nmap XML: syntax error"))
        await db.commit()
        await advance_playbook_run(db, run_id)
        db.expire_all()
        out = await playbook_run_out(db, await db.get(m.PlaybookRun, run_id))
        assert out.step_status["seed_hosts"] == "attention"
        assert out.step_status["smb_scan"] == "pending"
        assert "hosts output couldn't be read (nmap.hosts" in out.step_results["seed_hosts"]["reason"]
        assert out.step_results["seed_hosts"]["parse"] == {"output": "hosts", "parser": "nmap.hosts"}

        # What the AI would have drafted (no model in the tests): read each line as a host.
        spec = parse_parser_spec("id: plain-hosts\nname: Plain hosts\ntype: list<host>\nformat: lines\nfields: {ip: line}\n")
        proposal = m.ParserProposal(job_id=job.id, project_id=run.project_id, output_name="hosts", failed_parser="nmap.hosts",
                                    failure="not valid nmap XML", status="ready", spec=spec.model_dump(mode="json"),
                                    yaml="id: plain-hosts\nname: Plain hosts\ntype: list<host>\nformat: lines\nfields: {ip: line}\n", count=2)
        db.add(proposal)
        await db.commit()
        proposal_id = proposal.id

    listed = client.get(f"/api/jobs/{job.id}/parser-proposals", headers=auth).json()
    assert [p["status"] for p in listed] == ["ready"]
    saved_as = f"plain-hosts-{uuid.uuid4().hex[:6]}"
    r = client.post(f"/api/parser-proposals/{proposal_id}/apply", json={"save_as": saved_as}, headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "applied" and r.json()["saved_as"] == saved_as

    async with SessionLocal() as db:
        out = await playbook_run_out(db, await db.get(m.PlaybookRun, run_id))
        assert out.step_status["seed_hosts"] == "succeeded"
        assert out.step_status["smb_scan"] == "queued"  # fed by the hosts it reads now
        rows = (await db.execute(select(m.JobOutput).where(m.JobOutput.job_id == job.id))).scalars().all()
        assert rows[0].error is None and [h["ip"] for h in rows[0].value] == ["10.30.0.3", "10.30.0.4"]
    saved = client.get(f"/api/parsers/{saved_as}", headers=auth).json()
    assert saved["origin"] == "user" and "10.30.0.3" in saved["sample"]  # kept with the output it was written for
    assert client.post(f"/api/parser-proposals/{proposal_id}/apply", json={"save_as": saved_as}, headers=auth).status_code == 409


def test_new_routes_need_their_scopes():
    from castor_server.api_tokens import required_scope

    assert required_scope("POST", "/api/parsers/validate") == "library:read"
    assert required_scope("POST", "/api/parsers/draft") == "ai:use"
    assert required_scope("POST", "/api/parsers") == "library:write"
    assert required_scope("POST", "/api/parser-proposals/x/apply") == "jobs:write"
    assert required_scope("POST", "/api/jobs/x/outputs/y/parser-proposal") == "jobs:write"
