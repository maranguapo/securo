import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# applied: the model's category was set on the transaction.
# low_confidence: the model was unsure; nothing was applied.
# approved / changed: the user confirmed or replaced the suggestion.
AI_CATEGORIZATION_STATUSES = ("applied", "low_confidence", "approved", "changed")


class AiCategorization(Base):
    """One row per transaction the model has looked at.

    Doubles as the "already evaluated" marker (a transaction is never sent to
    the model twice) and as the review queue for the daily digest.
    """

    __tablename__ = "ai_categorizations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transactions.id", ondelete="CASCADE"),
        unique=True,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    suggested_category_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    # Why the model chose this category, in a few words. The review screen shows
    # it so a decision can be judged without opening the transaction.
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # A parcel that mirrors the answer for its purchase's representative. It is
    # on the ledger so it is never asked again, but it is never listed on its
    # own: the purchase is reviewed once, through the representative.
    is_follower: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Set when the transaction went out in a digest, so it is sent once.
    notified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reviewed_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
