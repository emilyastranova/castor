"""Terminal snapshots: an attachment's recipe

Revision ID: a3f1c9d27b40
Revises: 78d03d1c2b33
Create Date: 2026-11-13

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'a3f1c9d27b40'
down_revision: str | None = '78d03d1c2b33'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('attachments') as batch:
        batch.add_column(sa.Column('snapshot', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('attachments') as batch:
        batch.drop_column('snapshot')
