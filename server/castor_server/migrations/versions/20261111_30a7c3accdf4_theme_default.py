"""theme default

Revision ID: 30a7c3accdf4
Revises: 7606aeabdaad
Create Date: 2026-11-11 17:32:03.243746

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '30a7c3accdf4'
down_revision: str | None = '7606aeabdaad'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # None: never picked, so the instance default (app_settings.default_theme) applies. Nobody could
    # pick a default before, so "" (Castor's own) meant "never picked" too.
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('theme', existing_type=sa.VARCHAR(length=32), type_=sa.String(length=64), nullable=True,
                              server_default=None, existing_server_default=sa.text("('')"))
    op.execute("UPDATE users SET theme = NULL WHERE theme = ''")


def downgrade() -> None:
    op.execute("UPDATE users SET theme = '' WHERE theme IS NULL")
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('theme', existing_type=sa.String(length=64), type_=sa.VARCHAR(length=32), nullable=False,
                              server_default='')
