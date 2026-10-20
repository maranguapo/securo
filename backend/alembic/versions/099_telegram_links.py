"""Telegram chats linked to a workspace

A user links their Telegram chat to a workspace by sending the bot a
one-time code, so the bot only writes to chats that asked for it.

Revision ID: 099
Revises: 098
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "099"
down_revision = "098"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telegram_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_username", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "user_id", name="uq_telegram_link_workspace_user"),
        sa.UniqueConstraint("workspace_id", "chat_id", name="uq_telegram_link_workspace_chat"),
    )
    op.create_index("ix_telegram_links_workspace_id", "telegram_links", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_telegram_links_workspace_id", table_name="telegram_links")
    op.drop_table("telegram_links")
