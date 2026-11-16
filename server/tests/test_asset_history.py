"""One picture from many scans (asset_history.py): what each run saw of an asset, what a run
covered and didn't see ("not seen", `missing_since`), and a project's changes over a date range."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from castor_server import asset_history as H
from castor_server import assets as A


def test_ports_targets_and_timelines():
    assert H.parse_ports("22,80,8000-8100") == {"tcp": [(22, 22), (80, 80), (8000, 8100)]}
    assert H.parse_ports("T:80,U:53,161") == {"tcp": [(80, 80)], "udp": [(53, 53), (161, 161)]}
    assert H.parse_ports("-") == {"tcp": [(1, 65535)]}
    assert H.port_covered({"tcp": [(1, 1000)]}, "tcp", 443) and not H.port_covered({"tcp": [(1, 1000)]}, "udp", 443)
    assert H.target_matches("10.0.0.7", "10.0.0.1-20") and not H.target_matches("10.0.0.21", "10.0.0.1-20")
    assert H.target_matches("10.0.3.7", "10.0.1-3.*") and not H.target_matches("10.0.4.7", "10.0.1-3.*")
    assert H.target_matches("10.0.0.7", "10.0.0.0/24") and not H.target_matches("10.0.1.7", "10.0.0.0/24")
    assert H.target_matches("app.example.com", "https://app.example.com:8443/login")
    assert H.target_matches("10.0.0.5", "10.0.0.5:8080")
    xml = (Path(__file__).parents[1] / "castor_server/parser_samples/nmap-service-scan.xml").read_bytes()
    args, ports = H.nmap_coverage(xml)
    assert "-p 1-1000" in args and ports == {"tcp": [(1, 1000)]}

    class O:
        def __init__(self, fields=None, absent=False):
            self.fields, self.absent = fields or {}, absent

    steps = H.timeline([O({"port": 443, "version": "1.24"}), O({"port": 443, "version": "1.24"}), O(absent=True), O(absent=True),
                        O({"port": 443, "version": "1.26", "title": "Hi"})])
    assert [s["kind"] for s in steps] == ["new", "seen", "gone", "still_gone", "back"]
    assert {"field": "version", "before": "1.24", "after": "1.26"} in steps[-1]["changes"]
    assert {"field": "title", "before": None, "after": "Hi"} in steps[-1]["changes"]  # first reported then


def _project(client, auth):
    code = f"AH-{uuid.uuid4().hex[:6]}"
    return client.post("/api/projects", json={"codename": code, "name": code, "kind": "internal"}, headers=auth).json()


def _card(client, auth, p, command, **args):
    r = client.post(f"/api/projects/{p['id']}/tasks", json={"title": f"{command} {args}", "kind": "command", "command_ref": command,
                                                           "command_args": args}, headers=auth)
    assert r.status_code == 201, r.text
    return r.json()


def _run(task_id, outputs, status="succeeded"):
    """A finished job of the card with these parsed outputs, ingested as a real run would be."""

    async def go():
        from castor_server import models as m
        from castor_server.db import SessionLocal

        async with SessionLocal() as db:
            task = await db.get(m.Task, uuid.UUID(task_id))
            now = datetime.now(UTC)
            job = m.Job(task_id=task.id, command="x", status=m.JobStatus(status), started_at=now, finished_at=now)
            db.add(job)
            await db.flush()
            rows = [m.JobOutput(job_id=job.id, name=n, type="t", parser=p, value=v) for n, p, v in outputs]
            db.add_all(rows)
            await db.commit()
            await A.ingest_outputs(db, job, task, rows)
            return job.id

    return asyncio.run(go())


def _svc(port, version="", host="10.30.0.5"):
    return {"host": host, "port": port, "proto": "tcp", "state": "open", "name": "x", "product": "nginx", "version": version}


def test_runs_record_what_they_covered_and_did_not_see(client, auth):
    p = _project(client, auth)
    start = datetime.now(UTC) - timedelta(seconds=1)
    scan = _card(client, auth, p, "nmap-service-scan", targets="10.30.0.0/24", ports="1-1000")
    other_net = _card(client, auth, p, "nmap-service-scan", targets="10.30.1.0/24", ports="1-1000")
    smb_only = _card(client, auth, p, "nmap-service-scan", targets="10.30.0.0/24", ports="445")
    trig = client.post(f"/api/projects/{p['id']}/asset-triggers", json={
        "name": "Port went away", "asset_type": "service", "on_event": "missing", "action": "command", "target": "nmap-service-scan",
        "args": {"targets": "{{ asset.host }}"}}, headers=auth)
    assert trig.status_code == 201, trig.text

    _run(scan["id"], [("services", "nmap.services", [_svc(22), _svc(443, "1.24")])])
    services = {a["fields"]["port"]: a for a in client.get(f"/api/projects/{p['id']}/assets?type=service", headers=auth).json()["items"]}
    https = services[443]["id"]

    # The same scan a week later: 443 isn't there. Scans of another subnet or of other ports say nothing.
    job_b = _run(scan["id"], [("services", "nmap.services", [_svc(22)])])
    _run(other_net["id"], [("services", "nmap.services", [])])
    _run(smb_only["id"], [("services", "nmap.services", [])])
    _run(scan["id"], [("services", "nmap.services", [])], status="failed")  # a failed scan says nothing
    missing = client.get(f"/api/projects/{p['id']}/assets?type=service&seen=missing", headers=auth).json()["items"]
    assert [a["fields"]["port"] for a in missing] == [443] and missing[0]["missing_since"]
    assert client.get(f"/api/projects/{p['id']}/assets?type=service&seen=present", headers=auth).json()["total"] == 1
    feed = client.get(f"/api/dashboard/feed?project_id={p['id']}", headers=auth).json()["items"]
    assert any(i["type"] == "assets.missing" and "1 service" in i["message"] for i in feed)
    fires = client.get(f"/api/projects/{p['id']}/asset-trigger-fires", headers=auth).json()
    assert [f["asset"]["id"] for f in fires] == [https]  # the "missing" trigger (waiting: no scope set)

    # Parsing that run again doesn't say it twice.
    async def reparse():
        from castor_server import models as m
        from castor_server.db import SessionLocal
        from sqlalchemy import func, select

        async with SessionLocal() as db:
            job = await db.get(m.Job, job_b)
            task = await db.get(m.Task, job.task_id)
            outs = list((await db.execute(select(m.JobOutput).where(m.JobOutput.job_id == job.id))).scalars())
            await A.ingest_outputs(db, job, task, outs)
            return (await db.execute(select(func.count()).select_from(m.AssetObservation).where(
                m.AssetObservation.asset_id == uuid.UUID(https), m.AssetObservation.absent.is_(True)))).scalar_one()

    assert asyncio.run(reparse()) == 1

    # Back, with a newer version.
    _run(scan["id"], [("services", "nmap.services", [_svc(22), _svc(443, "1.26")])])
    detail = client.get(f"/api/assets/{https}", headers=auth).json()
    assert detail["missing_since"] is None
    kinds = [h["kind"] for h in detail["history"]]
    assert kinds == ["back", "gone", "new"], kinds
    assert {"field": "version", "before": "1.24", "after": "1.26"} in detail["history"][0]["changes"]
    assert detail["history"][1]["absent"] and detail["history"][1]["source"]["command"] == "nmap-service-scan"

    # The project's changes over the range.
    changes = client.get(f"/api/projects/{p['id']}/assets/changes", params={"since": start.isoformat()}, headers=auth).json()
    assert changes["counts"]["gone"] == 1 and changes["counts"]["back"] == 1 and changes["counts"]["new"] >= 3  # host and two services
    gone = client.get(f"/api/projects/{p['id']}/assets/changes", params={"since": start.isoformat(), "kind": "gone", "type": "service"},
                      headers=auth).json()["items"]
    assert [(g["asset"]["id"], g["kind"]) for g in gone] == [(https, "gone")]
    later = client.get(f"/api/projects/{p['id']}/assets/changes",
                       params={"since": (datetime.now(UTC) + timedelta(minutes=1)).isoformat()}, headers=auth).json()
    assert later["items"] == []
    assert client.get(f"/api/projects/{p['id']}/assets/changes", params={"since": start.isoformat(), "kind": "nope"},
                      headers=auth).status_code == 422
    client.delete(f"/api/projects/{p['id']}", headers=auth)


def test_host_discovery_and_web_probes(client, auth):
    p = _project(client, auth)
    sweep = _card(client, auth, p, "nmap-ping-sweep", targets="10.31.0.0/24", exclude="10.31.0.9")
    _run(sweep["id"], [("hosts", "nmap.hosts", [{"ip": "10.31.0.5", "status": "up"}, {"ip": "10.31.0.9", "status": "up"}])])
    _run(sweep["id"], [("hosts", "nmap.hosts", [])])
    hosts = {a["fields"]["ip"]: a for a in client.get(f"/api/projects/{p['id']}/assets?type=host", headers=auth).json()["items"]}
    assert hosts["10.31.0.5"]["missing_since"] and not hosts["10.31.0.9"]["missing_since"]  # excluded: not covered

    probe = _card(client, auth, p, "httpx-probe", targets="app.acme.test,10.31.0.5")
    _run(probe["id"], [("urls", "httpx.results", [{"url": "https://app.acme.test/", "status": 200, "title": "App"},
                                                   {"url": "https://app.acme.test/admin", "status": 200}])])
    # https answered, http didn't: the host is still there, and a path isn't what httpx looks at.
    _run(probe["id"], [("urls", "httpx.results", [{"url": "http://app.acme.test/", "status": 200, "title": "App v2"}])])
    urls = {a["fields"]["url"]: a for a in client.get(f"/api/projects/{p['id']}/assets?type=url", headers=auth).json()["items"]}
    assert not urls["https://app.acme.test/"]["missing_since"] and not urls["https://app.acme.test/admin"]["missing_since"]
    _run(probe["id"], [("urls", "httpx.results", [])])
    urls = {a["fields"]["url"]: a for a in client.get(f"/api/projects/{p['id']}/assets?type=url", headers=auth).json()["items"]}
    assert urls["https://app.acme.test/"]["missing_since"] and not urls["https://app.acme.test/admin"]["missing_since"]
    client.delete(f"/api/projects/{p['id']}", headers=auth)
