"""Month outlook: how much is still safe to spend, and whether spending keeps pace.

Composed from the services the rest of the dashboard already uses, so the
numbers reconcile with the cards next to it instead of re-deriving the P&L
rules a second time.
"""
import math
import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.core.config import get_settings
from app.models.credit_card_bill import CreditCardBill
from app.models.user import User
from app.schemas.dashboard import MonthOutlook, SafeToSpend, SpendPace
from app.services import (
    account_service,
    budget_service,
    dashboard_service,
    invoice_forecast_service,
)
from app.services.fx_rate_service import convert

CASH_ACCOUNT_TYPES = ("checking", "savings")
# Months before the current one used as the "usual" spending reference.
REFERENCE_MONTHS = 3


def compute_pace(
    spent: float, limit: float, day_of_month: int, days_in_month: int
) -> dict:
    """Linear burn-rate projection against ``limit``.

    ``exhaustion_day`` is the day of the month on which the limit is reached
    at the current daily rate; it is only set when that happens before the
    month ends, and ``status`` is ``over`` once the limit is already crossed.
    """
    day = max(1, min(day_of_month, days_in_month))
    daily_rate = spent / day
    projected = daily_rate * days_in_month
    exhaustion_day: Optional[int] = None
    if spent >= limit:
        status = "over"
    elif daily_rate > 0 and projected > limit:
        status = "warning"
        exhaustion_day = min(days_in_month, math.ceil(limit / daily_rate))
    else:
        status = "on_track"
    return {
        "daily_rate": daily_rate,
        "projected": projected,
        "status": status,
        "exhaustion_day": exhaustion_day,
    }


async def _to_primary(
    session: AsyncSession, amount: float, currency: str, primary: str
) -> float:
    if currency == primary:
        return float(amount)
    converted, _ = await convert(session, Decimal(str(amount)), currency, primary)
    return float(converted)


async def _card_bills_due(
    session: AsyncSession, account_id: uuid.UUID, start: date, end: date
) -> list[CreditCardBill]:
    result = await session.execute(
        select(CreditCardBill).where(
            CreditCardBill.account_id == account_id,
            CreditCardBill.due_date >= start,
            CreditCardBill.due_date < end,
        )
    )
    return list(result.scalars().all())


async def _safe_to_spend(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    primary: str,
    today: date,
    month_end: date,
    account_ids: Optional[list[uuid.UUID]],
) -> SafeToSpend:
    accounts = await account_service.get_accounts(session, workspace_id)
    if account_ids is not None:
        allowed = set(account_ids)
        accounts = [a for a in accounts if a["id"] in allowed]
    cash = [a for a in accounts if a["type"] in CASH_ACCOUNT_TYPES]
    cards = [a for a in accounts if a["type"] == "credit_card"]
    cash_ids = [a["id"] for a in cash]

    available = 0.0
    for acc in cash:
        available += await _to_primary(
            session, acc["current_balance"], acc["currency"], primary
        )

    income = 0.0
    expenses = 0.0

    if cash_ids:
        # Recurring entries not yet materialised, from today on. Transfer-like
        # categories stay in: they move cash, and a transfer between two cash
        # accounts nets to zero across the two rows.
        for proj in await dashboard_service._get_recurring_projections(
            session, workspace_id, today, month_end, cash_ids,
            include_transfer_like=True,
        ):
            amount = await _to_primary(
                session, proj["amount"], proj["currency"], primary
            )
            if proj["type"] == "credit":
                income += amount
            else:
                expenses += amount

        # Same pending/future rows the projected balance walks. A connected
        # provider already nets today's pending rows into its balance, so
        # those are skipped (recurring placeholders excepted, we invented them).
        for tx in await dashboard_service._get_forecast_transactions(
            session, workspace_id, date.min, month_end, cash_ids
        ):
            if tx.is_ignored or (tx.category and tx.category.is_ignored):
                continue
            if (
                tx.account
                and tx.account.connection_id
                and tx.status == "pending"
                and tx.date <= today
                and tx.source != "recurring"
            ):
                continue
            signed = await dashboard_service._signed_forecast_amount_primary(
                session, tx, primary
            )
            if signed >= 0:
                income += signed
            else:
                expenses += -signed

    # Open invoices have no account until they are paid, so they only count
    # when nothing narrows the dashboard to a subset of accounts.
    if account_ids is None:
        for claim in await invoice_forecast_service.claims_in_range(
            session, workspace_id, today, month_end
        ):
            amount = await _to_primary(
                session, float(claim.amount), claim.currency, primary
            )
            if claim.direction == "receivable":
                income += amount
            else:
                expenses += amount

    # Card bills due before month end. Capped at what the card actually owes
    # today so a bill that was already paid early is never counted twice.
    card_bills = 0.0
    for card in cards:
        owed = max(-float(card["current_balance"]), 0.0)
        if owed <= 0:
            continue
        bills = await _card_bills_due(session, card["id"], today, month_end)
        due = sum(float(b.total_amount) for b in bills)
        if due > 0:
            card_bills += await _to_primary(
                session, min(due, owed), card["currency"], primary
            )

    days_left = (month_end - today).days
    safe = available + income - expenses - card_bills
    return SafeToSpend(
        available_now=available,
        upcoming_income=income,
        upcoming_expenses=expenses,
        card_bills_due=card_bills,
        safe_to_spend=safe,
        days_left=days_left,
        daily_allowance=safe / days_left if safe > 0 and days_left > 0 else None,
    )


async def _spend_pace(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    month_start: date,
    today: date,
    days_in_month: int,
    account_ids: Optional[list[uuid.UUID]],
) -> SpendPace:
    budgets = await budget_service.get_budget_vs_actual(
        session, workspace_id, user_id, month_start
    )
    budgeted = [b for b in budgets if b.budget_amount and b.budget_amount > 0]
    trend = await dashboard_service.get_monthly_trend(
        session, workspace_id, user_id, REFERENCE_MONTHS + 1, account_ids
    )
    month_key = month_start.strftime("%Y-%m")
    current = next((t.expenses for t in trend if t.month == month_key), 0.0)
    prior = [t.expenses for t in trend if t.month < month_key and t.expenses > 0]

    # A budget is the user's own target, so it wins over a historical average.
    # It only measures the categories that have one, so spend is narrowed to
    # those; the average compares total spend against total spend.
    if budgeted:
        reference = "budget"
        limit = float(sum(b.budget_amount for b in budgeted))
        spent = float(sum(b.actual_amount for b in budgeted))
    elif prior:
        reference = "average"
        limit = sum(prior) / len(prior)
        spent = current
    else:
        return SpendPace(
            spent=current, reference=None, reference_amount=None,
            projected_month_end=None, status="no_reference",
            exhaustion_date=None, days_elapsed=today.day,
            days_in_month=days_in_month,
        )

    pace = compute_pace(spent, limit, today.day, days_in_month)
    exhaustion = (
        month_start.replace(day=pace["exhaustion_day"]).isoformat()
        if pace["exhaustion_day"] else None
    )
    return SpendPace(
        spent=spent,
        reference=reference,
        reference_amount=limit,
        projected_month_end=pace["projected"],
        status=pace["status"],
        exhaustion_date=exhaustion,
        days_elapsed=today.day,
        days_in_month=days_in_month,
    )


async def get_month_outlook(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    month: Optional[date] = None,
    account_ids: Optional[list[uuid.UUID]] = None,
) -> MonthOutlook:
    today = app_today()
    month_start = (month or today).replace(day=1)
    user = await session.get(User, user_id)
    primary = user.primary_currency if user else get_settings().default_currency
    _, month_end = dashboard_service._month_range(month_start)
    days_in_month = (month_end - month_start).days

    # Both blocks describe "the rest of this month"; elsewhere they say nothing.
    if month_start != today.replace(day=1):
        return MonthOutlook(primary_currency=primary, is_current_month=False)

    return MonthOutlook(
        primary_currency=primary,
        is_current_month=True,
        safe_to_spend=await _safe_to_spend(
            session, workspace_id, primary, today, month_end, account_ids
        ),
        pace=await _spend_pace(
            session, workspace_id, user_id, month_start, today, days_in_month,
            account_ids,
        ),
    )
