"""Previews and retries: a job's argument override, what it's for and what it follows; a card's preview choice

Revision ID: b8c0d2e4f6a7
Revises: a7b9c1d3e5f6
Create Date: 2026-11-23

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'b8c0d2e4f6a7'
down_revision: str | None = 'a7b9c1d3e5f6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('args_override', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=True))
        batch_op.add_column(sa.Column('purpose', sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column('follows_job_id', sa.Uuid(), nullable=True))
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('preview', sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_column('preview')
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_column('follows_job_id')
        batch_op.drop_column('purpose')
        batch_op.drop_column('args_override')
