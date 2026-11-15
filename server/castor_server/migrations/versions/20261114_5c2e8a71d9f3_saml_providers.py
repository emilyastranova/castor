"""SAML sign-on providers

Revision ID: 5c2e8a71d9f3
Revises: a3f1c9d27b40
Create Date: 2026-11-14

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '5c2e8a71d9f3'
down_revision: str | None = 'a3f1c9d27b40'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('sso_providers') as batch:
        batch.add_column(sa.Column('protocol', sa.String(length=8), nullable=False, server_default='oidc'))
        batch.add_column(sa.Column('saml', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('sso_providers') as batch:
        batch.drop_column('saml')
        batch.drop_column('protocol')
