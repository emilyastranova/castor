"""Approvals: a card asks before each run; who approved a job

Revision ID: b5e9d3a1c7f2
Revises: f4b1c8d2a7e9
Create Date: 2026-11-16

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b5e9d3a1c7f2'
down_revision: str | None = 'f4b1c8d2a7e9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('tasks') as batch:
        batch.add_column(sa.Column('confirm', sa.Boolean(), server_default='0', nullable=False))
    with op.batch_alter_table('jobs') as batch:
        batch.add_column(sa.Column('approved_by', sa.Uuid(), nullable=True))
        batch.add_column(sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key('fk_jobs_approved_by_users', 'users', ['approved_by'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    with op.batch_alter_table('jobs') as batch:
        batch.drop_constraint('fk_jobs_approved_by_users', type_='foreignkey')
        batch.drop_column('approved_at')
        batch.drop_column('approved_by')
    with op.batch_alter_table('tasks') as batch:
        batch.drop_column('confirm')
