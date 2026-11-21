"""Client logos (company branding)

Revision ID: f6a8b0c2d4e5
Revises: e5f7a9b1c3d4
Create Date: 2026-11-21

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'f6a8b0c2d4e5'
down_revision: str | None = 'e5f7a9b1c3d4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('clients', schema=None) as batch_op:
        batch_op.add_column(sa.Column('logo_file', sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column('logo_dark_file', sa.String(length=120), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('clients', schema=None) as batch_op:
        batch_op.drop_column('logo_dark_file')
        batch_op.drop_column('logo_file')
