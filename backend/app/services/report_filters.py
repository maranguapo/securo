"""Transaction-level filters shared by the report endpoints.

Account and collection narrowing lives next to each report; this covers what a
user may additionally slice by: category (or a whole group), payee, how the
charge came about, and its size. One object builds the SQL conditions and also
answers for rows that never reach SQL (recurring projections, forecast rows),
so a filtered report cannot show a projected row its actual rows would hide.
"""
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from fastapi import Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.models.transaction import Transaction

KINDS = ("recurring", "installment", "one_off")


@dataclass
class ReportFilters:
    category_ids: list[uuid.UUID] = field(default_factory=list)
    group_ids: list[uuid.UUID] = field(default_factory=list)
    uncategorized: bool = False
    exclude_categories: bool = False
    payee_ids: list[uuid.UUID] = field(default_factory=list)
    kind: Optional[str] = None
    min_amount: Optional[Decimal] = None
    max_amount: Optional[Decimal] = None

    @property
    def has_categories(self) -> bool:
        return bool(self.category_ids or self.group_ids or self.uncategorized)

    @property
    def active(self) -> bool:
        return (
            self.has_categories or bool(self.payee_ids) or self.kind is not None
            or self.min_amount is not None or self.max_amount is not None
        )

    async def resolve(self, session: AsyncSession, workspace_id: uuid.UUID) -> "ReportFilters":
        """Fold category groups into their member categories."""
        if self.group_ids:
            rows = await session.execute(
                select(Category.id).where(
                    Category.workspace_id == workspace_id,
                    Category.group_id.in_(self.group_ids),
                )
            )
            merged = {*self.category_ids, *(r[0] for r in rows.all())}
            self.category_ids = sorted(merged, key=str)
            self.group_ids = []
        return self

    # --- SQL ---------------------------------------------------------------

    def conditions(self) -> list:
        out: list = []
        if self.has_categories:
            ids = self.category_ids
            if self.exclude_categories:
                clause = [Transaction.category_id.notin_(ids)] if ids else []
                # A NULL category is "not in" nothing, so keep it unless asked to drop it.
                out.append(
                    and_(Transaction.category_id.is_not(None), *clause)
                    if self.uncategorized
                    else or_(Transaction.category_id.is_(None), *clause) if clause else Transaction.category_id.is_not(None)
                )
            else:
                parts = []
                if ids:
                    parts.append(Transaction.category_id.in_(ids))
                if self.uncategorized:
                    parts.append(Transaction.category_id.is_(None))
                out.append(or_(*parts))
        if self.payee_ids:
            out.append(Transaction.payee_id.in_(self.payee_ids))
        if self.kind == "recurring":
            out.append(Transaction.recurring_transaction_id.is_not(None))
        elif self.kind == "installment":
            out.append(and_(Transaction.installment_number.is_not(None), Transaction.total_installments > 1))
        elif self.kind == "one_off":
            out.append(Transaction.recurring_transaction_id.is_(None))
            out.append(or_(Transaction.installment_number.is_(None), Transaction.total_installments <= 1))
        amount = func.coalesce(Transaction.amount_primary, Transaction.amount)
        if self.min_amount is not None:
            out.append(func.abs(amount) >= self.min_amount)
        if self.max_amount is not None:
            out.append(func.abs(amount) <= self.max_amount)
        return out

    # --- Python-side rows --------------------------------------------------

    def _category_allows(self, category_id: Optional[uuid.UUID]) -> bool:
        if not self.has_categories:
            return True
        listed = (category_id is None and self.uncategorized) or (category_id in self.category_ids)
        return not listed if self.exclude_categories else listed

    def _amount_allows(self, amount: float) -> bool:
        size = abs(float(amount))
        if self.min_amount is not None and size < float(self.min_amount):
            return False
        if self.max_amount is not None and size > float(self.max_amount):
            return False
        return True

    def allows_projection(self, proj: dict) -> bool:
        """A recurring occurrence that has not been written as a row yet.

        It has a category and an amount; it is recurring by definition and has
        no payee, so a payee filter or the other kinds rule it out.
        """
        if self.payee_ids or self.kind in ("installment", "one_off"):
            return False
        return self._category_allows(proj.get("category_id")) and self._amount_allows(proj["amount"])

    def allows_transaction(self, tx: Transaction) -> bool:
        """A loaded forecast row (pending or future-dated)."""
        if self.payee_ids and tx.payee_id not in self.payee_ids:
            return False
        is_installment = tx.installment_number is not None and (tx.total_installments or 0) > 1
        is_recurring = tx.recurring_transaction_id is not None
        if self.kind == "recurring" and not is_recurring:
            return False
        if self.kind == "installment" and not is_installment:
            return False
        if self.kind == "one_off" and (is_recurring or is_installment):
            return False
        amount = float(tx.amount_primary if tx.amount_primary is not None else tx.amount)
        return self._category_allows(tx.category_id) and self._amount_allows(amount)


def report_filters(
    category_ids: Optional[list[uuid.UUID]] = Query(None),
    category_group_ids: Optional[list[uuid.UUID]] = Query(None),
    uncategorized: bool = Query(False),
    exclude_categories: bool = Query(False),
    payee_ids: Optional[list[uuid.UUID]] = Query(None),
    kind: Optional[str] = Query(None, pattern="^(recurring|installment|one_off)$"),
    min_amount: Optional[Decimal] = Query(None, ge=0),
    max_amount: Optional[Decimal] = Query(None, ge=0),
) -> ReportFilters:
    """FastAPI dependency; groups are resolved later, once a session exists."""
    return ReportFilters(
        category_ids=list(category_ids or []),
        group_ids=list(category_group_ids or []),
        uncategorized=uncategorized,
        exclude_categories=exclude_categories,
        payee_ids=list(payee_ids or []),
        kind=kind,
        min_amount=min_amount,
        max_amount=max_amount,
    )
