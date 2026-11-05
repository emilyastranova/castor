"""users.role (replaces is_admin), users.disabled_at, project_members (2f)

Existing users keep their access: is_admin → admin, everyone else → operator (they could
run jobs before, and still can), and every existing user becomes a member of every
existing project, so nobody loses sight of a project they could see before. Nothing else
about the users (passwords, names, ids) changes.

Revision ID: e5b1c7a93d42
Revises: 8c8bf25ced78
Create Date: 2026-11-04 21:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'e5b1c7a93d42'
down_revision: str | None = '8c8bf25ced78'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('role', sa.String(length=16), nullable=False, server_default='operator'))
        batch_op.add_column(sa.Column('disabled_at', sa.DateTime(timezone=True), nullable=True))
    op.execute(sa.text("UPDATE users SET role = 'admin' WHERE is_admin = :yes").bindparams(yes=True))
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('is_admin')

    op.create_table(
        'project_members',
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('project_id', 'user_id'),
    )
    with op.batch_alter_table('project_members', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_project_members_user_id'), ['user_id'], unique=False)
    op.execute(
        sa.text(
            "INSERT INTO project_members (project_id, user_id, created_at) "
            "SELECT projects.id, users.id, CURRENT_TIMESTAMP FROM projects CROSS JOIN users"
        )
    )


def downgrade() -> None:
    op.drop_table('project_members')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_admin', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute(sa.text("UPDATE users SET is_admin = :yes WHERE role = 'admin'").bindparams(yes=True))
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('disabled_at')
        batch_op.drop_column('role')
