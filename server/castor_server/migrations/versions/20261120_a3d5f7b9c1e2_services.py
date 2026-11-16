"""Services (commands that run until stopped): tasks.service, jobs.service

Revision ID: a3d5f7b9c1e2
Revises: f1c4a7e2b8d3
Create Date: 2026-11-20

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'a3d5f7b9c1e2'
down_revision: str | None = 'f1c4a7e2b8d3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def upgrade() -> None:
    for table in ('tasks', 'jobs'):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column('service', JSON, nullable=True))


def downgrade() -> None:
    for table in ('tasks', 'jobs'):
        with op.batch_alter_table(table) as batch:
            batch.drop_column('service')
