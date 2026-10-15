"""Phase 2b: parsers, output specs, and parsing a finished job's artifacts."""

import json
import time

import pytest

from castor_server import parsers
from castor_server.command_spec import parse_spec

FIXTURES = parsers.SAMPLES_DIR


def test_nmap_hosts():
    hosts = parsers.get("nmap.hosts").func((FIXTURES / "nmap-ping-sweep.xml").read_bytes())
    assert hosts == [
        {"ip": "10.10.0.1", "hostnames": ["gw.acme.internal"], "status": "up"},
        {"ip": "10.10.0.5", "hostnames": ["web01.acme.internal"], "status": "up"},
        {"ip": "10.10.0.6", "hostnames": [], "status": "down"},
    ]


def test_nmap_services_keeps_open_ports_only():
    services = parsers.get("nmap.services").func((FIXTURES / "nmap-service-scan.xml").read_bytes())
    assert [(s["port"], s["name"]) for s in services] == [(22, "ssh"), (80, "http"), (443, "http")]
    assert services[0] == {
        "host": "10.10.0.5",
        "port": 22,
        "proto": "tcp",
        "state": "open",
        "name": "ssh",
        "product": "OpenSSH",
        "version": "9.6p1 Ubuntu 3ubuntu13.5 Ubuntu Linux; protocol 2.0",
    }


def test_nmap_rejects_bad_input():
    with pytest.raises(parsers.ParseError, match="interrupted"):
        parsers.nmap_hosts(b"<nmaprun><host><status state='up'/>")
    with pytest.raises(parsers.ParseError, match="nmaprun"):
        parsers.nmap_hosts(b"<html/>")
    failed = (
        b'<nmaprun><runstats><finished exit="error" errormsg="Couldn&apos;t open a raw socket."/>'
        b'<hosts up="0" down="0" total="0"/></runstats></nmaprun>'
    )
    with pytest.raises(parsers.ParseError, match="stopped with an error: Couldn't open a raw socket"):
        parsers.nmap_services(failed)
    entity_bomb = b'<!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><nmaprun>&b;</nmaprun>'
    with pytest.raises(parsers.ParseError, match="unsafe"):
        parsers.nmap_hosts(entity_bomb)
    external = b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><nmaprun>&e;</nmaprun>'
    with pytest.raises(parsers.ParseError, match="unsafe"):
        parsers.nmap_hosts(external)


def test_ffuf_and_lines():
    doc = {"results": [{"url": "https://t/admin", "status": 301, "length": 0, "words": 1, "lines": 1}]}
    assert parsers.ffuf_results(json.dumps(doc).encode())[0]["url"] == "https://t/admin"
    with pytest.raises(parsers.ParseError):
        parsers.ffuf_results(b"[]")
    assert parsers.lines(b"a\n\n  b \n") == ["a", "b"]


def test_output_spec_types_come_from_the_parser():
    base = "id: x\nname: X\ncommand: echo\noutputs:\n"
    spec = parse_spec(base + "  - {name: hosts, type: list, parser: nmap.hosts, source: /work/a.xml}\n")
    assert spec.outputs[0].type == "list<host>"
    with pytest.raises(ValueError, match="unknown parser"):
        parse_spec(base + "  - {name: hosts, parser: nmap.nope}\n")
    with pytest.raises(ValueError, match="produces list<host>"):
        parse_spec(base + "  - {name: hosts, type: list<url>, parser: nmap.hosts}\n")
    with pytest.raises(ValueError, match="needs a parser"):
        parse_spec(base + "  - {name: hosts}\n")


def test_parser_options():
    sweep = (FIXTURES / "nmap-ping-sweep.xml").read_bytes()
    hosts = parsers.get("nmap.hosts")
    assert [h["ip"] for h in hosts.run(sweep, hosts.configure({"include_down": False}))] == ["10.10.0.1", "10.10.0.5"]

    scan = (FIXTURES / "nmap-service-scan.xml").read_bytes()
    services = parsers.get("nmap.services")
    assert len(services.run(scan, services.configure({"include_closed": True}))) == 4

    ffuf = parsers.get("ffuf.results")
    assert [r["status"] for r in ffuf.run(ffuf.sample_text().encode(), ffuf.configure({"status": [200]}))] == [200, 200]

    rx = parsers.get("regex")
    text = b"user=alice id=1\nuser=bob id=2\nuser=alice id=3"
    assert rx.run(text, rx.configure({"pattern": r"user=(\w+)", "group": 1})) == ["alice", "bob"]
    assert rx.run(text, rx.configure({"pattern": r"user=(?P<who>\w+)", "group": "who", "unique": False})) == ["alice", "bob", "alice"]
    assert rx.run(text, rx.configure({"pattern": "USER=b", "ignore_case": True})) == ["user=b"]
    with pytest.raises(parsers.ParseError, match="1 capture group"):
        rx.run(text, rx.configure({"pattern": "(a)", "group": 2}))
    with pytest.raises(parsers.OptionsError) as exc:
        rx.configure({"pattern": "(unclosed", "colour": "red"})
    assert set(exc.value.errors) == {"pattern", "colour"}


def test_every_sample_parses():
    for p in parsers.PARSERS.values():
        assert p.sample, f"{p.name} has no sample"
        value = p.run(p.sample_text().encode(), p.configure(p.sample_options))
        assert value, f"{p.name} sample produced nothing"


def test_output_spec_options_are_validated():
    base = "id: x\nname: X\ncommand: echo\noutputs:\n"
    spec = parse_spec(base + "  - {name: ids, parser: regex, options: {pattern: 'id=(\\d+)', group: 1}}\n")
    assert spec.outputs[0].options == {"pattern": "id=(\\d+)", "group": 1}
    with pytest.raises(ValueError, match="output ids: pattern: Field required"):
        parse_spec(base + "  - {name: ids, parser: regex}\n")
    with pytest.raises(ValueError, match="options need a parser"):
        parse_spec(base + "  - {name: f, source: /work/f, options: {a: 1}}\n")


def test_parsers_api(client, auth):
    listed = {p["name"]: p for p in client.get("/api/parsers", headers=auth).json()}
    assert listed["regex"]["options"][0]["name"] == "pattern" and listed["regex"]["options"][0]["required"]
    assert listed["nmap.hosts"]["sample"].startswith("<?xml")

    ok = client.post("/api/parsers/lines/test", json={"input": "a\nb\na", "options": {"unique": True}}, headers=auth).json()
    assert ok["value"] == ["a", "b"] and ok["error"] is None and ok["type"] == "list<string>"
    bad = client.post("/api/parsers/regex/test", json={"input": "x", "options": {"pattern": "("}}, headers=auth).json()
    assert "pattern" in bad["option_errors"] and bad["value"] is None
    broken = client.post("/api/parsers/nmap.hosts/test", json={"input": "<nmaprun>"}, headers=auth).json()
    assert "interrupted" in broken["error"]
    assert client.post("/api/parsers/nope/test", json={}, headers=auth).status_code == 404
    assert client.post("/api/parsers/lines/test", json={"input": "x"}).status_code == 401


# ---------- end to end through the fake agent ----------


def _project(client, auth):
    found = [p for p in client.get("/api/projects", headers=auth).json() if p["codename"] == "PARSE-TEST"]
    if found:
        return found[0]
    return client.post("/api/projects", json={"codename": "PARSE-TEST", "name": "Parsers"}, headers=auth).json()


def _hello(ws, name):
    ws.send_text(json.dumps({
        "type": "hello", "secret": "test-agent-secret", "name": name, "hostname": name,
        "capabilities": {"runtimes": ["podman", "host"]}, "max_concurrency": 20,
    }))
    assert json.loads(ws.receive_text())["type"] == "hello.ok"


def _wait_for_job(ws, job_id):
    while True:
        msg = json.loads(ws.receive_text())
        if msg["type"] != "job.run":
            continue
        if msg["job"]["id"] == job_id:
            return msg["job"]
        ws.send_text(json.dumps({"type": "job.finished", "job_id": msg["job"]["id"], "exit_code": 0}))


def _outputs(client, auth, job_id):
    for _ in range(100):
        outputs = client.get(f"/api/jobs/{job_id}/outputs", headers=auth).json()
        if outputs:
            return outputs
        time.sleep(0.05)
    raise AssertionError("outputs were never parsed")


def _run(client, auth, command_ref, args, upload):
    project = _project(client, auth)
    board = client.get(f"/api/projects/{project['id']}/board", headers=auth).json()
    active = next(c["id"] for c in board["columns"] if c["role"] == "active")
    with client.websocket_connect("/ws/agent") as ws:
        _hello(ws, "parser-agent")
        task = client.post(
            f"/api/projects/{project['id']}/tasks",
            json={"title": command_ref, "command_ref": command_ref, "command_args": args, "column_id": active},
            headers=auth,
        ).json()
        job_id = task["latest_job"]["id"]
        job = _wait_for_job(ws, job_id)
        for name, content in upload.items():
            r = client.post(
                f"/api/jobs/{job_id}/artifacts",
                files={"file": (name, content, "application/xml")},
                data={"path": name},
                headers={"X-Castor-Job-Token": job["artifacts"]["token"]},
            )
            assert r.status_code == 201, r.text
        ws.send_text(json.dumps({"type": "job.finished", "job_id": job_id, "exit_code": 0}))
        return job_id, _outputs(client, auth, job_id)


def test_finished_job_is_parsed(client, auth):
    xml = (FIXTURES / "nmap-service-scan.xml").read_bytes()
    job_id, outputs = _run(client, auth, "nmap-service-scan", {"targets": "10.10.0.5"}, {"service-scan.xml": xml})
    [services] = outputs
    assert services["name"] == "services" and services["type"] == "list<service>"
    assert services["error"] is None and services["sources"] == ["service-scan.xml"]
    assert len(services["value"]) == 3

    detail = client.get(f"/api/jobs/{job_id}", headers=auth).json()
    assert detail["outputs"][0]["name"] == "services"
    assert detail["output_specs"][0]["parser"] == "nmap.services"

    again = client.post(f"/api/jobs/{job_id}/outputs/parse", headers=auth)
    assert again.status_code == 200 and len(again.json()[0]["value"]) == 3


def test_missing_or_broken_files_are_reported_not_fatal(client, auth):
    job_id, [hosts] = _run(client, auth, "nmap-ping-sweep", {"targets": "10.10.0.0/29"}, {})
    assert hosts["value"] is None and "no file matched /work/ping-sweep.xml" in hosts["error"]
    assert client.get(f"/api/jobs/{job_id}", headers=auth).json()["status"] == "succeeded"

    _, [hosts] = _run(client, auth, "nmap-ping-sweep", {"targets": "10.10.0.0/29"}, {"ping-sweep.xml": b"<nmaprun><host>"})
    assert "interrupted" in hosts["error"]
