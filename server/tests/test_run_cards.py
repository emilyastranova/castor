"""Playbook runs as one card (Phase 3h): the card, its summary, hidden steps, moving once
when the run ends, and cards for runs from before."""

import asyncio
import uuid


def _project(client, auth):
    return client.post("/api/projects", json={"codename": f"RC-{uuid.uuid4().hex[:6]}", "name": "Run cards", "kind": "internal"},
                       headers=auth).json()


def _start(client, auth, project):
    r = client.post("/api/playbooks/branching-demo/run", json={"project_id": project["id"], "inputs": {}}, headers=auth)
    assert r.status_code == 201, r.text
    return r.json()


def _board(client, auth, project):
    board = client.get(f"/api/projects/{project['id']}/board", headers=auth).json()
    roles = {c["id"]: c["role"] for c in board["columns"]}
    return board, roles


def test_a_run_is_one_card(client, auth):
    project = _project(client, auth)
    run = _start(client, auth, project)
    assert run["card_id"]
    board, roles = _board(client, auth, project)
    titles = [t["title"] for t in board["tasks"]]
    assert titles == ["Branching Demo"]  # no step cards on the board
    card = board["tasks"][0]
    assert card["id"] == run["card_id"] and roles[card["column_id"]] == "active"
    summary = card["run"]
    assert summary["run_id"] == run["id"] and summary["status"] == "running"
    assert summary["total"] == len(run["step_status"]) and summary["step_task_ids"] == run["step_task_ids"]

    # Steps are still cards (their runs, comments and files work), just not on the board.
    step_id = next(iter(run["step_task_ids"].values()))
    assert client.get(f"/api/tasks/{step_id}", headers=auth).json()["playbook_step_id"]

    # Ending the run moves the card to Review.
    client.post(f"/api/playbook-runs/{run['id']}/cancel", headers=auth)
    board, roles = _board(client, auth, project)
    card = board["tasks"][0]
    assert roles[card["column_id"]] == "review" and card["run"]["status"] == "cancelled"


def test_a_moved_card_stays_where_it_was_put(client, auth):
    project = _project(client, auth)
    run = _start(client, auth, project)
    board, roles = _board(client, auth, project)
    todo = next(cid for cid, role in roles.items() if role == "todo")
    client.post(f"/api/tasks/{run['card_id']}/move", json={"column_id": todo, "position": 5}, headers=auth)
    client.post(f"/api/playbook-runs/{run['id']}/cancel", headers=auth)
    board, roles = _board(client, auth, project)
    assert roles[board["tasks"][0]["column_id"]] == "todo"


def test_runs_from_before_get_a_card(client, auth):
    project = _project(client, auth)
    run = _start(client, auth, project)
    client.post(f"/api/playbook-runs/{run['id']}/cancel", headers=auth)

    async def forget_card_then_backfill():
        from castor_server import models as m
        from castor_server.db import SessionLocal
        from castor_server.services import ensure_run_cards

        async with SessionLocal() as db:
            row = await db.get(m.PlaybookRun, uuid.UUID(run["id"]))
            card = await db.get(m.Task, row.card_id)
            row.card_id = None
            await db.delete(card)
            await db.commit()
            return await ensure_run_cards(db)

    assert asyncio.run(forget_card_then_backfill()) >= 1
    board, roles = _board(client, auth, project)
    card = board["tasks"][0]
    assert card["title"] == "Branching Demo" and roles[card["column_id"]] == "review" and card["run"]["run_id"] == run["id"]
