"""MCP connections (Castor as an MCP client)

Revision ID: e2f7a9c4d1b8
Revises: c3d8f1a2e6b4
Create Date: 2026-11-16

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'e2f7a9c4d1b8'
down_revision: str | None = 'c3d8f1a2e6b4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def upgrade() -> None:
    op.create_table(
        'mcp_connections',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('transport', sa.String(length=16), nullable=False),
        sa.Column('url', sa.String(length=1000), nullable=False),
        sa.Column('headers', JSON, nullable=False),
        sa.Column('env', JSON, nullable=False),
        sa.Column('variable_set_id', sa.Uuid(), nullable=True),
        sa.Column('command', JSON, nullable=False),
        sa.Column('tool', sa.String(length=64), nullable=True),
        sa.Column('image', sa.String(length=300), nullable=True),
        sa.Column('agent_selector', JSON, nullable=False),
        sa.Column('idle_s', sa.Integer(), nullable=False),
        sa.Column('project_ids', JSON, nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('server_info', JSON, nullable=True),
        sa.Column('tools', JSON, nullable=False),
        sa.Column('checked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['variable_set_id'], ['variable_sets.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('mcp_connections')
