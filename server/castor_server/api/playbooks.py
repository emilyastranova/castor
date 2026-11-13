from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import models as m
from .. import variables
from ..access import check_project, project_or_404, visible_project_ids
from ..deps import DB, CurrentUser, Operator, parse_uuid
from ..orchestrator.dispatcher import dispatcher, selector_matches
from ..orchestrator.gateway import gateway
from ..playbook_library import check_against_library, definition_from_spec, load_playbook_spec
from ..playbook_spec import parse_playbook_spec
from ..playbook_yaml import to_yaml
from ..plugin_library import cant_delete, read_only, why_read_only
from ..schemas import (
    AlertRules,
    PlaybookDefinitionIn,
    PlaybookDefinitionOut,
    PlaybookRunIn,
    PlaybookRunOut,
)
from ..services import (
    ResolveError,
    SelectorError,
    cancel_playbook_run,
    check_selector,
    library_activity,
    playbook_run_out,
    resolve_step,
    start_playbook_run,
)

router = APIRouter(tags=["playbooks"])


class RunRequest(BaseModel):
    project_id: uuid.UUID
    inputs: dict[str, Any] = Field(default_factory=dict)
    alerts: AlertRules | None = None
    # Where its steps run: {agent_id}, {tags} (one agent with them), or {tags, all: true}
    # (one run per connected agent with them).
    agent_selector: dict[str, Any] = Field(default_factory=dict)


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


class SpecIn(BaseModel):
    spec: dict[str, Any]


class SpecYamlOut(ValidateOut):
    yaml: str


@router.post("/playbooks/to-yaml", response_model=SpecYamlOut)
async def spec_to_yaml(body: SpecIn, db: DB, _: CurrentUser):
    """The visual editor's spec as YAML (what would be saved), validated like `validate`.
    The YAML is produced even when the spec has problems, so the editor can show both."""
    text = to_yaml(body.spec)
    try:
        spec = parse_playbook_spec(body.spec)
    except ValueError as exc:
        return SpecYamlOut(ok=False, errors=str(exc).split("; "), yaml=text)
    problems = await check_against_library(db, spec)
    return SpecYamlOut(ok=not problems, errors=problems, spec=spec.model_dump(mode="json"), yaml=text)


@router.post("/playbooks", response_model=PlaybookDefinitionOut, status_code=201)
async def create_playbook(body: PlaybookDefinitionIn, db: DB, user: Operator):
    spec = await _checked_spec(db, body.yaml)
    if await db.get(m.PlaybookDefinition, spec.id) is not None:
        raise HTTPException(409, f"A playbook with id “{spec.id}” already exists")

    row = m.PlaybookDefinition(id=spec.id, origin="user", version=1)
    definition_from_spec(row, spec, body.yaml)
    db.add(row)
    await library_activity(db, user, "playbook", "created", spec.id, row.name)
    await db.commit()
    return PlaybookDefinitionOut.model_validate(row)


@router.put("/playbooks/{playbook_id}", response_model=PlaybookDefinitionOut)
async def update_playbook(playbook_id: str, body: PlaybookDefinitionIn, db: DB, user: Operator):
    row = await db.get(m.PlaybookDefinition, playbook_id)
    if row is None:
        raise HTTPException(404, "Playbook not found")
    if read_only(row.origin):
        raise HTTPException(409, why_read_only(row.origin, "playbook"))

    spec = await _checked_spec(db, body.yaml)
    if spec.id != playbook_id:
        raise HTTPException(422, "id: cannot be changed (create a new playbook instead)")

    definition_from_spec(row, spec, body.yaml)
    await library_activity(db, user, "playbook", "updated", playbook_id, row.name)
    await db.commit()
    return PlaybookDefinitionOut.model_validate(row)


@router.delete("/playbooks/{playbook_id}", status_code=204)
async def delete_playbook(playbook_id: str, db: DB, user: Operator):
    row = await db.get(m.PlaybookDefinition, playbook_id)
    if row is None:
        raise HTTPException(404, "Playbook not found")
    if read_only(row.origin):
        raise HTTPException(409, cant_delete(row.origin, "playbook"))

    await db.delete(row)
    await library_activity(db, user, "playbook", "deleted", playbook_id, row.name)
    await db.commit()


@router.post("/playbooks/{playbook_id}/run", response_model=PlaybookRunOut, status_code=201)
async def run_playbook(playbook_id: str, body: RunRequest, db: DB, user: CurrentUser):
    return await _execute_playbook_run(db, playbook_id, body.project_id, body.inputs, user, body.alerts, body.agent_selector)


@router.post("/playbook-runs", response_model=PlaybookRunOut, status_code=201)
async def create_playbook_run(body: PlaybookRunIn, db: DB, user: CurrentUser):
    return await _execute_playbook_run(db, body.playbook_id, body.project_id, body.inputs, user, body.alerts, body.agent_selector)


async def _execute_playbook_run(
    db: DB, playbook_id: str, project_id: uuid.UUID, inputs: dict[str, Any], user: CurrentUser,
    alerts: AlertRules | None = None,
    agent_selector: dict[str, Any] | None = None,
) -> PlaybookRunOut:
    spec = await load_playbook_spec(db, playbook_id)
    if spec is None:
        raise HTTPException(404, f"Playbook “{playbook_id}” not found")

    await project_or_404(db, user, project_id, write=True)

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
        selector = await check_selector(db, agent_selector)
    except SelectorError as exc:
        raise HTTPException(422, str(exc)) from exc
    # Every agent with the tags: one run each, pinned to it, so each one's results stay apart.
    if selector.get("all"):
        targets = sorted((c for c in gateway.connections.values() if selector_matches(c, selector)), key=lambda c: c.name)
        if not targets:
            raise HTTPException(409, f"No connected agent has the tags {', '.join(selector['tags'])}")
        plans = [({"agent_id": str(c.agent_id)}, f"{spec.name} · {c.name}") for c in targets]
    else:
        plans = [(selector, None)]
    runs, queued = [], False
    for run_selector, title in plans:
        try:
            run, jobs = await start_playbook_run(db, spec, project_id, dict(inputs), user, run_selector, title)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if alerts is not None and run.card_id:
            card = await db.get(m.Task, run.card_id)
            card.alerts = alerts.stored()
        runs.append(run)
        queued |= bool(jobs)
    await db.commit()
    if queued:
        dispatcher.wake()
    out = await playbook_run_out(db, runs[0])
    out.sibling_run_ids = [r.id for r in runs[1:]]
    return out


async def _run_or_404(db: DB, user: m.User, run_id: str, *, write: bool = False) -> m.PlaybookRun:
    run = await db.get(m.PlaybookRun, parse_uuid(run_id))
    if run is None:
        raise HTTPException(404, "Playbook run not found")
    try:
        await check_project(db, user, run.project_id, write=write)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(404, "Playbook run not found") from exc
        raise
    return run


@router.get("/playbook-runs", response_model=list[PlaybookRunOut])
async def list_playbook_runs(
    db: DB,
    user: CurrentUser,
    project_id: str | None = None,
    playbook_id: str | None = None,
    limit: int = 50,
):
    query = select(m.PlaybookRun).order_by(m.PlaybookRun.created_at.desc()).limit(min(limit, 200))
    visible = await visible_project_ids(db, user)
    if visible is not None:
        query = query.where(m.PlaybookRun.project_id.in_(visible))
    if project_id:
        query = query.where(m.PlaybookRun.project_id == parse_uuid(project_id))
    if playbook_id:
        query = query.where(m.PlaybookRun.playbook_id == playbook_id)

    runs = (await db.execute(query)).scalars().all()
    return [await playbook_run_out(db, r) for r in runs]


@router.get("/playbook-runs/{run_id}", response_model=PlaybookRunOut)
async def get_playbook_run(run_id: str, db: DB, user: CurrentUser):
    run = await _run_or_404(db, user, run_id)
    return await playbook_run_out(db, run)


@router.post("/playbook-runs/{run_id}/cancel", response_model=PlaybookRunOut)
async def cancel_run(run_id: str, db: DB, user: CurrentUser):
    await _run_or_404(db, user, run_id, write=True)
    run = await cancel_playbook_run(db, parse_uuid(run_id), user)
    if run is None:
        raise HTTPException(404, "Playbook run not found")
    return await playbook_run_out(db, run)


class ResolveIn(BaseModel):
    action: str
    note: str = ""


@router.post("/playbook-runs/{run_id}/steps/{step_id}/resolve", response_model=PlaybookRunOut)
async def resolve_run_step(run_id: str, step_id: str, body: ResolveIn, db: DB, user: CurrentUser):
    """Decide what happens to a step waiting for a person: retry, continue, skip or fail."""
    run = await _run_or_404(db, user, run_id, write=True)
    try:
        jobs = await resolve_step(db, run, step_id, body.action, user, body.note)
    except ResolveError as exc:
        raise HTTPException(409, str(exc)) from exc
    if jobs:
        dispatcher.wake()
    return await playbook_run_out(db, run)


@router.post("/playbook-runs/{run_id}/steps/{step_id}/ai/suggest")
async def ai_suggest(run_id: str, step_id: str, db: DB, user: CurrentUser):
    """Ask the AI resolver what to do with a paused step (it doesn't act; see ai/apply)."""
    from .. import ai, llm

    run = await _run_or_404(db, user, run_id, write=True)
    try:
        return await ai.suggest_resolution(db, run, step_id, user)
    except llm.NotConfigured as exc:
        raise HTTPException(409, str(exc)) from exc
    except llm.AiError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("/playbook-runs/{run_id}/steps/{step_id}/ai/apply", response_model=PlaybookRunOut)
async def ai_apply(run_id: str, step_id: str, db: DB, user: CurrentUser):
    """Do what the AI suggested for a paused step, as the person clicking."""
    from .. import ai, llm

    run = await _run_or_404(db, user, run_id, write=True)
    try:
        jobs = await ai.apply_suggestion(db, run, step_id, user)
    except (ResolveError, llm.AiError) as exc:
        raise HTTPException(409, str(exc)) from exc
    if jobs:
        dispatcher.wake()
    return await playbook_run_out(db, run)
