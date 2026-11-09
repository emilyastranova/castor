"""single sign-on

Revision ID: 024bb138bad3
Revises: a810b6b46efc
Create Date: 2026-11-09 09:13:00.644854

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '024bb138bad3'
down_revision: str | None = 'a810b6b46efc'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('sso_providers',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('issuer', sa.String(length=500), nullable=False),
    sa.Column('client_id', sa.String(length=255), nullable=False),
    sa.Column('client_secret', sa.Text(), nullable=False),
    sa.Column('scopes', sa.String(length=255), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('default_role', sa.String(length=16), nullable=False),
    sa.Column('allowed_domains', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('allowed_groups', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('groups_claim', sa.String(length=64), nullable=False),
    sa.Column('role_map', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('link_by_email', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('user_identities',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('provider_id', sa.String(length=32), nullable=False),
    sa.Column('subject', sa.String(length=255), nullable=False),
    sa.Column('email', sa.String(length=254), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['provider_id'], ['sso_providers.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider_id', 'subject')
    )
    with op.batch_alter_table('user_identities', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_user_identities_user_id'), ['user_id'], unique=False)

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('email', sa.String(length=254), server_default='', nullable=False))
        batch_op.create_index(batch_op.f('ix_users_email'), ['email'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_email'))
        batch_op.drop_column('email')

    with op.batch_alter_table('user_identities', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_identities_user_id'))

    op.drop_table('user_identities')
    op.drop_table('sso_providers')
