"""Alert channels (Phase 3k): admins set them up; everyone can pick them for a card or run."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .. import alerts as A
from .. import models as m
from ..deps import DB, Admin, CurrentUser, parse_uuid
from ..events import bus
from ..services import actor_of, record_activity

router = APIRouter(tags=["alerts"])


class ChannelIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str
    enabled: bool = True
    # Field values; secret ones left blank keep what's stored.
    fields: dict[str, Any] = Field(default_factory=dict)
    events: list[str] = Field(default_factory=list)
    long_minutes: int | None = Field(default=None, ge=1, le=7 * 24 * 60)


class DeliveryOut(BaseModel):
    id: uuid.UUID
    event: str
    title: str
    ok: bool
    status: int | None
    error: str
    created_at: datetime


class ChannelOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    enabled: bool
    fields: dict[str, str]
    # Which secret fields have a value (the values never leave the server).
    secrets: dict[str, bool]
    events: list[str]
    long_minutes: int | None
    last: DeliveryOut | None = None
    failures_24h: int = 0


class ChannelBrief(BaseModel):
    id: uuid.UUID
    name: str
    kind: str


class TestOut(BaseModel):
    ok: bool
    status: int | None
    error: str


def _check(body: ChannelIn) -> None:
    unknown = set(body.events) - set(A.EVENTS)
    if unknown:
        raise HTTPException(422, f"events: unknown {', '.join(sorted(unknown))}")
    if "long" in body.events and not body.long_minutes:
        raise HTTPException(422, "long_minutes: say after how many minutes")


async def _out(db, ch: m.AlertChannel) -> ChannelOut:
    preset = A.PRESETS.get(ch.kind)
    last = (await db.execute(select(m.AlertDelivery).where(m.AlertDelivery.channel_id == ch.id)
                             .order_by(m.AlertDelivery.created_at.desc()).limit(1))).scalar_one_or_none()
    failures = (await db.execute(select(func.count()).where(
        m.AlertDelivery.channel_id == ch.id, m.AlertDelivery.ok.is_(False),
        m.AlertDelivery.created_at >= m.utcnow() - timedelta(days=1)))).scalar_one() if last else 0
    return ChannelOut(
        id=ch.id, name=ch.name, kind=ch.kind, enabled=ch.enabled, fields=ch.fields or {},
        secrets={f.name: f.name in (ch.secrets or {}) for f in (preset.fields if preset else []) if f.secret},
        events=ch.events or [], long_minutes=ch.long_minutes,
        last=DeliveryOut.model_validate(last, from_attributes=True) if last else None, failures_24h=failures,
    )


async def _channel(db, channel_id: str) -> m.AlertChannel:
    ch = await db.get(m.AlertChannel, parse_uuid(channel_id))
    if ch is None:
        raise HTTPException(404, "Channel not found")
    return ch


@router.get("/alert-presets")
async def presets(_: CurrentUser):
    return {"presets": A.presets_out(), "events": A.EVENTS, "variables": A.VARIABLES}


@router.get("/alert-channels", response_model=list[ChannelOut])
async def list_channels(db: DB, _: Admin):
    rows = (await db.execute(select(m.AlertChannel).order_by(m.AlertChannel.name))).scalars().all()
    return [await _out(db, r) for r in rows]


@router.get("/alert-channels/brief", response_model=list[ChannelBrief])
async def brief(db: DB, _: CurrentUser):
    """Names to pick from on a card or run (no settings)."""
    rows = (await db.execute(select(m.AlertChannel).where(m.AlertChannel.enabled.is_(True)).order_by(m.AlertChannel.name))).scalars()
    return [ChannelBrief(id=r.id, name=r.name, kind=r.kind) for r in rows]


@router.post("/alert-channels", response_model=ChannelOut, status_code=201)
async def create(body: ChannelIn, db: DB, user: Admin):
    _check(body)
    try:
        plain, secret = A.split_fields(body.kind, body.fields)
    except A.AlertError as exc:
        raise HTTPException(422, str(exc)) from exc
    ch = m.AlertChannel(name=body.name.strip(), kind=body.kind, enabled=body.enabled, fields=plain, secrets=secret,
                        events=body.events, long_minutes=body.long_minutes, created_by=user.id)
    db.add(ch)
    await db.flush()
    await record_activity(db, type="alerts.channel", message=f"Added the alert channel {ch.name}", project_id=None, **actor_of(user))
    await db.commit()
    bus.publish("alert_channels.changed", None, {})
    return await _out(db, ch)


@router.put("/alert-channels/{channel_id}", response_model=ChannelOut)
async def replace(channel_id: str, body: ChannelIn, db: DB, user: Admin):
    ch = await _channel(db, channel_id)
    _check(body)
    try:
        plain, secret = A.split_fields(body.kind, body.fields, ch if body.kind == ch.kind else None)
    except A.AlertError as exc:
        raise HTTPException(422, str(exc)) from exc
    ch.name, ch.kind, ch.enabled, ch.fields, ch.secrets = body.name.strip(), body.kind, body.enabled, plain, secret
    ch.events, ch.long_minutes = body.events, body.long_minutes
    await db.commit()
    bus.publish("alert_channels.changed", None, {})
    return await _out(db, ch)


@router.delete("/alert-channels/{channel_id}", status_code=204)
async def delete(channel_id: str, db: DB, user: Admin):
    ch = await _channel(db, channel_id)
    await record_activity(db, type="alerts.channel", message=f"Removed the alert channel {ch.name}", project_id=None, **actor_of(user))
    await db.delete(ch)
    await db.commit()
    bus.publish("alert_channels.changed", None, {})


@router.post("/alert-channels/{channel_id}/test", response_model=TestOut)
async def test_saved(channel_id: str, db: DB, _: Admin):
    ch = await _channel(db, channel_id)
    ok, status, error = await A.send_now(ch, A.sample_values())
    db.add(m.AlertDelivery(channel_id=ch.id, event="test", title="Test", ok=ok, status=status, error=error))
    await db.commit()
    return TestOut(ok=ok, status=status, error=error)


class TestIn(ChannelIn):
    # Editing an existing channel: its stored secrets fill blank secret fields.
    id: uuid.UUID | None = None


@router.post("/alert-channels/test", response_model=TestOut)
async def test_unsaved(body: TestIn, db: DB, _: Admin):
    """Try settings before saving them."""
    old = await db.get(m.AlertChannel, body.id) if body.id else None
    try:
        plain, secret = A.split_fields(body.kind, body.fields, old if old and old.kind == body.kind else None)
    except A.AlertError as exc:
        return TestOut(ok=False, status=None, error=str(exc))
    probe = m.AlertChannel(kind=body.kind, fields=plain, secrets=secret)
    ok, status, error = await A.send_now(probe, A.sample_values())
    return TestOut(ok=ok, status=status, error=error)


@router.get("/alert-channels/{channel_id}/deliveries", response_model=list[DeliveryOut])
async def deliveries(channel_id: str, db: DB, _: Admin, limit: int = 30):
    ch = await _channel(db, channel_id)
    rows = (await db.execute(select(m.AlertDelivery).where(m.AlertDelivery.channel_id == ch.id)
                             .order_by(m.AlertDelivery.created_at.desc()).limit(max(1, min(limit, 200))))).scalars()
    return [DeliveryOut.model_validate(r, from_attributes=True) for r in rows]

