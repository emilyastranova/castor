"""group assignees

Revision ID: 41dd59b73cbb
Revises: 2ceb6206ff2b
Create Date: 2026-11-09 09:46:22.865111

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '41dd59b73cbb'
down_revision: str | None = '2ceb6206ff2b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('group_assignees', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False, server_default='[]'))



def downgrade() -> None:
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_column('group_assignees')

