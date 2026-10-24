"""Repairs for databases stamped with a revision that no longer exists in versions/.

Alembic can't upgrade from a revision it doesn't know, and a normal revision can't fix it
either (the database's history lacks the revision that replaced the draft, so a migration
from the draft would create a second head). Each repair here reshapes the schema to match a
known revision; `db.run_migrations` runs it, stamps that revision, then upgrades as usual.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection


def _draft_container_flags(conn: Connection) -> None:
    """bc5b524cc990 was a draft of a81d4e2c901f that added `privileged` (bool) and
    `podman_flags` (JSON list) to tasks and jobs. a81d4e2c901f replaced both with one
    `container_flags` list, so privileged becomes a leading "--privileged" flag."""
    ops = Operations(MigrationContext.configure(conn))
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    for table in ("tasks", "jobs"):
        columns = {c["name"] for c in sa.inspect(conn).get_columns(table)}
        if "container_flags" not in columns:
            with ops.batch_alter_table(table) as batch:
                batch.add_column(
                    sa.Column("container_flags", json_type, nullable=False, server_default="[]")
                )
        if {"privileged", "podman_flags"} <= columns:
            rows = conn.execute(sa.text(f"SELECT id, privileged, podman_flags FROM {table}"))
            for row_id, privileged, raw_flags in rows.all():
                flags = json.loads(raw_flags) if isinstance(raw_flags, str) else (raw_flags or [])
                if privileged and "--privileged" not in flags:
                    flags = ["--privileged", *flags]
                if flags:
                    conn.execute(
                        sa.text(f"UPDATE {table} SET container_flags = :flags WHERE id = :id"),
                        {"flags": json.dumps(flags), "id": row_id},
                    )
        drop = [c for c in ("privileged", "podman_flags") if c in columns]
        if drop:
            with ops.batch_alter_table(table) as batch:
                for column in drop:
                    batch.drop_column(column)


# orphaned revision -> (repair, the known revision the schema matches afterwards)
REPAIRS: dict[str, tuple[Callable[[Connection], None], str]] = {
    "bc5b524cc990": (_draft_container_flags, "a81d4e2c901f"),
}
