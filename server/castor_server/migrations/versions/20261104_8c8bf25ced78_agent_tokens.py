"""agent_tokens: per-agent credentials (2e)

Revision ID: 8c8bf25ced78
Revises: c4a9e2d7b815
Create Date: 2026-11-04 20:22:45.774535

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '8c8bf25ced78'
down_revision: str | None = 'c4a9e2d7b815'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('agent_tokens',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('label', sa.String(length=128), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('prefix', sa.String(length=16), nullable=False),
    sa.Column('tags', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('agent_id', sa.Uuid(), nullable=True),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('rotated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    with op.batch_alter_table('agent_tokens', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_agent_tokens_agent_id'), ['agent_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('agent_tokens', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_agent_tokens_agent_id'))

    op.drop_table('agent_tokens')
