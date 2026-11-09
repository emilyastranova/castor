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


def test_point_output_at_edits_only_that_output():
    from castor_server.parser_library import point_output_at

    src = ("id: x\nname: X\nargs:\n  - name: hosts\n    type: list\ncommand: run {{ hosts }}  # keep me\noutputs:\n"
           "  - name: hosts   # what it found\n    parser: nmap.hosts\n    options:\n      include_down: false\n    source: /work/a.xml\n"
           "  - name: other\n    parser: lines\n")
    out = point_output_at(src, "hosts", "plain-hosts")
    assert "args:\n  - name: hosts\n    type: list\n" in out  # the argument of the same name is untouched
    assert "  - name: hosts   # what it found\n    parser: plain-hosts\n    source: /work/a.xml\n" in out
    assert "include_down" not in out and "# keep me" in out and "  - name: other\n    parser: lines" in out
    flow = point_output_at("id: x\nname: X\ncommand: r\noutputs: [{name: hosts, parser: lines}]\n", "hosts", "p")
    assert "parser: p" in flow


async def _job_with_unread_output(client, auth, command_ref):
    from castor_server import models as m
    from castor_server.db import SessionLocal

    p = client.post("/api/projects", json={"codename": f"PU-{uuid.uuid4().hex[:6]}", "name": "PU", "kind": "internal"}, headers=auth).json()
    args = {"targets": "10.0.0.0/24"} if command_ref == "nmap-ping-sweep" else {}
    task = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Sweep", "kind": "command", "command_ref": command_ref, "command_args": args}, headers=auth)
    assert task.status_code == 201, task.text
    async with SessionLocal() as db:
        job = m.Job(task_id=uuid.UUID(task.json()["id"]), command="x", status=m.JobStatus.succeeded, exit_code=0,
                    output_specs=[{"name": "hosts", "type": "list<host>", "parser": "nmap.hosts", "source": None, "options": {}}])
        db.add(job)
        await db.flush()
        db.add(m.JobLogChunk(job_id=job.id, seq=0, stream="stdout", data="10.50.0.1\n"))
        spec = parse_parser_spec("id: plain-hosts\nname: Plain hosts\ntype: list<host>\nformat: lines\nfields: {ip: line}\n")
        proposal = m.ParserProposal(job_id=job.id, project_id=uuid.UUID(p["id"]), output_name="hosts", failed_parser="nmap.hosts", status="ready",
                                    spec=spec.model_dump(mode="json"), yaml="id: plain-hosts\nname: Plain hosts\ntype: list<host>\nformat: lines\nfields: {ip: line}\n", count=1)
        db.add(proposal)
        await db.commit()
        return proposal.id


@pytest.mark.asyncio
async def test_keeping_a_draft_can_point_your_command_at_it(client, auth):
    cid = f"pu-sweep-{uuid.uuid4().hex[:6]}"
    yaml_cmd = f"id: {cid}\nname: Sweep\ncommand: sweep  # prints hosts\noutputs:\n  - name: hosts\n    parser: nmap.hosts\n"
    assert client.post("/api/commands", json={"yaml": yaml_cmd}, headers=auth).status_code == 201
    proposal_id = await _job_with_unread_output(client, auth, cid)
    kept = f"plain-hosts-{uuid.uuid4().hex[:6]}"
    r = client.post(f"/api/parser-proposals/{proposal_id}/apply", json={"save_as": kept, "use_in_command": True}, headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["command_note"] == f"Sweep reads hosts with {kept} from now on."
    cmd = client.get(f"/api/commands/{cid}", headers=auth).json()
    assert cmd["spec"]["outputs"][0]["parser"] == kept and "# prints hosts" in cmd["source_yaml"]
    assert client.get(f"/api/parsers/{kept}", headers=auth).json()["used_by"] == [cid]

    # A built-in command stays as it is; the note says how to use the parser with it.
    proposal_id = await _job_with_unread_output(client, auth, "nmap-ping-sweep")
    other = f"plain-hosts-{uuid.uuid4().hex[:6]}"
    r = client.post(f"/api/parser-proposals/{proposal_id}/apply", json={"save_as": other, "use_in_command": True}, headers=auth)
    assert "built in" in r.json()["command_note"]
    assert client.get("/api/commands/nmap-ping-sweep", headers=auth).json()["spec"]["outputs"][0]["parser"] == "nmap.hosts"


JS = """id: {id}
name: masscan (js)
type: list<service>
format: javascript
code: |
  function parse(text) {{
    return text.split('\\n')
      .filter(l => l.startsWith('open '))
      .map(l => {{ const [, proto, port, host] = l.split(/\\s+/); return {{ host, port, proto }}; }});
  }}
"""


def _js(body: str) -> str:
    return "id: js\nname: js\ntype: list<string>\nformat: javascript\ncode: |\n" + "".join(f"  {line}\n" for line in body.splitlines())


def test_javascript_parsers_run_sandboxed():
    services = run(parse_parser_spec(JS.format(id="m")), b"open tcp 80 10.0.0.1 1\nopen udp 53 10.0.0.2 1\n")
    assert services[0] == {"host": "10.0.0.1", "port": 80, "proto": "tcp", "state": "open", "name": "", "product": "", "version": ""}
    assert run(parse_parser_spec(_js("function parse(t) { return t.trim().split(',') }")), b"a,b") == ["a", "b"]
    from castor_server.parsers import ParseError

    # Nothing to reach: no require, process, fetch or timers.
    for body in ("function parse(t) { return [require('fs').readFileSync('/etc/passwd', 'utf8')] }",
                 "function parse(t) { return [process.env.HOME] }", "function parse(t) { return [fetch('http://x')] }"):
        with pytest.raises(ParseError, match="is not defined"):
            run(parse_parser_spec(_js(body)), b"x")
    with pytest.raises(ParseError, match="took longer"):
        run(parse_parser_spec(_js("function parse(t) { while (true) {} }")), b"x")
    with pytest.raises(ParseError, match="memory"):
        run(parse_parser_spec(_js("function parse(t) { const a = []; while (true) a.push(new Array(1e6).fill(t)) }")), b"x")
    with pytest.raises(ValueError, match="define a function parse"):
        parse_parser_spec(_js("function read(t) { return [] }"))
    with pytest.raises(ValueError, match="SyntaxError"):
        parse_parser_spec(_js("function parse(t) { return t."))
    with pytest.raises(ValueError, match="code is for format javascript"):
        parse_parser_spec("id: a\nname: a\ntype: list<string>\nformat: lines\ncode: 'function parse(t) {}'\n")


def test_javascript_parsers_through_the_api(client, auth):
    pid = f"js-{uuid.uuid4().hex[:6]}"
    r = client.post("/api/parsers", json={"yaml": JS.format(id=pid)}, headers=auth)
    assert r.status_code == 201, r.text
    tried = client.post(f"/api/parsers/{pid}/test", json={"input": "open tcp 22 10.9.9.9 1\n"}, headers=auth).json()
    assert tried["value"] == [{"host": "10.9.9.9", "port": 22, "proto": "tcp", "state": "open", "name": "", "product": "", "version": ""}]
    check = client.post("/api/parsers/validate", json={"yaml": _js("function parse(t) { throw new Error('nope') }"), "input": "x"}, headers=auth).json()
    assert not check["ok"] and "nope" in check["errors"][0]
    assert client.delete(f"/api/parsers/{pid}", headers=auth).status_code == 204


SSH_LOG = b"""Nov  8 10:01:02 web sshd[123]: Accepted publickey for alice from 10.0.0.5 port 50222 ssh2
Nov  8 10:03:00 web sshd[124]: Received disconnect from 10.0.0.5 port 50222:11: bye
Nov  8 10:05:13 web sshd[130]: Failed password for root from 203.0.113.9 port 40111 ssh2
"""
SSH_PARSER = r"""id: {id}
name: SSH logins
type: list<object>
format: regex
pattern: '^(?P<time>\w{{3}}\s+\d+ [\d:]+) \S+ sshd\[\d+\]: (?P<result>Accepted|Failed) (?P<method>\S+) for (?P<user>\S+) from (?P<ip>\S+) port (?P<port>\d+)'
types: {{port: int}}
"""


def test_objects_take_whatever_fields_the_parser_gives():
    rows = run(parse_parser_spec(SSH_PARSER.format(id="ssh")), SSH_LOG)
    assert rows == [
        {"time": "Nov  8 10:01:02", "result": "Accepted", "method": "publickey", "user": "alice", "ip": "10.0.0.5", "port": 50222},
        {"time": "Nov  8 10:05:13", "result": "Failed", "method": "password", "user": "root", "ip": "203.0.113.9", "port": 40111},
    ]  # named groups are the fields; types turns text into numbers; lines that don't match are left out
    assert run(parse_parser_spec("id: c\nname: c\ntype: list<object>\nformat: csv\ntypes: {n: int, admin: bool}\n"),
               b"user,n,admin\nalice,3,yes\nbob,1,no\n") == [{"user": "alice", "n": 3, "admin": True}, {"user": "bob", "n": 1, "admin": False}]
    assert run(parse_parser_spec("id: x\nname: x\ntype: list<object>\nformat: xml\nrecords: .//u\n"),
               b'<r><u id="1"><name>al</name></u></r>') == [{"id": "1", "name": "al"}]
    picked = run(parse_parser_spec("id: j\nname: j\ntype: list<object>\nformat: json\nrecords: items\nfields: {who: user.name}\nvalues: {source: api}\n"),
                 b'{"items": [{"user": {"name": "al"}}, {"user": {"name": "al"}}]}')
    assert picked == [{"who": "al", "source": "api"}]  # mapped, constant added, duplicates dropped
    assert run(parse_parser_spec("id: o\nname: o\ntype: object\nformat: json\n"), b'{"version": "9.6"}') == {"version": "9.6"}
    assert run(parse_parser_spec(_js("function parse(t) { return [{ user: 'a', logins: 2 }] }").replace("list<string>", "list<object>")), b"x") == [{"user": "a", "logins": 2}]
    with pytest.raises(ValueError, match="not a field of host"):
        parse_parser_spec("id: b\nname: b\ntype: list<host>\nformat: lines\ntypes: {foo: int}\n")


def test_objects_as_arguments():
    from castor_server.playbook_spec import resolve_output_value

    assert resolve_output_value([{"user": "alice"}, {"user": "bob"}]) == "alice,bob"  # one field: its value
    assert resolve_output_value([{"user": "alice", "n": 1}]) == '{"user":"alice","n":1}'


def test_ssh_logins_through_the_api(client, auth):
    pid = f"ssh-logins-{uuid.uuid4().hex[:6]}"
    r = client.post("/api/parsers", json={"yaml": SSH_PARSER.format(id=pid)}, headers=auth)
    assert r.status_code == 201, r.text
    assert r.json()["type"] == "list<object>"
    tried = client.post(f"/api/parsers/{pid}/test", json={"input": SSH_LOG.decode()}, headers=auth).json()
    assert [row["user"] for row in tried["value"]] == ["alice", "root"]
    client.delete(f"/api/parsers/{pid}", headers=auth)


def test_reference_comes_from_the_code(client, auth):
    ref = client.get("/api/parsers/reference", headers=auth).json()
    assert "javascript" in ref["formats"] and "int" in ref["field_types"] and ref["limits"]["js_seconds"] == 3.0
    types = {t["type"]: t for t in ref["types"]}
    assert types["list<object>"]["free"] and not types["list<object>"]["fields"]
    assert {"name": "ip", "kind": "str", "required": True} in types["list<host>"]["fields"]


def test_the_reference_guide_examples_parse_their_samples():
    """The editor's guide (ParserGuide.svelte) loads these with "Try this": each must read its sample."""
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "web/src/lib/components/ParserGuide.svelte").read_text()
    block = src[src.index("const EXAMPLES"):src.index("const KEYS")]
    found = re.findall(r"title: '(\w+)'.*?yaml: `(.*?)`,\s*sample: `(.*?)`", block, re.S)
    assert {t for t, _, _ in found} == {"lines", "regex", "json", "jsonl", "csv", "xml", "javascript"}
    for title, text, sample in found:
        # They're JavaScript template literals: \\ is one backslash.
        _, value, problems = try_parser(text.replace("\\\\", "\\"), sample.replace("\\\\", "\\"))
        assert not problems and value, (title, problems)
