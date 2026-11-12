"""asset relationships

Revision ID: 7606aeabdaad
Revises: 371fb6d6060c
Create Date: 2026-11-11 16:13:37.041744

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '7606aeabdaad'
down_revision: str | None = '371fb6d6060c'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('asset_links', schema=None) as batch_op:
        batch_op.add_column(sa.Column('last_seen', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('source', sa.String(length=16), server_default='parser', nullable=False))
        batch_op.add_column(sa.Column('parser', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('job_id', sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column('task_id', sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column('actor_id', sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column('confidence', sa.Integer(), server_default='85', nullable=False))
        batch_op.add_column(sa.Column('fields', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), server_default='{}',
                                      nullable=False))
        batch_op.add_column(sa.Column('note', sa.Text(), server_default='', nullable=False))
        batch_op.alter_column('kind', existing_type=sa.VARCHAR(length=16), type_=sa.String(length=24), existing_nullable=False)
        batch_op.create_index(batch_op.f('ix_asset_links_kind'), ['kind'], unique=False)
        batch_op.create_foreign_key('fk_asset_links_actor_id_users', 'users', ['actor_id'], ['id'], ondelete='SET NULL')
        batch_op.create_foreign_key('fk_asset_links_job_id_jobs', 'jobs', ['job_id'], ['id'], ondelete='SET NULL')
        batch_op.create_foreign_key('fk_asset_links_task_id_tasks', 'tasks', ['task_id'], ['id'], ondelete='SET NULL')

    with op.batch_alter_table('asset_trigger_fires', schema=None) as batch_op:
        batch_op.add_column(sa.Column('other_id', sa.Uuid(), nullable=True))
        batch_op.create_index(batch_op.f('ix_asset_trigger_fires_other_id'), ['other_id'], unique=False)
        batch_op.create_foreign_key('fk_asset_trigger_fires_other_id_assets', 'assets', ['other_id'], ['id'], ondelete='CASCADE')

    with op.batch_alter_table('asset_triggers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('link_kind', sa.String(length=24), nullable=True))
        batch_op.add_column(sa.Column('other_type', sa.String(length=16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('asset_triggers', schema=None) as batch_op:
        batch_op.drop_column('other_type')
        batch_op.drop_column('link_kind')

    with op.batch_alter_table('asset_trigger_fires', schema=None) as batch_op:
        batch_op.drop_constraint('fk_asset_trigger_fires_other_id_assets', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_asset_trigger_fires_other_id'))
        batch_op.drop_column('other_id')

    with op.batch_alter_table('asset_links', schema=None) as batch_op:
        for name in ('fk_asset_links_actor_id_users', 'fk_asset_links_job_id_jobs', 'fk_asset_links_task_id_tasks'):
            batch_op.drop_constraint(name, type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_asset_links_kind'))
        batch_op.alter_column('kind', existing_type=sa.String(length=24), type_=sa.VARCHAR(length=16), existing_nullable=False)
        for col in ('note', 'fields', 'confidence', 'actor_id', 'task_id', 'job_id', 'parser', 'source', 'last_seen'):
            batch_op.drop_column(col)
