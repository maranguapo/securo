"""what the AI review screen needs

- ai_categorizations.reason: why the model chose the category, in a few words.
- ai_categorizations.is_follower: a parcel that mirrors its purchase's answer.
  It stays on the ledger so it is never asked again, but is never listed on its
  own; the purchase is reviewed once.
- workspace_ai_settings.last_run_at / last_run_applied: when the categorization
  last ran and what it did, for the screen's status line.

Revision ID: 101
Revises: 100
"""
import sqlalchemy as sa

from alembic import op

revision = "101"
down_revision = "100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_categorizations", sa.Column("reason", sa.Text(), nullable=True))
    op.add_column(
        "ai_categorizations",
        sa.Column("is_follower", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "workspace_ai_settings",
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "workspace_ai_settings", sa.Column("last_run_applied", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("workspace_ai_settings", "last_run_applied")
    op.drop_column("workspace_ai_settings", "last_run_at")
    op.drop_column("ai_categorizations", "is_follower")
    op.drop_column("ai_categorizations", "reason")
