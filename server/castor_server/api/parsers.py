from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import ai, llm, parser_library, parsers
from .. import models as m
from ..access import job_or_404
from ..deps import DB, CurrentUser, Operator, parse_uuid
from ..outputs import _specs, is_mismatch, run_parser
from ..parser_spec import parse_parser_spec
from ..schemas import ParserProposalOut
from ..services import apply_parser_proposal, proposal_out, publish_parser_proposal

router = APIRouter(prefix="/parsers", tags=["parsers"])
proposals = APIRouter(tags=["parsers"])

# Test input is typed or pasted in the browser; real artifacts go through outputs.py.
MAX_TEST_BYTES = 2 * 1024 * 1024


class OptionFieldOut(BaseModel):
    name: str
    kind: str
    description: str
    required: bool
    default: Any
    example: Any


class ParserOut(BaseModel):
    name: str
    type: str
    description: str
    options: list[OptionFieldOut]
    sample: str
    sample_options: dict[str, Any]
    # builtin: code in Castor; user: a definition in the library (parser_spec.py).
    origin: str = "builtin"
    title: str = ""
    source_yaml: str = ""
    version: int = 0
    used_by: list[str] = Field(default_factory=list)


class ParseTestIn(BaseModel):
    input: str = ""
    options: dict[str, Any] = Field(default_factory=dict)


class ParseTestOut(BaseModel):
    type: str
    value: Any = None
    error: str | None = None
    option_errors: dict[str, str] = Field(default_factory=dict)
    elapsed_ms: float = 0


class ParserYamlIn(BaseModel):
    yaml: str = Field(max_length=200_000)


class ValidateIn(BaseModel):
    yaml: str = Field(max_length=200_000)
    # Output to try it on; empty means the definition's own sample.
    input: str | None = Field(None, max_length=MAX_TEST_BYTES)


class ValidateOut(BaseModel):
    ok: bool
    errors: list[str] = Field(default_factory=list)
    type: str | None = None
    value: Any = None
    count: int = 0


class DraftIn(BaseModel):
    sample: str = Field(min_length=1, max_length=MAX_TEST_BYTES)
    type: str | None = None
    hint: str = Field("", max_length=1000)
    current: str = Field("", max_length=60_000)


class ApplyIn(BaseModel):
    # Keep it in the library under this id (otherwise it's used for this job only).
    save_as: str | None = Field(None, max_length=64)


def _out(p: parsers.Parser, row: m.ParserDefinition | None = None, used_by: list[str] | None = None) -> ParserOut:
    return ParserOut(
        name=p.name,
        type=p.type,
        description=p.description,
        options=[OptionFieldOut(**f) for f in parsers.option_fields(p.options)],
        sample=p.sample_text(),
        sample_options=p.sample_options,
        origin=p.origin,
        title=row.name if row else "",
        source_yaml=row.source_yaml if row else "",
        version=row.version if row else 0,
        used_by=used_by or [],
    )


async def _used_by(db) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for cid, spec in (await db.execute(select(m.CommandDefinition.id, m.CommandDefinition.spec))).all():
        for o in (spec or {}).get("outputs", []):
            if o.get("parser"):
                out.setdefault(o["parser"], []).append(cid)
    return out


@router.get("", response_model=list[ParserOut])
async def list_parsers(db: DB, _: CurrentUser):
    rows = {r.id: r for r in (await db.execute(select(m.ParserDefinition))).scalars()}
    used = await _used_by(db)
    return [_out(p, rows.get(p.name), sorted(used.get(p.name, []))) for p in parsers.all_parsers()]


@router.post("/validate", response_model=ValidateOut)
async def validate(body: ValidateIn, _: CurrentUser):
    """Check a definition and run it on some output (or its own sample), without saving.
    Problems come back in the body so the editor can show them while you type."""
    try:
        spec = parse_parser_spec(body.yaml)
    except ValueError as exc:
        return ValidateOut(ok=False, errors=[str(exc)])
    sample = body.input if body.input is not None else spec.sample
    if not sample.strip():
        return ValidateOut(ok=True, type=spec.type)
    _, value, problems = await asyncio.to_thread(ai.try_parser, body.yaml, sample)
    count = len(value) if isinstance(value, list) else (0 if value is None else 1)
    preview = value[:200] if isinstance(value, list) else value
    return ValidateOut(ok=not problems, errors=problems, type=spec.type, value=preview, count=count)


@router.post("/draft")
async def draft(body: DraftIn, db: DB, user: CurrentUser):
    """A parser definition the AI wrote for this output, run on it (not saved)."""
    if body.type and body.type not in parsers.OUTPUT_TYPES:
        raise HTTPException(422, f"type must be one of {', '.join(parsers.OUTPUT_TYPES)}")
    try:
        return await ai.draft_parser(db, user, body.sample, want_type=body.type, hint=body.hint, current=body.current)
    except llm.AiError as exc:
        raise HTTPException(409 if isinstance(exc, llm.NotConfigured) else 502, str(exc)) from exc


@router.post("", response_model=ParserOut, status_code=201)
async def create_parser(body: ParserYamlIn, db: DB, _: Operator):
    try:
        spec = parse_parser_spec(body.yaml)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if await db.get(m.ParserDefinition, spec.id) is not None:
        raise HTTPException(409, f"A parser with id “{spec.id}” already exists")
    row = m.ParserDefinition(id=spec.id, origin="user", version=1)
    parser_library.definition_from_spec(row, spec, body.yaml)
    db.add(row)
    await db.commit()
    parser_library.register(spec)
    return _out(parsers.get(spec.id), row)


@router.get("/{name}", response_model=ParserOut)
async def get_parser(name: str, db: DB, _: CurrentUser):
    p = parsers.get(name)
    if p is None:
        raise HTTPException(404, "Parser not found")
    return _out(p, await db.get(m.ParserDefinition, name), sorted((await _used_by(db)).get(name, [])))


@router.put("/{name}", response_model=ParserOut)
async def update_parser(name: str, body: ParserYamlIn, db: DB, _: Operator):
    row = await db.get(m.ParserDefinition, name)
    if row is None:
        raise HTTPException(409 if name in parsers.PARSERS else 404, "Built-in parsers can't be changed" if name in parsers.PARSERS else "Parser not found")
    try:
        spec = parse_parser_spec(body.yaml)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if spec.id != name:
        raise HTTPException(422, "id: cannot be changed (make a new parser instead)")
    used = await parser_library.commands_using(db, name)
    if used and spec.type != row.spec.get("type"):
        raise HTTPException(409, f"{', '.join(used)} use{'s' if len(used) == 1 else ''} it as {row.spec.get('type')}; keep that type or make a new parser")
    parser_library.definition_from_spec(row, spec, body.yaml)
    await db.commit()
    parser_library.register(spec)
    return _out(parsers.get(name), row, used)


@router.delete("/{name}", status_code=204)
async def delete_parser(name: str, db: DB, _: Operator):
    row = await db.get(m.ParserDefinition, name)
    if row is None:
        raise HTTPException(409 if name in parsers.PARSERS else 404, "Built-in parsers can't be deleted" if name in parsers.PARSERS else "Parser not found")
    used = await parser_library.commands_using(db, name)
    if used:
        raise HTTPException(409, f"Used by {', '.join(used)}: change {'that command' if len(used) == 1 else 'those commands'} first")
    await db.delete(row)
    await db.commit()
    parser_library.unregister(name)


@router.post("/{name}/test", response_model=ParseTestOut)
async def test_parser(name: str, body: ParseTestIn, _: CurrentUser):
    """Run a parser on sample input. Problems come back in the body, not as HTTP errors,
    so the Parsers page can show them inline while the user types."""
    parser = parsers.get(name)
    if parser is None:
        raise HTTPException(404, "Parser not found")
    data = body.input.encode()
    if len(data) > MAX_TEST_BYTES:
        raise HTTPException(413, f"Test input is limited to {MAX_TEST_BYTES // (1024 * 1024)} MB")
    try:
        options = parser.configure(body.options)
    except parsers.OptionsError as exc:
        return ParseTestOut(type=parser.type, error="Fix the options above", option_errors=exc.errors)
    started = time.perf_counter()
    try:
        value = await asyncio.to_thread(run_parser, parser, options, [data])
    except parsers.ParseError as exc:
        return ParseTestOut(type=parser.type, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - a parser bug: show it rather than a bare 500
        return ParseTestOut(type=parser.type, error=f"parser error: {type(exc).__name__}: {exc}")
    elapsed = (time.perf_counter() - started) * 1000
    return ParseTestOut(type=parser.type, value=value, elapsed_ms=round(elapsed, 1))


# ---------- drafts for job outputs that didn't match their parser ----------


@proposals.get("/jobs/{job_id}/parser-proposals", response_model=list[ParserProposalOut])
async def job_proposals(job_id: str, db: DB, user: CurrentUser):
    job = await job_or_404(db, user, parse_uuid(job_id))
    rows = (await db.execute(select(m.ParserProposal).where(m.ParserProposal.job_id == job.id).order_by(m.ParserProposal.created_at))).scalars()
    return [proposal_out(p) for p in rows]


@proposals.post("/jobs/{job_id}/outputs/{name}/parser-proposal", response_model=ParserProposalOut, status_code=202)
async def request_proposal(job_id: str, name: str, db: DB, user: Operator):
    """Have the AI draft a parser for an output its parser couldn't read. It's drafted in
    the background; the result arrives as a `job.parser_proposal` event (and in the list)."""
    job = await job_or_404(db, user, parse_uuid(job_id), write=True)
    row = (await db.execute(select(m.JobOutput).where(m.JobOutput.job_id == job.id, m.JobOutput.name == name))).scalar_one_or_none()
    if row is None or not any(s.name == name for s in await _specs(db, job)):
        raise HTTPException(404, "The job has no such output")
    if not is_mismatch(row.error):
        raise HTTPException(409, "That output was read (or had nothing to read); there's nothing for a new parser to fix")
    if not await llm.enabled(db):
        raise HTTPException(409, "No AI model is set up (Settings → AI)")
    task = await db.get(m.Task, job.task_id)
    proposal = m.ParserProposal(job_id=job.id, project_id=task.project_id, output_name=name, failed_parser=row.parser,
                                failure=row.error or "", status="drafting", requested_by=user.id)
    db.add(proposal)
    await db.commit()
    await publish_parser_proposal(db, proposal)
    ai.schedule_parser(proposal.id)
    return proposal_out(proposal)


class OutputInputOut(BaseModel):
    text: str
    sources: list[str]
    truncated: bool
    parser: str
    type: str
    error: str | None = None


@proposals.get("/jobs/{job_id}/outputs/{name}/input", response_model=OutputInputOut)
async def output_input(job_id: str, name: str, db: DB, user: CurrentUser):
    """What an output's parser read (its files or stdout, up to 2 MB): the sample to write
    a parser against."""
    from ..outputs import read_inputs

    job = await job_or_404(db, user, parse_uuid(job_id))
    spec = next((s for s in await _specs(db, job) if s.name == name), None)
    if spec is None:
        raise HTTPException(404, "The job has no such output")
    row = (await db.execute(select(m.JobOutput).where(m.JobOutput.job_id == job.id, m.JobOutput.name == name))).scalar_one_or_none()
    try:
        blobs, sources = await read_inputs(db, job, spec)
    except parsers.ParseError as exc:
        raise HTTPException(409, str(exc)) from exc
    data = b"\n".join(blobs)
    return OutputInputOut(text=data[:MAX_TEST_BYTES].decode("utf-8", errors="replace"), sources=sources, truncated=len(data) > MAX_TEST_BYTES,
                          parser=spec.parser or "", type=spec.type or "", error=row.error if row else None)


async def _proposal(db, user, proposal_id: str, *, write: bool = False) -> m.ParserProposal:
    p = await db.get(m.ParserProposal, parse_uuid(proposal_id))
    if p is None:
        raise HTTPException(404, "Draft not found")
    await job_or_404(db, user, p.job_id, write=write)  # 404 for what the user can't see
    return p


@proposals.post("/parser-proposals/{proposal_id}/apply", response_model=ParserProposalOut)
async def apply_proposal(proposal_id: str, body: ApplyIn, db: DB, user: Operator):
    """Read the output again with the draft (for this job), or keep it in the library as
    `save_as` and read it with that. A playbook step waiting on this output carries on."""
    from ..orchestrator.dispatcher import dispatcher

    p = await _proposal(db, user, proposal_id, write=True)
    try:
        jobs = await apply_parser_proposal(db, p, user, save_as=body.save_as or None)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(409, str(exc)) from exc
    if jobs:
        dispatcher.wake()
    return proposal_out(p)


@proposals.post("/parser-proposals/{proposal_id}/dismiss", response_model=ParserProposalOut)
async def dismiss_proposal(proposal_id: str, db: DB, user: Operator):
    p = await _proposal(db, user, proposal_id, write=True)
    if p.status in ("drafting", "ready", "failed"):
        p.status = "dismissed"
        await db.commit()
        await publish_parser_proposal(db, p)
    return proposal_out(p)
