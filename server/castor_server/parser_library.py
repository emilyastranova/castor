"""Parsers in the library: definitions people and the AI wrote (parser_spec.py), kept in
step with the registry in parsers.py so commands name them like built-ins."""

from __future__ import annotations

import logging
from contextlib import contextmanager

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m
from . import parsers
from .parser_spec import ParserSpec, build

log = logging.getLogger(__name__)


def definition_from_spec(row: m.ParserDefinition, spec: ParserSpec, source_yaml: str) -> None:
    changed = row.spec != spec.model_dump(mode="json")
    row.name = spec.name
    row.description = spec.description
    row.spec = spec.model_dump(mode="json")
    row.source_yaml = source_yaml
    if changed and row.version:
        row.version += 1


def register(spec: ParserSpec) -> None:
    parsers.USER[spec.id] = build(spec)


def unregister(parser_id: str) -> None:
    parsers.USER.pop(parser_id, None)


async def load_all(db: AsyncSession) -> int:
    """Fill the registry from the database (at startup). A definition that no longer
    validates is skipped with a warning rather than stopping the server."""
    loaded: dict[str, parsers.Parser] = {}
    for row in (await db.execute(select(m.ParserDefinition))).scalars():
        try:
            loaded[row.id] = build(ParserSpec.model_validate(row.spec))
        except Exception as exc:  # noqa: BLE001
            log.warning("skipping parser %s: %s", row.id, exc)
    parsers.USER.clear()
    parsers.USER.update(loaded)
    return len(loaded)


async def commands_using(db: AsyncSession, parser_id: str) -> list[str]:
    rows = (await db.execute(select(m.CommandDefinition.id, m.CommandDefinition.spec))).all()
    return sorted(cid for cid, spec in rows if any(o.get("parser") == parser_id for o in (spec or {}).get("outputs", [])))


@contextmanager
def overlay(specs: list[ParserSpec]):
    """Make parsers from an import file known while its commands are checked, without
    adding them to the library."""
    added = [s.id for s in specs if s.id not in parsers.USER]
    for s in specs:
        if s.id not in parsers.USER:
            parsers.USER[s.id] = build(s)
    try:
        yield
    finally:
        for pid in added:
            parsers.USER.pop(pid, None)
