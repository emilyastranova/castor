"""Variable kinds and sets: storage, the API, binding to cards and playbooks, dispatch."""

import base64
import json
import sqlite3
import time
import uuid

from castor_server.config import settings

COMMAND = """
id: vartest-login
name: Variable test
args:
  - name: target
    required: true
vars:
  - name: creds
    kind: credential
  - name: key
    kind: ssh-key
    required: false
command: login {{ target }} -u {{ creds.username }} -p {{ creds.password }} -i {{ key.private_key }}
"""


def _project(client, auth, name="Vars"):
    r = client.post(
        "/api/projects",
        json={"codename": f"V-{uuid.uuid4().hex[:6]}", "name": name, "kind": "internal"},
        headers=auth,
    )
    return r.json()


def _set(client, auth, kind="credential", **values):
    body = {"kind_id": kind, "name": f"set-{uuid.uuid4().hex[:6]}", "values": values}
    r = client.post("/api/variables/sets", json=body, headers=auth)
    assert r.status_code == 201, r.text
    return r.json()


def _ensure_command(client, auth):
    if client.get("/api/commands/vartest-login", headers=auth).status_code == 404:
        r = client.post("/api/commands", json={"yaml": COMMAND}, headers=auth)
        assert r.status_code == 201, r.text


def test_builtin_kinds(client, auth):
    kinds = {k["id"]: k for k in client.get("/api/variables/kinds", headers=auth).json()}
    assert {"credential", "ssh-key", "listener", "api-token"} <= set(kinds)
    assert {f["name"]: f["type"] for f in kinds["ssh-key"]["fields"]}["private_key"] == "secret_file"


def test_secrets_are_encrypted_and_hidden_until_revealed(client, auth):
    s = _set(client, auth, username="alice", password="Sup3rS3cret!", domain="CORP")
    assert s["values"] == {"username": "alice", "domain": "CORP"}
    assert s["secrets"] == {"password": True, "nt_hash": False}
    assert s["missing"] == []
    assert "Sup3rS3cret!" not in json.dumps(client.get("/api/variables/sets", headers=auth).json())

    # Stored encrypted, not as plain text.
    db = sqlite3.connect(settings.database_url.split("///", 1)[1])
    raw = db.execute("SELECT secrets FROM variable_sets WHERE name = ?", (s["name"],)).fetchone()[0]
    assert "Sup3rS3cret!" not in raw

    r = client.post(f"/api/variables/sets/{s['id']}/reveal/password", headers=auth)
    assert r.json()["value"] == "Sup3rS3cret!"
    activity = client.get("/api/activity", headers=auth).json()
    assert any(a["type"] == "variable.revealed" and s["name"] in a["message"] for a in activity)

    # Patching leaves untouched fields alone; null clears.
    r = client.patch(f"/api/variables/sets/{s['id']}", json={"values": {"domain": None}}, headers=auth)
    assert r.json()["values"] == {"username": "alice"} and r.json()["secrets"] == {"password": True, "nt_hash": False}


def test_values_are_type_checked(client, auth):
    # A user-defined "scope" kind: a list of endpoints, a host and a port.
    kind_id = f"scope-{uuid.uuid4().hex[:6]}"
    r = client.post("/api/variables/kinds", json={"id": kind_id, "name": "Scope", "fields": [
        {"name": "endpoints", "type": "list"}, {"name": "dc", "type": "host"}, {"name": "port", "type": "port"},
    ]}, headers=auth)
    assert r.status_code == 201, r.text
    body = {"kind_id": kind_id, "name": f"s-{uuid.uuid4().hex[:6]}", "values": {"dc": "bad host!", "port": 70000}}
    r = client.post("/api/variables/sets", json=body, headers=auth)
    assert r.status_code == 422
    assert "dc: must be a hostname or IP address" in r.json()["detail"]
    assert "port: must be between 1 and 65535" in r.json()["detail"]
    ok = _set(client, auth, kind=kind_id, dc="10.0.0.5", port="4444",
              endpoints="https://app.acme.example\nacme.example.com, 10.0.0.0/24")
    assert ok["values"] == {
        "dc": "10.0.0.5", "port": 4444,
        "endpoints": ["https://app.acme.example", "acme.example.com", "10.0.0.0/24"],
    }


def test_builtin_credential_and_listener_shapes(client, auth):
    kinds = {k["id"]: k for k in client.get("/api/variables/kinds", headers=auth).json()}
    cred = {f["name"]: f for f in kinds["credential"]["fields"]}
    assert list(cred) == ["username", "password", "nt_hash", "domain"]
    assert not any(f["required"] for f in cred.values())
    assert cred["nt_hash"]["type"] == "secret"
    assert [f["name"] for f in kinds["listener"]["fields"]] == ["host"]
    # An empty credential is complete (a null session); a hash-only one too.
    assert _set(client, auth)["missing"] == []
    only_hash = _set(client, auth, username="admin", nt_hash="aad3b435b51404eeaad3b435b51404ee")
    assert only_hash["secrets"] == {"password": False, "nt_hash": True}


def test_removing_a_field_from_a_kind_drops_its_values(client, auth):
    kind_id = f"k-{uuid.uuid4().hex[:6]}"
    fields = [{"name": "host", "type": "host"}, {"name": "port", "type": "port"}]
    client.post("/api/variables/kinds", json={"id": kind_id, "name": "K", "fields": fields}, headers=auth)
    s = _set(client, auth, kind=kind_id, host="10.0.0.1", port=80)
    r = client.put(f"/api/variables/kinds/{kind_id}", json={"id": kind_id, "name": "K", "fields": fields[:1]}, headers=auth)
    assert r.status_code == 200, r.text
    assert client.get(f"/api/variables/sets/{s['id']}", headers=auth).json()["values"] == {"host": "10.0.0.1"}


def test_file_fields_upload_and_reveal(client, auth):
    s = _set(client, auth, kind="ssh-key", username="root")
    assert s["missing"] == ["private_key"]
    r = client.put(
        f"/api/variables/sets/{s['id']}/files/private_key",
        files={"file": ("id_ed25519", b"-----BEGIN KEY-----\nabc\n", "application/octet-stream")},
        headers=auth,
    )
    assert r.status_code == 200, r.text
    assert r.json()["files"] == {"private_key": {"filename": "id_ed25519", "size": 24}}
    assert r.json()["missing"] == []
    got = client.post(f"/api/variables/sets/{s['id']}/reveal/private_key", headers=auth).json()
    assert base64.b64decode(got["value"]) == b"-----BEGIN KEY-----\nabc\n"


def test_custom_kinds(client, auth):
    kind = {
        "id": f"k-{uuid.uuid4().hex[:6]}",
        "name": "Socket test",
        "fields": [{"name": "host", "type": "host", "required": True}, {"name": "tls", "type": "boolean", "default": "no"}],
    }
    r = client.post("/api/variables/kinds", json=kind, headers=auth)
    assert r.status_code == 201, r.text
    assert r.json()["fields"][1]["default"] is False
    bad = {**kind, "id": "bad-kind", "fields": [{"name": "Bad Name"}]}
    assert client.post("/api/variables/kinds", json=bad, headers=auth).status_code == 422
    assert client.put("/api/variables/kinds/credential", json={**kind, "id": "credential"}, headers=auth).status_code == 409


def test_commands_check_their_slots(client, auth):
    bad = COMMAND.replace("{{ creds.password }}", "{{ creds.pasword }}").replace("vartest-login", "vartest-bad")
    r = client.post("/api/commands", json={"yaml": bad}, headers=auth)
    assert r.status_code == 422
    assert "credential has no field pasword" in r.json()["detail"]
    undeclared = COMMAND.replace("{{ key.private_key }}", "{{ other.x }}").replace("vartest-login", "vartest-bad2")
    assert "undeclared vars slots: other" in client.post("/api/commands", json={"yaml": undeclared}, headers=auth).json()["detail"]


def test_card_bindings_are_checked(client, auth):
    _ensure_command(client, auth)
    project = _project(client, auth)
    other = _project(client, auth, "Other")
    listener = _set(client, auth, kind="listener", host="10.0.0.5")
    r = client.post(
        f"/api/projects/{project['id']}/tasks",
        json={"title": "x", "kind": "command", "command_ref": "vartest-login", "command_args": {"target": "h"},
              "var_bindings": {"creds": listener["id"]}},
        headers=auth,
    )
    assert r.status_code == 422 and "is a listener set, not credential" in r.json()["detail"]

    restricted = _set(client, auth, username="u", password="pass1234")
    client.patch(f"/api/variables/sets/{restricted['id']}", json={"project_ids": [other["id"]]}, headers=auth)
    r = client.post(
        f"/api/projects/{project['id']}/tasks",
        json={"title": "x", "kind": "command", "command_ref": "vartest-login", "command_args": {"target": "h"},
              "var_bindings": {"creds": restricted["id"]}},
        headers=auth,
    )
    assert r.status_code == 422 and "can't be used in this project" in r.json()["detail"]
    # The set is offered only to the project it's limited to.
    listed = client.get(f"/api/variables/sets?kind=credential&project_id={project['id']}", headers=auth).json()
    assert restricted["id"] not in [s["id"] for s in listed]


def _agent(client, tag):
    ws = client.websocket_connect("/ws/agent")
    agent = ws.__enter__()
    agent.send_text(json.dumps({
        "type": "hello", "secret": "test-agent-secret", "name": f"agent-{tag}", "hostname": "h", "os": "Linux",
        "username": "root", "version": "0.1.0", "capabilities": {"runtimes": ["host"]}, "tags": [tag],
        "max_concurrency": 8,
    }))
    assert json.loads(agent.receive_text())["type"] == "hello.ok"
    return ws, agent


def _receive_job(agent, job_id):
    """Wait for this job's job.run, finishing any other job the agent is sent meanwhile."""
    while True:
        msg = json.loads(agent.receive_text())
        if msg["type"] != "job.run":
            continue
        if msg["job"]["id"] == job_id:
            return msg["job"]
        agent.send_text(json.dumps({"type": "job.finished", "job_id": msg["job"]["id"], "exit_code": 0}))


def _wait(client, auth, job_id, status):
    deadline = time.time() + 5
    while time.time() < deadline:
        detail = client.get(f"/api/jobs/{job_id}", headers=auth).json()
        if detail["status"] == status:
            return detail
        time.sleep(0.05)
    raise AssertionError(detail)


def test_dispatch_sends_values_only_to_the_agent_and_redacts_logs(client, auth):
    _ensure_command(client, auth)
    project = _project(client, auth)
    creds = _set(client, auth, username="svc_scan", password="Pa55word!x")
    key = _set(client, auth, kind="ssh-key", username="root")
    client.put(f"/api/variables/sets/{key['id']}/files/private_key", files={"file": ("k", b"KEY", "text/plain")}, headers=auth)
    tag = uuid.uuid4().hex[:6]
    task = client.post(
        f"/api/projects/{project['id']}/tasks",
        json={"title": "login", "kind": "command", "command_ref": "vartest-login", "command_args": {"target": "10.0.0.9"},
              "var_bindings": {"creds": creds["id"], "key": key["id"]}, "agent_selector": {"tags": [tag]}},
        headers=auth,
    ).json()
    assert task["command"] == (
        "login 10.0.0.9 -u \"${CASTOR_VAR_CREDS_USERNAME}\" -p \"${CASTOR_VAR_CREDS_PASSWORD}\" "
        "-i \"${CASTOR_VAR_KEY_PRIVATE_KEY}\""
    )
    assert task["var_bindings"] == {"creds": creds["id"], "key": key["id"]}

    ws, agent = _agent(client, tag)
    try:
        job_id = client.post(f"/api/tasks/{task['id']}/run", headers=auth).json()["id"]
        job = _receive_job(agent, job_id)
        assert job["env"]["CASTOR_VAR_CREDS_USERNAME"] == "svc_scan"
        assert job["env"]["CASTOR_VAR_CREDS_PASSWORD"] == "Pa55word!x"
        # The output's first line shows the values, with the secret masked.
        assert job["display_command"] == "login 10.0.0.9 -u 'svc_scan' -p '••••' -i '.castor/vars/key/private_key'"
        assert job["files"] == [{
            "env": "CASTOR_VAR_KEY_PRIVATE_KEY", "path": ".castor/vars/key/private_key",
            "data": base64.b64encode(b"KEY").decode(), "mode": 0o600,
        }]
        agent.send_text(json.dumps({"type": "job.started", "job_id": job_id}))
        agent.send_text(json.dumps({"type": "job.log", "job_id": job_id, "seq": 0, "stream": "stdout",
                                    "data": "auth as svc_scan with Pa55word!x\n"}))
        agent.send_text(json.dumps({"type": "job.finished", "job_id": job_id, "exit_code": 0}))
        detail = _wait(client, auth, job_id, "succeeded")
    finally:
        ws.__exit__(None, None, None)
    assert detail["logs"][0]["data"] == "auth as svc_scan with ••••\n"
    # Nothing about the values is stored on the job.
    assert "Pa55word" not in json.dumps(detail) and "svc_scan" not in detail["command"]


def test_missing_binding_fails_the_job_with_a_reason(client, auth):
    _ensure_command(client, auth)
    project = _project(client, auth)
    tag = uuid.uuid4().hex[:6]
    task = client.post(
        f"/api/projects/{project['id']}/tasks",
        json={"title": "no creds", "kind": "command", "command_ref": "vartest-login",
              "command_args": {"target": "h"}, "agent_selector": {"tags": [tag]}},
        headers=auth,
    ).json()
    ws, _agent_conn = _agent(client, tag)
    try:
        job_id = client.post(f"/api/tasks/{task['id']}/run", headers=auth).json()["id"]
        detail = _wait(client, auth, job_id, "failed")
    finally:
        ws.__exit__(None, None, None)
    assert detail["error"] == "variables: no credential set chosen for creds"


PLAYBOOK = """
id: vartest-pb
name: Vartest
inputs:
  - name: creds
    type: varset
    kind: credential
    required: true
steps:
  - id: login
    name: Login
    command_ref: vartest-login
    args: { target: 10.0.0.1 }
    vars: { creds: "{{ inputs.creds }}" }
"""


def test_playbook_varset_inputs(client, auth):
    _ensure_command(client, auth)
    r = client.post("/api/playbooks/validate", json={"yaml": PLAYBOOK}, headers=auth)
    assert r.json()["ok"], r.json()
    wrong_kind = PLAYBOOK.replace("kind: credential", "kind: listener")
    errors = client.post("/api/playbooks/validate", json={"yaml": wrong_kind}, headers=auth).json()["errors"]
    assert errors == ["step login: vars.creds needs a credential set, input creds is listener"]
    unbound = PLAYBOOK.replace('    vars: { creds: "{{ inputs.creds }}" }\n', "")
    errors = client.post("/api/playbooks/validate", json={"yaml": unbound}, headers=auth).json()["errors"]
    assert errors == ["step login: variable slot creds (credential) is not bound"]

    if client.get("/api/playbooks/vartest-pb", headers=auth).status_code == 404:
        assert client.post("/api/playbooks", json={"yaml": PLAYBOOK}, headers=auth).status_code == 201
    project = _project(client, auth)
    creds = _set(client, auth, username="pb", password="pbpass99")
    # By name; stored as the id.
    r = client.post("/api/playbooks/vartest-pb/run", json={"project_id": project["id"], "inputs": {"creds": creds["name"]}}, headers=auth)
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["inputs"]["creds"] == creds["id"]
    assert run["tasks"][0]["var_bindings"] == {"creds": creds["id"]}
    client.post(f"/api/playbook-runs/{run['id']}/cancel", headers=auth)

    listener = _set(client, auth, kind="listener", host="1.2.3.4")
    r = client.post("/api/playbooks/vartest-pb/run", json={"project_id": project["id"], "inputs": {"creds": listener["id"]}}, headers=auth)
    assert r.status_code == 422 and "is a listener set, not credential" in r.json()["detail"]


def _card(client, auth, project, **extra):
    r = client.post(
        f"/api/projects/{project['id']}/tasks",
        json={"title": "manual", "kind": "command", "command_ref": "vartest-login", "command_args": {"target": "h"}, **extra},
        headers=auth,
    )
    return r


def test_one_off_sets_belong_to_the_card_that_uses_them(client, auth):
    _ensure_command(client, auth)
    project = _project(client, auth)
    r = client.post("/api/variables/sets", json={"kind_id": "credential", "ephemeral": True,
                                                  "values": {"username": "temp", "password": "temp-pass"}}, headers=auth)
    assert r.status_code == 201, r.text
    once = r.json()
    assert once["ephemeral"] is True
    # Not in the library.
    assert once["id"] not in [s["id"] for s in client.get("/api/variables/sets", headers=auth).json()]

    card = _card(client, auth, project, var_bindings={"creds": once["id"]})
    assert card.status_code == 201, card.text
    card = card.json()
    assert card["var_bindings"] == {"creds": once["id"]}
    # Another card can't borrow it.
    other = _card(client, auth, project, var_bindings={"creds": once["id"]})
    assert other.status_code == 422 and "belongs to another card" in other.json()["detail"]

    # A one-off set can be saved to the library later; it then stays with the card.
    r = client.patch(f"/api/variables/sets/{once['id']}", json={"ephemeral": False}, headers=auth)
    assert r.status_code == 422
    promoted_name = f"promoted-{uuid.uuid4().hex[:6]}"
    r = client.patch(f"/api/variables/sets/{once['id']}", json={"ephemeral": False, "name": promoted_name}, headers=auth)
    assert r.json()["ephemeral"] is False
    assert promoted_name in [x["name"] for x in client.get("/api/variables/sets", headers=auth).json()]
    once = client.post("/api/variables/sets", json={"kind_id": "credential", "ephemeral": True,
                                                     "values": {"username": "temp", "password": "temp-pass"}}, headers=auth).json()
    client.patch(f"/api/tasks/{card['id']}", json={"var_bindings": {"creds": once["id"]}}, headers=auth)

    # Choosing a library set instead deletes the one-off set.
    saved = _set(client, auth, username="keep", password="keep-pass")
    r = client.patch(f"/api/tasks/{card['id']}", json={"var_bindings": {"creds": saved["id"]}}, headers=auth)
    assert r.status_code == 200, r.text
    assert client.get(f"/api/variables/sets/{once['id']}", headers=auth).status_code == 404

    # Deleting the card deletes its one-off set.
    once2 = client.post("/api/variables/sets", json={"kind_id": "credential", "ephemeral": True,
                                                      "values": {"username": "t2", "password": "t2-pass"}}, headers=auth).json()
    client.patch(f"/api/tasks/{card['id']}", json={"var_bindings": {"creds": once2["id"]}}, headers=auth)
    assert client.delete(f"/api/tasks/{card['id']}", headers=auth).status_code == 204
    assert client.get(f"/api/variables/sets/{once2['id']}", headers=auth).status_code == 404


def test_one_off_set_as_playbook_input_is_shared_by_the_run(client, auth):
    _ensure_command(client, auth)
    if client.get("/api/playbooks/vartest-pb", headers=auth).status_code == 404:
        client.post("/api/playbooks", json={"yaml": PLAYBOOK}, headers=auth)
    project = _project(client, auth)
    once = client.post("/api/variables/sets", json={"kind_id": "credential", "ephemeral": True,
                                                     "values": {"username": "run", "password": "run-pass"}}, headers=auth).json()
    r = client.post("/api/playbooks/vartest-pb/run", json={"project_id": project["id"], "inputs": {"creds": once["id"]}}, headers=auth)
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["tasks"][0]["var_bindings"] == {"creds": once["id"]}
    client.post(f"/api/playbook-runs/{run['id']}/cancel", headers=auth)
    # Owned by that run now: a second run can't use it.
    r = client.post("/api/playbooks/vartest-pb/run", json={"project_id": project["id"], "inputs": {"creds": once["id"]}}, headers=auth)
    assert r.status_code == 422 and "belongs to another card or run" in r.json()["detail"]


def test_display_line_expands_like_the_shell_and_masks_only_secrets():
    from castor_server.variables import Resolved

    cmd = (
        'nxc smb t.txt -u "${CASTOR_VAR_CREDS_USERNAME}" -p "${CASTOR_VAR_CREDS_PASSWORD}" '
        '${CASTOR_VAR_CREDS_DOMAIN:+-d "$CASTOR_VAR_CREDS_DOMAIN"}'
    )
    shown = {"CASTOR_VAR_CREDS_USERNAME": "admin", "CASTOR_VAR_CREDS_PASSWORD": "••••"}
    with_domain = Resolved(display={**shown, "CASTOR_VAR_CREDS_DOMAIN": "acme.example.com"})
    assert with_domain.display_command(cmd, False) == "nxc smb t.txt -u 'admin' -p '••••' -d 'acme.example.com'"
    assert Resolved(display=shown).display_command(cmd, False) == "nxc smb t.txt -u 'admin' -p '••••'"
    quote = Resolved(display={**shown, "CASTOR_VAR_CREDS_USERNAME": "o'brien"})
    assert "-u 'o'\\''brien'" in quote.display_command(cmd, False)

    # Plain values stay readable even when they contain or equal a secret.
    same = Resolved(secrets=["guest"], plain=["guest", "acme.example.com"])
    assert same.redact("acme.example.com\\guest:guest") == "acme.example.com\\guest:guest"
    overlap = Resolved(secrets=["admin"], plain=["admin1"])
    assert overlap.redact("acme\\admin1:admin") == "acme\\admin1:••••"


def test_kind_field_choices(client, auth):
    kind_id = f"k-{uuid.uuid4().hex[:6]}"
    r = client.post("/api/variables/kinds", json={"id": kind_id, "name": "K", "fields": [
        {"name": "proto", "type": "text", "choices": ["smb", "ldap", " smb "]},
        {"name": "protos", "type": "list", "choices": ["smb", "ldap", "rdp"]},
    ]}, headers=auth)
    assert r.status_code == 201, r.text
    assert r.json()["fields"][0]["choices"] == ["smb", "ldap"]
    bad = client.post("/api/variables/sets", json={"kind_id": kind_id, "name": f"x-{uuid.uuid4().hex[:6]}",
                                                    "values": {"proto": "ftp", "protos": "smb, vnc"}}, headers=auth)
    assert bad.status_code == 422
    assert "proto: must be one of: smb, ldap" in bad.json()["detail"]
    assert "protos: vnc not in the choices" in bad.json()["detail"]
    ok = _set(client, auth, kind=kind_id, proto="ldap", protos="smb\nrdp")
    assert ok["values"] == {"proto": "ldap", "protos": ["smb", "rdp"]}
    # Only text and list fields take choices.
    r = client.post("/api/variables/kinds", json={"id": f"k-{uuid.uuid4().hex[:6]}", "name": "K", "fields": [
        {"name": "port", "type": "port", "choices": ["80"]}]}, headers=auth)
    assert r.status_code == 422 and "only text and list fields can have choices" in r.json()["detail"]


def test_seeded_netexec_protocols_feed_the_command_dropdown(client, auth):
    protos = next(s for s in client.get("/api/variables/sets?kind=choice-list", headers=auth).json()
                  if s["name"] == "NetExec protocols")
    assert protos["values"]["options"] == ["ftp", "ldap", "mssql", "nfs", "rdp", "smb", "ssh", "vnc", "winrm", "wmi"]
    cmd = client.get("/api/commands/netexec-auth", headers=auth).json()
    arg = cmd["spec"]["args"][0]
    assert arg["choices"] == protos["values"]["options"]
    assert arg["choices_from"] == {"set": "NetExec protocols", "field": "options"}

    r = client.post("/api/commands/netexec-auth/render", json={"args": {"protocol": "ldap", "targets": "10.0.0.1"}}, headers=auth)
    assert r.status_code == 200 and "nxc ldap targets.txt" in r.json()["command"]
    r = client.post("/api/commands/netexec-auth/render", json={"args": {"protocol": "telnet", "targets": "10.0.0.1"}}, headers=auth)
    assert r.status_code == 422 and "protocol" in r.json()["detail"]["errors"]


def test_choices_follow_the_set_and_are_checked_on_save(client, auth):
    name = f"Protocols {uuid.uuid4().hex[:6]}"
    s = client.post("/api/variables/sets", json={"kind_id": "choice-list", "name": name,
                                                  "values": {"options": "smb\nldap"}}, headers=auth).json()
    cmd_id = f"choice-{uuid.uuid4().hex[:6]}"
    yaml = f"""
id: {cmd_id}
name: Choice test
args:
  - name: protocol
    required: true
    choices_from: {{ set: "{name}", field: options }}
command: echo {{{{ protocol }}}}
"""
    assert client.post("/api/commands", json={"yaml": yaml}, headers=auth).status_code == 201
    assert client.get(f"/api/commands/{cmd_id}", headers=auth).json()["spec"]["args"][0]["choices"] == ["smb", "ldap"]
    # Editing the set changes the command's options without touching the command.
    client.patch(f"/api/variables/sets/{s['id']}", json={"values": {"options": "smb\nldap\nmssql"}}, headers=auth)
    assert client.get(f"/api/commands/{cmd_id}", headers=auth).json()["spec"]["args"][0]["choices"] == ["smb", "ldap", "mssql"]

    missing = yaml.replace(name, "No such set").replace(cmd_id, f"{cmd_id}-x")
    r = client.post("/api/commands", json={"yaml": missing}, headers=auth)
    assert r.status_code == 422 and "no variable set named No such set" in r.json()["detail"]
    both = yaml.replace("    choices_from:", "    choices: [a]\n    choices_from:").replace(cmd_id, f"{cmd_id}-y")
    assert "give choices or choices_from, not both" in client.post("/api/commands", json={"yaml": both}, headers=auth).json()["detail"]


def _target_list(client, auth, entries, name=None):
    r = client.post("/api/variables/sets", json={"kind_id": "target-list", "name": name or f"Lab {uuid.uuid4().hex[:6]}",
                                                  "values": {"entries": entries}}, headers=auth)
    assert r.status_code == 201, r.text
    return r.json()


def test_cards_link_target_lists_and_pick_up_edits(client, auth):
    project = _project(client, auth)
    lab = _target_list(client, auth, "10.0.0.0/24\n10.0.1.5")
    skip = _target_list(client, auth, "10.0.0.1, 10.0.0.254")
    r = client.post(f"/api/projects/{project['id']}/tasks", json={
        "title": "sweep", "kind": "command", "command_ref": "nmap-ping-sweep",
        "command_args": {"targets": {"list": lab["id"]}, "exclude": {"list": skip["name"]}},
    }, headers=auth)
    assert r.status_code == 201, r.text
    card = r.json()
    assert card["command"].endswith("10.0.0.0/24 10.0.1.5 --exclude 10.0.0.1,10.0.0.254")
    # The card keeps the references (what the form shows), not a copy of the entries.
    assert card["command_args"]["targets"] == {"list": lab["id"]}

    # Editing the list changes what the next run uses.
    client.patch(f"/api/variables/sets/{lab['id']}", json={"values": {"entries": "192.168.5.0/24"}}, headers=auth)
    job = client.post(f"/api/tasks/{card['id']}/run", headers=auth).json()
    assert job["command"].endswith("192.168.5.0/24 --exclude 10.0.0.1,10.0.0.254")
    client.post(f"/api/jobs/{job['id']}/cancel", headers=auth)


def test_list_refs_are_checked(client, auth):
    project = _project(client, auth)
    base = {"title": "x", "kind": "command", "command_ref": "nmap-ping-sweep"}
    r = client.post(f"/api/projects/{project['id']}/tasks", json={**base, "command_args": {"targets": {"list": "No such list"}}}, headers=auth)
    assert r.status_code == 422 and r.json()["detail"]["errors"] == {"targets": "no list named or with id No such list"}
    creds = _set(client, auth, username="u")
    r = client.post(f"/api/projects/{project['id']}/tasks", json={**base, "command_args": {"targets": {"list": creds["id"]}}}, headers=auth)
    assert r.status_code == 422 and "has no list field" in r.json()["detail"]["errors"]["targets"]
    # The live preview expands references too.
    lab = _target_list(client, auth, "10.9.0.0/24")
    r = client.post("/api/commands/nmap-ping-sweep/render", json={"args": {"targets": {"list": lab["id"]}}}, headers=auth)
    assert r.json()["command"].endswith("10.9.0.0/24")


def test_playbook_steps_can_name_a_target_list(client, auth):
    lab = _target_list(client, auth, "10.7.0.0/24")
    yaml = f"""
id: list-pb-{uuid.uuid4().hex[:6]}
name: List playbook
steps:
  - id: sweep
    name: Sweep
    command_ref: nmap-ping-sweep
    args:
      targets: {{ list: "{lab['name']}" }}
"""
    assert client.post("/api/playbooks/validate", json={"yaml": yaml}, headers=auth).json()["ok"]
    bad = yaml.replace(lab["name"], "Missing list")
    assert client.post("/api/playbooks/validate", json={"yaml": bad}, headers=auth).json()["errors"] == [
        "step sweep: targets: no list named or with id Missing list"
    ]
