"""Async SQLAlchemy engine and session factory."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy import event, inspect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings


class Base(DeclarativeBase):
    pass


def _ensure_sqlite_dir(url: str) -> None:
    if url.startswith("sqlite") and ":memory:" not in url:
        path = url.split("///", 1)[-1]
        Path(path).parent.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_dir(settings.database_url)

engine = create_async_engine(settings.database_url, echo=False, future=True)

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
    probe = create_async_engine(url)
    try:
        async with probe.connect() as conn:
            tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
    finally:
        await probe.dispose()

    cfg = alembic_config(url)
    # Alembic's env.py drives its own event loop, so run commands off this one.
    if tables and "alembic_version" not in tables:
        log.info("existing database has no migration history; stamping %s", BASELINE_REVISION)
        await asyncio.to_thread(command.stamp, cfg, BASELINE_REVISION)
    await asyncio.to_thread(command.upgrade, cfg, "head")
