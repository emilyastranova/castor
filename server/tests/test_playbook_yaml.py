"""Spec → YAML for the visual editor, and the to-yaml endpoint."""

from pathlib import Path

import pytest
import yaml

from castor_server.playbook_spec import parse_playbook_spec
from castor_server.playbook_yaml import authoring_dict, to_yaml

PLAYBOOKS = sorted((Path(__file__).resolve().parents[2] / "playbooks").glob("*.yaml"))


@pytest.mark.parametrize("path", PLAYBOOKS, ids=lambda p: p.stem)
def test_round_trip(path):
    spec = parse_playbook_spec(path.read_text())
    again = parse_playbook_spec(to_yaml(spec.model_dump(mode="json")))
    assert again.model_dump(mode="json") == spec.model_dump(mode="json")


def test_only_what_an_author_writes():
    spec = parse_playbook_spec("""
id: t
name: T
steps:
  - { id: a, name: A, command_ref: x }
  - id: b
    name: B
    command_ref: x
    depends_on: [a]
    for_each: "{{ steps.a.outputs.hosts }}"
    args: { t: "{{ item }}" }
  - id: c
    name: C
    transform: { input: "{{ steps.b.outputs.hosts }}" }
    depends_on: [a]
layout: { a: { x: 10.4, y: 20 }, gone: { x: 0, y: 0 } }
""")
    d = authoring_dict(spec.model_dump(mode="json"))
    a, b, c = d["steps"]
    assert a == {"id": "a", "name": "A", "command_ref": "x"}  # no kind, no defaults
    assert "depends_on" not in b  # implied by for_each
    assert b["for_each"] == "{{ steps.a.outputs.hosts }}"  # short form
    assert c["depends_on"] == ["a"]  # explicit, not implied by the input
    assert c["transform"] == {"input": "{{ steps.b.outputs.hosts }}"}
    assert d["layout"] == {"a": {"x": 10, "y": 20}}  # positions of unknown steps dropped
    assert yaml.safe_load(to_yaml(spec.model_dump(mode="json")))["steps"][0] == a


def test_to_yaml_endpoint(client, auth):
    spec = {"id": "ed-test", "name": "Ed", "steps": [{"id": "a", "name": "A", "command_ref": "nmap-ping-sweep", "args": {}}]}
    r = client.post("/api/playbooks/to-yaml", json={"spec": spec}, headers=auth)
    body = r.json()
    assert r.status_code == 200
    assert body["ok"] is False and "required argument targets is not bound" in body["errors"][0]
    assert body["yaml"].startswith("id: ed-test\nname: Ed\n")

    spec["steps"][0]["args"] = {"targets": "10.0.0.0/24"}
    spec["steps"].append({"id": "a", "name": "dup", "command_ref": "x"})
    body = client.post("/api/playbooks/to-yaml", json={"spec": spec}, headers=auth).json()
    assert body["ok"] is False and body["errors"] == ["duplicate step id: a"] and body["yaml"]


def test_transform_needs_an_input():
    with pytest.raises(ValueError, match="the transform has no input"):
        parse_playbook_spec("id: t\nname: T\nsteps:\n  - { id: a, name: A, transform: { input: [] } }\n")
