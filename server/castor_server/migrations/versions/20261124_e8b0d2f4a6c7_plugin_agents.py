"""Agent types from plugins: agents.plugin_id, agent_type, machine_ref, source_tags

Revision ID: e8b0d2f4a6c7
Revises: d7a9c1e3f5b6
Create Date: 2026-11-24

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'e8b0d2f4a6c7'
down_revision: str | None = 'd7a9c1e3f5b6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def upgrade() -> None:
    with op.batch_alter_table('agents') as batch:
        batch.add_column(sa.Column('plugin_id', sa.String(length=64), nullable=True))
        batch.add_column(sa.Column('agent_type', sa.String(length=64), nullable=True))
        batch.add_column(sa.Column('machine_ref', sa.String(length=255), nullable=True))
        batch.add_column(sa.Column('source_tags', JSON, nullable=True))
        batch.create_index('ix_agents_plugin_id', ['plugin_id'])
        batch.create_foreign_key('fk_agents_plugin_id_plugins', 'plugins', ['plugin_id'], ['id'], ondelete='CASCADE')


def downgrade() -> None:
    with op.batch_alter_table('agents') as batch:
        batch.drop_constraint('fk_agents_plugin_id_plugins', type_='foreignkey')
        batch.drop_index('ix_agents_plugin_id')
        batch.drop_column('source_tags')
        batch.drop_column('machine_ref')
        batch.drop_column('agent_type')
        batch.drop_column('plugin_id')
