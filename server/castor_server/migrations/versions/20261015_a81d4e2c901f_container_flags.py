"""add container_flags to tasks and jobs

Revision ID: a81d4e2c901f
Revises: c11afeff1468
Create Date: 2026-10-15 15:10:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'a81d4e2c901f'
down_revision: str | None = 'c11afeff1468'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'container_flags',
                sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'),
                nullable=False,
                server_default='[]',
            )
        )

    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'container_flags',
                sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'),
                nullable=False,
                server_default='[]',
            )
        )


def downgrade() -> None:
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_column('container_flags')

    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_column('container_flags')
