"""one-off (ephemeral) variable sets owned by a card or a playbook run

Revision ID: f52c8d0e3b71
Revises: e41b7c9d2a60
Create Date: 2026-10-27 18:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'f52c8d0e3b71'
down_revision: str | None = 'e41b7c9d2a60'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('variable_sets', schema=None) as batch_op:
        batch_op.add_column(sa.Column('ephemeral', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('task_id', sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column('playbook_run_id', sa.Uuid(), nullable=True))
        batch_op.create_index('ix_variable_sets_task_id', ['task_id'])
        batch_op.create_index('ix_variable_sets_playbook_run_id', ['playbook_run_id'])
        batch_op.create_foreign_key(
            'fk_variable_sets_task_id', 'tasks', ['task_id'], ['id'], ondelete='CASCADE'
        )
        batch_op.create_foreign_key(
            'fk_variable_sets_playbook_run_id', 'playbook_runs', ['playbook_run_id'], ['id'],
            ondelete='CASCADE',
        )


def downgrade() -> None:
    with op.batch_alter_table('variable_sets', schema=None) as batch_op:
        batch_op.drop_constraint('fk_variable_sets_playbook_run_id', type_='foreignkey')
        batch_op.drop_constraint('fk_variable_sets_task_id', type_='foreignkey')
        batch_op.drop_index('ix_variable_sets_playbook_run_id')
        batch_op.drop_index('ix_variable_sets_task_id')
        batch_op.drop_column('playbook_run_id')
        batch_op.drop_column('task_id')
        batch_op.drop_column('ephemeral')
