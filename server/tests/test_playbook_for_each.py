"""for_each: one card and job per item, concurrency, merged outputs, failures and retries."""

import uuid

import pytest
from sqlalchemy import select

from castor_server.playbook_spec import parse_playbook_spec, resolve_step_args

PLAYBOOK = """
id: fan-{suffix}
name: Fan {suffix}
inputs:
  - {{ name: hosts, type: string, default: "10.0.0.1,10.0.0.2,10.0.0.3" }}
steps:
  - id: scan
    name: Scan
    command_ref: demo-smb-scan
    on_failure: {on_failure}
    for_each:
      items: "{{{{ inputs.hosts }}}}"
      concurrency: 2
      max_items: {max_items}
    args:
      targets: "{{{{ item }}}}"
  - id: report
    name: Report
    command_ref: demo-report
    args:
      unsigned: "{{{{ steps.scan.outputs.hosts }}}}"
"""


def test_item_placeholders():
    item = {"host": "10.0.0.5", "port": 445}
    args = resolve_step_args({"t": "{{ item }}", "u": "smb://{{ item.host }}:{{ item.port }}/", "n": 3}, {}, {}, item)
    assert args == {"t": "10.0.0.5", "u": "smb://10.0.0.5:445/", "n": 3}
    assert resolve_step_args({"t": "{{ item }}"}, {}, {}, "a.example")["t"] == "a.example"


@pytest.mark.parametrize(
    ("step", "message"),
    [
        ("command_ref: x\n    args: { t: '{{ item }}' }", "only be used in a step with for_each"),
        ("command_ref: x\n    for_each: 'hosts'", "items must be one"),
        ("command_ref: x\n    for_each: { items: '{{ inputs.h }}', max_items: 5000 }", "less than or equal to 1000"),
        ("transform: { input: '{{ inputs.h }}' }\n    for_each: '{{ inputs.h }}'", "for command steps"),
    ],
)
def test_bad_for_each(step, message):
    with pytest.raises(ValueError, match=message):
        parse_playbook_spec(f"id: t\nname: T\ninputs: [{{name: h}}]\nsteps:\n  - id: a\n    name: A\n    {step}\n")


def test_for_each_reference_is_a_dependency():
    spec = parse_playbook_spec(
        "id: t\nname: T\nsteps:\n  - { id: a, name: A, command_ref: x }\n"
        "  - id: b\n    name: B\n    command_ref: x\n    for_each: '{{ steps.a.outputs.hosts }}'\n"
    )
    assert spec.steps[1].depends_on == ["a"]
    assert spec.steps[1].for_each.items == "{{ steps.a.outputs.hosts }}"


def _start(client, auth, on_failure="stop", max_items=10, hosts=None):
    suffix = uuid.uuid4().hex[:6]
    r = client.post("/api/playbooks", json={"yaml": PLAYBOOK.format(suffix=suffix, on_failure=on_failure, max_items=max_items)}, headers=auth)
    assert r.status_code == 201, r.text
    r = client.post(
        "/api/projects",
        json={"codename": f"FE-{suffix}", "name": "For each", "kind": "internal"},
        headers=auth,
    )
    inputs = {"hosts": hosts} if hosts is not None else {}
    r = client.post(f"/api/playbooks/fan-{suffix}/run", json={"project_id": r.json()["id"], "inputs": inputs}, headers=auth)
    assert r.status_code == 201, r.text
    return uuid.UUID(r.json()["id"]), r.json()


async def _items(db, run_id):
    from castor_server import models as m
    from castor_server.services import latest_jobs_for

    cards = (
        await db.execute(
            select(m.Task).where(m.Task.playbook_run_id == run_id, m.Task.playbook_step_id == "scan").order_by(m.Task.playbook_item)
        )
    ).scalars().all()
    latest = await latest_jobs_for(db, [c.id for c in cards])
    return cards, [latest[c.id].id if c.id in latest else None for c in cards]


async def _finish(db, run_id, job_id, ok=True, hosts=()):
    from castor_server import models as m
    from castor_server.services import advance_playbook_run

    job = await db.get(m.Job, job_id)
    job.status = m.JobStatus.succeeded if ok else m.JobStatus.failed
    job.exit_code = 0 if ok else 1
    if ok:
        db.add(m.JobOutput(job_id=job.id, name="hosts", type="list<host>", parser="t", value=[{"ip": h} for h in hosts]))
    await db.commit()
    await advance_playbook_run(db, run_id)
    db.expire_all()


async def _status(db, run_id):
    from castor_server import models as m
    from castor_server.services import playbook_run_out

    db.expire_all()
    return await playbook_run_out(db, await db.get(m.PlaybookRun, run_id))


async def test_items_run_with_concurrency_and_merge(client, auth):
    from castor_server.db import SessionLocal

    run_id, run = _start(client, auth)
    assert run["step_status"]["scan"] == "queued"
    async with SessionLocal() as db:
        cards, jobs = await _items(db, run_id)
        assert [c.command_args["targets"] for c in cards] == ["10.0.0.1", "10.0.0.2", "10.0.0.3"]
        assert [j is not None for j in jobs] == [True, True, False]  # concurrency 2

        await _finish(db, run_id, jobs[1], hosts=["10.0.0.2"])
        cards, jobs = await _items(db, run_id)
        assert jobs[2] is not None  # the third item took the free slot
        out = await _status(db, run_id)
        assert out.step_status["scan"] == "running" and out.step_status["report"] == "pending"

        await _finish(db, run_id, jobs[2], hosts=["10.0.0.3"])
        await _finish(db, run_id, jobs[0], hosts=["10.0.0.1"])
        out = await _status(db, run_id)
        assert out.step_status["scan"] == "succeeded"
        assert out.step_status["report"] == "queued"
        report = next(t for t in out.tasks if t.playbook_step_id == "report")
        # Merged in item order, not finishing order.
        assert report.command_args["unsigned"] == "10.0.0.1,10.0.0.2,10.0.0.3"


async def test_failed_item_asks_and_retry_reruns_only_failures(client, auth):
    from castor_server.db import SessionLocal

    run_id, _ = _start(client, auth, on_failure="ask", hosts="10.0.0.1,10.0.0.2")
    async with SessionLocal() as db:
        _, jobs = await _items(db, run_id)
        await _finish(db, run_id, jobs[0], hosts=["10.0.0.1"])
        await _finish(db, run_id, jobs[1], ok=False)
        out = await _status(db, run_id)
        assert out.step_status["scan"] == "attention"
        assert out.step_results["scan"]["reason"] == "1 of 2 items failed"
        first_job = jobs[0]

    r = client.post(f"/api/playbook-runs/{run_id}/steps/scan/resolve", json={"action": "retry"}, headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["step_status"]["scan"] == "running"  # one item done, one queued again
    async with SessionLocal() as db:
        _, jobs = await _items(db, run_id)
        assert jobs[0] == first_job  # the item that worked was left alone
        await _finish(db, run_id, jobs[1], hosts=["10.0.0.2"])
        out = await _status(db, run_id)
        assert out.step_status["scan"] == "succeeded" and out.step_status["report"] == "queued"


async def test_too_many_items_and_no_items(client, auth):
    _, run = _start(client, auth, max_items=2)
    assert run["step_status"]["scan"] == "failed"
    assert "more than max_items (2)" in run["step_results"]["scan"]["error"]
    assert not [t for t in run["tasks"] if t["playbook_step_id"] == "scan"]

    _, run = _start(client, auth, hosts=" ")
    assert run["step_status"]["scan"] == "skipped"
    assert run["step_status"]["report"] == "queued"
