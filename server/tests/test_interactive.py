"""Interactive jobs (3j): tty jobs reach the agent as such, the terminal socket forwards
keystrokes, sizes and signals, prompts alert people until someone takes over, and terminal
output reads as plain text where text is wanted."""

import json
import time
import uuid

from castor_server.interactive import plain


def test_plain_text_from_terminal_output():
    raw = "\x1b[1;32mok\x1b[0m done\r\nprogress 10%\rprogress 100%\r\n\x1b]0;title\x07end"
    assert plain(raw) == "ok done\nprogress 100%\nend"


def _wait(fn, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(0.05)
    return fn()


def test_terminal_socket_prompts_and_takeover(client, auth):
    tag = f"tty-{uuid.uuid4().hex[:6]}"
    p = client.post("/api/projects", json={"codename": f"TT-{uuid.uuid4().hex[:6]}", "name": "TTY", "kind": "internal"}, headers=auth).json()
    task = client.post(f"/api/projects/{p['id']}/tasks", json={
        "title": "Shell", "kind": "command", "command": "bash", "tty": True, "agent_selector": {"tags": [tag]},
    }, headers=auth).json()
    assert task["tty"] is True

    with client.websocket_connect("/ws/agent") as agent_ws:
        agent_ws.send_text(json.dumps({"type": "hello", "secret": "test-agent-secret", "name": f"agent-{tag}", "hostname": "h",
                                       "os": "Linux", "username": "u", "version": "0.1.0", "capabilities": {"runtimes": ["host"], "tty": True},
                                       "tags": [tag], "max_concurrency": 8}))
        assert json.loads(agent_ws.receive_text())["type"] == "hello.ok"
        job_id = client.post(f"/api/tasks/{task['id']}/run", headers=auth).json()["id"]
        # Other tests' queued jobs may come first (any agent can take them): wait for ours.
        while (run := json.loads(agent_ws.receive_text()))["type"] != "job.run" or run["job"]["id"] != job_id:
            pass
        assert run["job"]["tty"] is True
        agent_ws.send_text(json.dumps({"type": "job.started", "job_id": job_id}))
        agent_ws.send_text(json.dumps({"type": "job.log", "job_id": job_id, "seq": 0, "stream": "stdout", "data": "\x1b[32m$\x1b[0m sudo -v\r\n[sudo] password for u: "}))

        # Waiting at a prompt, nobody has typed: the person who ran it is told.
        agent_ws.send_text(json.dumps({"type": "job.prompt", "job_id": job_id, "text": "[sudo] password for u:"}))
        job = _wait(lambda: (j := client.get(f"/api/jobs/{job_id}", headers=auth).json())["waiting_input"] and j)
        assert job["waiting_input"] == "[sudo] password for u:"
        notes = client.get("/api/notifications?unread_only=true", headers=auth).json()["items"]
        assert any(n["kind"] == "job.input" and n["body"] == "[sudo] password for u:" for n in notes)

        with client.websocket_connect(f"/ws/jobs/{job_id}/terminal", headers=auth) as term:
            term.send_text(json.dumps({"t": "resize", "cols": 132, "rows": 43}))
            assert json.loads(agent_ws.receive_text()) == {"type": "job.resize", "cols": 132, "rows": 43, "job_id": job_id}
            term.send_text(json.dumps({"t": "in", "d": "hunter2\r"}))
            assert json.loads(agent_ws.receive_text()) == {"type": "job.stdin", "data": "hunter2\r", "job_id": job_id}
            term.send_text(json.dumps({"t": "signal", "name": "INT"}))
            assert json.loads(agent_ws.receive_text())["type"] == "job.signal"

        # Taken over: theirs now, the prompt is cleared, and later prompts don't alert.
        job = _wait(lambda: (j := client.get(f"/api/jobs/{job_id}", headers=auth).json())["input_by"] and j)
        assert job["waiting_input"] is None and job["tty_size"] == {"cols": 132, "rows": 43}
        agent_ws.send_text(json.dumps({"type": "job.prompt", "job_id": job_id, "text": "Continue? [y/N]"}))
        time.sleep(0.3)
        assert client.get(f"/api/jobs/{job_id}", headers=auth).json()["waiting_input"] is None
        activity = [a["message"] for a in client.get(f"/api/tasks/{task['id']}/activity", headers=auth).json()]
        assert any("took over the terminal" in a for a in activity) and not any("hunter2" in a for a in activity)

        agent_ws.send_text(json.dumps({"type": "job.finished", "job_id": job_id, "exit_code": 0}))
        _wait(lambda: client.get(f"/api/jobs/{job_id}", headers=auth).json()["status"] == "succeeded")
        assert client.get(f"/api/jobs/{job_id}/output", headers=auth).text == "$ sudo -v\n[sudo] password for u: "
        assert "\x1b[32m" in client.get(f"/api/jobs/{job_id}/output?raw=1", headers=auth).text


def test_only_editors_can_type(client, auth):
    p = client.post("/api/projects", json={"codename": f"TV-{uuid.uuid4().hex[:6]}", "name": "TTY", "kind": "internal"}, headers=auth).json()
    task = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Shell", "kind": "command", "command": "bash", "tty": True,
                                                              "agent_selector": {"tags": ["nobody-has-this"]}}, headers=auth).json()
    job = client.post(f"/api/tasks/{task['id']}/run", headers=auth).json()
    name = f"vw{uuid.uuid4().hex[:6]}"
    viewer = client.post("/api/users", json={"username": name, "role": "viewer", "password": "a password", "all_projects": True}, headers=auth).json()
    assert viewer["role"] == "viewer"
    token = client.post("/api/auth/login", json={"username": name, "password": "a password"}).json()["token"]
    client.cookies.clear()
    import pytest
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as exc, client.websocket_connect(f"/ws/jobs/{job['id']}/terminal", headers={"Authorization": f"Bearer {token}"}) as term:
        term.receive_text()
    assert exc.value.code == 4403
    client.post(f"/api/jobs/{job['id']}/cancel", headers=auth)


def test_interactive_jobs_only_go_to_agents_that_can_run_them():
    """An agent from before 3j doesn't advertise tty; it would run the job without a
    terminal (tmux: "open terminal failed: not a terminal"), so it isn't offered one."""
    from types import SimpleNamespace

    from castor_server import models as m
    from castor_server.orchestrator.dispatcher import agent_matches

    def agent(**caps):
        return SimpleNamespace(free_slots=1, capabilities=caps, runtimes={"host"}, tags=[], agent_id=uuid.uuid4())

    tty_job, plain_job = m.Job(command="tmux", tty=True), m.Job(command="echo", tty=False)
    assert not agent_matches(agent(), tty_job, {})
    assert agent_matches(agent(tty=True), tty_job, {})
    assert agent_matches(agent(), plain_job, {})
