"""Better asset detection: hinted lines, vulnerabilities, merging a host seen by name and by
address, confidence, and assets mentioned in text becoming suggestions."""

from __future__ import annotations

import asyncio
import time
import uuid

from castor_server.assets_text import extract


def _project(client, auth, **settings):
    p = client.post("/api/projects", json={"codename": f"AD-{uuid.uuid4().hex[:6]}", "name": "Detect", "kind": "internal"}, headers=auth).json()
    if settings:
        client.patch(f"/api/projects/{p['id']}", json={"settings": {**p["settings"], **settings}}, headers=auth)
    return p


def _assets(client, auth, pid, status="confirmed", type_=None):
    q = f"?status={status}" + (f"&type={type_}" if type_ else "")
    return client.get(f"/api/projects/{pid}/assets{q}", headers=auth).json()


def _wait(fn, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        value = fn()
        if value:
            return value
        time.sleep(0.15)
    return fn()


def test_text_finds_what_is_mentioned_and_skips_files():
    found = extract("""Scanned 10.20.0.5 and dc01.corp.local; see https://intranet.corp.local/login and \\\\fs01.corp.local\\Finance.
        RDP on 10.20.0.9:3389. Logged in as CORP\\alice. Vulnerable to CVE-2021-44228.
        Not these: results.txt, nmap.org, version 1.2.3, 127.0.0.1, 255.255.255.0, C:\\Windows\\System32""")
    kinds = {(k, tuple(sorted(f.items()))) for k, f, _ in found}
    assert ("host", (("ip", "10.20.0.5"),)) in kinds
    assert ("domain", (("name", "dc01.corp.local"),)) in kinds
    assert ("url", (("url", "https://intranet.corp.local/login"),)) in kinds
    assert ("share", (("host", "fs01.corp.local"), ("name", "Finance"))) in kinds
    assert ("service", (("host", "10.20.0.9"), ("port", 3389))) in kinds
    assert ("user", (("domain", "CORP"), ("username", "alice"))) in kinds
    assert ("vulnerability", (("id", "CVE-2021-44228"),)) in kinds
    names = {f.get("name") or f.get("ip") for _, f, _ in found}
    assert not names & {"results.txt", "nmap.org", "127.0.0.1", "255.255.255.0"}
    assert not any(k == "user" and f["domain"] == "C" for k, f, _ in found)


def test_a_comment_suggests_assets_until_someone_confirms(client, auth):
    p = _project(client, auth)
    card = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Notes", "kind": "manual"}, headers=auth).json()
    client.post(f"/api/tasks/{card['id']}/comments", json={"body": "The DC is 10.77.0.10, and 10.77.0.11 looks like a printer."}, headers=auth)
    listed = _wait(lambda: _assets(client, auth, p["id"], "suggested")["items"])
    assert {a["key"] for a in listed} == {"10.77.0.10", "10.77.0.11"}
    first = next(a for a in listed if a["key"] == "10.77.0.10")
    assert first["status"] == "suggested" and first["confidence"] == 50
    assert first["mentions"][0]["where"] == "a comment" and "The DC is 10.77.0.10" in first["mentions"][0]["excerpt"]
    assert first["mentions"][0]["link"].startswith(f"/projects/{p['id']}?task={card['id']}&comment=")
    assert _assets(client, auth, p["id"])["items"] == [] and _assets(client, auth, p["id"])["suggested"] == 2  # kept apart

    r = client.post(f"/api/assets/{first['id']}/confirm", headers=auth).json()
    assert r["status"] == "confirmed" and r["confidence"] == 100
    other = next(a for a in listed if a["key"] == "10.77.0.11")
    assert client.post(f"/api/assets/{other['id']}/dismiss", headers=auth).json()["status"] == "dismissed"
    client.post(f"/api/tasks/{card['id']}/comments", json={"body": "Again: 10.77.0.11"}, headers=auth)
    time.sleep(0.6)
    assert _assets(client, auth, p["id"], "dismissed")["items"][0]["key"] == "10.77.0.11"  # a mention doesn't bring it back
    assert [a["key"] for a in _assets(client, auth, p["id"])["items"]] == ["10.77.0.10"]


def test_mentions_can_be_turned_off(client, auth):
    p = _project(client, auth, asset_mentions=False)
    client.post(f"/api/projects/{p['id']}/posts", json={"body": "Found 10.55.0.1 open"}, headers=auth)
    time.sleep(0.6)
    assert _assets(client, auth, p["id"], "suggested")["suggested"] == 0


def test_a_host_seen_by_name_and_by_address_is_one(client, auth):
    from castor_server import assets as A
    from castor_server.db import SessionLocal

    p = _project(client, auth)
    pid = uuid.UUID(p["id"])

    async def go():
        async with SessionLocal() as db:
            batch = A.Batch(pid)
            await A.observe(db, pid, "host", {"hostname": "web01.corp.local"}, batch=batch)
            await A.observe(db, pid, "service", {"host": "web01.corp.local", "port": 443}, batch=batch)
            await A.observe(db, pid, "host", {"ip": "10.9.9.9"}, batch=batch, source="manual")
            await A.observe(db, pid, "host", {"ip": "10.9.9.9", "hostname": "web01.corp.local", "os": "Linux"}, batch=batch)
            await db.commit()

    asyncio.run(go())
    hosts = _assets(client, auth, p["id"], type_="host")["items"]
    assert len(hosts) == 1 and hosts[0]["key"] == "10.9.9.9" and hosts[0]["fields"]["hostname"] == "web01.corp.local"
    assert hosts[0]["confidence"] == 100  # someone added it by hand once
    services = _assets(client, auth, p["id"], type_="service")["items"]
    assert [s["key"] for s in services] == ["10.9.9.9:443/tcp"]  # moved onto the address


def test_hinted_lines_become_assets(client, auth):
    from castor_server import assets as A
    from castor_server import models as m
    from castor_server.db import SessionLocal

    p = _project(client, auth)
    card = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Scan", "kind": "command", "command": "true",
                                                              "command_args": {"target": "https://shop.acme.test"}}, headers=auth).json()

    async def go():
        async with SessionLocal() as db:
            job = m.Job(task_id=uuid.UUID(card["id"]), command="true", status=m.JobStatus.succeeded, exit_code=0, output_specs=[
                {"name": "found", "type": "list<string>", "parser": "lines", "asset": "vulnerability"},
                {"name": "shares", "type": "list<string>", "parser": "lines", "asset": "share"},
                {"name": "logins", "type": "list<string>", "parser": "lines", "asset": "login"},
                {"name": "subs", "type": "list<string>", "parser": "lines", "asset": "domain"},
            ])
            db.add(job)
            (await db.get(m.Task, uuid.UUID(card["id"]))).command_args = {"target": "https://shop.acme.test"}  # as a library command has
            await db.flush()
            outs = [
                m.JobOutput(job_id=job.id, name="found", type="list<string>", parser="lines", value=[
                    "[CVE-2021-41773] [http] [critical] https://shop.acme.test/cgi-bin/ [path traversal]", "CVE-2023-1234 in openssl"]),
                m.JobOutput(job_id=job.id, name="shares", type="list<string>", parser="lines", value=["10.1.1.5 SYSVOL READ"]),
                m.JobOutput(job_id=job.id, name="logins", type="list<string>", parser="lines", value=[
                    "SMB  10.1.1.5  445  DC01  [+] corp.local\\bob:******** (Pwn3d!)"]),
                m.JobOutput(job_id=job.id, name="subs", type="list<string>", parser="lines", value=["api.acme.test", "www.acme.test"]),
            ]
            db.add_all(outs)
            await db.commit()
            task = await db.get(m.Task, job.task_id)
            await A.ingest_outputs(db, job, task, outs)

    asyncio.run(go())
    vulns = _assets(client, auth, p["id"], type_="vulnerability")["items"]
    keys = {v["key"] for v in vulns}
    assert "cve-2021-41773@shop.acme.test" in keys and "cve-2023-1234@shop.acme.test" in keys
    nuclei = next(v for v in vulns if v["key"].startswith("cve-2021-41773"))
    assert nuclei["fields"]["severity"] == "critical" and nuclei["status"] == "confirmed"
    assert [s["key"] for s in _assets(client, auth, p["id"], type_="share")["items"]] == ["\\\\10.1.1.5\\sysvol"]
    cred = _assets(client, auth, p["id"], type_="credential")["items"][0]
    assert cred["fields"]["username"] == "bob" and cred["fields"]["admin"] is True and not cred["has_secret"]  # masked: no secret
    assert {d["key"] for d in _assets(client, auth, p["id"], type_="domain")["items"]} == {"api.acme.test", "www.acme.test"}
