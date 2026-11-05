"""tool library (3a): tool_definitions, and the tool a card/job runs with

Revision ID: c183a3f46e02
Revises: f7d2a4c81b96
Create Date: 2026-11-04 22:09:55.382307

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'c183a3f46e02'
down_revision: str | None = 'f7d2a4c81b96'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('tool_definitions',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('origin', sa.String(length=16), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('spec', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('source_yaml', sa.Text(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tool', sa.String(length=64), nullable=True))

    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tool', sa.String(length=64), nullable=True))



def downgrade() -> None:
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_column('tool')

    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_column('tool')

    op.drop_table('tool_definitions')
