"""A project's Files: every attachment, artifact, snapshot and pasted picture with where it came
from, filters with counts, a zip of a selection and gallery thumbnails."""

import asyncio
import io
import json
import uuid
import zipfile

from PIL import Image

from castor_server.api.files import category


def _png(w=800, h=500) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (40, 90, 200)).save(buf, "PNG")
    return buf.getvalue()


def _project(client, auth):
    code = f"FL-{uuid.uuid4().hex[:6]}"
    p = client.post("/api/projects", json={"codename": code, "name": code, "kind": "internal"}, headers=auth).json()
    card = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Web scan", "kind": "command", "command": "echo"},
                       headers=auth).json()
    other = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Notes from the call", "kind": "manual"},
                        headers=auth).json()

    async def go():
        from castor_server import models as m
        from castor_server.db import SessionLocal

        async with SessionLocal() as db:
            agent = m.Agent(name=f"files-agent-{uuid.uuid4().hex[:6]}")
            db.add(agent)
            await db.flush()
            job = m.Job(task_id=uuid.UUID(card["id"]), command="echo", status=m.JobStatus.succeeded, agent_id=agent.id)
            db.add(job)
            # A playbook run: its card on the board and a hidden step card.
            run_card = (await db.get(m.Task, uuid.UUID(other["id"])))
            run = m.PlaybookRun(playbook_id="recon", playbook_name="Recon", project_id=uuid.UUID(p["id"]),
                                status=m.PlaybookRunStatus.succeeded,
                                spec_snapshot={"id": "recon", "name": "Recon", "steps": []})
            db.add(run)
            await db.flush()
            board_card = m.Task(project_id=run.project_id, column_id=run_card.column_id, title="Recon run")
            db.add(board_card)
            await db.flush()
            run.card_id = board_card.id
            step = m.Task(project_id=run.project_id, column_id=run_card.column_id, title="Find hosts",
                          playbook_run_id=run.id, playbook_step_id="hosts")
            db.add(step)
            await db.flush()
            step_job = m.Job(task_id=step.id, command="echo", status=m.JobStatus.succeeded, agent_id=agent.id)
            db.add(step_job)
            await db.commit()
            return str(agent.id), str(job.id), str(run.id), str(board_card.id), str(step.id), str(step_job.id)

    return p, card, other, *asyncio.run(go())


def _artifact(job_id, task_id, project_id, name, data, content_type):
    async def go():
        from castor_server import models as m
        from castor_server.config import settings
        from castor_server.db import SessionLocal

        d = settings.uploads_dir / project_id / "artifacts" / job_id
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(data)
        async with SessionLocal() as db:
            a = m.Attachment(project_id=uuid.UUID(project_id), task_id=uuid.UUID(task_id), job_id=uuid.UUID(job_id),
                             kind="artifact", filename=name, content_type=content_type, size=len(data),
                             storage_path=str((d / name).relative_to(settings.data_dir)))
            db.add(a)
            await db.commit()
            return str(a.id)

    return asyncio.run(go())


def test_category_reads_type_and_extension():
    assert category("image/png", "a.png") == "image"
    assert category("application/octet-stream", "scan.xml") == "data"
    assert category("application/octet-stream", "nmap.gnmap") == "data"
    assert category("text/plain", "out.log") == "text"
    assert category("application/pdf", "report.pdf") == "document"
    assert category("application/octet-stream", "traffic.pcapng") == "archive"
    assert category("application/octet-stream", "blob") == "other"


def test_every_file_with_where_it_came_from(client, auth):
    p, card, other, agent_id, job_id, run_id, run_card_id, step_id, step_job_id = _project(client, auth)
    pid = p["id"]
    up = lambda task, name, data, ct: client.post(f"/api/tasks/{task}/attachments", headers=auth,  # noqa: E731
                                                 files={"file": (name, data, ct)}).json()
    shot = up(other["id"], "login page.png", _png(), "image/png")
    notes = up(other["id"], "notes.txt", b"call notes", "text/plain")
    xml = _artifact(job_id, card["id"], pid, "scan.xml", b"<nmaprun/>", "application/xml")
    hosts = _artifact(step_job_id, step_id, pid, "hosts.txt", b"10.0.0.1\n", "text/plain")
    pasted = client.post(f"/api/projects/{pid}/attachments", headers=auth,
                         files={"file": ("diagram.png", _png(40, 40), "image/png")}).json()
    snap = client.post(f"/api/tasks/{card['id']}/attachments", headers=auth,
                       files={"file": ("snapshot.png", _png(), "image/png")},
                       data={"snapshot": json.dumps({"v": 1}), "job_id": job_id}).json()

    res = client.get(f"/api/projects/{pid}/files", headers=auth)
    assert res.status_code == 200, res.text
    res = res.json()
    assert res["total"] == res["everything"] == 6
    by_id = {f["id"]: f for f in res["items"]}
    assert by_id[shot["id"]]["category"] == "image" and by_id[shot["id"]]["source"] == "upload"
    assert by_id[shot["id"]]["card_title"] == "Notes from the call" and by_id[shot["id"]]["uploader"]
    assert by_id[xml]["source"] == "artifact" and by_id[xml]["agent_id"] == agent_id and by_id[xml]["category"] == "data"
    # A step's file belongs to the run's card on the board, with the step named.
    h = by_id[hosts]
    assert h["card_id"] == run_card_id and h["task_id"] == step_id and h["run_id"] == run_id
    assert h["step"] == "Find hosts" and h["run_name"] == "Recon" and h["run_started"]
    assert by_id[pasted["id"]]["source"] == "project" and by_id[pasted["id"]]["card_id"] is None
    assert by_id[snap["id"]]["source"] == "snapshot"
    assert res["items"][0]["id"] == snap["id"]  # newest first

    # Filters, and counts that leave their own filter out.
    images = client.get(f"/api/projects/{pid}/files?category=image", headers=auth).json()
    assert images["total"] == 3
    cats = {f["value"]: f["count"] for f in images["facets"]["category"]}
    assert cats == {"image": 3, "text": 2, "data": 1}
    assert {f["value"]: f["count"] for f in images["facets"]["source"]} == {"upload": 1, "snapshot": 1, "project": 1}
    assert client.get(f"/api/projects/{pid}/files?run={run_id}", headers=auth).json()["total"] == 1
    assert client.get(f"/api/projects/{pid}/files?card={run_card_id}", headers=auth).json()["total"] == 1
    assert client.get(f"/api/projects/{pid}/files?card=none", headers=auth).json()["total"] == 1
    assert client.get(f"/api/projects/{pid}/files?agent={agent_id}", headers=auth).json()["total"] == 3
    assert client.get(f"/api/projects/{pid}/files?q=find+hosts", headers=auth).json()["total"] == 1
    assert client.get(f"/api/projects/{pid}/files?source=artifact,snapshot&category=data", headers=auth).json()["total"] == 1
    assert client.get(f"/api/projects/{pid}/files?since=2000-01-01&until=2000-01-02", headers=auth).json()["total"] == 0
    largest = client.get(f"/api/projects/{pid}/files?sort=largest&limit=2", headers=auth).json()
    assert len(largest["items"]) == 2 and largest["items"][0]["size"] >= largest["items"][1]["size"] and largest["total"] == 6
    card_facet = {f["label"]: f["count"] for f in res["facets"]["card"]}
    assert card_facet["Notes and posts"] == 1 and card_facet["Recon run"] == 1

    # A zip of a selection, a folder per card (the run's steps inside its card).
    r = client.post(f"/api/projects/{pid}/files/zip", headers=auth, json={"ids": [shot["id"], notes["id"], hosts, pasted["id"]]})
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    names = sorted(zipfile.ZipFile(io.BytesIO(r.content)).namelist())
    assert names == ["Notes and posts/diagram.png", "Notes from the call/login page.png", "Notes from the call/notes.txt",
                     "Recon run/Find hosts/hosts.txt"]
    # …or of everything a filter matches, with clashing names numbered.
    up(other["id"], "notes.txt", b"more", "text/plain")
    r = client.post(f"/api/projects/{pid}/files/zip", headers=auth, json={"filter": {"category": "text", "card": other["id"]}})
    assert sorted(zipfile.ZipFile(io.BytesIO(r.content)).namelist()) == ["Notes from the call/notes (2).txt",
                                                                         "Notes from the call/notes.txt"]
    assert client.post(f"/api/projects/{pid}/files/zip", headers=auth, json={"ids": []}).status_code == 422

    # Thumbnails: small WebPs, kept; not for what isn't a picture.
    t = client.get(f"/api/attachments/{shot['id']}/thumbnail?size=360", headers=auth)
    assert t.status_code == 200 and t.headers["content-type"] == "image/webp"
    assert Image.open(io.BytesIO(t.content)).width == 360
    assert client.get(f"/api/attachments/{notes['id']}/thumbnail", headers=auth).status_code == 415
    # Deleting the file takes its thumbnails with it.
    from castor_server.api.files import thumbnails_dir

    assert list(thumbnails_dir(uuid.UUID(pid)).glob(f"{shot['id']}-*"))
    client.delete(f"/api/attachments/{shot['id']}", headers=auth)
    assert not list(thumbnails_dir(uuid.UUID(pid)).glob(f"{shot['id']}-*"))


def test_files_need_the_project(client, auth):
    p, *_ = _project(client, auth)
    other = str(uuid.uuid4())
    assert client.get(f"/api/projects/{other}/files", headers=auth).status_code == 404
    assert client.get(f"/api/projects/{p['id']}/files").status_code == 401
