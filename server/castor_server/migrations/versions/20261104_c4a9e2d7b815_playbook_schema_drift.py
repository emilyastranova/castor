"""bring the playbook tables in line with the models

The playbooks migration created names of 120 characters (the models allow 200), no
indexes on playbook_runs.playbook_id / status, and tasks.playbook_run_id with ON DELETE
SET NULL; the models (and the ORM relationship) delete a run's cards with the run.

Revision ID: c4a9e2d7b815
Revises: b7d3e91f4a20
Create Date: 2026-11-04 15:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c4a9e2d7b815'
down_revision: str | None = 'b7d3e91f4a20'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('playbook_definitions', schema=None) as batch_op:
        batch_op.alter_column(
            'name', existing_type=sa.String(length=120), type_=sa.String(length=200), existing_nullable=False
        )

    with op.batch_alter_table('playbook_runs', schema=None) as batch_op:
        batch_op.alter_column(
            'playbook_name', existing_type=sa.String(length=120), type_=sa.String(length=200), existing_nullable=False
        )
        batch_op.create_index('ix_playbook_runs_playbook_id', ['playbook_id'], unique=False)
        batch_op.create_index('ix_playbook_runs_status', ['status'], unique=False)

    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_constraint('fk_tasks_playbook_run_id', type_='foreignkey')
        batch_op.create_foreign_key(
            'fk_tasks_playbook_run_id', 'playbook_runs', ['playbook_run_id'], ['id'], ondelete='CASCADE'
        )


def downgrade() -> None:
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_constraint('fk_tasks_playbook_run_id', type_='foreignkey')
        batch_op.create_foreign_key(
            'fk_tasks_playbook_run_id', 'playbook_runs', ['playbook_run_id'], ['id'], ondelete='SET NULL'
        )

    with op.batch_alter_table('playbook_runs', schema=None) as batch_op:
        batch_op.drop_index('ix_playbook_runs_status')
        batch_op.drop_index('ix_playbook_runs_playbook_id')
        batch_op.alter_column(
            'playbook_name', existing_type=sa.String(length=200), type_=sa.String(length=120), existing_nullable=False
        )

    with op.batch_alter_table('playbook_definitions', schema=None) as batch_op:
        batch_op.alter_column(
            'name', existing_type=sa.String(length=200), type_=sa.String(length=120), existing_nullable=False
        )
