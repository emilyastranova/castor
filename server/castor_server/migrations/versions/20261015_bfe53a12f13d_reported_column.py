"""Add a "Reported" column (role=reported) after Done to existing projects

Revision ID: bfe53a12f13d
Revises: 50c19d965b5a
Create Date: 2026-10-15 02:29:07.421062

"""
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = 'bfe53a12f13d'
down_revision: str | None = '50c19d965b5a'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


columns = sa.table(
    "board_columns",
    sa.column("id", sa.Uuid),
    sa.column("project_id", sa.Uuid),
    sa.column("name", sa.String),
    sa.column("position", sa.Float),
    sa.column("role", sa.String),
    sa.column("wip_limit", sa.Integer),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)
tasks = sa.table("tasks", sa.column("column_id", sa.Uuid))


def upgrade() -> None:
    """Every project without a Reported column gets one right after Done (or at the end)."""
    conn = op.get_bind()
    rows = conn.execute(sa.select(columns.c.project_id, columns.c.position, columns.c.role)).all()
    by_project: dict = {}
    for project_id, position, role in rows:
        by_project.setdefault(project_id, []).append((position, role))
    now = datetime.now(UTC)
    new_rows = []
    for project_id, cols in by_project.items():
        if any(role == "reported" for _, role in cols):
            continue
        positions = sorted(p for p, _ in cols)
        done = [p for p, role in cols if role == "done"]
        if done:
            after = [p for p in positions if p > done[0]]
            position = (done[0] + after[0]) / 2 if after else done[0] + 10
        else:
            position = positions[-1] + 10
        new_rows.append(
            {
                "id": uuid.uuid4(),
                "project_id": project_id,
                "name": "Reported",
                "position": position,
                "role": "reported",
                "wip_limit": None,
                "created_at": now,
                "updated_at": now,
            }
        )
    if new_rows:
        op.bulk_insert(columns, new_rows)


def downgrade() -> None:
    """Move cards out of Reported columns (into the project's Done column) before removing them."""
    conn = op.get_bind()
    reported = conn.execute(
        sa.select(columns.c.id, columns.c.project_id).where(columns.c.role == "reported")
    ).all()
    for column_id, project_id in reported:
        done = conn.execute(
            sa.select(columns.c.id)
            .where(columns.c.project_id == project_id, columns.c.role == "done")
            .limit(1)
        ).scalar()
        if done is not None:
            conn.execute(tasks.update().where(tasks.c.column_id == column_id).values(column_id=done))
        else:
            # No Done column to receive the cards: keep the column, just drop its role.
            conn.execute(columns.update().where(columns.c.id == column_id).values(role=None))
            continue
        conn.execute(columns.delete().where(columns.c.id == column_id))
