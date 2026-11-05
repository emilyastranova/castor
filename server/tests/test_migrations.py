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
