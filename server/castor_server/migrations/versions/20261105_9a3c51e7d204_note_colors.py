"""note colors: a sticky-note color per pinned note

Revision ID: 9a3c51e7d204
Revises: 5b0e9d2c7a41
Create Date: 2026-11-05 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '9a3c51e7d204'
down_revision: str | None = '5b0e9d2c7a41'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('project_notes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('color', sa.String(length=16), server_default='default', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('project_notes', schema=None) as batch_op:
        batch_op.drop_column('color')
