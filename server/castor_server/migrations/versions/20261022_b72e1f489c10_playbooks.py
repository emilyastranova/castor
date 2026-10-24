"""add playbooks and task dependencies

Revision ID: b72e1f489c10
Revises: a81d4e2c901f
Create Date: 2026-10-22 18:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'b72e1f489c10'
down_revision: str | None = 'a81d4e2c901f'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'playbook_definitions',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('origin', sa.String(length=16), nullable=False, server_default='builtin'),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('tags', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False, server_default='[]'),
        sa.Column('spec', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
        sa.Column('source_yaml', sa.Text(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'playbook_runs',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('playbook_id', sa.String(length=64), nullable=False),
        sa.Column('playbook_name', sa.String(length=120), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column(
            'status',
            sa.Enum('running', 'succeeded', 'failed', 'cancelled', name='playbook_run_status', native_enum=False, length=16),
            nullable=False,
            server_default='running',
        ),
        sa.Column('inputs', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False, server_default='{}'),
        sa.Column('step_task_ids', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False, server_default='{}'),
        sa.Column('spec_snapshot', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False, server_default='{}'),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('playbook_runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_playbook_runs_project_id'), ['project_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_playbook_runs_created_at'), ['created_at'], unique=False)

    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('playbook_run_id', sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column('playbook_step_id', sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column(
                'depends_on',
                sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'),
                nullable=False,
                server_default='[]',
            )
        )
        batch_op.create_foreign_key(
            'fk_tasks_playbook_run_id',
            'playbook_runs',
            ['playbook_run_id'],
            ['id'],
            ondelete='SET NULL',
        )
        batch_op.create_index(batch_op.f('ix_tasks_playbook_run_id'), ['playbook_run_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_tasks_playbook_run_id'))
        batch_op.drop_constraint('fk_tasks_playbook_run_id', type_='foreignkey')
        batch_op.drop_column('depends_on')
        batch_op.drop_column('playbook_step_id')
        batch_op.drop_column('playbook_run_id')

    with op.batch_alter_table('playbook_runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_playbook_runs_created_at'))
        batch_op.drop_index(batch_op.f('ix_playbook_runs_project_id'))

    op.drop_table('playbook_runs')
    op.drop_table('playbook_definitions')
