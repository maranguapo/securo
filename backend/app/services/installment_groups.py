"""Which transactions are parcels of the same purchase.

A purchase paid in twelve parcels is twelve transactions, but one decision:
what the purchase was. Categorizing them one by one means twelve model calls
and twelve items to review, all with the same answer.

The key is the one the bank sync already uses to let a new parcel inherit an
earlier parcel's category: the card account, the purchase date, the number of
parcels, the total amount and the direction identify the purchase without
relying on the description, whose "03/12" suffix changes every month. Parcels
created together by the user's own installment series share a series id.
"""
import uuid
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction

GroupKey = tuple


def group_key(tx: Transaction) -> Optional[GroupKey]:
    """Identity of the purchase this transaction is a parcel of, or None when
    it is not a parcel (or lacks what is needed to say which purchase)."""
    if tx.installment_series_id is not None:
        return ("series", tx.installment_series_id)
    if (
        tx.installment_number is None
        or tx.total_installments is None
        or tx.total_installments < 2
        or tx.installment_total_amount is None
        or tx.installment_purchase_date is None
    ):
        return None
    return (
        "purchase",
        tx.account_id,
        tx.source,
        tx.installment_purchase_date,
        tx.total_installments,
        tx.installment_total_amount,
        tx.type,
    )


def _same_purchase(tx: Transaction) -> Optional[list]:
    if tx.installment_series_id is not None:
        return [
            Transaction.workspace_id == tx.workspace_id,
            Transaction.installment_series_id == tx.installment_series_id,
        ]
    if group_key(tx) is None:
        return None
    return [
        Transaction.workspace_id == tx.workspace_id,
        Transaction.account_id == tx.account_id,
        Transaction.source == tx.source,
        Transaction.installment_purchase_date == tx.installment_purchase_date,
        Transaction.total_installments == tx.total_installments,
        Transaction.installment_total_amount == tx.installment_total_amount,
        Transaction.type == tx.type,
    ]


async def siblings(session: AsyncSession, tx: Transaction) -> list[Transaction]:
    """The other parcels of the same purchase, in any state, in parcel order."""
    conditions = _same_purchase(tx)
    if conditions is None:
        return []
    result = await session.execute(
        select(Transaction)
        .where(*conditions, Transaction.id != tx.id)
        .order_by(Transaction.installment_number, Transaction.date)
    )
    return list(result.scalars().all())


async def count_siblings(session: AsyncSession, tx: Transaction) -> int:
    conditions = _same_purchase(tx)
    if conditions is None:
        return 0
    return int(
        await session.scalar(
            select(func.count()).select_from(Transaction).where(*conditions, Transaction.id != tx.id)
        )
        or 0
    )


def inherited_category(parcels: list[Transaction]) -> Optional[uuid.UUID]:
    """The category an uncategorized parcel should take from the others.

    Like the sync does, the latest parcel that has one wins, so a correction
    the user made on a later parcel outranks what an earlier one carries.
    """
    categorized = [p for p in parcels if p.category_id is not None]
    if not categorized:
        return None
    return max(categorized, key=lambda p: (p.installment_number or 0, p.date)).category_id
