"""Agents only some people may use; whose permission a job runs with

Revision ID: d4e6f8a0b2c3
Revises: c3d5e7f9a1b2
Create Date: 2026-11-17

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'd4e6f8a0b2c3'
down_revision: str | None = 'c3d5e7f9a1b2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('agents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('people', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=True))
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('run_as', sa.Uuid(), nullable=True))
        batch_op.create_foreign_key('fk_jobs_run_as_users', 'users', ['run_as'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_constraint('fk_jobs_run_as_users', type_='foreignkey')
        batch_op.drop_column('run_as')
    with op.batch_alter_table('agents', schema=None) as batch_op:
        batch_op.drop_column('people')
