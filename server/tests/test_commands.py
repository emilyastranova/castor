"""Command definition parsing and template rendering."""

import shlex
from pathlib import Path

import pytest

from castor_server.command_render import ArgumentError, render_command
from castor_server.command_spec import parse_spec

COMMANDS_DIR = Path(__file__).resolve().parents[2] / "commands"

SPEC = parse_spec(
    """
id: demo
name: Demo
args:
  - {name: targets, type: list, required: true, separator: ","}
  - {name: ports, type: string, default: "1-1000", regex: '^[0-9,-]+$'}
  - {name: verbose, type: boolean, default: false}
  - {name: rate, type: number}
  - {name: mode, type: string, choices: [fast, slow], default: fast}
command: scan -p {{ ports }} --mode {{ mode }} --rate {{ rate }} {{ targets }}
"""
)


def test_builtin_definitions_parse():
    files = sorted(COMMANDS_DIR.glob("*.yaml"))
    assert files, "expected built-in definitions in commands/"
    for path in files:
        spec = parse_spec(path.read_text())
        assert spec.id == path.stem


def test_render_with_defaults_and_list_join():
    cmd = render_command(SPEC, {"targets": "10.0.0.1, 10.0.0.2", "rate": "100"})
    assert shlex.split(cmd) == ["scan", "-p", "1-1000", "--mode", "fast", "--rate", "100", "10.0.0.1,10.0.0.2"]


def test_values_cannot_escape_their_argument():
    spec = parse_spec("id: echoer\nname: E\nargs: [{name: text}]\ncommand: echo {{ text }}\n")
    tricky = "a; echo injected $(id) `id` 'q' \"d\""
    cmd = render_command(spec, {"text": tricky})
    assert shlex.split(cmd) == ["echo", tricky]


def test_validation_errors_are_per_argument():
    with pytest.raises(ArgumentError) as exc:
        render_command(SPEC, {"ports": "80;x", "mode": "medium", "rate": "lots", "bogus": 1})
    assert set(exc.value.errors) == {"targets", "ports", "mode", "rate", "bogus"}


@pytest.mark.parametrize(
    "yaml_text, message",
    [
        ("id: Bad_Id\nname: x\ncommand: x\n", "id"),
        ("id: ok\nname: x\ncommand: run {{ missing }}\n", "undefined"),
        ("id: ok\nname: x\nargs: [{name: a}, {name: a}]\ncommand: x\n", "unique"),
        ("- not a mapping\n", "mapping"),
    ],
)
def test_invalid_definitions(yaml_text, message):
    with pytest.raises(ValueError, match=message):
        parse_spec(yaml_text)


# ---------- API ----------

USER_YAML = """
id: http-probe
name: HTTP probe
image: docker.io/library/alpine:3.20
args:
  - {name: url, required: true, regex: '^https?://\\S+$'}
  - {name: timeout, type: number, default: 5}
command: wget -q -T {{ timeout }} -O - {{ url }}
"""


def _project(client, auth):
    existing = [p for p in client.get("/api/projects", headers=auth).json() if p["codename"] == "CMD-TEST"]
    if existing:
        return existing[0]
    return client.post("/api/projects", json={"codename": "CMD-TEST", "name": "Command tests"}, headers=auth).json()


def test_builtins_are_loaded_and_read_only(client, auth):
    ids = {c["id"]: c for c in client.get("/api/commands", headers=auth).json()}
    assert {"nmap-ping-sweep", "nmap-service-scan", "ffuf-dir"} <= set(ids)
    assert ids["nmap-ping-sweep"]["origin"] == "builtin"
    r = client.put("/api/commands/nmap-ping-sweep", json={"yaml": "id: nmap-ping-sweep\nname: x\ncommand: x\n"}, headers=auth)
    assert r.status_code == 409
    assert client.delete("/api/commands/nmap-ping-sweep", headers=auth).status_code == 409


def test_user_definition_lifecycle_and_render(client, auth):
    r = client.post("/api/commands", json={"yaml": USER_YAML}, headers=auth)
    assert r.status_code == 201, r.text
    assert r.json()["origin"] == "user" and r.json()["spec"]["image"] == "docker.io/library/alpine:3.20"
    assert client.post("/api/commands", json={"yaml": USER_YAML}, headers=auth).status_code == 409
    bad = client.post("/api/commands", json={"yaml": "id: Nope\nname: x\ncommand: x\n"}, headers=auth)
    assert bad.status_code == 422 and "id" in bad.json()["detail"]

    r = client.post("/api/commands/http-probe/render", json={"args": {"url": "https://example.test/a b"}}, headers=auth)
    assert r.status_code == 422 and set(r.json()["detail"]["errors"]) == {"url"}
    r = client.post("/api/commands/http-probe/render", json={"args": {"url": "https://example.test/"}}, headers=auth)
    assert r.json() == {"command": "wget -q -T 5 -O - https://example.test/", "image": "docker.io/library/alpine:3.20"}


def test_card_from_library_command(client, auth):
    project = _project(client, auth)
    r = client.post(
        f"/api/projects/{project['id']}/tasks",
        json={"title": "Sweep", "kind": "command", "command_ref": "nmap-ping-sweep", "command_args": {"targets": "10.0.0.0/24"}},
        headers=auth,
    )
    assert r.status_code == 201, r.text
    task = r.json()
    assert task["command_ref"] == "nmap-ping-sweep"
    assert task["command"] == "nmap -sn -oX /work/ping-sweep.xml 10.0.0.0/24"
    assert task["image"] == "docker.io/instrumentisto/nmap"

    # Editing the args re-renders the command line
    r = client.patch(f"/api/tasks/{task['id']}", json={"command_args": {"targets": "10.0.1.0/24"}}, headers=auth)
    assert r.json()["command"].endswith("10.0.1.0/24")
    # Invalid args are rejected per argument and leave the card unchanged
    r = client.patch(f"/api/tasks/{task['id']}", json={"command_args": {"targets": "10.0.0.0/24 && id"}}, headers=auth)
    assert r.status_code == 422 and "targets" in r.json()["detail"]["errors"]
    # The raw command line belongs to the library while attached
    assert client.patch(f"/api/tasks/{task['id']}", json={"command": "id"}, headers=auth).status_code == 409
    # Detaching keeps the rendered line as a plain shell command, which is then editable
    r = client.patch(f"/api/tasks/{task['id']}", json={"command_ref": None}, headers=auth)
    assert r.json()["command_ref"] is None and r.json()["command"].endswith("10.0.1.0/24")
    assert client.patch(f"/api/tasks/{task['id']}", json={"command": "id"}, headers=auth).json()["command"] == "id"


def test_deleting_user_definition_detaches_cards(client, auth):
    project = _project(client, auth)
    task = client.post(
        f"/api/projects/{project['id']}/tasks",
        json={"title": "Probe", "command_ref": "http-probe", "command_args": {"url": "https://example.test/"}},
        headers=auth,
    ).json()
    assert task["kind"] == "command"
    assert client.get("/api/commands/http-probe", headers=auth).json()["in_use"] is True
    assert client.delete("/api/commands/http-probe", headers=auth).status_code == 204
    after = client.get(f"/api/tasks/{task['id']}", headers=auth).json()
    assert after["command_ref"] is None and after["command"].startswith("wget")
