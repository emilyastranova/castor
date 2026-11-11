"""Assets (3f): identity and merging, ingest from parsed outputs, the API, scope, assets as
command arguments, and triggers with their guards."""

import asyncio
import json
import uuid

import pytest

from castor_server import assets as A


def _project(client, auth, scope=None):
    code = f"AS-{uuid.uuid4().hex[:6]}"
    p = client.post("/api/projects", json={"codename": code, "name": code, "kind": "internal"}, headers=auth).json()
    if scope is not None:
        client.put(f"/api/projects/{p['id']}/scope", json={"include": scope}, headers=auth)
    return p


def _list(client, auth, p, **params):
    return client.get(f"/api/projects/{p['id']}/assets", params=params, headers=auth).json()


def test_identity_and_where():
    assert A.type_of("ip").name == "host" and A.identity("ip", {"ip": " 10.0.0.1 "})[0] == "10.0.0.1"  # ip is a host
    key, label, f = A.identity("host", {"ip": "10.0.0.5", "hostname": "Web01.Corp.Local."})
    assert key == "10.0.0.5" and label == "10.0.0.5 · web01.corp.local" and f["hostname"] == "web01.corp.local"
    assert A.identity("service", {"host": "10.0.0.5", "port": "445"})[0] == "10.0.0.5:445/tcp"
    assert A.identity("credential", {"username": "svc", "domain": "CORP"}, "pw")[2]["fingerprint"]
    with pytest.raises(A.AssetError):
        A.identity("host", {"os": "Linux"})
    conds, words = A.parse_where("smb_signing:false port>100 web")
    assert words == ["web"] and A.conditions_met(conds[:1], {"smb_signing": False})
    assert not A.conditions_met(conds[:1], {"smb_signing": True})
    assert A.conditions_met([conds[1]], {"port": 445}) and not A.conditions_met([conds[1]], {"port": 22})
    windows, _ = A.parse_where("os:windows")
    assert A.conditions_met(windows, {"os": "Windows Server 2019"})  # ":" on text means contains
    exact, _ = A.parse_where("os=windows")
    assert not A.conditions_met(exact, {"os": "Windows Server 2019"})


def test_scope_matching():
    class P:
        settings = {"scope": {"include": ["10.20.0.0/16", "corp.local"], "exclude": ["10.20.99.0/24"]}}

    def host(**f):
        return type("X", (), {"type": "host", "fields": f})()

    assert A.in_scope(P, host(ip="10.20.4.5"))
    assert not A.in_scope(P, host(ip="10.20.99.7"))  # excluded
    assert not A.in_scope(P, host(ip="192.168.1.1"))
    assert A.in_scope(P, host(hostname="dc01.corp.local"))
    P.settings = {}
    assert not A.in_scope(P, host(ip="10.20.4.5"))  # no scope: nothing is in it


def _ingest(task_id, outputs):
    async def go():
        from castor_server import models as m
        from castor_server.db import SessionLocal

        async with SessionLocal() as db:
            task = await db.get(m.Task, uuid.UUID(task_id))
            job = m.Job(task_id=task.id, command="x", status=m.JobStatus.succeeded)
            db.add(job)
            await db.flush()
            rows = [m.JobOutput(job_id=job.id, name=n, type="t", parser=p, value=v) for n, p, v in outputs]
            db.add_all(rows)
            await db.commit()
            return await A.ingest_outputs(db, job, task, rows)

    return asyncio.run(go())


def test_ingest_merges_tools_into_one_record(client, auth):
    p = _project(client, auth)
    task = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Scan"}, headers=auth).json()
    _ingest(task["id"], [
        ("hosts", "nmap.hosts", [{"ip": "10.9.0.5", "hostnames": ["web01.corp.local"], "status": "up"}, {"ip": "10.9.0.6", "status": "down"}]),
        ("services", "nmap.services", [{"host": "10.9.0.5", "port": 445, "proto": "tcp", "state": "open", "name": "microsoft-ds"}]),
    ])
    _ingest(task["id"], [("smb", "netexec.smb", [{"host": "10.9.0.5", "port": 445, "name": "WEB01", "domain": "corp.local", "os": "Windows", "signing": False, "smbv1": False}])])

    listing = _list(client, auth, p)
    assert listing["counts"] == {"host": 1, "service": 1, "domain": 1}  # the down host isn't an asset
    host = next(a for a in listing["items"] if a["type"] == "host")
    assert host["fields"]["smb_signing"] is False and host["fields"]["os"] == "Windows" and host["fields"]["status"] == "up"
    assert host["fields"]["hostname"] == "web01"  # the newer report wins
    detail = client.get(f"/api/assets/{host['id']}", headers=auth).json()
    names = [v["value"] for v in detail["provenance"]["hostname"]]
    assert names == ["web01", "web01.corp.local"]  # conflicting values are kept, newest first
    assert detail["provenance"]["smb_signing"][0]["source"]["parser"] == "netexec.smb"
    assert {lk["label"] for lk in detail["links"]} == {"has", "resolved from"}
    assert _list(client, auth, p, type="host", where="smb_signing:false")["total"] == 1
    assert _list(client, auth, p, type="host", where="smb_signing:true")["total"] == 0
    feed = client.get(f"/api/dashboard/feed?project_id={p['id']}", headers=auth).json()["items"]
    assert any(i["type"] == "assets.found" and "Found 1 host" in i["message"] for i in feed)


def test_manual_import_credentials_and_access(client, auth):
    p = _project(client, auth)
    r = client.post(f"/api/projects/{p['id']}/assets/import", json={"type": "host", "text": "10.1.1.1, 10.1.1.2\nfs01.corp.local"}, headers=auth).json()
    assert r["added"] == 3
    cred = client.post(f"/api/projects/{p['id']}/assets", json={"type": "credential", "fields": {"username": "svc_sql", "domain": "CORP"},
                                                              "secret": "Winter2026!"}, headers=auth).json()
    assert cred["has_secret"] and "Winter2026!" not in json.dumps(cred)
    assert "Winter2026!" not in json.dumps(_list(client, auth, p))
    assert client.post(f"/api/assets/{cred['id']}/reveal", headers=auth).json()["secret"] == "Winter2026!"

    name = f"as{uuid.uuid4().hex[:6]}"
    client.post("/api/users", json={"username": name, "role": "operator", "password": "a password"}, headers=auth)
    token = client.post("/api/auth/login", json={"username": name, "password": "a password"}).json()["token"]
    client.cookies.clear()
    other = {"Authorization": f"Bearer {token}"}
    assert client.get(f"/api/assets/{cred['id']}", headers=other).status_code == 404
    assert client.get(f"/api/projects/{p['id']}/assets", headers=other).status_code == 404


def test_assets_as_command_arguments(client, auth):
    p = _project(client, auth)
    client.post(f"/api/projects/{p['id']}/assets/import", json={"type": "ip", "text": "10.4.0.1 10.4.0.2"}, headers=auth)
    task = client.post(f"/api/projects/{p['id']}/tasks", json={
        "title": "Sweep", "kind": "command", "command_ref": "nmap-ping-sweep",
        "command_args": {"targets": {"assets": {"type": "host", "where": "", "field": "ip"}}},
    }, headers=auth)
    assert task.status_code == 201, task.text
    t = task.json()
    assert "10.4.0.1" in t["command"] and "10.4.0.2" in t["command"]
    assert t["command_args"]["targets"] == {"assets": {"type": "host", "where": "", "field": "ip"}}  # the reference is kept


def test_triggers_run_in_scope_and_wait_outside(client, auth):
    p = _project(client, auth, scope=["10.50.0.0/16"])
    trig = client.post(f"/api/projects/{p['id']}/asset-triggers", json={
        "name": "Sweep new hosts", "asset_type": "host", "on_event": "created", "action": "command", "target": "nmap-ping-sweep",
        "args": {"targets": "{{ asset.ip }}"}, "conditions": [{"field": "ip", "op": "exists"}], "max_per_hour": 2,
    }, headers=auth)
    assert trig.status_code == 201, trig.text
    client.post(f"/api/projects/{p['id']}/assets/import", json={"type": "ip", "text": "10.50.1.1 192.168.7.7"}, headers=auth)

    fires = {f["asset"]["label"]: f for f in client.get(f"/api/projects/{p['id']}/asset-trigger-fires", headers=auth).json()}
    assert fires["10.50.1.1"]["status"] == "started" and fires["10.50.1.1"]["task_id"]
    card = client.get(f"/api/tasks/{fires['10.50.1.1']['task_id']}", headers=auth).json()
    assert "10.50.1.1" in card["command"] and card["latest_job"]["status"] in ("queued", "dispatched", "running", "cancelled", "succeeded", "failed")
    assert fires["192.168.7.7"]["status"] == "waiting" and "scope" in fires["192.168.7.7"]["reason"]

    # Approving the out-of-scope one runs it; once per asset means re-seeing it does nothing.
    approved = client.post(f"/api/asset-trigger-fires/{fires['192.168.7.7']['id']}/approve", headers=auth).json()
    assert approved["status"] == "started"
    client.post(f"/api/projects/{p['id']}/assets/import", json={"type": "ip", "text": "10.50.1.1"}, headers=auth)
    assert len(client.get(f"/api/projects/{p['id']}/asset-trigger-fires", headers=auth).json()) == 2

    # The rate limit: at most 2 started per hour.
    client.post(f"/api/projects/{p['id']}/assets/import", json={"type": "ip", "text": "10.50.2.1"}, headers=auth)
    statuses = [f["status"] for f in client.get(f"/api/projects/{p['id']}/asset-trigger-fires", headers=auth).json()]
    assert statuses.count("limited") == 1

    # Clean up queued jobs so other tests' agents don't take them.
    for f in client.get(f"/api/projects/{p['id']}/asset-trigger-fires", headers=auth).json():
        if f["task_id"]:
            job = client.get(f"/api/tasks/{f['task_id']}", headers=auth).json()["latest_job"]
            if job:
                client.post(f"/api/jobs/{job['id']}/cancel", headers=auth)


def test_scope_comes_in_named_groups(client, auth):
    import uuid

    p = client.post("/api/projects", json={"codename": f"SG-{uuid.uuid4().hex[:6]}", "name": "Groups", "kind": "internal"}, headers=auth).json()
    pid = p["id"]
    for ip in ("10.5.0.7", "203.0.113.9", "10.5.9.9", "192.0.2.1"):
        client.post(f"/api/projects/{pid}/assets", json={"type": "host", "fields": {"ip": ip}}, headers=auth)
    r = client.put(f"/api/projects/{pid}/scope", json={"groups": [
        {"name": "Internal", "include": ["10.5.0.0/16"]}, {"name": "External", "include": ["203.0.113.0/24"]},
        {"name": "Sensitive", "include": ["10.5.0.7"]}], "exclude": ["10.5.9.9"]}, headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["include"] == ["10.5.0.0/16", "203.0.113.0/24", "10.5.0.7"]
    by_ip = {a["key"]: a for a in client.get(f"/api/projects/{pid}/assets", headers=auth).json()["items"]}
    assert by_ip["10.5.0.7"]["scope_groups"] == ["Internal", "Sensitive"] and by_ip["10.5.0.7"]["in_scope"]
    assert by_ip["203.0.113.9"]["scope_groups"] == ["External"]
    assert by_ip["10.5.9.9"]["scope_groups"] == [] and not by_ip["10.5.9.9"]["in_scope"]  # excluded wins
    assert not by_ip["192.0.2.1"]["in_scope"]
    only = client.get(f"/api/projects/{pid}/assets?scope=sensitive", headers=auth).json()["items"]
    assert [a["key"] for a in only] == ["10.5.0.7"]
    outside = client.get(f"/api/projects/{pid}/assets?scope=-", headers=auth).json()["items"]
    assert {a["key"] for a in outside} == {"10.5.9.9", "192.0.2.1"}
    dup = client.put(f"/api/projects/{pid}/scope", json={"groups": [{"name": "OT", "include": []}, {"name": "ot", "include": []}]}, headers=auth)
    assert dup.status_code == 422
    legacy = client.put(f"/api/projects/{pid}/scope", json={"include": ["10.5.0.0/16"], "exclude": []}, headers=auth).json()
    assert legacy["groups"] == [{"name": "Scope", "include": ["10.5.0.0/16"]}]


def test_assets_have_descriptions_and_scope_takes_one_entry(client, auth):
    import uuid

    p = client.post("/api/projects", json={"codename": f"SD-{uuid.uuid4().hex[:6]}", "name": "Desc", "kind": "internal"}, headers=auth).json()
    a = client.post(f"/api/projects/{p['id']}/assets", json={"type": "host", "fields": {"ip": "10.8.0.4"}}, headers=auth).json()
    r = client.patch(f"/api/assets/{a['id']}", json={"description": "The **jump host** the client gave us. Owner: IT ops."}, headers=auth)
    assert r.status_code == 200 and r.json()["description"].startswith("The **jump host**")
    assert client.get(f"/api/assets/{a['id']}", headers=auth).json()["description"].endswith("IT ops.")
    hits = client.get("/api/search?q=jump%20host", headers=auth).json()
    assert any(h["id"] == a["id"] for group in hits.get("groups", []) for h in group.get("items", [])) or "jump" in str(hits)
    s = client.post(f"/api/projects/{p['id']}/scope/add", json={"entry": "10.8.0.0/24", "group": "OT"}, headers=auth).json()
    assert s["groups"] == [{"name": "OT", "include": ["10.8.0.0/24"]}]
    s = client.post(f"/api/projects/{p['id']}/scope/add", json={"entry": "10.9.0.1", "group": "ot"}, headers=auth).json()
    assert s["groups"] == [{"name": "OT", "include": ["10.8.0.0/24", "10.9.0.1"]}]
    assert client.get(f"/api/assets/{a['id']}", headers=auth).json()["scope_groups"] == ["OT"]
