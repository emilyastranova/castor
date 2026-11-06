"""Duplicating a card (3m): the blueprint lands at the top of To Do; comments, files and
finished runs come only when asked, with new ids; run and step cards are refused."""

import asyncio
import uuid


def _project(client, auth):
    code = f"DU-{uuid.uuid4().hex[:6]}"
    return client.post("/api/projects", json={"codename": code, "name": code, "kind": "internal"}, headers=auth).json()


def _finished_job(task_id):
    """A finished run with a log line, a parsed output and an artifact."""

    async def go():
        from castor_server import models as m
        from castor_server.config import settings
        from castor_server.db import SessionLocal

        async with SessionLocal() as db:
            task = await db.get(m.Task, uuid.UUID(task_id))
            job = m.Job(task_id=task.id, command="echo hi", status=m.JobStatus.succeeded, exit_code=0)
            db.add(job)
            await db.flush()
            db.add(m.JobLogChunk(job_id=job.id, seq=1, stream="stdout", data="hi\n"))
            db.add(m.JobOutput(job_id=job.id, name="hosts", type="hosts", parser="lines", value=["10.0.0.1"]))
            art_id = uuid.uuid4()
            path = settings.uploads_dir / str(task.project_id) / f"{art_id}-result.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("result")
            db.add(m.Attachment(id=art_id, project_id=task.project_id, task_id=task.id, job_id=job.id, kind="artifact",
                                filename="result.txt", size=6, storage_path=str(path.relative_to(settings.data_dir))))
            await db.commit()
            return str(job.id)

    return asyncio.run(go())


def test_blueprint_goes_to_the_top_of_todo(client, auth):
    p = _project(client, auth)
    board = client.get(f"/api/projects/{p['id']}/board", headers=auth).json()
    todo = next(c for c in board["columns"] if c["role"] == "todo")
    done = next(c for c in board["columns"] if c["role"] == "done")
    first = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Already here"}, headers=auth).json()
    src = client.post(f"/api/projects/{p['id']}/tasks", json={
        "title": "Sweep", "kind": "command", "command_ref": "nmap-ping-sweep", "command_args": {"targets": "10.1.0.0/24"},
        "tags": ["recon"], "priority": 2, "assignees": ["admin"], "column_id": done["id"],
    }, headers=auth).json()
    client.post(f"/api/tasks/{src['id']}/comments", json={"body": "a note"}, headers=auth)
    _finished_job(src["id"])

    r = client.post(f"/api/tasks/{src['id']}/duplicate", json={}, headers=auth)
    assert r.status_code == 201, r.text
    copy = r.json()
    assert copy["id"] != src["id"] and copy["title"] == "Sweep (copy)" and copy["column_id"] == todo["id"]
    assert copy["position"] < first["position"]  # on top
    assert copy["command"] == src["command"] and copy["command_ref"] == "nmap-ping-sweep"
    assert copy["tags"] == ["recon"] and copy["priority"] == 2 and copy["assignees"] == src["assignees"]
    assert copy["latest_job"] is None and copy["comment_count"] == 0 and copy["attachment_count"] == 0
    history = client.get(f"/api/tasks/{copy['id']}/activity", headers=auth).json()
    assert any("Duplicated “Sweep”" in h["message"] for h in history)

    bare = client.post(f"/api/tasks/{src['id']}/duplicate", json={"assignees": False}, headers=auth).json()
    assert bare["assignees"] == []


def test_comments_files_and_output_when_asked(client, auth):
    p = _project(client, auth)
    src = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Recon"}, headers=auth).json()
    att = client.post(f"/api/tasks/{src['id']}/attachments", files={"file": ("shot.png", b"\x89PNG fake", "image/png")}, headers=auth).json()
    link = f"/api/attachments/{att['id']}/download?inline=true"
    client.patch(f"/api/tasks/{src['id']}", json={"description": f"![shot]({link})"}, headers=auth)
    client.post(f"/api/tasks/{src['id']}/comments", json={"body": f"see ![shot]({link}) @admin"}, headers=auth)
    job_id = _finished_job(src["id"])

    copy = client.post(f"/api/tasks/{src['id']}/duplicate", json={"comments": True, "attachments": True, "output": True}, headers=auth).json()
    files = client.get(f"/api/tasks/{copy['id']}/attachments", headers=auth).json()
    assert {f["filename"] for f in files} == {"shot.png", "result.txt"} and att["id"] not in {f["id"] for f in files}
    shot = next(f for f in files if f["filename"] == "shot.png")
    assert client.get(f"/api/attachments/{shot['id']}/download", headers=auth).content == b"\x89PNG fake"
    detail = client.get(f"/api/tasks/{copy['id']}", headers=auth).json()
    assert shot["id"] in detail["description"] and att["id"] not in detail["description"]
    comments = client.get(f"/api/tasks/{copy['id']}/comments", headers=auth).json()
    assert len(comments) == 1 and shot["id"] in comments[0]["body"] and comments[0]["mentions"]
    jobs = client.get(f"/api/tasks/{copy['id']}/jobs", headers=auth).json()
    assert len(jobs) == 1 and jobs[0]["id"] != job_id and jobs[0]["status"] == "succeeded"
    full = client.get(f"/api/jobs/{jobs[0]['id']}", headers=auth).json()
    assert "hi" in "".join(c["data"] for c in full["logs"])
    assert client.get(f"/api/jobs/{jobs[0]['id']}/outputs", headers=auth).json()[0]["value"] == ["10.0.0.1"]

    # Deleting the original leaves the copy's files intact.
    client.delete(f"/api/tasks/{src['id']}", headers=auth)
    assert client.get(f"/api/attachments/{shot['id']}/download", headers=auth).status_code == 200

    # Without "attachments", uploads stay behind; without "output", so do runs and artifacts.
    src2 = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Two"}, headers=auth).json()
    client.post(f"/api/tasks/{src2['id']}/attachments", files={"file": ("a.txt", b"a", "text/plain")}, headers=auth)
    _finished_job(src2["id"])
    only_output = client.post(f"/api/tasks/{src2['id']}/duplicate", json={"output": True}, headers=auth).json()
    assert [f["filename"] for f in client.get(f"/api/tasks/{only_output['id']}/attachments", headers=auth).json()] == ["result.txt"]


def test_run_and_step_cards_are_refused(client, auth):
    p = _project(client, auth)
    run = client.post("/api/playbooks/pipeline-demo/run", json={"project_id": p["id"], "inputs": {}}, headers=auth)
    assert run.status_code == 201, run.text
    run = run.json()
    detail = client.get(f"/api/playbook-runs/{run['id']}", headers=auth).json()
    assert client.post(f"/api/tasks/{detail['card_id']}/duplicate", json={}, headers=auth).status_code == 409
    step_task = next(iter(detail["step_task_ids"].values()))
    assert client.post(f"/api/tasks/{step_task}/duplicate", json={}, headers=auth).status_code == 409
    client.post(f"/api/playbook-runs/{run['id']}/cancel", headers=auth)
