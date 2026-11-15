"""Plugin library actions: a rate limit on a plugin's actions

Revision ID: f4b1c8d2a7e9
Revises: e2f7a9c4d1b8
Create Date: 2026-11-16

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'f4b1c8d2a7e9'
down_revision: str | None = 'e2f7a9c4d1b8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('plugins') as batch:
        batch.add_column(sa.Column('actions_rate_limit', sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('plugins') as batch:
        batch.drop_column('actions_rate_limit')
