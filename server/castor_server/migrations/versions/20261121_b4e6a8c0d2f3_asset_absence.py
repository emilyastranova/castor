"""One picture from many scans: asset_observations.absent, assets.missing_since

Revision ID: b4e6a8c0d2f3
Revises: a3d5f7b9c1e2
Create Date: 2026-11-21

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b4e6a8c0d2f3'
down_revision: str | None = 'a3d5f7b9c1e2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('asset_observations') as batch:
        batch.add_column(sa.Column('absent', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column('agent_id', sa.Uuid(), nullable=True))
        batch.create_index('ix_asset_observations_observed_at', ['observed_at'])
    with op.batch_alter_table('assets') as batch:
        batch.add_column(sa.Column('missing_since', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('assets') as batch:
        batch.drop_column('missing_since')
    with op.batch_alter_table('asset_observations') as batch:
        batch.drop_index('ix_asset_observations_observed_at')
        batch.drop_column('agent_id')
        batch.drop_column('absent')
