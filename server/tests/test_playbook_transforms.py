"""Transform steps: the op pipeline, spec validation, and the engine running the
pipeline-demo playbook end to end (each queued command really runs under /bin/sh)."""

import subprocess
import tempfile
import uuid
from pathlib import Path

import pytest

from castor_server.playbook_spec import parse_playbook_spec, step_statuses
from castor_server.playbook_transforms import TransformSpec, parse_op, run_transform

SERVICES = [
    {"host": "10.0.0.1", "port": 22, "name": "ssh"},
    {"host": "10.0.0.1", "port": 80, "name": "http"},
    {"host": "10.0.0.2", "port": 8080, "name": "HTTP-Proxy"},
    {"host": "10.0.0.1", "port": 80, "name": "http"},
]


def transform(ops, *values, **output):
    return run_transform(TransformSpec(input="{{ inputs.x }}", ops=ops, output=output), list(values))


def test_filter_format_unique():
    urls = transform(
        [
            {"filter": {"field": "name", "in": ["http", "http-proxy"], "ignore_case": True}},
            {"format": "http://{{ host }}:{{ port }}/"},
            "unique",
        ],
        SERVICES,
    )
    assert urls == ["http://10.0.0.1:80/", "http://10.0.0.2:8080/"]


def test_filter_tests():
    assert transform([{"filter": {"field": "port", "equals": 80}}], SERVICES) == [SERVICES[1], SERVICES[3]]
    assert len(transform([{"filter": {"field": "port", "not_in": [22]}}], SERVICES)) == 3
    assert transform([{"filter": {"field": "name", "matches": "^ss"}}, {"pluck": "port"}], SERVICES) == [22]
    assert transform([{"filter": {"field": "name", "contains": "proxy", "ignore_case": True}}], SERVICES) == [SERVICES[2]]
    assert transform([{"filter": {"field": "nope", "exists": False}}], SERVICES) == SERVICES
    # Plain values filter on themselves.
    assert transform([{"filter": {"matches": r"\.lab$"}}], ["a.lab", "b.corp"]) == ["a.lab"]


def test_sort_limit_and_concatenated_inputs():
    assert transform([{"sort": "-port"}, {"limit": 2}, {"pluck": "port"}], SERVICES) == [8080, 80]
    assert transform([{"unique": "host"}, {"pluck": "host"}], SERVICES) == ["10.0.0.1", "10.0.0.2"]
    # Several inputs are concatenated; comma text and None are accepted.
    assert transform(["unique", "sort"], ["b", "a"], "c,a", None) == ["a", "b", "c"]


def test_result_type():
    spec = TransformSpec(input="{{ inputs.x }}", ops=[{"filter": {"field": "port", "equals": 80}}])
    assert spec.result_type("list<service>") == "list<service>"
    assert TransformSpec(input="{{ inputs.x }}", ops=[{"pluck": "host"}]).result_type("list<service>") == "list<string>"
    declared = TransformSpec(input="{{ inputs.x }}", output={"name": "o", "type": "list<host>"})
    assert declared.result_type("list<string>") == "list<host>"


@pytest.mark.parametrize(
    ("op", "message"),
    [
        ({"filter": {"field": "port"}}, "exactly one test"),
        ({"filter": {"field": "port", "equals": 1, "in": [1]}}, "exactly one test"),
        ({"filter": {"field": "x", "matches": "("}}, "not a valid regular expression"),
        ({"filter": {"field": "bad field", "equals": 1}}, "not a field name"),
        ({"format": "no placeholders"}, "at least one"),
        ({"explode": True}, "unknown op"),
        ({"pluck": "a", "sort": "b"}, "one key"),
        ({"limit": -1}, "greater than or equal"),
        ({"filter": "port"}, "takes a mapping"),
    ],
)
def test_bad_ops(op, message):
    with pytest.raises(ValueError, match=message):
        parse_op(op)


PLAYBOOK = """
id: t
name: T
inputs:
  - name: targets
steps:
  - id: scan
    name: Scan
    command_ref: nmap-service-scan
    args: { targets: "{{ inputs.targets }}" }
  - id: web
    name: Web
    transform:
      input: "{{ steps.scan.outputs.services }}"
      ops: [{ pluck: host }]
      output: { name: hosts }
  - id: next
    name: Next
    command_ref: nmap-service-scan
    args: { targets: "{{ steps.web.outputs.hosts }}" }
"""


def test_spec_transform_steps_and_implicit_dependencies():
    spec = parse_playbook_spec(PLAYBOOK)
    kinds = {s.id: (s.kind, s.depends_on) for s in spec.steps}
    assert kinds == {
        "scan": ("command", []),
        "web": ("transform", ["scan"]),
        "next": ("command", ["web"]),
    }


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (("{{ steps.web.outputs.hosts }}", "{{ steps.web.outputs.urls }}"), "output is named hosts"),
        (("{{ inputs.targets }}", "{{ inputs.nope }}"), "undeclared input nope"),
        (("{{ steps.scan.outputs.services }}", "{{ steps.ghost.outputs.x }}"), "unknown step ghost"),
        (("    command_ref: nmap-service-scan\n    args: { targets: \"{{ inputs.targets }}\" }\n  - id: web",
          "    command_ref: nmap-service-scan\n    transform: { input: x }\n  - id: web"), "either command_ref or transform"),
        (("      output: { name: hosts }", "      output: { name: hosts }\n    args: { a: 1 }"), "take no args"),
    ],
)
def test_spec_rejects_bad_references(change, message):
    old, new = change
    assert old in PLAYBOOK
    with pytest.raises(ValueError, match=message):
        parse_playbook_spec(PLAYBOOK.replace(old, new, 1))


def test_step_statuses_block_downstream_of_failures():
    spec = parse_playbook_spec(PLAYBOOK)
    assert step_statuses(spec, {"scan": "failed"}, {}) == {
        "scan": "failed",
        "web": "blocked",
        "next": "blocked",
    }
    assert step_statuses(spec, {"scan": "succeeded"}, {"web": {"status": "skipped"}}) == {
        "scan": "succeeded",
        "web": "skipped",
        "next": "pending",
    }
    assert step_statuses(spec, {"scan": "running"}, {}, "cancelled")["next"] == "cancelled"
    # A re-run job wins over a stored failure.
    assert step_statuses(spec, {"next": "queued"}, {"next": {"status": "failed"}})["next"] == "queued"


# ---------- the demo playbook, end to end ----------


def _execute(command: str, output_specs: list[dict]) -> tuple[int, dict]:
    """What an agent plus the output parser would do for a host-shell job."""
    from castor_server import parsers
    from castor_server.outputs import _matches

    with tempfile.TemporaryDirectory() as workdir:
        proc = subprocess.run(
            ["/bin/sh", "-c", command], check=False, cwd=workdir, capture_output=True, timeout=30
        )
        outputs = {}
        if proc.returncode == 0:
            for spec in output_specs:
                parser = parsers.get(spec["parser"])
                if spec.get("source"):
                    files = [
                        p for p in sorted(Path(workdir).rglob("*"))
                        if p.is_file() and _matches(spec["source"], str(p.relative_to(workdir)))
                    ]
                    data = files[0].read_bytes()
                else:
                    data = proc.stdout
                outputs[spec["name"]] = (parser.type, parser.run(data, parser.configure(spec.get("options"))))
        return proc.returncode, outputs


async def _drive(run_id: uuid.UUID) -> None:
    """Run every queued job of the run until nothing is left, like the agent would."""
    from sqlalchemy import select

    from castor_server import models as m
    from castor_server.command_library import load_spec
    from castor_server.db import SessionLocal
    from castor_server.services import advance_playbook_run

    for _ in range(20):
        async with SessionLocal() as db:
            run = await db.get(m.PlaybookRun, run_id)
            task_ids = [uuid.UUID(t) for t in run.step_task_ids.values()]
            queued = (
                await db.execute(
                    select(m.Job).where(m.Job.task_id.in_(task_ids), m.Job.status == m.JobStatus.queued)
                )
            ).scalars().all()
            if not queued:
                return
            for job in queued:
                task = await db.get(m.Task, job.task_id)
                spec = await load_spec(db, task.command_ref)
                code, outputs = _execute(job.command, [o.model_dump() for o in spec.outputs])
                job.exit_code = code
                job.status = m.JobStatus.succeeded if code == 0 else m.JobStatus.failed
                for name, (type_, value) in outputs.items():
                    db.add(m.JobOutput(job_id=job.id, name=name, type=type_, parser="", value=value))
            await db.commit()
            await advance_playbook_run(db, run_id)
    raise AssertionError("demo playbook did not settle")


def _start(client, auth, **inputs) -> tuple[uuid.UUID, str]:
    r = client.post(
        "/api/projects",
        json={"codename": f"DEMO-{uuid.uuid4().hex[:6]}", "name": "Demo", "kind": "internal"},
        headers=auth,
    )
    project_id = r.json()["id"]
    r = client.post(
        "/api/playbooks/pipeline-demo/run",
        json={"project_id": project_id, "inputs": {"speed": 0, **inputs}},
        headers=auth,
    )
    assert r.status_code == 201, r.text
    run = r.json()
    # Only command steps get cards; only the root is queued straight away.
    assert len(run["tasks"]) == 7
    assert run["step_status"]["seed_hosts"] == "queued"
    assert run["step_status"]["web_targets"] == "pending"
    return uuid.UUID(run["id"]), project_id


async def test_demo_playbook_runs_to_completion(client, auth):
    run_id, _ = _start(client, auth)
    await _drive(run_id)
    run = client.get(f"/api/playbook-runs/{run_id}", headers=auth).json()

    assert run["status"] == "succeeded"
    assert set(run["step_status"].values()) == {"succeeded"}
    results = run["step_results"]
    assert results["web_targets"]["outputs"]["urls"] == [
        "http://10.20.0.10:80/",
        "http://10.20.0.12:80/",
        "http://10.20.0.13:8080/",
        "http://10.20.0.14:80/",
    ]
    assert results["web_targets"]["input_count"] == 12
    assert results["web_targets"]["type"] == "list<string>"
    assert results["unsigned"]["outputs"]["hosts"] == ["10.20.0.11", "10.20.0.13"]
    assert results["interesting"]["count"] == 6

    report = next(t for t in run["tasks"] if t["playbook_step_id"] == "report")
    assert report["command_args"]["unsigned"] == "10.20.0.11,10.20.0.13"
    assert "http://10.20.0.13:8080/.git/HEAD" in report["command_args"]["findings"]


async def test_demo_playbook_failure_blocks_only_its_branch(client, auth):
    run_id, _ = _start(client, auth, fail_at="smb_audit")
    await _drive(run_id)
    run = client.get(f"/api/playbook-runs/{run_id}", headers=auth).json()

    status = run["step_status"]
    assert run["status"] == "failed"
    assert status["smb_audit"] == "failed"
    assert status["unsigned"] == "blocked"
    assert status["report"] == "blocked"
    # The other branches still ran to the end.
    assert status["interesting"] == "succeeded"
    assert status["dns_names"] == "succeeded"


async def test_demo_playbook_empty_results_skip_downstream(client, auth):
    run_id, _ = _start(client, auth, empty_at="seed_hosts")
    await _drive(run_id)
    run = client.get(f"/api/playbook-runs/{run_id}", headers=auth).json()

    status = run["step_status"]
    assert run["status"] == "succeeded"
    for step in ("svc_scan", "smb_scan", "dns_names", "smb_audit", "dir_brute"):
        assert status[step] == "skipped", step
    # Transforms of nothing are empty, and the fan-in report still runs.
    assert run["step_results"]["web_targets"]["count"] == 0
    assert status["report"] == "succeeded"
    skipped = next(t for t in run["tasks"] if t["playbook_step_id"] == "svc_scan")
    assert skipped["latest_job"] is None


# ---------- save-time checks ----------


def test_unknown_keys_are_errors():
    with pytest.raises(ValueError, match=r"steps\.0\.depend_on: unknown key"):
        parse_playbook_spec(PLAYBOOK.replace("    command_ref: nmap-service-scan\n", "    command_ref: nmap-service-scan\n    depend_on: []\n", 1))
    # x- keys are free (YAML anchors, notes).
    assert parse_playbook_spec(PLAYBOOK + "x-notes: anything\n").id == "t"


def test_validate_endpoint_checks_against_the_library(client, auth):
    r = client.post("/api/playbooks/validate", json={"yaml": PLAYBOOK}, headers=auth)
    assert r.json() == {"ok": True, "errors": [], "spec": r.json()["spec"]}
    assert r.json()["spec"]["steps"][1]["depends_on"] == ["scan"]

    bad = (
        PLAYBOOK.replace("outputs.services", "outputs.servicez")
        .replace("args: { targets: \"{{ steps.web.outputs.hosts }}\" }", "args: { target: x, ports: [1] }")
        .replace("command_ref: nmap-service-scan\n    args: { targets: \"{{ inputs.targets }}\" }", "command_ref: no-such-command")
    )
    errors = client.post("/api/playbooks/validate", json={"yaml": bad}, headers=auth).json()["errors"]
    assert "step scan: unknown command no-such-command" in errors
    assert any("has no argument target" in e for e in errors)
    assert any("required argument targets is not bound" in e for e in errors)

    bad_output = PLAYBOOK.replace("outputs.services", "outputs.servicez")
    errors = client.post("/api/playbooks/validate", json={"yaml": bad_output}, headers=auth).json()["errors"]
    assert errors == ["step web: scan (nmap-service-scan) has no parsed output servicez (outputs: services)"]

    syntax = client.post("/api/playbooks/validate", json={"yaml": "id: Bad"}, headers=auth).json()
    assert syntax["ok"] is False and syntax["spec"] is None
    # Saving runs the same checks.
    assert client.post("/api/playbooks", json={"yaml": bad_output}, headers=auth).status_code == 422
