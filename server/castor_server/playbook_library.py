"""Playbook library: syncing built-in playbooks and loading playbook specs."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m
from .config import settings
from .playbook_spec import PlaybookSpec, parse_playbook_spec

log = logging.getLogger(__name__)


def builtin_dir() -> Path:
    return Path(settings.playbooks_dir)


def definition_from_spec(row: m.PlaybookDefinition, spec: PlaybookSpec, source_yaml: str) -> None:
    changed = row.spec != spec.model_dump(mode="json")
    row.name = spec.name
    row.description = spec.description
    row.tags = spec.tags
    row.spec = spec.model_dump(mode="json")
    row.source_yaml = source_yaml
    if changed and row.version:
        row.version += 1


async def sync_builtin_playbooks(db: AsyncSession) -> int:
    """Upsert every playbooks/*.yaml as a built-in playbook definition. Returns how many were loaded."""
    directory = builtin_dir()
    if not directory.is_dir():
        log.info("no built-in playbook directory at %s", directory)
        return 0
    loaded = 0
    for path in sorted(directory.glob("*.y*ml")):
        text = path.read_text()
        try:
            spec = parse_playbook_spec(text)
        except ValueError as exc:
            log.warning("skipping built-in playbook %s: %s", path.name, exc)
            continue
        row = await db.get(m.PlaybookDefinition, spec.id)
        if row is not None and row.origin != "builtin":
            log.warning("built-in playbook %s shadowed by a user definition", spec.id)
            continue
        if row is None:
            row = m.PlaybookDefinition(id=spec.id, origin="builtin", version=1)
            db.add(row)
        definition_from_spec(row, spec, text)
        loaded += 1
    await db.commit()
    return loaded


async def load_playbook_spec(db: AsyncSession, playbook_id: str) -> PlaybookSpec | None:
    row = await db.get(m.PlaybookDefinition, playbook_id)
    return PlaybookSpec.model_validate(row.spec) if row is not None else None
