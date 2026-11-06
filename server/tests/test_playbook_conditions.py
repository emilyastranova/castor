"""Conditions (when/expect), joins, on_failure and decisions on paused steps."""

import uuid

import pytest
from sqlalchemy import select

from castor_server.playbook_conditions import Condition, Expect
from castor_server.playbook_spec import parse_playbook_spec, run_outcome, step_statuses


def cond(raw):
    return Condition.model_validate(raw)


def test_value_tests():
    hosts = [{"host": "10.0.0.1", "port": 445}, {"host": "10.0.0.2", "port": 80}]
    met, checks = cond({"value": "x", "count": {"gt": 1, "lt": 3}}).evaluate(lambda v: hosts, {})
    assert met and checks[0]["actual"] == "2 items"
    assert cond({"value": "x", "some": {"field": "port", "equals": 445}}).evaluate(lambda v: hosts, {})[0]
    assert not cond({"value": "x", "every": {"field": "port", "equals": 445}}).evaluate(lambda v: hosts, {})[0]
    assert cond({"value": "x", "none": {"field": "port", "equals": 22}}).evaluate(lambda v: hosts, {})[0]
    assert cond({"value": "x", "empty": True}).evaluate(lambda v: [], {})[0]
    assert cond({"value": "x", "count": 0}).evaluate(lambda v: None, {})[0]
    assert cond({"value": "x", "equals": "SMB", "ignore_case": True}).evaluate(lambda v: "smb", {})[0]
    assert cond({"value": "x", "matches": r"^10\.0\."}).evaluate(lambda v: ["10.0.0.1"], {})[0]
    assert cond({"value": "x", "contains": "b"}).evaluate(lambda v: ["a", "b"], {})[0]
    assert not cond({"value": "x", "contains": "b"}).evaluate(lambda v: ["abc"], {})[0]


def test_combinators_explain_every_test():
    c = cond({"any": [{"step": "a", "status": "failed"}, {"not": {"value": "x", "empty": True}}]})
    met, checks = c.evaluate(lambda v: [1], {"a": "succeeded"})
    assert met
    assert [ch["met"] for ch in checks] == [False, True]  # both tests reported, not short-circuited
    assert checks[1]["text"].startswith("not (")
    # A plain list means all.
    assert not cond([{"value": "x", "empty": False}, {"value": "x", "count": 5}]).evaluate(lambda v: [1], {})[0]


def test_expect():
    exp = Expect.model_validate({"outputs": {"hosts": {"empty": False}}, "log": {"not_matches": "DENIED"}})
    assert exp.evaluate({"hosts": [1]}, "all good")[0]
    met, checks = exp.evaluate({"hosts": [1]}, "STATUS_ACCESS_DENIED")
    assert not met and checks[-1]["met"] is False
    with pytest.raises(ValueError, match="log takes"):
        Expect.model_validate({"log": {"count": 1}})


@pytest.mark.parametrize(
    ("when", "message"),
    [
        ("{ value: x }", "give at least one test"),
        ("{ step: a, status: weird }", "status"),
        ("{ foo: 1 }", "needs value:, step:"),
        ("{ step: nope, status: failed }", "unknown step nope"),
        ("{ value: '{{ steps.nope.outputs.x }}', empty: true }", "unknown step nope"),
    ],
)
def test_bad_conditions(when, message):
    with pytest.raises(ValueError, match=message):
        parse_playbook_spec(
            f"id: t\nname: T\nsteps:\n  - id: a\n    name: A\n    command_ref: x\n"
            f"  - id: b\n    name: B\n    command_ref: x\n    when: {when}\n"
        )


BRANCHES = """
id: t
name: T
steps:
  - { id: a, name: A, command_ref: x }
  - id: yes_branch
    name: Yes branch
    command_ref: x
    when: { value: "{{ steps.a.outputs.hosts }}", empty: false }
  - { id: after_yes, name: After yes, command_ref: x, depends_on: [yes_branch] }
  - id: no_branch
    name: No branch
    command_ref: x
    when: { value: "{{ steps.a.outputs.hosts }}", empty: true }
  - { id: merge, name: Merge, command_ref: x, join: any, depends_on: [after_yes, no_branch] }
  - id: on_fail
    name: On failure
    command_ref: x
    when: { step: a, status: failed }
"""


def test_statuses_branching_and_joins():
    spec = parse_playbook_spec(BRANCHES)
    # a succeeded, the yes branch ran; the no branch and the failure handler weren't taken.
    results = {"no_branch": {"status": "not_taken"}, "on_fail": {"status": "not_taken"}}
    jobs = {"a": "succeeded", "yes_branch": "succeeded"}
    st = step_statuses(spec, jobs, results)
    assert st["after_yes"] == "pending"
    assert st["merge"] == "pending"  # join any waits for after_yes to end
    st = step_statuses(spec, {**jobs, "after_yes": "succeeded", "merge": "succeeded"}, results)
    assert run_outcome(spec, st) == "succeeded"

    # The yes branch not taken: what needs it is left out too, the merge still runs.
    st = step_statuses(spec, {"a": "succeeded", "no_branch": "succeeded"}, {"yes_branch": {"status": "not_taken"}, "on_fail": {"status": "not_taken"}})
    assert st["after_yes"] == "not_taken"
    assert st["merge"] == "pending"

    # a failed, and on_fail handles it: the branches that need a's outputs are left out
    # (not blocked), and the run can still succeed.
    st = step_statuses(spec, {"a": "failed", "on_fail": "succeeded"}, {})
    assert st["yes_branch"] == "not_taken" and st["no_branch"] == "not_taken"
    assert st["merge"] == "not_taken"
    assert run_outcome(spec, st) == "succeeded"


def test_on_failure_continue_and_attention():
    spec = parse_playbook_spec("""
id: t
name: T
steps:
  - { id: a, name: A, command_ref: x, on_failure: continue }
  - { id: b, name: B, command_ref: x, depends_on: [a] }
  - { id: c, name: C, command_ref: x, on_failure: ask }
  - { id: d, name: D, command_ref: x, depends_on: [c] }
""")
    # A paused step (attention, recorded for its job) wins over the job's status and holds
    # its dependents; the run is still running.
    st = step_statuses(
        spec,
        {"a": "failed", "b": "succeeded", "c": "failed"},
        {"c": {"status": "attention", "job_id": "j1"}},
        job_id_by_step={"c": "j1"},
    )
    assert st["c"] == "attention" and st["d"] == "pending"
    assert run_outcome(spec, st) == "running"
    # Re-running the card (a new job) moves past the recorded decision.
    st = step_statuses(spec, {"c": "running"}, {"c": {"status": "attention", "job_id": "j1"}}, job_id_by_step={"c": "j2"})
    assert st["c"] == "running"
    st = step_statuses(spec, {"a": "failed", "b": "succeeded", "c": "succeeded", "d": "succeeded"}, {})
    assert run_outcome(spec, st) == "succeeded"  # a's failure is tolerated


# ---------- end to end with the branching demo ----------


def _start(client, auth, **inputs):
    r = client.post(
        "/api/projects",
        json={"codename": f"BR-{uuid.uuid4().hex[:6]}", "name": "Branching", "kind": "internal"},
        headers=auth,
    )
    assert r.status_code == 201
    r = client.post(
        "/api/playbooks/branching-demo/run",
        json={"project_id": r.json()["id"], "inputs": inputs},
        headers=auth,
    )
    assert r.status_code == 201, r.text
    return uuid.UUID(r.json()["id"])


async def _finish(db, run_id, step, status="succeeded", outputs=None):
    from castor_server import models as m
    from castor_server.services import advance_playbook_run

    run = await db.get(m.PlaybookRun, run_id)
    tid = uuid.UUID(run.step_task_ids[step])
    job = (
        await db.execute(select(m.Job).where(m.Job.task_id == tid).order_by(m.Job.queued_at.desc()).limit(1))
    ).scalar_one()
    job.status = m.JobStatus(status)
    job.exit_code = 0 if status == "succeeded" else 1
    for name, (type_, value) in (outputs or {}).items():
        db.add(m.JobOutput(job_id=job.id, name=name, type=type_, parser="test", value=value))
    await db.commit()
    await advance_playbook_run(db, run_id)


async def _statuses(db, run_id):
    from castor_server import models as m
    from castor_server.services import playbook_run_out

    db.expire_all()
    run = await db.get(m.PlaybookRun, run_id)
    out = await playbook_run_out(db, run)
    return out.step_status, out


HOSTS = ("list<host>", [{"ip": "10.30.0.3"}, {"ip": "10.30.0.4"}])
MIXED = ("list<smb_host>", [{"host": "10.30.0.3", "signing": True}, {"host": "10.30.0.4", "signing": False}])


@pytest.mark.asyncio
async def test_branch_taken_and_continue_on_failure(client, auth):
    from castor_server.db import SessionLocal

    run_id = _start(client, auth)
    async with SessionLocal() as db:
        await _finish(db, run_id, "seed_hosts", outputs={"hosts": HOSTS})
        await _finish(db, run_id, "smb_scan", outputs={"hosts": HOSTS})
        await _finish(db, run_id, "smb_audit", outputs={"smb_hosts": MIXED})
        st, run = await _statuses(db, run_id)
        assert st["unsigned"] == "succeeded"
        assert st["relay_scan"] == "queued"
        assert st["all_signed"] == "not_taken" and st["fallback_names"] == "not_taken"
        assert run.step_results["relay_scan"]["condition"]["met"] is True
        assert run.step_results["all_signed"]["condition"]["checks"][0]["actual"] == "1 item"
        assert st["report"] == "pending"

        await _finish(db, run_id, "relay_scan", status="failed")  # on_failure: continue
        st, _ = await _statuses(db, run_id)
        assert st["relay_scan"] == "failed" and st["report"] == "queued"
        await _finish(db, run_id, "report")
        st, run = await _statuses(db, run_id)
        assert run.status == "succeeded"


@pytest.mark.asyncio
async def test_unexpected_output_asks_and_retry(client, auth):
    from castor_server.db import SessionLocal

    run_id = _start(client, auth)
    async with SessionLocal() as db:
        await _finish(db, run_id, "seed_hosts", outputs={"hosts": ("list<host>", [])})
        st, run = await _statuses(db, run_id)
        assert st["seed_hosts"] == "attention"
        assert st["smb_scan"] == "pending"
        assert "unexpected output" in run.step_results["seed_hosts"]["reason"]
        assert run.status == "running"

    notes = client.get("/api/notifications?unread_only=true", headers=auth).json()
    mine = [n for n in notes["items"] if n["payload"].get("run_id") == str(run_id)]
    assert mine and mine[0]["kind"] == "playbook_step.attention"
    # It opens the step on top of the run's card, where the decision is answered (3h).
    step_card = run.step_task_ids[mine[0]["payload"]["step_id"]]
    assert mine[0]["link"] == f"/projects/{run.project_id}?task={run.card_id}&step={step_card}"

    r = client.post(f"/api/playbook-runs/{run_id}/steps/smb_scan/resolve", json={"action": "continue"}, headers=auth)
    assert r.status_code == 409  # not waiting for a decision
    r = client.post(f"/api/playbook-runs/{run_id}/steps/seed_hosts/resolve", json={"action": "retry", "note": "try again"}, headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["step_status"]["seed_hosts"] == "queued"
    assert r.json()["step_results"]["seed_hosts"]["resolution"]["note"] == "try again"
    notes = client.get("/api/notifications?unread_only=true", headers=auth).json()
    assert not [n for n in notes["items"] if n["payload"].get("run_id") == str(run_id)]

    async with SessionLocal() as db:
        await _finish(db, run_id, "seed_hosts", outputs={"hosts": HOSTS})
        st, _ = await _statuses(db, run_id)
        assert st["seed_hosts"] == "succeeded" and st["smb_scan"] == "queued"


@pytest.mark.asyncio
async def test_failure_decision_takes_the_failure_branch(client, auth):
    from castor_server.db import SessionLocal

    run_id = _start(client, auth)
    async with SessionLocal() as db:
        await _finish(db, run_id, "seed_hosts", outputs={"hosts": HOSTS})
        await _finish(db, run_id, "smb_scan", outputs={"hosts": HOSTS})
        await _finish(db, run_id, "smb_audit", status="failed")
        st, _ = await _statuses(db, run_id)
        assert st["smb_audit"] == "attention"
        assert st["fallback_names"] == "pending"

    r = client.post(f"/api/playbook-runs/{run_id}/steps/smb_audit/resolve", json={"action": "fail"}, headers=auth)
    assert r.status_code == 200, r.text
    st = r.json()["step_status"]
    assert st["smb_audit"] == "failed"
    assert st["fallback_names"] == "queued"
    assert st["unsigned"] == "not_taken" and st["relay_scan"] == "not_taken"

    async with SessionLocal() as db:
        await _finish(db, run_id, "fallback_names", outputs={"names": ("list<string>", ["host-3"])})
        st, _ = await _statuses(db, run_id)
        assert st["report"] == "queued"
        await _finish(db, run_id, "report")
        _, run = await _statuses(db, run_id)
        assert run.status == "succeeded"


def test_webhook_formats(monkeypatch):
    import json

    from castor_server import notifications
    from castor_server.config import settings

    monkeypatch.setattr(settings, "public_url", "https://castor.lab")
    msg = {"title": "Run: “Audit” needs you", "body": "the job failed", "link": "/playbooks?run=1",
           "severity": "warning", "kind": "playbook_step.attention", "payload": {"run_id": "1"}}
    slack = notifications.webhook_request("https://hooks.slack.com/services/x", **msg)
    assert "https://castor.lab/playbooks?run=1" in json.loads(slack.data)["text"]
    discord = notifications.webhook_request("https://discord.com/api/webhooks/x", **msg)
    assert "content" in json.loads(discord.data)
    ntfy = notifications.webhook_request("https://ntfy.sh/castor-alerts", **msg)
    assert ntfy.data == b"the job failed"
    assert ntfy.get_header("Click") == "https://castor.lab/playbooks?run=1"
    assert ntfy.get_header("Priority") == "high"
    other = json.loads(notifications.webhook_request("https://example.test/hook", **msg).data)
    assert other["run_id"] == "1" and other["severity"] == "warning"
