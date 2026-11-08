"""Playbook specification, validation, API, and orchestration tests."""

import uuid
from pathlib import Path

import pytest

from castor_server.playbook_spec import parse_playbook_spec, resolve_output_value, resolve_step_args

PLAYBOOKS_DIR = Path(__file__).resolve().parents[2] / "playbooks"


def test_builtin_playbooks_parse():
    files = sorted(PLAYBOOKS_DIR.glob("*.yaml"))
    assert files, "expected built-in playbooks in playbooks/"
    for path in files:
        spec = parse_playbook_spec(path.read_text())
        assert spec.id == path.stem
        assert len(spec.steps) > 0


def test_playbook_dag_validation_cycles():
    cycle_yaml = """
id: cyclic-playbook
name: Cyclic Playbook
steps:
  - id: step_a
    name: Step A
    command_ref: nmap-port-445
    depends_on: [step_b]
  - id: step_b
    name: Step B
    command_ref: netexec-smb
    depends_on: [step_a]
"""
    with pytest.raises(ValueError, match="cycle or circular dependency"):
        parse_playbook_spec(cycle_yaml)


def test_playbook_dag_validation_unknown_dep():
    bad_dep_yaml = """
id: bad-dep
name: Bad Dep
steps:
  - id: step_a
    name: Step A
    command_ref: nmap-port-445
    depends_on: [non_existent]
"""
    with pytest.raises(ValueError, match="depends on unknown step"):
        parse_playbook_spec(bad_dep_yaml)


def test_playbook_dag_validation_self_dep():
    self_dep_yaml = """
id: self-dep
name: Self Dep
steps:
  - id: step_a
    name: Step A
    command_ref: nmap-port-445
    depends_on: [step_a]
"""
    with pytest.raises(ValueError, match="cannot depend on itself"):
        parse_playbook_spec(self_dep_yaml)


def test_resolve_output_value_extracts_ips():
    output_val = [{"ip": "192.168.1.10"}, {"ip": "192.168.1.20"}]
    resolved = resolve_output_value(output_val)
    assert resolved == "192.168.1.10,192.168.1.20"

    # Single IP dict
    assert resolve_output_value({"ip": "10.0.0.1"}) == "10.0.0.1"
    # Plain strings
    assert resolve_output_value(["host1", "host2"]) == "host1,host2"
    # Scalar string
    assert resolve_output_value("test") == "test"


def test_resolve_step_args():
    step_args = {
        "targets": "{{ steps.scan.outputs.hosts }}",
        "custom": "{{ inputs.domain }}",
        "static": "445",
    }
    inputs = {"domain": "corp.local"}
    step_outputs = {
        "scan": {
            "hosts": [{"ip": "192.168.1.5"}, {"ip": "192.168.1.6"}],
        }
    }
    resolved = resolve_step_args(step_args, inputs, step_outputs)
    assert resolved["targets"] == "192.168.1.5,192.168.1.6"
    assert resolved["custom"] == "corp.local"
    assert resolved["static"] == "445"


def test_playbooks_api_crud(client, auth):
    # 1. List playbooks (builtin should be present)
    r = client.get("/api/playbooks", headers=auth)
    assert r.status_code == 200
    playbooks = r.json()
    ids = {p["id"] for p in playbooks}
    assert "smb-signing-discovery" in ids

    # 2. Get specific playbook
    r = client.get("/api/playbooks/smb-signing-discovery", headers=auth)
    assert r.status_code == 200
    pb = r.json()
    assert pb["name"] == "SMB Signing Status Discovery"
    assert len(pb["spec"]["steps"]) == 2

    # 3. Cannot modify builtin playbook
    r = client.put(
        "/api/playbooks/smb-signing-discovery",
        json={"yaml": "id: smb-signing-discovery\nname: Modified\nsteps: []"},
        headers=auth,
    )
    assert r.status_code == 409

    # 4. Cannot delete builtin playbook
    r = client.delete("/api/playbooks/smb-signing-discovery", headers=auth)
    assert r.status_code == 409

    # 5. Create user playbook
    custom_yaml = """
id: custom-chain
name: Custom Chain
steps:
  - id: step_one
    name: First Step
    command_ref: nmap-port-445
    args: { targets: 10.0.0.1 }
"""
    r = client.post("/api/playbooks", json={"yaml": custom_yaml}, headers=auth)
    assert r.status_code == 201
    custom_pb = r.json()
    assert custom_pb["id"] == "custom-chain"
    assert custom_pb["origin"] == "user"

    # 6. Update user playbook
    updated_yaml = """
id: custom-chain
name: Custom Chain Updated
steps:
  - id: step_one
    name: First Step
    command_ref: nmap-port-445
    args: { targets: 10.0.0.1 }
"""
    r = client.put("/api/playbooks/custom-chain", json={"yaml": updated_yaml}, headers=auth)
    assert r.status_code == 200
    assert r.json()["name"] == "Custom Chain Updated"

    # 7. Delete user playbook
    r = client.delete("/api/playbooks/custom-chain", headers=auth)
    assert r.status_code == 204
    r = client.get("/api/playbooks/custom-chain", headers=auth)
    assert r.status_code == 404


def test_playbook_run_lifecycle(client, auth):
    # Create project
    proj_code = f"PB-{uuid.uuid4().hex[:6]}"
    r = client.post(
        "/api/projects",
        json={"codename": proj_code, "name": "Playbook Test Project", "kind": "internal"},
        headers=auth,
    )
    assert r.status_code == 201
    project = r.json()
    proj_id = project["id"]

    # Start smb-signing-discovery playbook
    r = client.post(
        "/api/playbooks/smb-signing-discovery/run",
        json={"project_id": proj_id, "inputs": {"targets": "192.168.1.0/24"}},
        headers=auth,
    )
    assert r.status_code == 201, r.text
    run = r.json()
    run_id = run["id"]
    assert run["status"] == "running"
    assert len(run["tasks"]) == 2

    # Check tasks
    tasks = {t["playbook_step_id"]: t for t in run["tasks"]}
    scan_task = tasks["nmap_scan"]
    smb_task = tasks["netexec_smb"]

    assert scan_task["latest_job"] is not None  # initial step is queued
    assert smb_task["latest_job"] is None  # depends on scan, not yet queued

    # List runs endpoint
    r = client.get(f"/api/playbook-runs?project_id={proj_id}", headers=auth)
    assert r.status_code == 200
    runs = r.json()
    assert len(runs) >= 1
    assert runs[0]["id"] == run_id

    # Get specific run
    r = client.get(f"/api/playbook-runs/{run_id}", headers=auth)
    assert r.status_code == 200
    assert r.json()["id"] == run_id

    # Cancel the run
    r = client.post(f"/api/playbook-runs/{run_id}/cancel", headers=auth)
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_playbook_chaining_and_output_binding(client, auth):
    from castor_server import models as m
    from castor_server.db import SessionLocal
    from castor_server.services import advance_playbook_run

    proj_code = f"PB-{uuid.uuid4().hex[:6]}"
    r = client.post(
        "/api/projects",
        json={"codename": proj_code, "name": "Playbook Advance Project", "kind": "internal"},
        headers=auth,
    )
    assert r.status_code == 201
    proj_id = r.json()["id"]

    r = client.post(
        "/api/playbooks/smb-signing-discovery/run",
        json={"project_id": proj_id, "inputs": {"targets": "10.0.0.0/24"}},
        headers=auth,
    )
    assert r.status_code == 201
    run_id = uuid.UUID(r.json()["id"])
    tasks = {t["playbook_step_id"]: t for t in r.json()["tasks"]}
    scan_job_id = uuid.UUID(tasks["nmap_scan"]["latest_job"]["id"])

    # Simulate scan job succeeding and outputting parsed hosts
    async with SessionLocal() as db:
        scan_job = await db.get(m.Job, scan_job_id)
        assert scan_job is not None
        scan_job.status = m.JobStatus.succeeded
        db.add(
            m.JobOutput(
                job_id=scan_job.id,
                name="hosts",
                type="list<host>",
                parser="nmap.hosts",
                value=[{"ip": "10.0.0.50"}, {"ip": "10.0.0.60"}],
            )
        )
        await db.commit()

        # Advance playbook run
        run, enqueued = await advance_playbook_run(db, run_id)
        assert len(enqueued) == 1
        new_job = enqueued[0]

        # Check downstream task has received the bound targets
        smb_task_id = uuid.UUID(run.step_task_ids["netexec_smb"])
        smb_task = await db.get(m.Task, smb_task_id)
        assert smb_task is not None
        assert smb_task.command_args.get("targets") == "10.0.0.50,10.0.0.60"
        assert "10.0.0.50,10.0.0.60" in smb_task.command

        # Now simulate smb job succeeding
        new_job.status = m.JobStatus.succeeded
        await db.commit()

        # Advance again
        run, enqueued = await advance_playbook_run(db, run_id)
        assert len(enqueued) == 0
        assert run.status == m.PlaybookRunStatus.succeeded



def test_builtin_playbooks_check_against_the_library(client, auth):
    """Every shipped playbook names real commands, arguments and outputs (what saving checks)."""
    for path in sorted(PLAYBOOKS_DIR.glob("*.yaml")):
        r = client.post("/api/playbooks/validate", json={"yaml": path.read_text()}, headers=auth)
        assert r.status_code == 200, (path.name, r.text)
        assert r.json().get("errors") in (None, []), (path.name, r.json())
