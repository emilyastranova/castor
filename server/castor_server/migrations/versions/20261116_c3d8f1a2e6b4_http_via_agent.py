"""HTTP actions sent through an agent: jobs.via_agent_id

Revision ID: c3d8f1a2e6b4
Revises: a91c4e7f2b30
Create Date: 2026-11-16

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c3d8f1a2e6b4'
down_revision: str | None = 'a91c4e7f2b30'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('jobs') as batch:
        batch.add_column(sa.Column('via_agent_id', sa.Uuid(), nullable=True))
        batch.create_foreign_key('fk_jobs_via_agent_id_agents', 'agents', ['via_agent_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    with op.batch_alter_table('jobs') as batch:
        batch.drop_constraint('fk_jobs_via_agent_id_agents', type_='foreignkey')
        batch.drop_column('via_agent_id')
