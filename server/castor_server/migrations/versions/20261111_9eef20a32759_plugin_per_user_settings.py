"""plugin per-user settings

Revision ID: 9eef20a32759
Revises: f07d17bde112
Create Date: 2026-11-11 19:12:23.705063

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '9eef20a32759'
down_revision: str | None = 'f07d17bde112'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:

    op.create_table('plugin_user_settings',
    sa.Column('plugin_id', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('settings', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('secrets', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['plugin_id'], ['plugins.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('plugin_id', 'user_id')
    )
    with op.batch_alter_table('plugins', schema=None) as batch_op:
        batch_op.add_column(sa.Column('per_user', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), server_default='[]', nullable=False))




def downgrade() -> None:

    with op.batch_alter_table('plugins', schema=None) as batch_op:
        batch_op.drop_column('per_user')

    op.drop_table('plugin_user_settings')

