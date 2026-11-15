"""Cards with subtasks: tasks.parent_id

Revision ID: d6a2f8c1e4b9
Revises: b5e9d3a1c7f2
Create Date: 2026-11-17

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'd6a2f8c1e4b9'
down_revision: str | None = 'b5e9d3a1c7f2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('tasks') as batch:
        batch.add_column(sa.Column('parent_id', sa.Uuid(), nullable=True))
        batch.create_index('ix_tasks_parent_id', ['parent_id'], unique=False)
        batch.create_foreign_key('fk_tasks_parent_id_tasks', 'tasks', ['parent_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    with op.batch_alter_table('tasks') as batch:
        batch.drop_constraint('fk_tasks_parent_id_tasks', type_='foreignkey')
        batch.drop_index('ix_tasks_parent_id')
        batch.drop_column('parent_id')
