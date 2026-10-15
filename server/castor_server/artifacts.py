"""Per-job artifact upload tokens and which files an agent should collect."""

from __future__ import annotations

import hashlib
import hmac
import secrets

from .command_spec import CommandSpec

# Agents always collect anything a job writes under /work/artifacts/, in addition to the
# files a command definition declares as outputs.
DEFAULT_COLLECT = ["/work/artifacts/**"]


def new_token() -> tuple[str, str]:
    """Return (token for the agent, hash to store on the job)."""
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def token_matches(token: str | None, stored_hash: str | None) -> bool:
    if not token or not stored_hash:
        return False
    return hmac.compare_digest(hash_token(token), stored_hash)


def collect_patterns(spec: CommandSpec | None) -> list[str]:
    patterns = [o.source for o in spec.outputs if o.source] if spec else []
    return [*dict.fromkeys(patterns + DEFAULT_COLLECT)]
