"""Emoji reactions on comments and posts, and custom emoji

Revision ID: b2c4d6e8f0a1
Revises: a1b3c5d7e9f2
Create Date: 2026-11-27

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b2c4d6e8f0a1'
down_revision: str | None = 'a1b3c5d7e9f2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'reactions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('comment_id', sa.Uuid(), nullable=True),
        sa.Column('post_id', sa.Uuid(), nullable=True),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('emoji', sa.String(length=80), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['comment_id'], ['comments.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['post_id'], ['project_posts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_reactions_project_id', 'reactions', ['project_id'])
    op.create_index('ix_reactions_comment_id', 'reactions', ['comment_id'])
    op.create_index('ix_reactions_post_id', 'reactions', ['post_id'])
    op.create_table(
        'custom_emojis',
        sa.Column('name', sa.String(length=32), nullable=False),
        sa.Column('file', sa.String(length=100), nullable=False),
        sa.Column('content_type', sa.String(length=32), nullable=False),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('name'),
    )


def downgrade() -> None:
    op.drop_table('custom_emojis')
    op.drop_index('ix_reactions_post_id', table_name='reactions')
    op.drop_index('ix_reactions_comment_id', table_name='reactions')
    op.drop_index('ix_reactions_project_id', table_name='reactions')
    op.drop_table('reactions')
