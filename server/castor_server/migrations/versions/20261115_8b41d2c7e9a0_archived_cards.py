"""Archived cards

Revision ID: 8b41d2c7e9a0
Revises: 5c2e8a71d9f3
Create Date: 2026-11-15

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '8b41d2c7e9a0'
down_revision: str | None = '5c2e8a71d9f3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('tasks') as batch:
        batch.add_column(sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column('archived_by', sa.Uuid(), nullable=True))
        batch.create_index(batch.f('ix_tasks_archived_at'), ['archived_at'], unique=False)
        batch.create_foreign_key('fk_tasks_archived_by_users', 'users', ['archived_by'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    with op.batch_alter_table('tasks') as batch:
        batch.drop_constraint('fk_tasks_archived_by_users', type_='foreignkey')
        batch.drop_index(batch.f('ix_tasks_archived_at'))
        batch.drop_column('archived_by')
        batch.drop_column('archived_at')
