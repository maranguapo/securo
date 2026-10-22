import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


class CategoryRef(BaseModel):
    id: uuid.UUID
    name: str
    group: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None


class InstallmentInfo(BaseModel):
    number: Optional[int] = None
    total: Optional[int] = None
    # Other parcels of the same purchase that share this answer.
    others: int = 0


class AiItemRead(BaseModel):
    id: uuid.UUID
    transaction_id: uuid.UUID
    status: str
    confidence: Optional[float] = None
    reason: Optional[str] = None
    decided_at: datetime
    reviewed_at: Optional[datetime] = None
    pending_telegram: bool
    description: str
    payee: Optional[str] = None
    merchant: str
    merchant_key: str
    amount: float
    currency: str
    date: date
    account_name: str
    category: Optional[CategoryRef] = None
    suggested: Optional[CategoryRef] = None
    installment: Optional[InstallmentInfo] = None


class AiItemsResponse(BaseModel):
    items: list[AiItemRead]
    has_more: bool


class TelegramState(BaseModel):
    connected: bool
    digest_enabled: bool
    digest_time: Optional[str] = None
    pending: int


class AiSummaryRead(BaseModel):
    enabled: bool
    paused: bool
    last_run_at: Optional[datetime] = None
    last_run_applied: Optional[int] = None
    transactions: int
    decided: int
    decided_percent: int
    to_review: int
    unconfirmed: int
    applied_total: int
    awaiting_ai: int
    telegram: TelegramState


class IdsRequest(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


class CategoryRequest(IdsRequest):
    category_id: uuid.UUID


class RuleRequest(BaseModel):
    id: uuid.UUID
    category_id: uuid.UUID


class BulkResultRead(BaseModel):
    done: int
    skipped: int


class RuleResultRead(BaseModel):
    rule_id: uuid.UUID
    rule_name: str
    applied: int
