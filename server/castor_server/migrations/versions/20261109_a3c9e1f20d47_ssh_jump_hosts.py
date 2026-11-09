"""ssh hosts: jump hosts from the SSH config; port and username may come from it

Revision ID: a3c9e1f20d47
Revises: 55d3b75a972b
Create Date: 2026-11-09 08:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'a3c9e1f20d47'
down_revision: str | None = '55d3b75a972b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def upgrade() -> None:
    with op.batch_alter_table('ssh_hosts') as batch:
        batch.add_column(sa.Column('jump_host_keys', JSON, nullable=False, server_default='{}'))
        batch.add_column(sa.Column('jump_host_keys_pending', JSON, nullable=False, server_default='{}'))
        batch.alter_column('port', existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    op.execute("UPDATE ssh_hosts SET port = 22 WHERE port IS NULL")
    with op.batch_alter_table('ssh_hosts') as batch:
        batch.alter_column('port', existing_type=sa.Integer(), nullable=False)
        batch.drop_column('jump_host_keys_pending')
        batch.drop_column('jump_host_keys')
