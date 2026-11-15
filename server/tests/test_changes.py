"""Comparing runs: what's new, gone and changed in parsed results since an earlier run, for a
card's job and for a playbook run step by step."""

import asyncio
import uuid
from datetime import timedelta

from castor_server import compare


def svc(host, port, state="open", name="http", proto="tcp", **kw):
    return {"host": host, "port": port, "proto": proto, "state": state, "name": name, "product": "", "version": "", **kw}


# ---------- the comparison itself ----------


def test_services_are_matched_by_host_port_and_protocol():
    before = [svc("10.0.0.1", 22, name="ssh"), svc("10.0.0.1", 80), svc("10.0.0.2", 445, name="microsoft-ds")]
    after = [svc("10.0.0.1", 22, name="ssh"), svc("10.0.0.1", 80, state="filtered"), svc("10.0.0.3", 443, name="https"),
             svc("10.0.0.3", 443, proto="udp", name="quic")]
    d = compare.diff("services", "list<service>", before, after)
    assert d["key"] == ["host", "port", "proto"] and d["status"] == "changed"
    assert d["counts"] == {"added": 2, "removed": 1, "changed": 1} and d["unchanged"] == 1
    assert {(r["host"], r["port"], r["proto"]) for r in d["added"]} == {("10.0.0.3", 443, "tcp"), ("10.0.0.3", 443, "udp")}
    assert d["removed"][0]["port"] == 445
    assert d["changed"][0]["fields"] == [{"field": "state", "before": "open", "after": "filtered"}]
    assert (d["before_count"], d["after_count"]) == (3, 4)


def test_keys_are_found_for_other_records_and_plain_values():
    # Findings (no type Castor knows): template and where it matched.
    f = lambda t, at, sev="low": {"template-id": t, "matched-at": at, "severity": sev}
    d = compare.diff("findings", "list<object>", [f("tls-old", "a:443"), f("tls-old", "b:443")],
                     [f("tls-old", "a:443", "medium"), f("git-config", "a:443")])
    assert d["key"] == ["template-id", "matched-at"]
    assert d["counts"] == {"added": 1, "removed": 1, "changed": 1}
    # Records with nothing unique are matched whole: only new and gone.
    d = compare.diff("rows", "list<object>", [{"a": 1}, {"a": 1}], [{"a": 2}])
    assert d["key"] is None and d["counts"] == {"added": 1, "removed": 1, "changed": 0}
    # Plain values as sets.
    d = compare.diff("subdomains", "list<string>", ["a.example", "b.example"], ["b.example", "c.example"])
    assert d["added"] == ["c.example"] and d["removed"] == ["a.example"] and d["unchanged"] == 1
    # A value that isn't a list, an object field by field.
    assert compare.diff("count", "number", 3, 3)["status"] == "same"
    d = compare.diff("summary", "object", {"open": 3, "os": "Linux"}, {"open": 4, "os": "Linux"})
    assert d["status"] == "changed" and d["changed"][0]["fields"] == [{"field": "open", "before": 3, "after": 4}]
    # An output only one side has.
    assert compare.diff("hosts", "list<host>", None, [{"ip": "10.0.0.1"}])["status"] == "new"
    gone = compare.diff("hosts", "list<host>", [{"ip": "10.0.0.1"}], None)
    assert gone["status"] == "gone" and gone["counts"]["removed"] == 1 and gone["after_count"] is None


def test_long_lists_are_cut_but_counted():
    d = compare.diff("hosts", "list<host>", [], [{"ip": f"10.0.{i // 256}.{i % 256}"} for i in range(800)])
    assert d["counts"]["added"] == 800 and len(d["added"]) == compare.LIMIT


# ---------- through the API ----------


def _setup(client, auth):
    code = f"CMP-{uuid.uuid4().hex[:6]}"
    p = client.post("/api/projects", json={"codename": code, "name": code, "kind": "internal"}, headers=auth).json()
    card = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Port scan", "kind": "command", "command": "nmap x"},
                       headers=auth).json()
    other = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Port scan again", "kind": "command", "command": "nmap y"},
                        headers=auth).json()
    return p, card, other


def _jobs(task_id, runs, command_ref=None, step_id=None, run_id=None):
    """Finished jobs on a card, oldest first, each with its outputs: [{name: (type, value | Exception)}]."""
    async def go():
        from castor_server import models as m
        from castor_server.db import SessionLocal

        async with SessionLocal() as db:
            task = await db.get(m.Task, uuid.UUID(task_id))
            if command_ref:
                task.command_ref = command_ref
            if step_id:
                task.playbook_step_id, task.playbook_run_id = step_id, run_id
            ids = []
            start = m.utcnow() - timedelta(hours=len(runs) + 1)
            for i, outputs in enumerate(runs):
                job = m.Job(task_id=task.id, command="nmap", status=m.JobStatus.succeeded, queued_at=start + timedelta(hours=i))
                db.add(job)
                await db.flush()
                for name, (type_, value) in outputs.items():
                    err = str(value) if isinstance(value, Exception) else None
                    db.add(m.JobOutput(job_id=job.id, name=name, type=type_, parser="nmap.services", sources=["stdout"],
                                       value=None if err else value, error=err))
                ids.append(str(job.id))
            await db.commit()
            return ids

    return asyncio.run(go())


def test_a_cards_run_against_the_one_before(client, auth):
    p, card, other = _setup(client, auth)
    first = [svc("10.0.0.1", 22), svc("10.0.0.1", 80)]
    second = [svc("10.0.0.1", 22), svc("10.0.0.1", 443)]
    third = [svc("10.0.0.1", 22, state="filtered"), svc("10.0.0.1", 443)]
    j1, j2, j3 = _jobs(card["id"], [{"services": ("list<service>", first)}, {"services": ("list<service>", second),
                                     "hosts": ("list<host>", [{"ip": "10.0.0.1"}])}, {"services": ("list<service>", third),
                                     "hosts": ("list<host>", RuntimeError("no file matched"))}], command_ref="nmap-tcp")

    c = client.get(f"/api/jobs/{j2}/changes", headers=auth).json()
    assert c["base"]["job_id"] == j1 and c["base"]["how"] == "card"
    services = next(o for o in c["outputs"] if o["name"] == "services")
    assert services["counts"] == {"added": 1, "removed": 1, "changed": 0} and services["added"][0]["port"] == 443
    assert next(o for o in c["outputs"] if o["name"] == "hosts")["status"] == "new"
    assert c["totals"] == {"added": 2, "removed": 1, "changed": 0}

    c = client.get(f"/api/jobs/{j3}/changes", headers=auth).json()
    assert c["base"]["job_id"] == j2 and [x["job_id"] for x in c["candidates"]][:2] == [j2, j1]
    hosts = next(o for o in c["outputs"] if o["name"] == "hosts")
    assert hosts["status"] == "error" and "no file matched" in hosts["error"]  # can't say what changed
    assert next(o for o in c["outputs"] if o["name"] == "services")["counts"]["changed"] == 1
    # Against another run of your choosing.
    c = client.get(f"/api/jobs/{j3}/changes", params={"base": j1}, headers=auth).json()
    assert c["base"]["job_id"] == j1 and next(o for o in c["outputs"] if o["name"] == "services")["counts"]["removed"] == 1
    # The first run has nothing before it.
    assert client.get(f"/api/jobs/{j1}/changes", headers=auth).json()["base"] is None

    # Another card running the same library command counts as an earlier run of it.
    (k1,) = _jobs(other["id"], [{"services": ("list<service>", third + [svc("10.0.0.9", 21, name="ftp")])}], command_ref="nmap-tcp")
    c = client.get(f"/api/jobs/{k1}/changes", headers=auth).json()
    assert c["base"]["how"] == "command" and c["base"]["card_title"] == "Port scan"
    assert c["totals"]["added"] == 1

    # Other projects' runs are out of reach.
    q = client.post("/api/projects", json={"codename": f"X-{uuid.uuid4().hex[:5]}", "name": "x", "kind": "internal"}, headers=auth).json()
    x = client.post(f"/api/projects/{q['id']}/tasks", json={"title": "elsewhere", "kind": "command", "command": "e"}, headers=auth).json()
    (xj,) = _jobs(x["id"], [{"services": ("list<service>", first)}])
    assert client.get(f"/api/jobs/{j3}/changes", params={"base": xj}, headers=auth).status_code == 422
    for pid in (p["id"], q["id"]):
        client.delete(f"/api/projects/{pid}", headers=auth)


def _run(project_id, column_card, steps_outputs, age_hours):
    """A finished run of a 'recon' playbook: one command step 'scan' with its card and job."""
    async def go():
        from castor_server import models as m
        from castor_server.db import SessionLocal

        async with SessionLocal() as db:
            col = (await db.get(m.Task, uuid.UUID(column_card))).column_id
            spec = {"id": "recon", "name": "Recon", "steps": [{"id": sid, "name": sid.title(), "command_ref": "nmap-tcp"} for sid in steps_outputs]}
            run = m.PlaybookRun(playbook_id="recon", playbook_name="Recon", project_id=uuid.UUID(project_id),
                                status=m.PlaybookRunStatus.succeeded, spec_snapshot=spec,
                                created_at=m.utcnow() - timedelta(hours=age_hours))
            db.add(run)
            await db.flush()
            ids = {}
            for sid, outputs in steps_outputs.items():
                t = m.Task(project_id=run.project_id, column_id=col, title=sid, playbook_run_id=run.id, playbook_step_id=sid,
                           command_ref="nmap-tcp")
                db.add(t)
                await db.flush()
                ids[sid] = str(t.id)
                job = m.Job(task_id=t.id, command="nmap", status=m.JobStatus.succeeded)
                db.add(job)
                await db.flush()
                for name, (type_, value) in outputs.items():
                    db.add(m.JobOutput(job_id=job.id, name=name, type=type_, parser="nmap.services", sources=["stdout"], value=value))
            run.step_task_ids = ids
            await db.commit()
            return str(run.id)

    return asyncio.run(go())


def test_a_playbook_run_step_by_step(client, auth):
    p, card, _ = _setup(client, auth)
    r1 = _run(p["id"], card["id"], {"scan": {"services": ("list<service>", [svc("10.0.0.1", 22)])},
                                    "dns": {"names": ("list<string>", ["a.example"])}}, 5)
    r2 = _run(p["id"], card["id"], {"scan": {"services": ("list<service>", [svc("10.0.0.1", 22), svc("10.0.0.2", 80)])},
                                    "web": {"urls": ("list<url>", [{"url": "http://10.0.0.2/"}])}}, 1)
    c = client.get(f"/api/playbook-runs/{r2}/changes", headers=auth).json()
    assert c["base"]["run_id"] == r1 and [x["run_id"] for x in c["candidates"]] == [r1]
    steps = {s["step_id"]: s for s in c["steps"]}
    assert steps["scan"]["status"] == "changed" and steps["scan"]["totals"]["added"] == 1
    assert steps["scan"]["outputs"][0]["key"] == ["host", "port", "proto"]  # its type, from the parsed output
    assert steps["web"]["status"] == "new" and steps["dns"]["status"] == "gone"
    assert c["totals"] == {"added": 2, "removed": 1, "changed": 0}
    assert client.get(f"/api/playbook-runs/{r1}/changes", headers=auth).json()["base"] is None
    assert client.get(f"/api/playbook-runs/{r1}/changes", params={"base": r2}, headers=auth).json()["totals"]["removed"] == 2
    client.delete(f"/api/projects/{p['id']}", headers=auth)
