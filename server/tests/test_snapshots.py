"""Terminal snapshots: a PNG of a job's output attached to its card with the recipe that made it,
changed in place later."""

import asyncio
import json
import uuid

PNG = b"\x89PNG\r\n\x1a\n" + b"picture"


def _card_with_job(client, auth):
    code = f"SN-{uuid.uuid4().hex[:6]}"
    p = client.post("/api/projects", json={"codename": code, "name": code, "kind": "internal"}, headers=auth).json()
    task = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Scan", "kind": "command", "command": "echo hi"},
                       headers=auth).json()

    async def go():
        from castor_server import models as m
        from castor_server.db import SessionLocal

        async with SessionLocal() as db:
            job = m.Job(task_id=uuid.UUID(task["id"]), command="echo hi", status=m.JobStatus.succeeded, exit_code=0)
            db.add(job)
            await db.commit()
            return str(job.id)

    return p, task, asyncio.run(go())


def test_a_snapshot_keeps_its_recipe_and_changes_in_place(client, auth):
    p, task, job_id = _card_with_job(client, auth)
    recipe = {"v": 1, "from": 0, "to": 3, "theme": "console", "job_id": "someone-else's"}
    r = client.post(f"/api/tasks/{task['id']}/attachments", headers=auth,
                    files={"file": ("snapshot.png", PNG, "image/png")},
                    data={"snapshot": json.dumps(recipe), "job_id": job_id})
    assert r.status_code == 201, r.text
    att = r.json()
    assert att["kind"] == "screenshot" and att["job_id"] == job_id
    assert att["snapshot"]["theme"] == "console" and att["snapshot"]["job_id"] == job_id  # the server's, not the client's

    # Changed: the same attachment, a new picture and recipe.
    r = client.put(f"/api/attachments/{att['id']}", headers=auth,
                   files={"file": ("snapshot.png", PNG + b"2", "image/png")}, data={"snapshot": json.dumps({**recipe, "to": 5})})
    assert r.status_code == 200, r.text
    assert r.json()["id"] == att["id"] and r.json()["snapshot"]["to"] == 5 and r.json()["size"] == len(PNG) + 1
    assert client.get(f"/api/attachments/{att['id']}/download", headers=auth).content == PNG + b"2"
    assert [a["id"] for a in client.get(f"/api/tasks/{task['id']}/attachments", headers=auth).json()] == [att["id"]]
    feed = client.get(f"/api/tasks/{task['id']}/activity", headers=auth).json()
    assert any(e["type"] == "attachment.updated" for e in feed)


def test_snapshots_are_checked(client, auth):
    p, task, job_id = _card_with_job(client, auth)
    url = f"/api/tasks/{task['id']}/attachments"
    # Not a PNG.
    r = client.post(url, headers=auth, files={"file": ("s.png", b"<svg/>", "image/png")}, data={"snapshot": "{}", "job_id": job_id})
    assert r.status_code == 422
    # Not an object, or too big.
    assert client.post(url, headers=auth, files={"file": ("s.png", PNG, "image/png")}, data={"snapshot": "[1]"}).status_code == 422
    big = json.dumps({"x": "a" * 600000})
    assert client.post(url, headers=auth, files={"file": ("s.png", PNG, "image/png")}, data={"snapshot": big}).status_code == 422
    # Another card's job.
    _, other, other_job = _card_with_job(client, auth)
    r = client.post(url, headers=auth, files={"file": ("s.png", PNG, "image/png")}, data={"snapshot": "{}", "job_id": other_job})
    assert r.status_code == 422
    # A plain file can't be "changed" as a snapshot.
    plain = client.post(url, headers=auth, files={"file": ("notes.txt", b"hello", "text/plain")}).json()
    r = client.put(f"/api/attachments/{plain['id']}", headers=auth, files={"file": ("s.png", PNG, "image/png")}, data={"snapshot": "{}"})
    assert r.status_code == 409


def test_a_snapshot_of_text_needs_no_job(client, auth):
    """A picture of pasted text or a code block: the text travels in the recipe."""
    p, task, _ = _card_with_job(client, auth)
    recipe = {"v": 1, "source": "text", "input": "print('hi')\n" * 2000, "language": "python"}
    r = client.post(f"/api/tasks/{task['id']}/attachments", headers=auth,
                    files={"file": ("snapshot.png", PNG, "image/png")}, data={"snapshot": json.dumps(recipe)})
    assert r.status_code == 201, r.text
    att = r.json()
    assert att["job_id"] is None and att["snapshot"]["input"].startswith("print") and att["snapshot"]["source"] == "text"
    r = client.put(f"/api/attachments/{att['id']}", headers=auth,
                   files={"file": ("snapshot.png", PNG, "image/png")}, data={"snapshot": json.dumps({**recipe, "input": "x"})})
    assert r.status_code == 200 and r.json()["snapshot"]["input"] == "x" and r.json()["job_id"] is None
