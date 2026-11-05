"""project notes (3d): pinned markdown notes on a project's dashboard

Revision ID: 5b0e9d2c7a41
Revises: c183a3f46e02
Create Date: 2026-11-05 10:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '5b0e9d2c7a41'
down_revision: str | None = 'c183a3f46e02'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('project_notes',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('project_id', sa.Uuid(), nullable=False),
    sa.Column('title', sa.String(length=120), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('updated_by', sa.Uuid(), nullable=True),
    sa.Column('history', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('project_notes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_project_notes_project_id'), ['project_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('project_notes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_project_notes_project_id'))
    op.drop_table('project_notes')
