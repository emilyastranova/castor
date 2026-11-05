"""Startup migrations, including the repair of databases at an orphaned draft revision."""

import asyncio
import json
import sqlite3
import tempfile
from pathlib import Path

from alembic import command
from alembic.script import ScriptDirectory

from castor_server.db import alembic_config, run_migrations


def test_repairs_draft_container_flags_revision():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "castor.db"
        url = f"sqlite+aiosqlite:///{path}"
        # A database migrated with the draft of a81d4e2c901f: privileged + podman_flags.
        command.upgrade(alembic_config(url), "c11afeff1468")
        db = sqlite3.connect(path)
        for table in ("tasks", "jobs"):
            db.execute(f"ALTER TABLE {table} ADD COLUMN privileged BOOLEAN DEFAULT 0 NOT NULL")
            db.execute(f"ALTER TABLE {table} ADD COLUMN podman_flags JSON DEFAULT '[]' NOT NULL")
        # sqlite3 doesn't enforce foreign keys by default, so the card needs no parents.
        db.execute(
            "INSERT INTO tasks (id, project_id, column_id, position, title, description, kind, command, "
            "agent_selector, run_on_move, priority, tags, assignees, custom_fields, created_at, updated_at, "
            "command_args, privileged, podman_flags) VALUES ('t1', 'p1', 'c1', 0, 'T', '', 'command', 'id', "
            "'{}', 1, 0, '[]', '[]', '{}', '2026-01-01', '2026-01-01', '{}', 1, '[\"--network\", \"bridge\"]')"
        )
        db.execute("UPDATE alembic_version SET version_num = 'bc5b524cc990'")
        db.commit()
        db.close()

        asyncio.run(run_migrations(url))

        db = sqlite3.connect(path)
        head = db.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        assert head == ScriptDirectory.from_config(alembic_config(url)).get_current_head()
        columns = {r[1] for r in db.execute("PRAGMA table_info(tasks)")}
        assert "container_flags" in columns and not {"privileged", "podman_flags"} & columns
        flags = db.execute("SELECT container_flags FROM tasks WHERE id = 't1'").fetchone()[0]
        assert json.loads(flags) == ["--privileged", "--network", "bridge"]
        assert (Path(tmp) / "backups" / "castor-before-repair-bc5b524cc990.db").exists()


def test_migrations_match_the_models():
    """`alembic check`: a fresh database at head has exactly the models' schema."""
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import create_engine

    from castor_server.models import Base

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "castor.db"
        command.upgrade(alembic_config(f"sqlite+aiosqlite:///{path}"), "head")
        engine = create_engine(f"sqlite:///{path}")
        with engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
        engine.dispose()
        assert diff == []


def test_playbook_cards_survive_the_fk_change_and_now_cascade():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "castor.db"
        url = f"sqlite+aiosqlite:///{path}"
        command.upgrade(alembic_config(url), "b7d3e91f4a20")
        db = sqlite3.connect(path)
        db.execute(
            "INSERT INTO playbook_runs (id, playbook_id, playbook_name, project_id, status, inputs, step_task_ids, "
            "step_results, spec_snapshot, created_at, updated_at) VALUES ('r1', 'pb', 'PB', 'p1', 'running', '{}', "
            "'{}', '{}', '{}', '2026-01-01', '2026-01-01')"
        )
        db.execute(
            "INSERT INTO tasks (id, project_id, column_id, position, title, description, kind, command, "
            "agent_selector, run_on_move, priority, tags, assignees, custom_fields, created_at, updated_at, "
            "command_args, playbook_run_id) VALUES ('t1', 'p1', 'c1', 0, 'T', '', 'command', 'id', '{}', 1, 0, "
            "'[]', '[]', '{}', '2026-01-01', '2026-01-01', '{}', 'r1')"
        )
        db.commit()
        db.close()

        command.upgrade(alembic_config(url), "head")

        db = sqlite3.connect(path)
        assert db.execute("SELECT playbook_run_id FROM tasks WHERE id = 't1'").fetchone() == ("r1",)
        fks = {r[3]: r[6] for r in db.execute("PRAGMA foreign_key_list(tasks)")}
        assert fks["playbook_run_id"] == "CASCADE"
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("DELETE FROM playbook_runs WHERE id = 'r1'")
        assert db.execute("SELECT count(*) FROM tasks WHERE id = 't1'").fetchone() == (0,)
        db.close()


def _seed_old_install(conn):
    """What an install from before roles looks like: the initial admin plus two users made
    by hand (one admin, one not), a project with a card, at the first schema revision."""
    from datetime import UTC, datetime

    import sqlalchemy as sa

    t = sa.DateTime(timezone=True)
    users = sa.table("users", *(sa.column(c) for c in ("username", "display_name", "password_hash", "is_admin")),
                     sa.column("id", sa.Uuid), sa.column("created_at", t), sa.column("updated_at", t))
    projects = sa.table("projects", *(sa.column(c) for c in ("codename", "name", "kind", "status", "description")),
                        sa.column("settings", sa.JSON), sa.column("id", sa.Uuid), sa.column("created_at", t), sa.column("updated_at", t))
    columns = sa.table("board_columns", *(sa.column(c) for c in ("name", "position", "role")),
                       sa.column("id", sa.Uuid), sa.column("project_id", sa.Uuid), sa.column("created_at", t), sa.column("updated_at", t))
    tasks = sa.table("tasks", *(sa.column(c) for c in ("title", "description", "kind", "command", "run_on_move", "priority", "position")),
                     *(sa.column(c, sa.JSON) for c in ("agent_selector", "tags", "assignees", "custom_fields")),
                     *(sa.column(c, sa.Uuid) for c in ("id", "project_id", "column_id", "created_by")),
                     sa.column("created_at", t), sa.column("updated_at", t))
    now = datetime(2026, 10, 15, 12, tzinfo=UTC)
    stamp = {"created_at": now, "updated_at": now}
    for label, name, admin in (("u1", "admin", True), ("u2", "alice", True), ("u3", "bob", False)):
        conn.execute(users.insert().values(id=_uuid(label), username=name, display_name=name,
                                           password_hash=f"hash-of-{name}", is_admin=admin, **stamp))
    conn.execute(projects.insert().values(id=_uuid("p1"), codename="OLD-1", name="Old project", kind="internal",
                                          status="active", description="", settings={}, **stamp))
    conn.execute(columns.insert().values(id=_uuid("c1"), project_id=_uuid("p1"), name="To Do", position=1, role="todo", **stamp))
    conn.execute(tasks.insert().values(id=_uuid("t1"), project_id=_uuid("p1"), column_id=_uuid("c1"), position=1,
                                       title="Old card", description="", kind="manual", command="", agent_selector={},
                                       run_on_move=True, priority=0, tags=[], assignees=[], custom_fields={},
                                       created_by=_uuid("u3"), **stamp))


def _uuid(label: str):
    import uuid as _u

    return _u.uuid5(_u.NAMESPACE_URL, label)


def _check_upgraded(conn):
    import sqlalchemy as sa

    users = sa.table("users", sa.column("username"), sa.column("password_hash"), sa.column("role"), sa.column("disabled_at"))
    members = sa.table("project_members", sa.column("user_id", sa.Uuid), sa.column("project_id", sa.Uuid))
    tasks = sa.table("tasks", sa.column("title"), sa.column("created_by", sa.Uuid))
    rows = {r.username: r for r in conn.execute(sa.select(users))}
    assert {n: u.role for n, u in rows.items()} == {"admin": "admin", "alice": "admin", "bob": "operator"}
    assert rows["bob"].password_hash == "hash-of-bob" and rows["bob"].disabled_at is None
    assert set(conn.execute(sa.select(members.c.user_id, members.c.project_id))) == {
        (_uuid(u), _uuid("p1")) for u in ("u1", "u2", "u3")
    }
    assert tuple(conn.execute(sa.select(tasks)).one()) == ("Old card", _uuid("u3"))


def _upgrade_old_install(url: str):
    from sqlalchemy.ext.asyncio import create_async_engine

    async def run(fn):
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(fn)
        await engine.dispose()

    command.upgrade(alembic_config(url), "c6680e13126d")  # the first schema
    asyncio.run(run(_seed_old_install))
    command.upgrade(alembic_config(url), "head")
    asyncio.run(run(_check_upgraded))


def test_old_install_with_hand_made_users_upgrades_on_sqlite():
    with tempfile.TemporaryDirectory() as tmp:
        _upgrade_old_install(f"sqlite+aiosqlite:///{Path(tmp) / 'castor.db'}")


def test_old_install_with_hand_made_users_upgrades_on_postgres():
    """Runs when CASTOR_TEST_POSTGRES_URL points at an empty Postgres database, e.g.
    postgresql+asyncpg://postgres:pw@localhost:55432/postgres (compose uses Postgres)."""
    import os

    import pytest

    url = os.environ.get("CASTOR_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("CASTOR_TEST_POSTGRES_URL not set")
    _upgrade_old_install(url)


def test_sqlite_is_backed_up_before_an_upgrade():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "castor.db"
        url = f"sqlite+aiosqlite:///{path}"
        command.upgrade(alembic_config(url), "8c8bf25ced78")
        asyncio.run(run_migrations(url))
        assert (Path(tmp) / "backups" / "castor-before-upgrade-8c8bf25ced78.db").exists()
        asyncio.run(run_migrations(url))  # already at head: nothing more
        assert len(list((Path(tmp) / "backups").iterdir())) == 1
