"""Async SQLAlchemy engine and session factory."""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy import event, inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from .config import settings


class Base(DeclarativeBase):
    pass


def _ensure_sqlite_dir(url: str) -> None:
    if url.startswith("sqlite") and ":memory:" not in url:
        path = url.split("///", 1)[-1]
        Path(path).parent.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_dir(settings.database_url)

# SQLite: a fresh connection per session (NullPool). Opening a local file is cheap, and pooled
# aiosqlite connections are tied to the event loop that created them, which breaks when work
# runs on more than one loop (e.g. the test client's WebSocket sessions).
_pool = {"poolclass": NullPool} if settings.database_url.startswith("sqlite") else {}
engine = create_async_engine(settings.database_url, echo=False, future=True, **_pool)

if settings.database_url.startswith("sqlite"):
    # SQLite only enforces foreign keys (and so ON DELETE CASCADE) when asked, per connection.
    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_foreign_keys(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
# Databases created before Castor used Alembic (tables built with create_all) match this
# revision; they are stamped with it and then upgraded normally.
BASELINE_REVISION = "c6680e13126d"


def alembic_config(url: str):
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


async def run_migrations(url: str | None = None) -> None:
    """Bring the database schema up to date (creating it if the database is empty)."""
    from alembic import command

    url = url or settings.database_url
    _ensure_sqlite_dir(url)
    # This engine has no foreign-key pragma, so SQLite table rebuilds can't cascade-delete.
    probe = create_async_engine(url)
    try:
        async with probe.connect() as conn:
            tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
        if "alembic_version" in tables:
            await _repair_orphaned_revision(probe, url)
    finally:
        await probe.dispose()

    cfg = alembic_config(url)
    # Alembic's env.py drives its own event loop, so run commands off this one.
    if tables and "alembic_version" not in tables:
        log.info("existing database has no migration history; stamping %s", BASELINE_REVISION)
        await asyncio.to_thread(command.stamp, cfg, BASELINE_REVISION)
    await asyncio.to_thread(command.upgrade, cfg, "head")


async def _repair_orphaned_revision(probe, url: str) -> None:
    """A database stamped with a revision that is no longer in versions/ (a draft that was
    replaced) can't be upgraded; reshape it to a known revision first (migrations/repairs.py)."""
    from .migrations.repairs import REPAIRS

    async with probe.connect() as conn:
        current = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()
    if current not in REPAIRS:
        return
    repair, target = REPAIRS[current]
    if url.startswith("sqlite"):
        path = Path(url.split("///", 1)[-1])
        backup = path.parent / "backups" / f"{path.stem}-before-repair-{current}{path.suffix}"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
        log.warning("database is at orphaned revision %s; backed up to %s", current, backup)
    async with probe.begin() as conn:
        await conn.run_sync(repair)
        await conn.execute(text("UPDATE alembic_version SET version_num = :v"), {"v": target})
    log.warning("repaired orphaned revision %s; schema now matches %s", current, target)
