"""Per-job artifact upload: token issued at dispatch, files land on the card."""

import json

from castor_server.config import settings


def _project(client, auth):
    found = [p for p in client.get("/api/projects", headers=auth).json() if p["codename"] == "ART-TEST"]
    if found:
        return found[0]
    return client.post("/api/projects", json={"codename": "ART-TEST", "name": "Artifacts"}, headers=auth).json()


def _hello(ws, name):
    ws.send_text(json.dumps({
        "type": "hello", "secret": "test-agent-secret", "name": name, "hostname": name,
        "capabilities": {"runtimes": ["podman", "host"]}, "max_concurrency": 20,
    }))
    assert json.loads(ws.receive_text())["type"] == "hello.ok"


def _wait_for_job(ws, job_id):
    """Receive job.run messages until ours arrives; finish any older queued jobs we get."""
    while True:
        msg = json.loads(ws.receive_text())
        if msg["type"] != "job.run":
            continue
        if msg["job"]["id"] == job_id:
            return msg["job"]
        ws.send_text(json.dumps({"type": "job.finished", "job_id": msg["job"]["id"], "exit_code": 0}))


def test_artifact_upload_flow(client, auth):
    project = _project(client, auth)
    board = client.get(f"/api/projects/{project['id']}/board", headers=auth).json()
    active = next(c["id"] for c in board["columns"] if c["role"] == "active")

    with client.websocket_connect("/ws/agent") as ws:
        _hello(ws, "artifact-agent")
        task = client.post(
            f"/api/projects/{project['id']}/tasks",
            json={"title": "Sweep", "command_ref": "nmap-ping-sweep", "command_args": {"targets": "10.0.0.0/30"}, "column_id": active},
            headers=auth,
        ).json()
        job_id = task["latest_job"]["id"]
        job = _wait_for_job(ws, job_id)

        artifacts = job["artifacts"]
        assert artifacts["token"]
        assert artifacts["collect"] == ["/work/ping-sweep.xml", "/work/artifacts/**"]

        url = f"/api/jobs/{job_id}/artifacts"
        files = {"file": ("ping-sweep.xml", b"<nmaprun/>", "application/xml")}
        assert client.post(url, files=files, data={"path": "ping-sweep.xml"}, headers={"X-Castor-Job-Token": "wrong"}).status_code == 401
        assert client.post(url, files=files, data={"path": "ping-sweep.xml"}).status_code == 401
        assert client.post("/api/jobs/not-a-uuid/artifacts", files=files, headers={"X-Castor-Job-Token": artifacts["token"]}).status_code == 401

        r = client.post(url, files=files, data={"path": "ping-sweep.xml"}, headers={"X-Castor-Job-Token": artifacts["token"]})
        assert r.status_code == 201, r.text
        assert r.json()["kind"] == "artifact" and r.json()["job_id"] == job_id and r.json()["filename"] == "ping-sweep.xml"

        old_limit = settings.artifact_max_bytes
        settings.artifact_max_bytes = 4
        try:
            big = client.post(url, files={"file": ("big.bin", b"too big", "application/octet-stream")}, headers={"X-Castor-Job-Token": artifacts["token"]})
            assert big.status_code == 413
        finally:
            settings.artifact_max_bytes = old_limit

        ws.send_text(json.dumps({"type": "job.finished", "job_id": job_id, "exit_code": 0}))

    listed = client.get(f"/api/jobs/{job_id}/artifacts", headers=auth).json()
    assert [a["filename"] for a in listed] == ["ping-sweep.xml"]
    content = client.get(f"/api/attachments/{listed[0]['id']}/download", headers=auth).content
    assert content == b"<nmaprun/>"
    assert client.get(f"/api/tasks/{task['id']}", headers=auth).json()["attachment_count"] == 1
