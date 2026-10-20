import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TelegramLink(Base):
    """A Telegram chat that receives a workspace's messages.

    The link is made by the user from the chat itself (`/start <code>`), so
    the bot only ever writes to chats that opted in.
    """

    __tablename__ = "telegram_links"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_telegram_link_workspace_user"),
        UniqueConstraint("workspace_id", "chat_id", name="uq_telegram_link_workspace_chat"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    chat_id: Mapped[int] = mapped_column(BigInteger)
    telegram_username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
