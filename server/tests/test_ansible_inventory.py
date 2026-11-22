"""Ansible inventories as SSH hosts (ansible_inventory.py, api/ssh_hosts.py's from-inventory)."""

import json
import uuid

import pytest

from castor_server import ansible_inventory as inv

INI = """
# the lab
bastion ansible_host=203.0.113.4 ansible_user=ops

[web]
web[01:03].lab ansible_port=2222
win1 ansible_connection=winrm

[db]
db-[a:b].lab

[db:vars]
ansible_user=postgres

[prod:children]
web
db

[prod:vars]
ansible_user=deploy
ansible_port=22
"""

YAML = """
all:
  vars: {ansible_user: root}
  hosts:
    solo: {ansible_host: 10.0.0.9}
  children:
    web:
      hosts:
        w1: {ansible_host: 10.0.0.1, ansible_port: 2200}
        w2:
      vars: {ansible_user: www}
    tmpl:
      hosts:
        t1: {ansible_host: "{{ lookup('env', 'IP') }}"}
"""

LIST = {
    "_meta": {"hostvars": {"a.lab": {"ansible_host": "10.1.0.1", "ansible_user": "ubuntu"}, "b.lab": {}}},
    "all": {"children": ["ungrouped", "app"]},
    "app": {"hosts": ["a.lab", "b.lab"], "vars": {"ansible_port": 2022}},
}


def by_name(hosts):
    return {h.name: h for h in hosts}


def test_ini():
    hosts = by_name(inv.read(INI))
    assert list(hosts) == ["bastion", "web01.lab", "web02.lab", "web03.lab", "win1", "db-a.lab", "db-b.lab"]
    assert hosts["bastion"].host == "203.0.113.4" and hosts["bastion"].user == "ops" and hosts["bastion"].groups == []
    # A host's own vars win over its group's, its group's over the group above it.
    assert (hosts["web02.lab"].port, hosts["web02.lab"].user, hosts["web02.lab"].groups) == (2222, "deploy", ["prod", "web"])
    assert (hosts["db-b.lab"].port, hosts["db-b.lab"].user) == (22, "postgres")
    assert hosts["win1"].skip and "winrm" in hosts["win1"].skip


def test_yaml_and_list_json():
    hosts = by_name(inv.read(YAML))
    assert (hosts["solo"].host, hosts["solo"].user) == ("10.0.0.9", "root")
    assert (hosts["w1"].host, hosts["w1"].port, hosts["w1"].user, hosts["w1"].groups) == ("10.0.0.1", 2200, "www", ["web"])
    assert hosts["w2"].host == "w2"
    assert hosts["t1"].skip and "template" in hosts["t1"].skip
    listed = by_name(inv.read(json.dumps(LIST)))
    assert (listed["a.lab"].host, listed["a.lab"].user, listed["a.lab"].port, listed["a.lab"].groups) == ("10.1.0.1", "ubuntu", 2022, ["app"])
    rows = by_name(inv.from_rows([{"name": "x", "host": "10.9.9.9", "port": "22", "groups": ["all", "web"]}, {"bad": 1}]))
    assert list(rows) == ["x"] and rows["x"].port == 22 and rows["x"].groups == ["web"]


@pytest.mark.parametrize("text", ["", "[web:nope]\nx", "web[01:a]"])
def test_bad(text):
    with pytest.raises(inv.InventoryError):
        inv.read(text)


def test_adding_an_inventorys_hosts(client, auth):
    prefix = f"inv{uuid.uuid4().hex[:4]}-"
    preview = client.post(f"/api/ssh-hosts/inventory?prefix={prefix}", json={"inventory": INI}, headers=auth)
    assert preview.status_code == 200, preview.text
    rows = {r["name"]: r for r in preview.json()}
    assert rows["web01.lab"]["agent_name"] == f"{prefix}web01.lab" and not rows["web01.lab"]["exists"]
    r = client.post("/api/ssh-hosts/from-inventory", json={
        "inventory": INI, "names": ["web01.lab", "db-a.lab", "win1", "nowhere"], "prefix": prefix, "generate_key": True,
        "tags": ["lab"]}, headers=auth)
    assert r.status_code == 201, r.text
    out = r.json()
    added = {h["name"]: h for h in out["added"]}
    assert set(added) == {f"{prefix}web01.lab", f"{prefix}db-a.lab"}
    web = added[f"{prefix}web01.lab"]
    assert (web["host"], web["port"], web["username"], web["tags"]) == ("web01.lab", 2222, "deploy", ["lab", "prod", "web"])
    assert web["public_key"] == out["public_key"] == added[f"{prefix}db-a.lab"]["public_key"]  # one key for them all
    assert set(out["skipped"]) == {"win1", "nowhere"}
    again = client.post("/api/ssh-hosts/from-inventory", json={"inventory": INI, "names": ["web01.lab"], "prefix": prefix, "generate_key": True},
                        headers=auth).json()
    assert not again["added"] and "already exists" in again["skipped"]["web01.lab"]
    for h in out["added"]:
        assert client.delete(f"/api/ssh-hosts/{h['id']}", headers=auth).status_code == 204
