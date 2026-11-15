"""HTTP actions: job kind, runner, request, progress and resume time; rate limits

Revision ID: a91c4e7f2b30
Revises: 7e3c19a4b2d8
Create Date: 2026-11-16

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'a91c4e7f2b30'
down_revision: str | None = '7e3c19a4b2d8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def upgrade() -> None:
    with op.batch_alter_table('jobs') as batch:
        batch.add_column(sa.Column('kind', sa.String(length=16), server_default='command', nullable=False))
        batch.add_column(sa.Column('runner', sa.String(length=32), nullable=True))
        batch.add_column(sa.Column('request', JSON, nullable=True))
        batch.add_column(sa.Column('progress', JSON, nullable=True))
        batch.add_column(sa.Column('resume_at', sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table('tasks') as batch:
        batch.add_column(sa.Column('rate_limit', sa.String(length=64), nullable=True))
    with op.batch_alter_table('variable_sets') as batch:
        batch.add_column(sa.Column('rate_limit', sa.String(length=64), nullable=True))
    op.create_table(
        'rate_buckets',
        sa.Column('key', sa.String(length=120), nullable=False),
        sa.Column('tokens', sa.Float(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('paused_until', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('key'),
    )


def downgrade() -> None:
    op.drop_table('rate_buckets')
    with op.batch_alter_table('variable_sets') as batch:
        batch.drop_column('rate_limit')
    with op.batch_alter_table('tasks') as batch:
        batch.drop_column('rate_limit')
    with op.batch_alter_table('jobs') as batch:
        batch.drop_column('resume_at')
        batch.drop_column('progress')
        batch.drop_column('request')
        batch.drop_column('runner')
        batch.drop_column('kind')
