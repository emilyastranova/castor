"""Routes: the agents a route works from

Revision ID: c3d5e7f9a1b2
Revises: b2c4d6e8f0a1
Create Date: 2026-11-17

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'c3d5e7f9a1b2'
down_revision: str | None = 'b2c4d6e8f0a1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('routes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('agents', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('routes', schema=None) as batch_op:
        batch_op.drop_column('agents')
