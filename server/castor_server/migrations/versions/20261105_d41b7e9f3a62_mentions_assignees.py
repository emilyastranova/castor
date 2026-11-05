"""mentions and assignees (3b): comments.mentions; tasks.assignees hold user ids

Revision ID: d41b7e9f3a62
Revises: 9a3c51e7d204
Create Date: 2026-11-05 14:00:00.000000

"""
import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'd41b7e9f3a62'
down_revision: str | None = '9a3c51e7d204'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def upgrade() -> None:
    with op.batch_alter_table('comments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('mentions', JSON, server_default='[]', nullable=False))

    # Assignees were free text (nothing in the UI set them); they now hold user ids.
    # Values naming a user (username or id) become that user's id; anything else is dropped.
    conn = op.get_bind()
    # SQLite stores UUIDs as 32 hex characters and Postgres as uuid; the app writes str(uuid).
    users = {}
    for raw, username in conn.execute(sa.text('SELECT id, username FROM users')):
        try:
            uid = raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw))
        except ValueError:
            continue
        users[str(uid)] = str(uid)
        users[uid.hex] = str(uid)
        users[username.lower()] = str(uid)
    if not users:
        return
    update = sa.text('UPDATE tasks SET assignees = :assignees WHERE id = :id').bindparams(
        sa.bindparam('assignees', type_=JSON)
    )
    for task_id, assignees in conn.execute(sa.text('SELECT id, assignees FROM tasks')):
        if isinstance(assignees, str):  # SQLite returns JSON as text here
            assignees = json.loads(assignees or '[]')
        if not assignees:
            continue
        ids = []
        for value in assignees:
            uid = users.get(str(value).strip().lower())
            if uid and uid not in ids:
                ids.append(uid)
        if ids != assignees:
            conn.execute(update, {'assignees': ids, 'id': task_id})


def downgrade() -> None:
    with op.batch_alter_table('comments', schema=None) as batch_op:
        batch_op.drop_column('mentions')
