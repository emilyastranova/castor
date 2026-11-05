"""Per-agent tokens: issuing, checking and binding them to agents.

A token is `castor_` plus 32 random bytes (URL-safe base64). The server keeps only its
SHA-256, so a token is shown once, when it's created or rotated."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m
from .config import settings
from .models import utcnow

PREFIX = "castor_"


def new_token() -> tuple[str, str, str]:
    """(token, its hash, the prefix shown in the UI)."""
    token = PREFIX + secrets.token_urlsafe(32)
    return token, hash_token(token), token[: len(PREFIX) + 6]


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class Auth:
    """Who a hello authenticated as: a token (maybe not bound yet), or the shared secret."""

    ok: bool
    reason: str = ""
    token: m.AgentToken | None = None


async def authenticate(db: AsyncSession, hello: dict) -> Auth:
    token = hello.get("token")
    if isinstance(token, str) and token:
        row = (
            await db.execute(select(m.AgentToken).where(m.AgentToken.token_hash == hash_token(token)))
        ).scalar_one_or_none()
        if row is None:
            return Auth(False, "unknown token (it may have been rotated)")
        if row.revoked_at is not None:
            return Auth(False, "token revoked")
        return Auth(True, token=row)
    secret = hello.get("secret")
    if not isinstance(secret, str) or not secret:
        return Auth(False, "no token or secret")
    if not settings.allow_shared_agent_secret or not settings.agent_secret:
        return Auth(False, "this server only accepts agent tokens")
    if not hmac.compare_digest(secret.encode(), settings.agent_secret.encode()):
        return Auth(False, "bad secret")
    return Auth(True)


async def has_token(db: AsyncSession, agent_id: uuid.UUID) -> bool:
    """Whether the agent was ever given a token. Such an agent can't connect with the shared
    secret, even after its token is revoked (that would undo the revoke); forgetting the
    agent deletes its tokens and lifts this."""
    found = await db.execute(select(m.AgentToken.id).where(m.AgentToken.agent_id == agent_id).limit(1))
    return found.first() is not None


def touch(row: m.AgentToken) -> None:
    row.last_used_at = utcnow()
