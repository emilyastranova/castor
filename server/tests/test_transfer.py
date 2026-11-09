"""Import and export (3i): archives round-trip a project and library items with new ids,
secrets travel only under a passphrase, conflicts resolve as chosen, and bad files are
refused clearly."""

import io
import json
import uuid
import zipfile

COMMAND = """\
id: {id}
name: Transfer test
description: Echo a target
args:
  - name: target
    required: true
command: echo {{{{ target }}}}
"""


def _export(client, auth, **body):
    r = client.post("/api/export", json=body, headers=auth)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    return r.content


def _preview(client, auth, data, name="x.castor.zip", passphrase=None):
    form = {"passphrase": passphrase} if passphrase else {}
    r = client.post("/api/import/preview", files={"file": (name, data, "application/zip")}, data=form, headers=auth)
    return r


def _import(client, auth, preview, **body):
    r = client.post("/api/import", json={"token": preview["token"], "filename": preview["filename"], **body}, headers=auth)
    assert r.status_code == 200, r.text
    return r.json()


def _setup(client, auth):
    code = f"TR-{uuid.uuid4().hex[:6]}"
    p = client.post("/api/projects", json={"codename": code, "name": "Transfer", "kind": "internal"}, headers=auth).json()
    cmd_id = f"tr-{uuid.uuid4().hex[:6]}"
    assert client.post("/api/commands", json={"yaml": COMMAND.format(id=cmd_id)}, headers=auth).status_code == 201
    task = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Recon", "assignees": ["admin"]}, headers=auth).json()
    att = client.post(f"/api/tasks/{task['id']}/attachments", files={"file": ("shot.png", b"\x89PNG fake", "image/png")}, headers=auth).json()
    link = f"/api/attachments/{att['id']}/download?inline=true"
    client.patch(f"/api/tasks/{task['id']}", json={"description": f"See ![shot]({link})"}, headers=auth)
    client.post(f"/api/tasks/{task['id']}/comments", json={"body": "Looks good @admin"}, headers=auth)
    client.post(f"/api/projects/{p['id']}/notes", json={"title": "Status", "body": "External done"}, headers=auth)
    client.post(f"/api/projects/{p['id']}/assets", json={"type": "credential", "fields": {"username": "svc", "domain": "CORP"}, "secret": "Winter2026!"}, headers=auth)
    vs = client.post("/api/variables/sets", json={"kind_id": "credential", "name": f"tr-{uuid.uuid4().hex[:6]}",
                                                  "values": {"username": "alice", "password": "Sup3rS3cret!"}}, headers=auth).json()
    return p, cmd_id, task, att, vs


def test_project_round_trip_with_new_ids_and_secrets(client, auth):
    p, cmd_id, task, att, vs = _setup(client, auth)
    data = _export(client, auth, projects=[p["id"]], commands=[cmd_id], variable_sets=[vs["id"]], passphrase="correct horse")
    zf = zipfile.ZipFile(io.BytesIO(data))
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["format"] == 1 and manifest["contents"] == {"commands": 1, "variable_sets": 1, "projects": 1}
    assert f"files/{att['id']}" in zf.namelist() and f"library/commands/{cmd_id}.yaml" in zf.namelist()
    everything = b"".join(zf.read(n) for n in zf.namelist())
    assert b"Winter2026!" not in everything and b"Sup3rS3cret!" not in everything
    assert b"password_hash" not in everything

    # Without the passphrase the preview says secrets would be left behind; a wrong one is refused.
    preview = _preview(client, auth, data).json()
    assert preview["secrets"] == {"mode": "passphrase", "unlocked": False}
    states = {i["kind"]: i for i in preview["items"]}
    assert states["project"]["state"] == "exists" and states["project"]["default"] == "copy"
    assert states["command"]["state"] == "same" and states["variable_set"]["state"] == "exists"
    assert "admin" in preview["people"]["matched"]
    bad = client.post("/api/import/preview", data={"token": preview["token"], "passphrase": "wrong"}, headers=auth)
    assert bad.status_code == 422 and "passphrase" in bad.json()["detail"]
    preview = client.post("/api/import/preview", data={"token": preview["token"], "passphrase": "correct horse"}, headers=auth).json()
    assert preview["secrets"]["unlocked"]

    set_key = f"variable_set:{vs['id']}"
    result = _import(client, auth, preview, passphrase="correct horse", choices={set_key: "copy"})
    assert result["imported"]["projects"] == [f"{p['codename']}-copy"] and len(result["projects"]) == 1
    new_id = result["projects"][0]
    assert new_id != p["id"]

    board = client.get(f"/api/projects/{new_id}/board", headers=auth).json()
    card = next(t for t in board["tasks"] if t["title"] == "Recon")
    assert card["id"] != task["id"] and card["assignees"] == [task["assignees"][0]]
    files = client.get(f"/api/tasks/{card['id']}/attachments", headers=auth).json()
    assert len(files) == 1 and files[0]["id"] != att["id"]
    assert client.get(f"/api/attachments/{files[0]['id']}/download", headers=auth).content == b"\x89PNG fake"
    detail = client.get(f"/api/tasks/{card['id']}", headers=auth).json()
    assert files[0]["id"] in detail["description"] and att["id"] not in detail["description"]  # image link follows
    comments = client.get(f"/api/tasks/{card['id']}/comments", headers=auth).json()
    assert comments[0]["body"] == "Looks good @admin" and comments[0]["mentions"]
    assert [n["title"] for n in client.get(f"/api/projects/{new_id}/notes", headers=auth).json()] == ["Status"]

    cred = client.get(f"/api/projects/{new_id}/assets", headers=auth).json()["items"][0]
    assert client.post(f"/api/assets/{cred['id']}/reveal", headers=auth).json()["secret"] == "Winter2026!"
    copy = next(s for s in client.get("/api/variables/sets", headers=auth).json() if s["name"] == f"{vs['name']} 2")
    assert client.post(f"/api/variables/sets/{copy['id']}/reveal/password", headers=auth).json()["value"] == "Sup3rS3cret!"
    feed = client.get(f"/api/dashboard/feed?project_id={new_id}", headers=auth).json()["items"]
    assert any(i["type"] == "project.imported" for i in feed)

    # The same file again makes another copy; skipping everything changes nothing.
    again = _import(client, auth, _preview(client, auth, data).json())
    assert again["imported"]["projects"] == [f"{p['codename']}-copy-2"]
    skip = _preview(client, auth, data).json()
    result = _import(client, auth, skip, choices={i["key"]: "skip" for i in skip["items"]})
    assert result["imported"] == {}
    history = client.get("/api/transfer/history", headers=auth).json()
    assert {"transfer.export", "transfer.import"} <= {h["type"] for h in history}


def test_secrets_stay_behind_without_a_passphrase(client, auth):
    p, _, _, _, vs = _setup(client, auth)
    data = _export(client, auth, projects=[p["id"]], variable_sets=[vs["id"]])
    assert json.loads(zipfile.ZipFile(io.BytesIO(data)).read("manifest.json"))["secrets"] == {"mode": "none"}
    preview = _preview(client, auth, data).json()
    item = next(i for i in preview["items"] if i["kind"] == "variable_set")
    assert any("password" in n for n in item["notes"])
    result = _import(client, auth, preview, choices={item["key"]: "copy"})
    cred = client.get(f"/api/projects/{result['projects'][0]}/assets", headers=auth).json()["items"][0]
    assert not cred["has_secret"]
    copy = next(s for s in client.get("/api/variables/sets", headers=auth).json() if s["name"] == f"{vs['name']} 2")
    assert copy["secrets"]["password"] is False and copy["values"]["username"] == "alice"


def test_library_conflicts_and_single_yaml(client, auth):
    cmd_id = f"tr-{uuid.uuid4().hex[:6]}"
    text = COMMAND.format(id=cmd_id)
    preview = _preview(client, auth, text.encode(), name=f"{cmd_id}.yaml").json()
    assert preview["items"][0]["state"] == "new"
    _import(client, auth, preview)
    assert client.get(f"/api/commands/{cmd_id}", headers=auth).json()["name"] == "Transfer test"

    changed = text.replace("Transfer test", "Transfer test v2")
    preview = _preview(client, auth, changed.encode(), name=f"{cmd_id}.yaml").json()
    item = preview["items"][0]
    assert item["state"] == "exists" and item["choices"] == ["skip", "replace", "copy"] and item["new_name"] == f"{cmd_id}-2"
    _import(client, auth, preview, choices={item["key"]: "copy"})
    assert client.get(f"/api/commands/{cmd_id}-2", headers=auth).json()["name"] == "Transfer test v2"
    preview = _preview(client, auth, changed.encode(), name=f"{cmd_id}.yaml").json()
    _import(client, auth, preview, choices={preview["items"][0]["key"]: "replace"})
    assert client.get(f"/api/commands/{cmd_id}", headers=auth).json()["name"] == "Transfer test v2"

    # Built-ins are never replaced.
    builtin = client.get("/api/commands/nmap-ping-sweep", headers=auth).json()
    data = builtin["source_yaml"].replace(builtin["name"], "Hacked").encode()
    preview = _preview(client, auth, data, name="nmap.yaml").json()
    assert preview["items"][0]["state"] == "builtin" and "replace" not in preview["items"][0]["choices"]
    r = client.post("/api/import", json={"token": preview["token"], "choices": {preview["items"][0]["key"]: "replace"}}, headers=auth)
    assert r.status_code == 422


def test_bad_files_and_permissions(client, auth):
    assert _preview(client, auth, b"not a zip").status_code == 422
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format": 99, "castor_version": "9.0"}))
    r = _preview(client, auth, buf.getvalue())
    assert r.status_code == 422 and "newer" in r.json()["detail"]
    assert client.post("/api/import", json={"token": "x" * 32}, headers=auth).status_code == 422
    assert client.post("/api/export", json={}, headers=auth).status_code == 422  # nothing asked for

    name = f"tr{uuid.uuid4().hex[:6]}"
    client.post("/api/users", json={"username": name, "role": "operator", "password": "a password"}, headers=auth)
    token = client.post("/api/auth/login", json={"username": name, "password": "a password"}).json()["token"]
    client.cookies.clear()
    other = {"Authorization": f"Bearer {token}"}
    assert client.post("/api/export", json={"everything": True}, headers=other).status_code == 403
    hidden = client.post("/api/projects", json={"codename": f"TH-{uuid.uuid4().hex[:6]}", "name": "Hidden", "kind": "internal"}, headers=auth).json()
    assert client.post("/api/export", json={"projects": [hidden["id"]]}, headers=other).status_code == 404
    assert client.get("/api/transfer/history", headers=other).status_code == 403


def test_everything_creates_missing_people(client, auth):
    data = _export(client, auth, everything=True, label="all")
    zf = zipfile.ZipFile(io.BytesIO(data))
    users = json.loads(zf.read("users.json"))
    assert any(u["username"] == "admin" and u["role"] == "admin" for u in users)
    assert all("password_hash" not in u for u in users)
    # Pretend the file came from a host with someone this one doesn't have.
    stranger = f"st{uuid.uuid4().hex[:6]}"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        for n in zf.namelist():
            if n == "users.json":
                out.writestr(n, json.dumps([*users, {"id": str(uuid.uuid4()), "username": stranger, "display_name": "Stranger", "role": "viewer"}]))
            elif not n.startswith(("projects/", "files/")):  # keep the test quick
                out.writestr(n, zf.read(n))
    preview = _preview(client, auth, buf.getvalue()).json()
    assert {"username": stranger, "display_name": "Stranger"} in preview["people"]["missing"]
    result = _import(client, auth, preview, create_users=True, choices={i["key"]: "skip" for i in preview["items"]})
    assert result["created_users"] == [stranger]
    assert any(u["username"] == stranger for u in client.get("/api/users", headers=auth).json())


def test_commands_travel_with_their_library_parsers(client, auth):
    """A command that names a library parser imports with it, even where that parser doesn't
    exist yet: the archive's parsers are known while its commands are checked."""
    pid = f"tr-ports-{uuid.uuid4().hex[:6]}"
    cid = f"tr-cmd-{uuid.uuid4().hex[:6]}"
    parser_yaml = f"id: {pid}\nname: Ports\ntype: list<service>\nformat: regex\npattern: '^(?P<host>\\S+):(?P<port>\\d+)$'\n"
    assert client.post("/api/parsers", json={"yaml": parser_yaml}, headers=auth).status_code == 201
    cmd_yaml = f"id: {cid}\nname: Ports\ncommand: echo 10.0.0.1:22\noutputs:\n  - name: services\n    parser: {pid}\n"
    assert client.post("/api/commands", json={"yaml": cmd_yaml}, headers=auth).status_code == 201
    data = _export(client, auth, commands=[cid], parsers=[pid])
    client.delete(f"/api/commands/{cid}", headers=auth)
    client.delete(f"/api/parsers/{pid}", headers=auth)
    assert client.get(f"/api/parsers/{pid}", headers=auth).status_code == 404

    preview = _preview(client, auth, data).json()
    kinds = {i["kind"]: i for i in preview["items"]}
    assert kinds["parser"]["state"] == "new" and kinds["command"]["state"] == "new" and not preview["warnings"]
    _import(client, auth, preview)
    assert client.get(f"/api/parsers/{pid}", headers=auth).json()["used_by"] == [cid]
    tried = client.post(f"/api/parsers/{pid}/test", json={"input": "10.0.0.9:443\n"}, headers=auth).json()
    assert tried["value"][0]["port"] == 443  # registered as soon as the import committed
    client.delete(f"/api/commands/{cid}", headers=auth)
    client.delete(f"/api/parsers/{pid}", headers=auth)


def test_playbook_import_notes_missing_commands(client, auth):
    pid = f"tr-pb-{uuid.uuid4().hex[:6]}"
    text = f"""id: {pid}
name: Transfer playbook
steps:
  - id: sweep
    name: Sweep
    command_ref: nmap-ping-sweep
  - id: gone
    name: Gone
    command_ref: no-such-command-{pid}
"""
    preview = _preview(client, auth, text.encode(), name=f"{pid}.yaml").json()
    notes = preview["items"][0]["notes"]
    assert notes == [f"step gone runs no-such-command-{pid}, which isn't here"]
