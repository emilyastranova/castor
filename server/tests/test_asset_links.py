"""Relationships between assets: read from results (logins, admin rights, files taken from shares,
vulnerabilities), added by hand, filtered, listed, and triggers on new ones."""

import asyncio
import uuid

from castor_server import assets as A


def _project(client, auth, scope=None):
    code = f"RL-{uuid.uuid4().hex[:6]}"
    p = client.post("/api/projects", json={"codename": code, "name": code, "kind": "internal"}, headers=auth).json()
    if scope is not None:
        client.put(f"/api/projects/{p['id']}/scope", json={"include": scope}, headers=auth)
    return p


def _hinted(task_id, hint, lines, args=None):
    """A job whose output (lines a command says are `hint`) was parsed, then read as assets."""

    async def go():
        from castor_server import models as m
        from castor_server.db import SessionLocal

        async with SessionLocal() as db:
            task = await db.get(m.Task, uuid.UUID(task_id))
            if args:
                task.command_args = args
            job = m.Job(task_id=task.id, command="x", status=m.JobStatus.succeeded,
                        output_specs=[{"name": "out", "type": "list<string>", "parser": "lines", "asset": hint}])
            db.add(job)
            await db.flush()
            if hint == "download":
                db.add(m.Attachment(project_id=task.project_id, task_id=task.id, job_id=job.id, kind="artifact",
                                    filename="artifacts/Groups.xml", size=321, storage_path="x"))
            out = m.JobOutput(job_id=job.id, name="out", type="list<string>", parser="lines", value=lines)
            db.add(out)
            await db.commit()
            return await A.ingest_outputs(db, job, task, [out])

    return asyncio.run(go())


def _assets(client, auth, p, **params):
    return client.get(f"/api/projects/{p['id']}/assets", params=params, headers=auth).json()["items"]


def _one(client, auth, p, type_, **params):
    items = _assets(client, auth, p, type=type_, **params)
    assert len(items) == 1, items
    return client.get(f"/api/assets/{items[0]['id']}", headers=auth).json()


def test_a_login_records_where_it_worked_and_who_is_admin(client, auth):
    p = _project(client, auth)
    task = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Spray"}, headers=auth).json()
    _hinted(task["id"], "login", [
        "SMB  10.30.0.5  445  DC01  [+] CORP\\svc_backup:Summer2026! (Pwn3d!)",
        "SMB  10.30.0.6  445  FS01  [+] CORP\\svc_backup:Summer2026!",
    ])

    cred = _one(client, auth, p, "credential")
    works = {(lk["label"], lk["asset"]["type"], lk["asset"]["label"]) for lk in cred["links"]}
    assert ("works on", "host", "10.30.0.5") in works and ("works on", "host", "10.30.0.6") in works
    assert ("works on", "service", "10.30.0.5:445/tcp") in works
    assert ("belongs to", "user", "corp\\svc_backup") in works
    on_dc = next(lk for lk in cred["links"] if lk["asset"]["label"] == "10.30.0.5")
    assert on_dc["fields"] == {"protocol": "smb", "port": 445, "admin": True} and on_dc["source"] == "parser"
    assert on_dc["task"]["id"] == task["id"]

    user = _one(client, auth, p, "user")
    assert [(lk["label"], lk["asset"]["label"]) for lk in user["links"] if lk["kind"] == "admin_on"] == [("admin on", "10.30.0.5")]
    host = client.get(f"/api/assets/{on_dc['asset']['id']}", headers=auth).json()
    assert {lk["label"] for lk in host["links"]} >= {"accepts", "has admin", "has"}

    # Filters: hosts some credential works on, hosts with an admin, and the one without.
    hosts = lambda where: sorted(a["label"] for a in _assets(client, auth, p, type="host", where=where))
    assert hosts("accepts:*") == ["10.30.0.5", "10.30.0.6"]
    assert hosts("has_admin:svc_backup") == ["10.30.0.5"]
    assert hosts("has_admin!=*") == ["10.30.0.6"]
    assert [a["label"] for a in _assets(client, auth, p, type="credential", where="valid_on:10.30.0.6")] == [cred["label"]]

    # The list: every relationship with counts per kind, searchable.
    rels = client.get(f"/api/projects/{p['id']}/asset-links", headers=auth).json()
    assert rels["counts"]["valid_on"] == 4 and rels["counts"]["admin_on"] == 1
    found = client.get(f"/api/projects/{p['id']}/asset-links", params={"q": "admin dc01"}, headers=auth).json()
    assert found["total"] == 0  # "dc01" is the NetExec name, not the asset's
    found = client.get(f"/api/projects/{p['id']}/asset-links", params={"q": "admin 10.30.0.5"}, headers=auth).json()
    assert {x["kind"] for x in found["items"]} == {"admin_on", "valid_on"}  # the login's details say admin too
    only = client.get(f"/api/projects/{p['id']}/asset-links", params={"kind": "admin_on,belongs_to"}, headers=auth).json()
    assert only["total"] == 2

    feed = client.get(f"/api/dashboard/feed?project_id={p['id']}", headers=auth).json()["items"]
    assert any(i["type"] == "assets.linked" and "works on ×4" in i["message"] for i in feed)

    # Seen again: no new relationships, no new feed entry.
    batch = _hinted(task["id"], "login", ["SMB  10.30.0.6  445  FS01  [+] CORP\\svc_backup:Summer2026!"])
    assert batch.links == []


def test_files_taken_from_shares_and_vulnerabilities(client, auth):
    p = _project(client, auth)
    task = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Loot"}, headers=auth).json()
    _hinted(task["id"], "download", ["\\\\10.31.0.5\\SYSVOL\\corp.local\\Policies\\Groups.xml"])
    _hinted(task["id"], "file", ["SMB 10.31.0.5 445 DC01 //10.31.0.5/Finance/Q3 report.xlsx [lastm:'2026-01-01 10:00' size:2048]"])

    groups = _one(client, auth, p, "file", where="name:groups.xml")
    assert groups["fields"]["share"] == "SYSVOL" and groups["attachment"]["filename"] == "artifacts/Groups.xml"
    assert [(lk["label"], lk["asset"]["label"]) for lk in groups["links"]] == [("downloaded from", "\\\\10.31.0.5\\SYSVOL")]
    report = _one(client, auth, p, "file", where="name:report")
    assert report["fields"]["size"] == 2048 and report["fields"]["name"] == "Q3 report.xlsx"
    assert [lk["label"] for lk in report["links"]] == ["on"]
    share = _one(client, auth, p, "share", where="name:sysvol")
    assert {lk["label"] for lk in share["links"]} == {"gave", "on"}

    vtask = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Nuclei"}, headers=auth).json()
    client.post(f"/api/projects/{p['id']}/assets", json={"type": "service", "fields": {"host": "10.31.0.5", "port": 8443}}, headers=auth)
    _hinted(vtask["id"], "vulnerability", ["[CVE-2024-1234] [http] [high] https://10.31.0.5:8443/login"])
    vuln = _one(client, auth, p, "vulnerability")
    assert [(lk["label"], lk["asset"]["label"]) for lk in vuln["links"]] == [("affects", "10.31.0.5:8443/tcp")]


def test_relationships_by_hand(client, auth):
    p = _project(client, auth)
    host = client.post(f"/api/projects/{p['id']}/assets", json={"type": "host", "fields": {"ip": "10.32.0.9"}}, headers=auth).json()
    cred = client.post(f"/api/projects/{p['id']}/assets", json={"type": "credential", "fields": {"username": "admin"}, "secret": "x"},
                       headers=auth).json()

    kinds = {k["name"]: k for k in client.get("/api/assets/link-kinds", headers=auth).json()}
    assert kinds["valid_on"]["reverse"] == "accepts" and ["credential", "host"] in kinds["valid_on"]["pairs"]

    # Wrong way round for the kind: refused, with what it connects.
    bad = client.post(f"/api/assets/{host['id']}/links", json={"kind": "valid_on", "to_id": cred["id"]}, headers=auth)
    assert bad.status_code == 422 and "credential → host" in bad.json()["detail"]
    # From the host's side, "in" reads the other way: the credential works on this host.
    made = client.post(f"/api/assets/{host['id']}/links", json={"kind": "valid_on", "to_id": cred["id"], "direction": "in",
                                                                "note": "RDP, tried by hand"}, headers=auth)
    assert made.status_code == 201, made.text
    lk = made.json()
    assert lk["label"] == "accepts" and lk["asset"]["id"] == cred["id"] and lk["source"] == "manual" and lk["by"]
    assert lk["from"]["id"] == cred["id"] and lk["to"]["id"] == host["id"]

    edited = client.patch(f"/api/asset-links/{lk['id']}", json={"note": "RDP and WinRM", "fields": {"protocol": "rdp"}}, headers=auth).json()
    assert edited["note"] == "RDP and WinRM" and edited["fields"] == {"protocol": "rdp"}
    other = client.post(f"/api/assets/{host['id']}/links", json={"kind": "related", "to_id": cred["id"]}, headers=auth)
    assert other.status_code == 201  # "related to" fits any two
    feed = client.get(f"/api/dashboard/feed?project_id={p['id']}", headers=auth).json()["items"]
    assert any(i["type"] == "assets.linked" and i["message"] == "Noted that admin (password) works on 10.32.0.9" for i in feed)

    assert client.delete(f"/api/asset-links/{lk['id']}", headers=auth).status_code == 204
    left = client.get(f"/api/assets/{cred['id']}", headers=auth).json()["links"]
    assert [x["kind"] for x in left] == ["related"]
    assert client.patch(f"/api/asset-links/{lk['id']}", json={"note": "x"}, headers=auth).status_code == 404


def test_a_trigger_on_a_new_relationship(client, auth):
    p = _project(client, auth, scope=["10.33.0.0/16"])
    trig = client.post(f"/api/projects/{p['id']}/asset-triggers", json={
        "name": "Shares where a login works", "asset_type": "credential", "on_event": "linked", "link_kind": "valid_on",
        "other_type": "host", "action": "command", "target": "nmap-ping-sweep",
        "args": {"targets": "{{ other.ip }}"},
    }, headers=auth)
    assert trig.status_code == 201, trig.text
    assert trig.json()["link_kind"] == "valid_on"
    wrong = client.post(f"/api/projects/{p['id']}/asset-triggers", json={
        "name": "x", "asset_type": "host", "on_event": "linked", "link_kind": "valid_on", "action": "command", "target": "nmap-ping-sweep",
    }, headers=auth)
    assert wrong.status_code == 422

    task = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Spray"}, headers=auth).json()
    _hinted(task["id"], "login", ["SMB  10.33.0.5  445  WS01  [+] CORP\\alice:pw", "SMB  192.168.40.4  445  X  [+] CORP\\alice:pw"])
    fires = {f["other"]["label"]: f for f in client.get(f"/api/projects/{p['id']}/asset-trigger-fires", headers=auth).json()}
    assert set(fires) == {"10.33.0.5", "192.168.40.4"}  # the hosts, not the services
    assert fires["10.33.0.5"]["status"] == "started" and fires["10.33.0.5"]["asset"]["type"] == "credential"
    card = client.get(f"/api/tasks/{fires['10.33.0.5']['task_id']}", headers=auth).json()
    assert "10.33.0.5" in card["command"] and "works on the host 10.33.0.5" in card["description"]
    assert fires["192.168.40.4"]["status"] == "waiting"  # its host is outside the scope
    approved = client.post(f"/api/asset-trigger-fires/{fires['192.168.40.4']['id']}/approve", headers=auth).json()
    assert approved["status"] == "started"

    # Once per relationship: the same login again doesn't fire; a new host does.
    _hinted(task["id"], "login", ["SMB  10.33.0.5  445  WS01  [+] CORP\\alice:pw", "SMB  10.33.0.6  445  WS02  [+] CORP\\alice:pw"])
    fires = client.get(f"/api/projects/{p['id']}/asset-trigger-fires", headers=auth).json()
    assert sorted(f["other"]["label"] for f in fires) == ["10.33.0.5", "10.33.0.6", "192.168.40.4"]

    for f in fires:
        if f["task_id"]:
            job = client.get(f"/api/tasks/{f['task_id']}", headers=auth).json()["latest_job"]
            if job:
                client.post(f"/api/jobs/{job['id']}/cancel", headers=auth)


def test_merging_keeps_relationships():
    async def go():
        from castor_server import models as m
        from castor_server.db import SessionLocal

        async with SessionLocal() as db:
            project = m.Project(codename=f"RM-{uuid.uuid4().hex[:6]}", name="merge", kind="internal")
            db.add(project)
            await db.flush()
            batch = A.Batch(project.id)
            by_name = await A.observe(db, project.id, "host", {"hostname": "fs02"}, batch=batch)
            cred = await A.observe(db, project.id, "credential", {"username": "bob"}, batch=batch)
            await A.link(db, project.id, cred, by_name, "valid_on", batch=batch)
            # The address and name together: the name-only host folds into this one, its relationship too.
            both = await A.observe(db, project.id, "host", {"ip": "10.34.0.2", "hostname": "fs02"}, batch=batch)
            rows = (await db.execute(m.AssetLink.__table__.select().where(m.AssetLink.from_id == cred.id))).all()
            assert [r.to_id for r in rows] == [both.id]
            await db.rollback()

    asyncio.run(go())
