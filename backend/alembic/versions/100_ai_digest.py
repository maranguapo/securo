"""daily digest of AI categorizations

`workspace_ai_settings` gains the digest schedule (on/off, local time, and the
local date it last went out). `ai_categorizations` gains what the review needs:
when a row went out in a digest and who answered it.

Revision ID: 100
Revises: 099
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "100"
down_revision = "099"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspace_ai_settings",
        sa.Column("digest_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "workspace_ai_settings",
        sa.Column("digest_time", sa.Time(), nullable=False, server_default="20:00:00"),
    )
    op.add_column(
        "workspace_ai_settings", sa.Column("last_digest_on", sa.Date(), nullable=True)
    )
    op.add_column(
        "ai_categorizations",
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ai_categorizations",
        sa.Column(
            "reviewed_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "ai_categorizations",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_categorizations", "reviewed_at")
    op.drop_column("ai_categorizations", "reviewed_by_user_id")
    op.drop_column("ai_categorizations", "notified_at")
    op.drop_column("workspace_ai_settings", "last_digest_on")
    op.drop_column("workspace_ai_settings", "digest_time")
    op.drop_column("workspace_ai_settings", "digest_enabled")
