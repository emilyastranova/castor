"""tasks.playbook_item: a for_each step's item cards

Revision ID: b7d3e91f4a20
Revises: ac527554d5e5
Create Date: 2026-11-04 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b7d3e91f4a20'
down_revision: str | None = 'ac527554d5e5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('playbook_item', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_column('playbook_item')
