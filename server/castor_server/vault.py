"""Encryption at rest for variable-set secrets and files (Fernet: AES-128-CBC + HMAC).

The key comes from CASTOR_VAULT_KEY, or is generated once into <data_dir>/vault.key with
owner-only permissions. It is deliberately separate from CASTOR_SECRET_KEY so rotating the
session key doesn't lose the vault.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from .config import settings

log = logging.getLogger(__name__)


class VaultError(Exception):
    pass


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    if settings.vault_key:
        key = settings.vault_key.encode()
    else:
        path = settings.data_dir / "vault.key"
        if path.exists():
            key = path.read_bytes().strip()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            key = Fernet.generate_key()
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(key + b"\n")
            log.warning("generated a new vault key at %s; back it up with the database", path)
    try:
        return Fernet(key)
    except ValueError as exc:
        raise VaultError("CASTOR_VAULT_KEY is not a valid Fernet key") from exc


def encrypt(data: bytes) -> str:
    return _fernet().encrypt(data).decode()


def decrypt(token: str) -> bytes:
    try:
        return _fernet().decrypt(token.encode())
    except InvalidToken as exc:
        raise VaultError("cannot decrypt a stored secret (was the vault key changed?)") from exc
