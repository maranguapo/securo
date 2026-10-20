"""per-workspace provider credentials

Pluggy credentials used to come from the server environment, one pair for
the whole instance. A self-hosted install shared with family or friends
would then put every user on a single Pluggy account. They now live on the
workspace, with the secret stored as Fernet ciphertext.

Existing Pluggy connections keep working once the workspace owner enters
the same client id and secret in Workspace settings.

Revision ID: 097
Revises: 096
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "097"
down_revision = "096"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_integrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("client_id", sa.String(255), nullable=False),
        sa.Column("client_secret_encrypted", sa.String(1000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "workspace_id", "provider", name="uq_workspace_integration_provider"
        ),
    )
    op.create_index(
        "ix_workspace_integrations_workspace_id",
        "workspace_integrations",
        ["workspace_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workspace_integrations_workspace_id", table_name="workspace_integrations"
    )
    op.drop_table("workspace_integrations")
