"""Vantage points (segmentation.py): an agent's network segment on every observation, the
reachability matrix, segment-aware "not seen", and segmentation rules that record findings."""

import asyncio
import uuid
from datetime import UTC, datetime

from castor_server import assets as A


def _agent(name, segment):
    async def go():
        from castor_server import models as m
        from castor_server.db import SessionLocal

        async with SessionLocal() as db:
            a = m.Agent(name=name, segment=segment)
            db.add(a)
            await db.commit()
            return a.id

    return asyncio.run(go())


def _run(task_id, agent_id, rows, batch_id=None):
    async def go():
        from castor_server import models as m
        from castor_server.db import SessionLocal

        async with SessionLocal() as db:
            task = await db.get(m.Task, uuid.UUID(task_id))
            now = datetime.now(UTC)
            job = m.Job(task_id=task.id, command="x", status=m.JobStatus.succeeded, started_at=now, finished_at=now, agent_id=agent_id,
                        batch_id=batch_id)
            db.add(job)
            await db.flush()
            out = m.JobOutput(job_id=job.id, name="services", type="list<service>", parser="nmap.services", value=rows)
            db.add(out)
            await db.commit()
            await A.ingest_outputs(db, job, task, [out])

    asyncio.run(go())


def _svc(host, port, version=""):
    return {"host": host, "port": port, "proto": "tcp", "state": "open", "name": "x", "product": "OpenSSH", "version": version}


def test_segments_reach_and_rules(client, auth):
    n = uuid.uuid4().hex[:6]
    code = f"SG-{n}"
    p = client.post("/api/projects", json={"codename": code, "name": code, "kind": "internal"}, headers=auth).json()
    client.put(f"/api/projects/{p['id']}/scope", json={"groups": [{"name": "Management", "include": ["10.60.9.0/24"]},
                                                                   {"name": "Servers", "include": ["10.60.1.0/24"]}]}, headers=auth)
    server, guest = _agent(f"srv-{n}", ""), _agent(f"guest-{n}", "Guest")
    r = client.patch(f"/api/agents/{server}", json={"segment": " Server VLAN "}, headers=auth)
    assert r.status_code == 200 and r.json()["segment"] == "Server VLAN"

    bad = client.put(f"/api/projects/{p['id']}/segmentation", json={"rules": [{"name": "x", "from": "Guest", "severity": "dire"}]}, headers=auth)
    assert bad.status_code == 422
    rules = client.put(f"/api/projects/{p['id']}/segmentation", json={"rules": [
        {"name": "Management from the guest network", "from": "Guest", "type": "service", "scope": "Management", "severity": "high"}]},
        headers=auth)
    assert rules.status_code == 200, rules.text
    rule = rules.json()["rules"][0]
    assert rule["id"] and rule["type"] == "service"

    card = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Scan from everywhere", "kind": "command",
                                                               "command_ref": "nmap-service-scan",
                                                               "command_args": {"targets": "10.60.0.0/16", "ports": "1-1000"}}, headers=auth).json()
    batch = uuid.uuid4()
    _run(card["id"], server, [_svc("10.60.9.5", 22, "9.6"), _svc("10.60.1.5", 443, "1.24")], batch)
    _run(card["id"], guest, [_svc("10.60.9.5", 22, "9.7")], batch)  # reaches management SSH; not the server's 443

    services = {a["fields"]["port"]: a for a in client.get(f"/api/projects/{p['id']}/assets?type=service", headers=auth).json()["items"]}
    assert services[443]["missing_since"] is None  # never seen from Guest: a difference in reach, not gone
    hist = client.get(f"/api/assets/{services[443]['id']}", headers=auth).json()["history"]
    assert [h["kind"] for h in hist] == ["unreached", "new"] and hist[0]["source"]["segment"] == "Guest"

    vulns = client.get(f"/api/projects/{p['id']}/assets?type=vulnerability", headers=auth).json()["items"]
    assert len(vulns) == 1 and vulns[0]["fields"]["severity"] == "high" and vulns[0]["fields"]["tool"] == "segmentation"
    assert "Guest reaches 10.60.9.5:22/tcp" in vulns[0]["fields"]["title"]
    links = client.get(f"/api/assets/{vulns[0]['id']}", headers=auth).json()["links"]
    assert [lk["asset"]["id"] for lk in links] == [services[22]["id"]]

    m = client.get(f"/api/projects/{p['id']}/assets/reachability", params={"type": "service"}, headers=auth).json()
    assert m["segments"] == ["Guest", "Server VLAN"] and "Guest" in m["agent_segments"]
    rows = {r["asset"]["id"]: r for r in m["rows"]}
    ssh, https = rows[services[22]["id"]], rows[services[443]["id"]]
    assert m["rows"][0]["asset"]["id"] == services[22]["id"]  # breaking a rule comes first
    assert ssh["rules"] == ["Management from the guest network"] and ssh["differs"] == ["version"]
    assert ssh["cells"]["Guest"]["fields"]["version"] == "9.7" and ssh["cells"]["Server VLAN"]["agent"] == f"srv-{n}"
    assert https["differs"] == ["reach"] and https["cells"]["Guest"]["state"] == "not_seen"
    only = client.get(f"/api/projects/{p['id']}/assets/reachability", params={"batch": str(batch), "differs": "true"}, headers=auth).json()
    assert len(only["rows"]) == 2
    one = client.get(f"/api/projects/{p['id']}/assets/reachability", params={"asset": services[443]["id"]}, headers=auth).json()
    assert [r["asset"]["id"] for r in one["rows"]] == [services[443]["id"]]

    # Checking again finds the same finding, nothing new; the server VLAN losing 443 is "gone".
    assert client.post(f"/api/projects/{p['id']}/segmentation/check", headers=auth).json() == {"reached": 1, "new": 0}
    _run(card["id"], server, [_svc("10.60.9.5", 22, "9.6")])
    services = {a["fields"]["port"]: a for a in client.get(f"/api/projects/{p['id']}/assets?type=service", headers=auth).json()["items"]}
    assert services[443]["missing_since"]
    client.delete(f"/api/projects/{p['id']}", headers=auth)
