"""Per-agent tokens: enrollment, binding, rotate, revoke, and the shared-secret fallback."""

import json
import uuid

import pytest
from starlette.websockets import WebSocketDisconnect

from castor_server.agent_tokens import hash_token, new_token
from castor_server.config import settings


def hello(**creds):
    return json.dumps(
        {
            "type": "hello",
            "hostname": "box",
            "os": "Linux",
            "username": "u",
            "version": "0.1.0",
            "capabilities": {"runtimes": ["host"]},
            "max_concurrency": 1,
            **creds,
        }
    )


def connect(client, **creds) -> dict:
    """Say hello and return the server's reply (closing the socket afterwards)."""
    with client.websocket_connect("/ws/agent") as ws:
        ws.send_text(hello(**creds))
        return json.loads(ws.receive_text())


def agents(client, auth) -> dict:
    return {a["name"]: a for a in client.get("/api/agents", headers=auth).json()}


def test_token_format():
    token, digest, prefix = new_token()
    assert token.startswith("castor_") and len(token) > 40
    assert digest == hash_token(token) and token not in digest
    assert token.startswith(prefix) and len(prefix) == 13


def test_enroll_bind_and_use(client, auth):
    name = f"tok-{uuid.uuid4().hex[:6]}"
    r = client.post("/api/agent-tokens", json={"label": name, "tags": ["dmz", " "]}, headers=auth)
    assert r.status_code == 201
    created = r.json()
    token = created["token"]
    assert created["agent_id"] is None and created["tags"] == ["dmz"]
    # Never shown again.
    listed = next(t for t in client.get("/api/agent-tokens", headers=auth).json()["tokens"] if t["id"] == created["id"])
    assert "token" not in listed and listed["prefix"] == token[:13]

    assert connect(client, token=token, name=name)["type"] == "hello.ok"
    agent = agents(client, auth)[name]
    assert "dmz" in agent["tags"]
    listed = next(t for t in client.get("/api/agent-tokens", headers=auth).json()["tokens"] if t["id"] == created["id"])
    assert listed["agent_id"] == agent["id"] and listed["agent_name"] == name and listed["last_used_at"]

    # The token decides who it is: a different --name renames the same agent.
    assert connect(client, token=token, name=f"{name}-b")["type"] == "hello.ok"
    assert agents(client, auth)[f"{name}-b"]["id"] == agent["id"]

    # The shared secret can't connect as an agent that has a token.
    reply = connect(client, secret="test-agent-secret", name=f"{name}-b")
    assert reply["type"] == "hello.error" and "has its own token" in reply["reason"]


def test_rotate_and_revoke_disconnect(client, auth):
    name = f"rot-{uuid.uuid4().hex[:6]}"
    created = client.post("/api/agent-tokens", json={"label": name}, headers=auth).json()
    old = created["token"]
    with client.websocket_connect("/ws/agent") as ws:
        ws.send_text(hello(token=old, name=name))
        assert json.loads(ws.receive_text())["type"] == "hello.ok"
        r = client.post(f"/api/agent-tokens/{created['id']}/rotate", headers=auth)
        assert r.status_code == 200
        new = r.json()["token"]
        assert new != old
        assert json.loads(ws.receive_text()) == {"type": "auth.revoked", "reason": "token rotated"}
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()
        assert closed.value.code == 4401

    assert connect(client, token=old, name=name) == {"type": "hello.error", "reason": "unknown token (it may have been rotated)"}
    assert connect(client, token=new, name=name)["type"] == "hello.ok"

    r = client.post(f"/api/agent-tokens/{created['id']}/revoke", headers=auth)
    assert r.json()["revoked_at"]
    assert connect(client, token=new, name=name)["reason"] == "token revoked"
    # Revoking also keeps the shared secret out, or it would undo the revoke.
    assert "has its own token" in connect(client, secret="test-agent-secret", name=name)["reason"]

    # Forgetting the agent deletes its tokens and lifts that.
    agent_id = agents(client, auth)[name]["id"]
    assert client.delete(f"/api/agents/{agent_id}", headers=auth).status_code == 204
    assert all(t["id"] != created["id"] for t in client.get("/api/agent-tokens", headers=auth).json()["tokens"])
    assert connect(client, secret="test-agent-secret", name=name)["type"] == "hello.ok"


def test_delete_needs_revoke_first(client, auth):
    name = f"del-{uuid.uuid4().hex[:6]}"
    created = client.post("/api/agent-tokens", json={}, headers=auth).json()
    unused = client.post("/api/agent-tokens", json={}, headers=auth).json()
    assert client.delete(f"/api/agent-tokens/{unused['id']}", headers=auth).status_code == 204  # never used
    connect(client, token=created["token"], name=name)
    assert client.delete(f"/api/agent-tokens/{created['id']}", headers=auth).status_code == 409
    client.post(f"/api/agent-tokens/{created['id']}/revoke", headers=auth)
    assert client.delete(f"/api/agent-tokens/{created['id']}", headers=auth).status_code == 204


def test_shared_secret_can_be_turned_off(client, auth, monkeypatch):
    assert connect(client, secret="wrong", name="x")["reason"] == "bad secret"
    assert connect(client, token="castor_nope", name="x")["reason"].startswith("unknown token")
    monkeypatch.setattr(settings, "allow_shared_agent_secret", False)
    assert connect(client, secret="test-agent-secret", name="x")["reason"] == "this server only accepts agent tokens"
    assert client.get("/api/agent-tokens", headers=auth).json()["shared_secret_allowed"] is False
