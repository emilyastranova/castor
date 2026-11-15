"""Agents limited to projects

Revision ID: d4a7e2b19c50
Revises: 8b41d2c7e9a0
Create Date: 2026-11-15

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'd4a7e2b19c50'
down_revision: str | None = '8b41d2c7e9a0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def upgrade() -> None:
    with op.batch_alter_table('agents') as batch:
        batch.add_column(sa.Column('project_ids', JSON, nullable=True))
    with op.batch_alter_table('agent_tokens') as batch:
        batch.add_column(sa.Column('project_ids', JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('agent_tokens') as batch:
        batch.drop_column('project_ids')
    with op.batch_alter_table('agents') as batch:
        batch.drop_column('project_ids')
