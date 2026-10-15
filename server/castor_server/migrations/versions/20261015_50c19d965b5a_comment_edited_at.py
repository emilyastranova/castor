"""comment edited_at

Revision ID: 50c19d965b5a
Revises: c6680e13126d
Create Date: 2026-10-15 02:15:15.153328

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '50c19d965b5a'
down_revision: str | None = 'c6680e13126d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('comments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('edited_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('comments', schema=None) as batch_op:
        batch_op.drop_column('edited_at')

