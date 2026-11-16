"""Richer assets: asset_evidence (screenshots, certificates, headers, files on an asset)

Revision ID: c5f7b9d1e3a4
Revises: b4e6a8c0d2f3
Create Date: 2026-11-22

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'c5f7b9d1e3a4'
down_revision: str | None = 'b4e6a8c0d2f3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def upgrade() -> None:
    op.create_table(
        'asset_evidence',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('asset_id', sa.Uuid(), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('attachment_id', sa.Uuid(), nullable=True),
        sa.Column('job_id', sa.Uuid(), nullable=True),
        sa.Column('task_id', sa.Uuid(), nullable=True),
        sa.Column('agent_id', sa.Uuid(), nullable=True),
        sa.Column('parser', sa.String(length=64), nullable=True),
        sa.Column('look', sa.String(length=96), nullable=True),
        sa.Column('look_changed', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('data', JSON, nullable=False),
        sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['asset_id'], ['assets.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['attachment_id'], ['attachments.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    for col in ('project_id', 'asset_id', 'attachment_id', 'observed_at'):
        op.create_index(f'ix_asset_evidence_{col}', 'asset_evidence', [col])


def downgrade() -> None:
    op.drop_table('asset_evidence')
