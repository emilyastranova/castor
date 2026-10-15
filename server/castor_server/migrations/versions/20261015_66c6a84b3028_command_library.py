"""Command library: command_definitions, task command_ref/args, job artifact token

Revision ID: 66c6a84b3028
Revises: bfe53a12f13d
Create Date: 2026-10-15 03:47:06.447242

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '66c6a84b3028'
down_revision: str | None = 'bfe53a12f13d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('command_definitions',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('origin', sa.String(length=16), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('tags', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('spec', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('source_yaml', sa.Text(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('artifact_token_hash', sa.String(length=64), nullable=True))

    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('command_ref', sa.String(length=64), nullable=True))
        # Existing cards have no library command: default their args to an empty object.
        batch_op.add_column(
            sa.Column(
                'command_args',
                sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'),
                nullable=False,
                server_default='{}',
            )
        )


def downgrade() -> None:
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_column('command_args')
        batch_op.drop_column('command_ref')

    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_column('artifact_token_hash')

    op.drop_table('command_definitions')
