"""Theme library: built-ins from themes/*.yaml, custom themes from Settings → Themes."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m
from .config import settings
from .theme_spec import ThemeSpec, parse_theme_spec

log = logging.getLogger(__name__)


def definition_from_spec(row: m.ThemeDefinition, spec: ThemeSpec, source_yaml: str) -> None:
    changed = row.spec != spec.model_dump(mode="json")
    row.name = spec.name
    row.description = spec.description
    row.spec = spec.model_dump(mode="json")
    row.source_yaml = source_yaml
    if changed and row.version:
        row.version += 1


async def forget_theme(db: AsyncSession, theme_id: str) -> None:
    """People who had this theme go back to the instance default; if it was the default,
    the default goes back to Castor's own."""
    await db.execute(update(m.User).where(m.User.theme == theme_id).values(theme=None))
    row = await db.get(m.AppSetting, "default_theme")
    if row is not None and row.value == theme_id:
        row.value = ""


async def sync_builtin_themes(db: AsyncSession) -> int:
    directory = Path(settings.themes_dir)
    if not directory.is_dir():
        log.info("no built-in theme directory at %s", directory)
        return 0
    loaded, seen, complete = 0, set(), True
    for path in sorted(directory.glob("*.y*ml")):
        text = path.read_text()
        try:
            spec = parse_theme_spec(text)
        except ValueError as exc:
            log.warning("skipping built-in theme %s: %s", path.name, exc)
            complete = False
            continue
        seen.add(spec.id)
        row = await db.get(m.ThemeDefinition, spec.id)
        if row is not None and row.origin != "builtin":
            log.warning("built-in theme %s shadowed by a custom one", spec.id)
            continue
        if row is None:
            row = m.ThemeDefinition(id=spec.id, origin="builtin", version=1)
            db.add(row)
        definition_from_spec(row, spec, text)
        loaded += 1
    if complete:  # a file that didn't parse might be one of these; retire nothing then
        gone = (await db.execute(select(m.ThemeDefinition).where(
            m.ThemeDefinition.origin == "builtin", m.ThemeDefinition.id.not_in(seen)))).scalars().all()
        for row in gone:
            await forget_theme(db, row.id)
            await db.delete(row)
            log.info("retired built-in theme %s (its file is gone)", row.id)
    await db.commit()
    return loaded
