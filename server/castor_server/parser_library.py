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


def point_output_at(text: str, output: str, parser_id: str) -> str:
    """A command's YAML with output `output` read by `parser_id`: its `parser:` line set (or
    added), and the old parser's `options:` and `type:` dropped. Edits the lines in place so
    comments and layout stay; falls back to rewriting the YAML for unusual layouts."""
    import re

    import yaml

    lines = text.splitlines()
    # Only inside the top-level `outputs:` block (arguments are `- name:` items too).
    start = next((n for n, line in enumerate(lines) if re.match(r"^outputs:\s*(#.*)?$", line)), None)
    stop = len(lines)
    if start is not None:
        stop = next((n for n in range(start + 1, len(lines)) if lines[n][:1] not in ("", " ", "-", "#")), len(lines))
    for i, line in enumerate(lines):
        if start is None or not start < i < stop:
            continue
        m = re.match(r"^(\s*)-\s+name:\s*['\"]?" + re.escape(output) + r"['\"]?\s*(#.*)?$", line)
        if not m:
            continue
        dash = len(m.group(1))
        key_indent = " " * (dash + 2)
        end = i + 1
        while end < len(lines) and (not lines[end].strip() or len(lines[end]) - len(lines[end].lstrip()) > dash):
            end += 1
        body = lines[i + 1 : end]
        kept: list[str] = []
        skipping = None
        for row in body:
            indent = len(row) - len(row.lstrip())
            if skipping is not None and row.strip() and indent > skipping:
                continue
            skipping = None
            key = row.strip().split(":", 1)[0]
            if key in ("options", "type") and indent == dash + 2:
                skipping = indent
                continue
            if key == "parser" and indent == dash + 2:
                continue
            kept.append(row)
        lines[i + 1 : end] = [f"{key_indent}parser: {parser_id}", *kept]
        return "\n".join(lines) + "\n"
    raw = yaml.safe_load(text)
    for o in raw.get("outputs") or []:
        if isinstance(o, dict) and o.get("name") == output:
            o["parser"] = parser_id
            o.pop("options", None)
            o.pop("type", None)
            return yaml.safe_dump(raw, sort_keys=False)
    raise ValueError(f"the command has no output {output}")
