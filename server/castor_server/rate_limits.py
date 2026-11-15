"""Rate limits on HTTP actions (docs/ACTIONS.md): per API credential (the variable set that
holds the token), and per card (or the definition's suggested limit). A request needs room
under every limit that applies; without it the runner waits (or parks the job, if the wait is
long), so a limit never fails a request by itself.

Each limit is a token bucket kept in `rate_buckets`, so a restart doesn't hand out a fresh
burst; "N at once" is counted in memory (requests in flight end with the process)."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m
from .db import SessionLocal
from .http_spec import RateLimit, parse_rate


@dataclass
class Scope:
    key: str  # "set:<id>", "task:<id>"
    label: str  # for the log: "the Jira (prod) rate limit"
    rate: RateLimit
    # Pausing for Retry-After applies to the credential (its key is what the API counts).
    credential: bool = False


@dataclass
class _Bucket:
    tokens: float
    updated: float
    paused_until: float = 0.0
    in_flight: int = 0
    loaded: bool = field(default=False, repr=False)


class Limiter:
    def __init__(self) -> None:
        self._buckets: dict[str, _Bucket] = {}
        self._lock = asyncio.Lock()

    async def _bucket(self, scope: Scope) -> _Bucket:
        bucket = self._buckets.get(scope.key)
        if bucket is None:
            now = time.time()
            async with SessionLocal() as db:
                row = await db.get(m.RateBucket, scope.key)
            if row is not None:
                bucket = _Bucket(tokens=row.tokens, updated=row.updated_at.timestamp(),
                                 paused_until=row.paused_until.timestamp() if row.paused_until else 0.0)
            else:
                bucket = _Bucket(tokens=float(scope.rate.count), updated=now)
            self._buckets[scope.key] = bucket
        # Refill for the time since; a lowered limit caps what's saved up.
        now = time.time()
        rate = scope.rate.count / scope.rate.period_s
        bucket.tokens = min(float(scope.rate.count), bucket.tokens + max(now - bucket.updated, 0) * rate)
        bucket.updated = now
        return bucket

    async def take(self, scopes: list[Scope]) -> tuple[float, Scope | None]:
        """Take room for one request under every scope: (0, None) when taken, else (seconds
        to wait, the scope that's full) and nothing is taken."""
        if not scopes:
            return 0.0, None
        async with self._lock:
            now = time.time()
            wait, full = 0.0, None
            buckets = []
            for scope in scopes:
                bucket = await self._bucket(scope)
                buckets.append(bucket)
                need = 0.0
                if bucket.paused_until > now:
                    need = bucket.paused_until - now
                elif bucket.tokens < 1:
                    need = (1 - bucket.tokens) / (scope.rate.count / scope.rate.period_s)
                elif scope.rate.concurrent and bucket.in_flight >= scope.rate.concurrent:
                    need = 0.25
                if need > wait:
                    wait, full = need, scope
            if wait > 0:
                return wait, full
            for bucket in buckets:
                bucket.tokens -= 1
                bucket.in_flight += 1
        await self._save(scopes)
        return 0.0, None

    def release(self, scopes: list[Scope]) -> None:
        for scope in scopes:
            bucket = self._buckets.get(scope.key)
            if bucket is not None and bucket.in_flight > 0:
                bucket.in_flight -= 1

    async def pause(self, scopes: list[Scope], seconds: float) -> None:
        """The API said to wait (429/503 Retry-After, or no requests left until a reset):
        nothing under these scopes is sent until then."""
        if not scopes or seconds <= 0:
            return
        async with self._lock:
            until = time.time() + seconds
            for scope in scopes:
                bucket = await self._bucket(scope)
                bucket.paused_until = max(bucket.paused_until, until)
        await self._save(scopes)

    async def _save(self, scopes: list[Scope]) -> None:
        async with SessionLocal() as db:
            for scope in scopes:
                bucket = self._buckets.get(scope.key)
                if bucket is None:
                    continue
                row = await db.get(m.RateBucket, scope.key)
                if row is None:
                    row = m.RateBucket(key=scope.key)
                    db.add(row)
                row.tokens = bucket.tokens
                row.updated_at = datetime.fromtimestamp(bucket.updated, UTC)
                row.paused_until = datetime.fromtimestamp(bucket.paused_until, UTC) if bucket.paused_until else None
            await db.commit()

    def forget(self, key: str) -> None:
        self._buckets.pop(key, None)


limiter = Limiter()


async def scopes_for(db: AsyncSession, task: m.Task, bindings: dict[str, Any], suggested: str | None,
                     plugin_id: str | None = None) -> list[Scope]:
    """The limits a card's requests come under: each bound variable set's, and the card's
    own (or else the definition's suggested one)."""
    out: list[Scope] = []
    for slot, set_id in sorted((bindings or {}).items()):
        try:
            row = await db.get(m.VariableSet, uuid.UUID(str(set_id)))
        except ValueError:
            row = None
        rate = _rate(row.rate_limit) if row is not None else None
        if rate is not None:
            out.append(Scope(f"set:{row.id}", f"the {row.name} rate limit", rate, credential=True))
    rate = _rate(task.rate_limit) or _rate(suggested)
    if rate is not None:
        out.append(Scope(f"task:{task.id}", "this card's rate limit", rate))
    if plugin_id:
        row = await db.get(m.Plugin, plugin_id)
        rate = _rate(row.actions_rate_limit) if row is not None else None
        if rate is not None:
            out.append(Scope(f"plugin:{plugin_id}", f"the {row.name} plugin's rate limit", rate))
    return out


def _rate(text: str | None) -> RateLimit | None:
    try:
        return parse_rate(text)
    except ValueError:
        return None
