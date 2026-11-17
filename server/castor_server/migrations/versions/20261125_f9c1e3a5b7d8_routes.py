"""Network routes: routes, and the route of cards, playbook runs, agents and jobs

Revision ID: f9c1e3a5b7d8
Revises: e8b0d2f4a6c7
Create Date: 2026-11-25

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'f9c1e3a5b7d8'
down_revision: str | None = 'e8b0d2f4a6c7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def upgrade() -> None:
    op.create_table(
        'routes',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('tags', JSON, nullable=False),
        sa.Column('project_ids', JSON, nullable=False),
        sa.Column('hops', JSON, nullable=False),
        sa.Column('dns', sa.String(length=8), nullable=False),
        sa.Column('strict', sa.Boolean(), nullable=False),
        sa.Column('segment', sa.String(length=80), server_default='', nullable=False),
        sa.Column('last_test', JSON, nullable=True),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )
    with op.batch_alter_table('tasks') as batch:
        batch.add_column(sa.Column('route', sa.String(length=40), nullable=True))
    with op.batch_alter_table('playbook_runs') as batch:
        batch.add_column(sa.Column('route', sa.String(length=40), nullable=True))
    with op.batch_alter_table('agents') as batch:
        batch.add_column(sa.Column('route', sa.String(length=40), nullable=True))
    with op.batch_alter_table('jobs') as batch:
        batch.add_column(sa.Column('route_info', JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('jobs') as batch:
        batch.drop_column('route_info')
    with op.batch_alter_table('agents') as batch:
        batch.drop_column('route')
    with op.batch_alter_table('playbook_runs') as batch:
        batch.drop_column('route')
    with op.batch_alter_table('tasks') as batch:
        batch.drop_column('route')
    op.drop_table('routes')
