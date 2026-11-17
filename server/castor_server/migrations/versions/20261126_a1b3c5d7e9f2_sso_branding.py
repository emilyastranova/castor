"""Sign-on buttons: sso_providers.branding and position

Revision ID: a1b3c5d7e9f2
Revises: f9c1e3a5b7d8
Create Date: 2026-11-26

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'a1b3c5d7e9f2'
down_revision: str | None = 'f9c1e3a5b7d8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def upgrade() -> None:
    with op.batch_alter_table('sso_providers') as batch:
        batch.add_column(sa.Column('branding', JSON, nullable=True))
        batch.add_column(sa.Column('position', sa.Integer(), server_default='0', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('sso_providers') as batch:
        batch.drop_column('position')
        batch.drop_column('branding')
