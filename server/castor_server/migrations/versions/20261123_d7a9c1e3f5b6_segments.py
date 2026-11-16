"""Vantage points: agents.segment, asset_observations.segment

Revision ID: d7a9c1e3f5b6
Revises: c5f7b9d1e3a4
Create Date: 2026-11-23

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'd7a9c1e3f5b6'
down_revision: str | None = 'c5f7b9d1e3a4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('agents') as batch:
        batch.add_column(sa.Column('segment', sa.String(length=80), nullable=False, server_default=''))
    with op.batch_alter_table('asset_observations') as batch:
        batch.add_column(sa.Column('segment', sa.String(length=80), nullable=True))
        batch.create_index('ix_asset_observations_segment', ['segment'])


def downgrade() -> None:
    with op.batch_alter_table('asset_observations') as batch:
        batch.drop_index('ix_asset_observations_segment')
        batch.drop_column('segment')
    with op.batch_alter_table('agents') as batch:
        batch.drop_column('segment')
