"""AI (Phase 3e): providers and their test, usage, and the things people ask it to do."""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import ai, llm
from .. import models as m
from ..access import job_or_404
from ..deps import DB, Admin, CurrentUser, Operator, parse_uuid
from ..services import actor_of, record_activity

router = APIRouter(prefix="/ai", tags=["ai"])


def _ai_errors(exc: Exception) -> HTTPException:
    if isinstance(exc, llm.NotConfigured):
        return HTTPException(409, str(exc))
    return HTTPException(502, str(exc))


class ProviderIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str
    enabled: bool = True
    fields: dict[str, str] = Field(default_factory=dict)


class ProviderOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    enabled: bool
    fields: dict[str, str]
    secrets: dict[str, bool]


def _out(p: m.AiProvider) -> ProviderOut:
    spec = llm.KINDS.get(p.kind)
    return ProviderOut(id=p.id, name=p.name, kind=p.kind, enabled=p.enabled, fields=p.fields or {},
                       secrets={f.name: f.name in (p.secrets or {}) for f in (spec.fields if spec else []) if f.secret})


async def _provider(db, provider_id: str) -> m.AiProvider:
    p = await db.get(m.AiProvider, parse_uuid(provider_id))
    if p is None:
        raise HTTPException(404, "Provider not found")
    return p


@router.get("/status")
async def status(db: DB, _: CurrentUser):
    """Whether AI features are available (the UI shows its buttons only then)."""
    return {"enabled": await llm.enabled(db, "default"), "resolver": await llm.enabled(db, "resolver")}


@router.get("/kinds")
async def kinds(_: Admin):
    return llm.kinds_out()


@router.get("/providers", response_model=list[ProviderOut])
async def providers(db: DB, _: Admin):
    rows = (await db.execute(select(m.AiProvider).order_by(m.AiProvider.name))).scalars()
    return [_out(p) for p in rows]


@router.post("/providers", response_model=ProviderOut, status_code=201)
async def create_provider(body: ProviderIn, db: DB, user: Admin):
    try:
        plain, secret = llm.split_fields(body.kind, body.fields)
    except llm.AiError as exc:
        raise HTTPException(422, str(exc)) from exc
    p = m.AiProvider(name=body.name.strip(), kind=body.kind, enabled=body.enabled, fields=plain, secrets=secret, created_by=user.id)
    db.add(p)
    await db.flush()
    await record_activity(db, type="ai.provider", message=f"Added the AI provider {p.name}", project_id=None, **actor_of(user))
    await db.commit()
    return _out(p)


@router.put("/providers/{provider_id}", response_model=ProviderOut)
async def update_provider(provider_id: str, body: ProviderIn, db: DB, _: Admin):
    p = await _provider(db, provider_id)
    try:
        plain, secret = llm.split_fields(body.kind, body.fields, p if p.kind == body.kind else None)
    except llm.AiError as exc:
        raise HTTPException(422, str(exc)) from exc
    p.name, p.kind, p.enabled, p.fields, p.secrets = body.name.strip(), body.kind, body.enabled, plain, secret
    await db.commit()
    return _out(p)


@router.delete("/providers/{provider_id}", status_code=204)
async def delete_provider(provider_id: str, db: DB, user: Admin):
    p = await _provider(db, provider_id)
    await record_activity(db, type="ai.provider", message=f"Removed the AI provider {p.name}", project_id=None, **actor_of(user))
    await db.delete(p)
    await db.commit()


class TryIn(ProviderIn):
    model: str = Field(min_length=1, max_length=200)
    # Editing a saved provider: its stored secrets fill blank fields.
    id: uuid.UUID | None = None


@router.post("/providers/test")
async def test_provider(body: TryIn, db: DB, _: Admin):
    """One short request with these settings (saved or not)."""
    old = await db.get(m.AiProvider, body.id) if body.id else None
    try:
        plain, secret = llm.split_fields(body.kind, body.fields, old if old and old.kind == body.kind else None)
    except llm.AiError as exc:
        return {"ok": False, "error": str(exc)}
    probe = m.AiProvider(name=body.name or body.kind, kind=body.kind, fields=plain, secrets=secret)
    started = time.monotonic()
    try:
        answer = await llm.call(probe, body.model, "Reply with exactly: OK", [{"role": "user", "content": "Say OK."}], max_tokens=20)
    except llm.AiError as exc:
        return {"ok": False, "error": str(exc)}
    if not answer.text.strip():
        return {"ok": False, "error": f"{probe.name}: the model answered with nothing"}
    return {"ok": True, "text": answer.text.strip()[:200], "ms": int((time.monotonic() - started) * 1000)}


class ModelsIn(ProviderIn):
    id: uuid.UUID | None = None


@router.post("/providers/models")
async def provider_models(body: ModelsIn, db: DB, _: Admin):
    """The models the provider offers (or the usual ones, where it can't say)."""
    old = await db.get(m.AiProvider, body.id) if body.id else None
    try:
        plain, secret = llm.split_fields(body.kind, body.fields, old if old and old.kind == body.kind else None)
        return {"models": await llm.list_models(m.AiProvider(name=body.name or body.kind, kind=body.kind, fields=plain, secrets=secret))}
    except llm.AiError as exc:
        return {"models": list(llm.KINDS[body.kind].models) if body.kind in llm.KINDS else [], "error": str(exc)}


class CallOut(BaseModel):
    id: uuid.UUID
    purpose: str
    provider_name: str
    model: str
    ok: bool
    error: str
    input_tokens: int | None
    output_tokens: int | None
    ms: int
    created_at: datetime


@router.get("/usage")
async def usage(db: DB, _: Admin):
    calls = await ai.usage(db)
    return {"calls": [CallOut.model_validate(c, from_attributes=True) for c in calls], **(await ai.totals(db))}


class GenerateIn(BaseModel):
    kind: Literal["command", "tool", "playbook", "variable_kind"]
    prompt: str = Field(min_length=3, max_length=4000)
    # The definition being edited, to change rather than start over.
    current: str = Field(default="", max_length=60000)


@router.post("/generate")
async def generate(body: GenerateIn, db: DB, user: Operator):
    """A draft library item from a description, checked like saving it (not saved)."""
    try:
        return await ai.generate(db, user, body.kind, body.prompt, body.current)
    except llm.AiError as exc:
        raise _ai_errors(exc) from exc


class RegexIn(BaseModel):
    sample: str = Field(min_length=1, max_length=200_000)
    want: str = Field(min_length=2, max_length=1000)


@router.post("/regex")
async def regex(body: RegexIn, db: DB, user: CurrentUser):
    """Regex parser options that pull `want` out of the sample, tried on it."""
    try:
        return await ai.suggest_regex(db, user, body.sample, body.want)
    except llm.AiError as exc:
        raise _ai_errors(exc) from exc


@router.post("/explain/{job_id}")
async def explain(job_id: str, db: DB, user: CurrentUser):
    """Why a run failed and what to try."""
    job = await job_or_404(db, user, parse_uuid(job_id))
    task = await db.get(m.Task, job.task_id)
    try:
        return await ai.explain_job(db, user, job, task)
    except llm.AiError as exc:
        raise _ai_errors(exc) from exc
