from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import models as m
from .. import variables
from ..deps import DB, CurrentUser, parse_uuid
from ..orchestrator.dispatcher import dispatcher
from ..playbook_library import check_against_library, definition_from_spec, load_playbook_spec
from ..playbook_spec import parse_playbook_spec
from ..schemas import (
    PlaybookDefinitionIn,
    PlaybookDefinitionOut,
    PlaybookRunIn,
    PlaybookRunOut,
)
from ..services import (
    cancel_playbook_run,
    playbook_run_out,
    start_playbook_run,
)

router = APIRouter(tags=["playbooks"])


class RunRequest(BaseModel):
    project_id: uuid.UUID
    inputs: dict[str, Any] = Field(default_factory=dict)


@router.get("/playbooks", response_model=list[PlaybookDefinitionOut])
async def list_playbooks(db: DB, _: CurrentUser):
    rows = (
        await db.execute(select(m.PlaybookDefinition).order_by(m.PlaybookDefinition.name))
    ).scalars().all()
    return [PlaybookDefinitionOut.model_validate(r) for r in rows]


@router.get("/playbooks/{playbook_id}", response_model=PlaybookDefinitionOut)
async def get_playbook(playbook_id: str, db: DB, _: CurrentUser):
    row = await db.get(m.PlaybookDefinition, playbook_id)
    if row is None:
        raise HTTPException(404, "Playbook not found")
    return PlaybookDefinitionOut.model_validate(row)


class ValidateOut(BaseModel):
    ok: bool
    errors: list[str]
    # The normalised spec (kinds and implicit depends_on filled in) when it parses.
    spec: dict[str, Any] | None = None


async def _checked_spec(db: DB, source: str):
    try:
        spec = parse_playbook_spec(source)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    problems = await check_against_library(db, spec)
    if problems:
        raise HTTPException(422, "; ".join(problems))
    return spec


@router.post("/playbooks/validate", response_model=ValidateOut)
async def validate_playbook(body: PlaybookDefinitionIn, db: DB, _: CurrentUser):
    """Check a playbook without saving it (for editors and agents writing playbooks)."""
    try:
        spec = parse_playbook_spec(body.yaml)
    except ValueError as exc:
        return ValidateOut(ok=False, errors=str(exc).split("; "))
    problems = await check_against_library(db, spec)
    return ValidateOut(ok=not problems, errors=problems, spec=spec.model_dump(mode="json"))


@router.post("/playbooks", response_model=PlaybookDefinitionOut, status_code=201)
async def create_playbook(body: PlaybookDefinitionIn, db: DB, _: CurrentUser):
    spec = await _checked_spec(db, body.yaml)
    if await db.get(m.PlaybookDefinition, spec.id) is not None:
        raise HTTPException(409, f"A playbook with id “{spec.id}” already exists")

    row = m.PlaybookDefinition(id=spec.id, origin="user", version=1)
    definition_from_spec(row, spec, body.yaml)
    db.add(row)
    await db.commit()
    return PlaybookDefinitionOut.model_validate(row)


@router.put("/playbooks/{playbook_id}", response_model=PlaybookDefinitionOut)
async def update_playbook(playbook_id: str, body: PlaybookDefinitionIn, db: DB, _: CurrentUser):
    row = await db.get(m.PlaybookDefinition, playbook_id)
    if row is None:
        raise HTTPException(404, "Playbook not found")
    if row.origin == "builtin":
        raise HTTPException(409, "Built-in playbooks are read-only; duplicate it to customise")

    spec = await _checked_spec(db, body.yaml)
    if spec.id != playbook_id:
        raise HTTPException(422, "id: cannot be changed (create a new playbook instead)")

    definition_from_spec(row, spec, body.yaml)
    await db.commit()
    return PlaybookDefinitionOut.model_validate(row)


@router.delete("/playbooks/{playbook_id}", status_code=204)
async def delete_playbook(playbook_id: str, db: DB, _: CurrentUser):
    row = await db.get(m.PlaybookDefinition, playbook_id)
    if row is None:
        raise HTTPException(404, "Playbook not found")
    if row.origin == "builtin":
        raise HTTPException(409, "Built-in playbooks cannot be deleted")

    await db.delete(row)
    await db.commit()


@router.post("/playbooks/{playbook_id}/run", response_model=PlaybookRunOut, status_code=201)
async def run_playbook(playbook_id: str, body: RunRequest, db: DB, user: CurrentUser):
    return await _execute_playbook_run(db, playbook_id, body.project_id, body.inputs, user)


@router.post("/playbook-runs", response_model=PlaybookRunOut, status_code=201)
async def create_playbook_run(body: PlaybookRunIn, db: DB, user: CurrentUser):
    return await _execute_playbook_run(db, body.playbook_id, body.project_id, body.inputs, user)


async def _execute_playbook_run(
    db: DB, playbook_id: str, project_id: uuid.UUID, inputs: dict[str, Any], user: CurrentUser
) -> PlaybookRunOut:
    spec = await load_playbook_spec(db, playbook_id)
    if spec is None:
        raise HTTPException(404, f"Playbook “{playbook_id}” not found")

    project = await db.get(m.Project, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")

    # Validate required inputs
    for inp in spec.inputs:
        if inp.name not in inputs or inputs[inp.name] is None or inputs[inp.name] == "":
            if inp.default is not None:
                inputs[inp.name] = inp.default
            elif inp.required:
                raise HTTPException(422, f"Missing required input “{inp.name}”")
        if inp.type == "varset" and inputs.get(inp.name):
            # Accept a set id or name; store the id so the run is unaffected by renames.
            row = await variables.find_set(db, inputs[inp.name])
            if row is None:
                raise HTTPException(422, f"{inp.name}: variable set not found")
            if row.kind_id != inp.kind:
                raise HTTPException(422, f"{inp.name}: {row.name} is a {row.kind_id} set, not {inp.kind}")
            if not variables.allowed_in(row, project_id):
                raise HTTPException(422, f"{inp.name}: {row.name} can't be used in this project")
            if not variables.owned_by(row, None):
                raise HTTPException(422, f"{inp.name}: that manual entry belongs to another card or run")
            inputs[inp.name] = str(row.id)

    try:
        run, jobs = await start_playbook_run(db, spec, project_id, inputs, user)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    await db.commit()
    if jobs:
        dispatcher.wake()
    return await playbook_run_out(db, run)


@router.get("/playbook-runs", response_model=list[PlaybookRunOut])
async def list_playbook_runs(
    db: DB,
    _: CurrentUser,
    project_id: str | None = None,
    playbook_id: str | None = None,
    limit: int = 50,
):
    query = select(m.PlaybookRun).order_by(m.PlaybookRun.created_at.desc()).limit(min(limit, 200))
    if project_id:
        query = query.where(m.PlaybookRun.project_id == parse_uuid(project_id))
    if playbook_id:
        query = query.where(m.PlaybookRun.playbook_id == playbook_id)

    runs = (await db.execute(query)).scalars().all()
    return [await playbook_run_out(db, r) for r in runs]


@router.get("/playbook-runs/{run_id}", response_model=PlaybookRunOut)
async def get_playbook_run(run_id: str, db: DB, _: CurrentUser):
    run = await db.get(m.PlaybookRun, parse_uuid(run_id))
    if run is None:
        raise HTTPException(404, "Playbook run not found")
    return await playbook_run_out(db, run)


@router.post("/playbook-runs/{run_id}/cancel", response_model=PlaybookRunOut)
async def cancel_run(run_id: str, db: DB, user: CurrentUser):
    run = await cancel_playbook_run(db, parse_uuid(run_id), user)
    if run is None:
        raise HTTPException(404, "Playbook run not found")
    return await playbook_run_out(db, run)
