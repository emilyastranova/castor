"""run cards (3h): a playbook run's one card on the board

Revision ID: e7a2c4d19b55
Revises: d41b7e9f3a62
Create Date: 2026-11-05 18:00:00.000000

Cards for runs that already exist are created at startup (services.ensure_run_cards), where
the ORM fills in every column of a new card.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'e7a2c4d19b55'
down_revision: str | None = 'd41b7e9f3a62'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('playbook_runs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('card_id', sa.Uuid(), nullable=True))
        batch_op.create_index(batch_op.f('ix_playbook_runs_card_id'), ['card_id'], unique=False)
        batch_op.create_foreign_key('fk_playbook_runs_card_id', 'tasks', ['card_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    with op.batch_alter_table('playbook_runs', schema=None) as batch_op:
        batch_op.drop_constraint('fk_playbook_runs_card_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_playbook_runs_card_id'))
        batch_op.drop_column('card_id')
