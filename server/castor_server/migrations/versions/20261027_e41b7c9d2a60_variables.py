"""variable kinds and sets; variable bindings on tasks and jobs

Revision ID: e41b7c9d2a60
Revises: d3e8a61f0b27
Create Date: 2026-10-27 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'e41b7c9d2a60'
down_revision: str | None = 'd3e8a61f0b27'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def upgrade() -> None:
    op.create_table(
        'variable_kinds',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('origin', sa.String(length=16), nullable=False, server_default='user'),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('spec', JSON, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'variable_sets',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('kind_id', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('project_ids', JSON, nullable=False, server_default='[]'),
        sa.Column('values', JSON, nullable=False, server_default='{}'),
        sa.Column('secrets', JSON, nullable=False, server_default='{}'),
        sa.Column('files', JSON, nullable=False, server_default='{}'),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['kind_id'], ['variable_kinds.id']),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )
    op.create_index('ix_variable_sets_kind_id', 'variable_sets', ['kind_id'])
    for table in ('tasks', 'jobs'):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column('var_bindings', JSON, nullable=False, server_default='{}'))


def downgrade() -> None:
    for table in ('jobs', 'tasks'):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column('var_bindings')
    op.drop_index('ix_variable_sets_kind_id', table_name='variable_sets')
    op.drop_table('variable_sets')
    op.drop_table('variable_kinds')
