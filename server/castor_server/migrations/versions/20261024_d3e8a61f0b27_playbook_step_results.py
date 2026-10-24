"""playbook run step results (transform steps, skipped steps)

Revision ID: d3e8a61f0b27
Revises: b72e1f489c10
Create Date: 2026-10-24 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'd3e8a61f0b27'
down_revision: str | None = 'b72e1f489c10'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('playbook_runs', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'step_results',
                sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'),
                nullable=False,
                server_default='{}',
            )
        )


def downgrade() -> None:
    with op.batch_alter_table('playbook_runs', schema=None) as batch_op:
        batch_op.drop_column('step_results')
