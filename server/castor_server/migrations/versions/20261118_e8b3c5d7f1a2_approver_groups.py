"""Approver groups: tasks.approvers, asset_triggers.approvers

Revision ID: e8b3c5d7f1a2
Revises: d6a2f8c1e4b9
Create Date: 2026-11-18

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'e8b3c5d7f1a2'
down_revision: str | None = 'd6a2f8c1e4b9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def upgrade() -> None:
    for table in ('tasks', 'asset_triggers'):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column('approvers', JSON, nullable=True))


def downgrade() -> None:
    for table in ('tasks', 'asset_triggers'):
        with op.batch_alter_table(table) as batch:
            batch.drop_column('approvers')
