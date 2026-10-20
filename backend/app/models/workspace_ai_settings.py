import uuid
from datetime import datetime, timezone
from typing import Optional

from datetime import date, time

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, String, Time
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class WorkspaceAiSettings(Base):
    """How a workspace uses an LLM to categorize its transactions.

    One row per workspace. The LLM endpoint is one of the configuring user's
    own connections (`agent_llm_connections`), so the API key stays where the
    user already manages it and is never copied here.
    """

    __tablename__ = "workspace_ai_settings"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    ai_categorization_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    llm_connection_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_llm_connections.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Overrides the connection's default model when set.
    llm_model: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    # Suggestions below this confidence are not applied; the transaction stays
    # uncategorized and is left for the user to decide.
    min_confidence: Mapped[float] = mapped_column(Float, default=0.6, server_default="0.6")
    # The connection belongs to this user, so the background job needs to know
    # whose it is. Cleared if that user is deleted.
    configured_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Daily digest of what the model categorized, sent to the workspace's
    # Telegram chats for review. `digest_time` is wall-clock time in the
    # workspace's own timezone; `last_digest_on` is the workspace-local date it
    # last went out, which is what makes a day's digest go out only once.
    digest_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    digest_time: Mapped[time] = mapped_column(Time, default=lambda: time(20, 0), server_default="20:00:00")
    last_digest_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
