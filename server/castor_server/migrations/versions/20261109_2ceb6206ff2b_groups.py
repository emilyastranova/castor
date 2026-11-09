"""groups

Revision ID: 2ceb6206ff2b
Revises: 024bb138bad3
Create Date: 2026-11-09 09:33:21.454252

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '2ceb6206ff2b'
down_revision: str | None = '024bb138bad3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('groups',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('slug', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('sso_group', sa.String(length=255), server_default='', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('groups', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_groups_slug'), ['slug'], unique=True)

    op.create_table('group_members',
    sa.Column('group_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('group_id', 'user_id')
    )
    with op.batch_alter_table('group_members', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_group_members_user_id'), ['user_id'], unique=False)

    op.create_table('project_groups',
    sa.Column('project_id', sa.Uuid(), nullable=False),
    sa.Column('group_id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('project_id', 'group_id')
    )
    with op.batch_alter_table('project_groups', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_project_groups_group_id'), ['group_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('project_groups', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_project_groups_group_id'))

    op.drop_table('project_groups')
    with op.batch_alter_table('group_members', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_group_members_user_id'))

    op.drop_table('group_members')
    with op.batch_alter_table('groups', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_groups_slug'))

    op.drop_table('groups')
