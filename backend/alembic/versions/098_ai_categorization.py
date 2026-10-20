"""AI categorization: per-workspace settings and a review ledger

`workspace_ai_settings` holds how a workspace uses an LLM to categorize
transactions (which of the owner's connections, which model, the confidence
floor). `ai_categorizations` records every transaction the model has looked
at, so none is sent twice, and is the queue the daily digest reads from.

Revision ID: 098
Revises: 097
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "098"
down_revision = "097"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_ai_settings",
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "ai_categorization_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "llm_connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_llm_connections.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("llm_model", sa.String(120), nullable=True),
        sa.Column("min_confidence", sa.Float(), nullable=False, server_default="0.6"),
        sa.Column(
            "configured_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "ai_categorizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "transaction_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transactions.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "suggested_category_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("categories.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_ai_categorizations_workspace_id", "ai_categorizations", ["workspace_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_ai_categorizations_workspace_id", table_name="ai_categorizations")
    op.drop_table("ai_categorizations")
    op.drop_table("workspace_ai_settings")
