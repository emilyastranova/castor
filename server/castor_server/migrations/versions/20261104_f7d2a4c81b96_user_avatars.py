"""users: profile pictures (upload, URL or Gravatar)

Revision ID: f7d2a4c81b96
Revises: e5b1c7a93d42
Create Date: 2026-11-04 23:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'f7d2a4c81b96'
down_revision: str | None = 'e5b1c7a93d42'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('avatar_kind', sa.String(length=16), nullable=False, server_default='initials'))
        batch_op.add_column(sa.Column('avatar_link', sa.String(length=500), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('gravatar_email', sa.String(length=254), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('avatar_file', sa.String(length=200), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('avatar_version', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        for column in ('avatar_version', 'avatar_file', 'gravatar_email', 'avatar_link', 'avatar_kind'):
            batch_op.drop_column(column)
