"""Fixed commitments of a month: recurring rules and installment purchases.

Whatever else the month spends is variable. Income is left to the caller, which
already holds the dashboard summary, so the share of income committed is
computed next to the numbers it is displayed with.
"""
import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.core.config import get_settings
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.dashboard import CommitmentItem, Commitments
from app.services import dashboard_service
from app.services._query_filters import counts_as_user_pnl, reporting_date_col
from app.services.admin_service import get_credit_card_accounting_mode
from app.services.fx_rate_service import convert

MAX_ITEMS = 8


async def get_commitments(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    month: Optional[date] = None,
    account_ids: Optional[list[uuid.UUID]] = None,
) -> Commitments:
    month_start, month_end = dashboard_service._month_range(month or app_today())
    user = await session.get(User, user_id)
    primary = user.primary_currency if user else get_settings().default_currency
    report_date = reporting_date_col(await get_credit_card_accounting_mode(session))

    if account_ids is not None and len(account_ids) == 0:
        return Commitments(
            primary_currency=primary, recurring_total=0.0, installments_total=0.0,
            fixed_total=0.0, items=[],
        )

    stmt = select(Transaction).where(
        Transaction.workspace_id == workspace_id,
        Transaction.type == "debit",
        Transaction.source != "opening_balance",
        report_date >= month_start,
        report_date < month_end,
        counts_as_user_pnl(),
        # Pending and future-dated rows are still commitments of the month.
        (Transaction.recurring_transaction_id.is_not(None))
        | (
            Transaction.installment_number.is_not(None)
            & (Transaction.total_installments > 1)
        ),
    )
    if account_ids:
        stmt = stmt.where(Transaction.account_id.in_(account_ids))
    rows = (await session.execute(stmt)).scalars().all()

    # description -> [kind, amount, installment label]
    grouped: dict[tuple[str, str], list] = {}
    recurring_total = 0.0
    installments_total = 0.0

    for tx in rows:
        amount = (
            float(tx.amount_primary)
            if tx.amount_primary is not None
            else float(await _to_primary(session, tx.amount, tx.currency, primary))
        )
        # A recurring rule that is also an installment counts once, as recurring.
        kind = "recurring" if tx.recurring_transaction_id else "installment"
        if kind == "recurring":
            recurring_total += amount
        else:
            installments_total += amount
        entry = grouped.setdefault((kind, tx.description), [0.0, None])
        entry[0] += amount
        if kind == "installment" and tx.installment_number:
            entry[1] = f"{tx.installment_number}/{tx.total_installments}"

    # Occurrences that exist only as a schedule, not as rows yet.
    for proj in await dashboard_service._get_recurring_projections(
        session, workspace_id, month_start, month_end, account_ids
    ):
        if proj["type"] != "debit":
            continue
        amount = float(await _to_primary(session, proj["amount"], proj["currency"], primary))
        recurring_total += amount
        entry = grouped.setdefault(("recurring", proj["description"]), [0.0, None])
        entry[0] += amount

    items = sorted(
        (
            CommitmentItem(description=desc, amount=vals[0], kind=kind, installment=vals[1])
            for (kind, desc), vals in grouped.items()
        ),
        key=lambda i: i.amount,
        reverse=True,
    )[:MAX_ITEMS]

    return Commitments(
        primary_currency=primary,
        recurring_total=recurring_total,
        installments_total=installments_total,
        fixed_total=recurring_total + installments_total,
        items=items,
    )


async def _to_primary(
    session: AsyncSession, amount: Decimal, currency: str, primary: str
) -> Decimal:
    if currency == primary:
        return Decimal(amount)
    converted, _ = await convert(session, Decimal(amount), currency, primary)
    return converted
