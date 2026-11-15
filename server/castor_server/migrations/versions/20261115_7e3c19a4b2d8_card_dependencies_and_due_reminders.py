"""Cards waiting on other cards; due date reminders

Revision ID: 7e3c19a4b2d8
Revises: d4a7e2b19c50
Create Date: 2026-11-15

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '7e3c19a4b2d8'
down_revision: str | None = 'd4a7e2b19c50'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('tasks') as batch:
        batch.add_column(sa.Column('due_reminded', sa.String(length=16), nullable=True))
        batch.add_column(sa.Column('waits_on', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=True))
        batch.create_index(batch.f('ix_tasks_due_at'), ['due_at'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('tasks') as batch:
        batch.drop_index(batch.f('ix_tasks_due_at'))
        batch.drop_column('waits_on')
        batch.drop_column('due_reminded')
