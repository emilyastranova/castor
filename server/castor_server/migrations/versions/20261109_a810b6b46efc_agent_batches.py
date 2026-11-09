"""agent batches

Revision ID: a810b6b46efc
Revises: a3c9e1f20d47
Create Date: 2026-11-09 00:22:58.318153

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'a810b6b46efc'
down_revision: str | None = 'a3c9e1f20d47'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('batch_id', sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column('target_agent_id', sa.Uuid(), nullable=True))
        batch_op.create_index(batch_op.f('ix_jobs_batch_id'), ['batch_id'], unique=False)
        batch_op.create_foreign_key('fk_jobs_target_agent_id_agents', 'agents', ['target_agent_id'], ['id'], ondelete='SET NULL')

    with op.batch_alter_table('playbook_runs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('agent_selector', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False, server_default='{}'))



def downgrade() -> None:
    with op.batch_alter_table('playbook_runs', schema=None) as batch_op:
        batch_op.drop_column('agent_selector')

    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_constraint('fk_jobs_target_agent_id_agents', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_jobs_batch_id'))
        batch_op.drop_column('target_agent_id')
        batch_op.drop_column('batch_id')

