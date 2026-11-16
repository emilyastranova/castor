"""Richer assets (evidence.py): screenshots, certificates and headers kept on an asset, a page's
look (a perceptual hash) that changes only when it looks different, the gallery grouped by look,
and GoWitness results mapped onto URL assets."""

import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, ImageDraw

from castor_server import assets as A
from castor_server import evidence as E
from castor_server.config import settings


def _page(kind: str, stamp: str = "") -> Image.Image:
    """A fake web page: a login box, a dashboard with a table, or a blank error page."""
    img = Image.new("RGB", (1280, 800), "white")
    d = ImageDraw.Draw(img)
    if kind == "login":
        d.rectangle((0, 0, 1280, 80), fill=(30, 60, 120))
        d.rectangle((440, 220, 840, 560), outline=(80, 80, 80), width=3)
        d.rectangle((480, 300, 800, 340), fill=(230, 230, 230))
        d.rectangle((480, 380, 800, 420), fill=(230, 230, 230))
        d.rectangle((560, 470, 720, 510), fill=(40, 120, 200))
    elif kind == "dashboard":
        d.rectangle((0, 0, 240, 800), fill=(40, 40, 40))
        for y in range(120, 760, 60):
            d.rectangle((280, y, 1240, y + 40), fill=(200, 220, 240) if y % 120 else (240, 240, 240))
    elif kind == "splash":
        d.rectangle((0, 0, 1280, 800), fill=(0, 90, 158))
        d.text((520, 380), "Welcome", fill="white")
    else:
        d.text((40, 40), "404 Not Found", fill="black")
    if stamp:
        d.text((1100, 770), stamp, fill=(120, 120, 120))  # a clock in the corner
    return img


def test_looks_match_the_same_page_and_tell_others_apart(tmp_path):
    paths = {}
    for name, kind, stamp in [("a", "login", "10:01"), ("a2", "login", "10:47"), ("b", "dashboard", ""), ("c", "error", ""), ("d", "splash", "")]:
        paths[name] = tmp_path / f"{name}.jpeg"
        _page(kind, stamp).save(paths[name], quality=60)
    looks = {k: E.look_of(p) for k, p in paths.items()}
    assert all(v and len(v) == 80 for v in looks.values())
    assert E.similar(looks["a"], looks["a2"])
    assert not E.similar(looks["a"], looks["b"]) and not E.similar(looks["b"], looks["c"])
    assert not E.similar(looks["c"], looks["d"])  # two flat pages: a blank one and a blue one
    groups = E.group_by_look([(looks["b"], "b"), (looks["a"], "a"), (looks["a2"], "a2")])
    assert groups == [["a", "a2"], ["b"]]
    assert E.look_of(tmp_path / "missing.png") is None


def _project(client, auth):
    code = f"EV-{uuid.uuid4().hex[:6]}"
    return client.post("/api/projects", json={"codename": code, "name": code, "kind": "internal"}, headers=auth).json()


def _line(url, file_name, title="Sign in", status=200, server="nginx", subject="app.acme.test"):
    return json.dumps({
        "url": url, "final_url": url, "response_code": status, "title": title, "content_length": 100, "file_name": file_name,
        "failed": False, "technologies": [{"value": "Nginx"}], "headers": [{"key": "Server", "value": server}],
        "tls": {"subject_name": subject, "issuer": "R11", "san_list": [{"value": subject}], "valid_from": "2026-10-01T00:00:00Z",
                "valid_to": "2026-12-30T00:00:00Z"},
    })


def _run(task_id, pages: dict[str, Image.Image], lines: list[str]):
    """A finished gowitness job: its screenshots as artifacts, its JSON lines parsed and ingested."""

    async def go():
        from castor_server import models as m
        from castor_server.db import SessionLocal
        from castor_server.parsers import gowitness_results

        async with SessionLocal() as db:
            task = await db.get(m.Task, uuid.UUID(task_id))
            now = datetime.now(UTC)
            job = m.Job(task_id=task.id, command="x", status=m.JobStatus.succeeded, started_at=now, finished_at=now)
            db.add(job)
            await db.flush()
            folder = Path(settings.data_dir) / "test-shots" / str(job.id)
            folder.mkdir(parents=True, exist_ok=True)
            for name, img in pages.items():
                img.save(folder / name, quality=60)
                db.add(m.Attachment(project_id=task.project_id, task_id=task.id, job_id=job.id, kind="artifact",
                                    filename=f"artifacts/screenshots/{name}", content_type="image/jpeg", size=(folder / name).stat().st_size,
                                    storage_path=str((folder / name).relative_to(settings.data_dir))))
            rows = gowitness_results("\n".join(lines).encode())
            out = m.JobOutput(job_id=job.id, name="pages", type="list<url>", parser="gowitness.results", value=rows)
            db.add(out)
            await db.commit()
            await A.ingest_outputs(db, job, task, [out])
            return job.id, out.id

    return asyncio.run(go())


def test_gowitness_screenshots_become_evidence_and_looks(client, auth):
    p = _project(client, auth)
    card = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Screenshots", "kind": "command", "command_ref": "gowitness-screenshots",
                                                               "command_args": {"targets": "app.acme.test"}}, headers=auth).json()
    trig = client.post(f"/api/projects/{p['id']}/asset-triggers", json={
        "name": "Page looks different", "asset_type": "url", "on_event": "updated", "action": "command", "target": "httpx-probe",
        "args": {"targets": "{{ asset.url }}"}, "conditions": [{"field": "look", "op": "changed"}], "once_per_asset": False}, headers=auth)
    assert trig.status_code == 201, trig.text

    _run(card["id"], {"https---app.acme.test-443.jpeg": _page("login", "10:01"), "https---b.acme.test-443.jpeg": _page("login", "11:30"),
                      "https---c.acme.test-443.jpeg": _page("dashboard")},
         [_line("https://app.acme.test:443", "https---app.acme.test-443.jpeg"), _line("https://b.acme.test:443", "https---b.acme.test-443.jpeg"),
          _line("https://c.acme.test:443", "https---c.acme.test-443.jpeg", title="Dashboard")])
    urls = {a["fields"]["url"]: a for a in client.get(f"/api/projects/{p['id']}/assets?type=url", headers=auth).json()["items"]}
    assert set(urls) == {"https://app.acme.test", "https://b.acme.test", "https://c.acme.test"}  # default ports dropped
    app = urls["https://app.acme.test"]
    assert app["fields"]["look"] and app["fields"]["cert_issuer"] == "R11" and app["fields"]["tech"] == ["Nginx"]
    detail = client.get(f"/api/assets/{app['id']}", headers=auth).json()
    kinds = sorted(e["kind"] for e in detail["evidence"])
    assert kinds == ["certificate", "headers", "screenshot"]
    shot = next(e for e in detail["evidence"] if e["kind"] == "screenshot")
    assert shot["attachment"]["filename"].endswith("https---app.acme.test-443.jpeg") and shot["source"]["command"] == "gowitness-screenshots"
    assert next(e for e in detail["evidence"] if e["kind"] == "headers")["data"] == {"Server": "nginx"}

    # The gallery: the two login pages are one group.
    gallery = client.get(f"/api/projects/{p['id']}/assets/gallery", params={"type": "url"}, headers=auth).json()
    assert [g["size"] for g in gallery["groups"]] == [2, 1] and gallery["total"] == 3
    assert {i["fields"]["url"] for i in gallery["groups"][0]["items"]} == {"https://app.acme.test", "https://b.acme.test"}
    assert client.get(f"/api/attachments/{shot['attachment']['id']}/thumbnail", headers=auth).status_code == 200

    # A week later: the clock moved (same look, no trigger), then the page really changed.
    _run(card["id"], {"https---app.acme.test-443.jpeg": _page("login", "09:12")},
         [_line("https://app.acme.test:443", "https---app.acme.test-443.jpeg", server="nginx")])
    assert client.get(f"/api/assets/{app['id']}", headers=auth).json()["fields"]["look"] == app["fields"]["look"]
    assert client.get(f"/api/projects/{p['id']}/asset-trigger-fires", headers=auth).json() == []
    job, out_id = _run(card["id"], {"https---app.acme.test-443.jpeg": _page("dashboard")},
                       [_line("https://app.acme.test:443", "https---app.acme.test-443.jpeg", title="Admin", server="Apache")])
    detail = client.get(f"/api/assets/{app['id']}", headers=auth).json()
    assert detail["fields"]["look"] != app["fields"]["look"]
    assert [e["look_changed"] for e in detail["evidence"] if e["kind"] == "screenshot"] == [True, False, False]
    assert len([e for e in detail["evidence"] if e["kind"] == "headers"]) == 2  # kept again only when different
    assert any(c["field"] == "look" for c in detail["history"][0]["changes"])
    fires = client.get(f"/api/projects/{p['id']}/asset-trigger-fires", headers=auth).json()
    assert [f["asset"]["id"] for f in fires] == [app["id"]]
    changes = client.get(f"/api/projects/{p['id']}/assets/changes", params={"since": "2020-01-01T00:00:00Z", "kind": "changed"},
                         headers=auth).json()["items"]
    look = next(c for c in changes if any(x["field"] == "look" for x in c["changes"]))
    assert look["pictures"][0] and look["pictures"][1] and look["pictures"][0] != look["pictures"][1]

    # Parsing again adds nothing; deleting the card keeps the asset's screenshots.
    async def reparse():
        from castor_server import models as m
        from castor_server.db import SessionLocal

        async with SessionLocal() as db:
            j = await db.get(m.Job, job)
            await A.ingest_outputs(db, j, await db.get(m.Task, j.task_id), [await db.get(m.JobOutput, out_id)])

    asyncio.run(reparse())
    assert len(client.get(f"/api/assets/{app['id']}", headers=auth).json()["evidence"]) == len(detail["evidence"])
    assert client.delete(f"/api/tasks/{card['id']}", headers=auth).status_code == 204
    after = client.get(f"/api/assets/{app['id']}", headers=auth).json()["evidence"]
    assert len([e for e in after if e["kind"] == "screenshot"]) == 3
    assert client.get(f"/api/attachments/{next(e for e in after if e['kind'] == 'screenshot')['attachment']['id']}/thumbnail", headers=auth).status_code == 200
    client.delete(f"/api/projects/{p['id']}", headers=auth)
