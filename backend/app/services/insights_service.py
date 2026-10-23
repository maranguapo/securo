"""Read-only personal-finance insights for the dashboard.

Every function reuses the P&L rules the dashboard already follows
(``counts_as_user_pnl``, the reporting-date column, primary-currency amounts) so
an insight never disagrees with the card next to it. Split offsets between
group members are not applied here: amounts are the full transaction value.
"""
import statistics
import uuid
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.core.config import get_settings
from app.models.ai_categorization import AiCategorization
from app.models.asset import Asset
from app.models.category import Category
from app.models.goal import Goal
from app.models.payee import Payee
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.dashboard import (
    Anomaly,
    CategoryVariance,
    CategoryVarianceReport,
    EmergencyFund,
    GoalPace,
    HealthMetrics,
    InstallmentMonth,
    InstallmentsOutlook,
    InvestmentAllocation,
    InvestmentsSummary,
    SpendingByWeekday,
    SubscriptionItem,
    Subscriptions,
    TopPayee,
    TopPayees,
    WeekdaySpending,
)
from app.services import account_service, asset_service, commitments_service, dashboard_service, goal_service
from app.services._query_filters import counts_as_user_pnl, reporting_date_col
from app.services.admin_service import get_credit_card_accounting_mode
from app.services.fx_rate_service import convert

CASH_TYPES = ("checking", "savings")
# Monthly equivalent of one charge, per recurrence frequency.
MONTHLY_FACTOR = {
    "weekly": 52 / 12,
    "biweekly": 26 / 12,
    "monthly": 1.0,
    "quarterly": 1 / 3,
    "semiannual": 1 / 6,
    "yearly": 1 / 12,
}


def _primary_amt():
    return func.coalesce(Transaction.amount_primary, Transaction.amount)


def _shift_month(d: date, delta: int) -> date:
    idx = d.year * 12 + (d.month - 1) + delta
    return date(idx // 12, idx % 12 + 1, 1)


async def _primary_currency(session: AsyncSession, user_id: uuid.UUID) -> str:
    user = await session.get(User, user_id)
    return user.primary_currency if user else get_settings().default_currency


async def _to_primary(
    session: AsyncSession, amount: float, currency: str, primary: str
) -> float:
    if currency == primary or amount == 0:
        return float(amount)
    converted, _ = await convert(session, Decimal(str(amount)), currency, primary)
    return float(converted)


def _expense_filters(workspace_id: uuid.UUID, report_date, start: date, end: date,
                     account_ids: Optional[list[uuid.UUID]]) -> list:
    """Posted debits that count as the user's spending in ``[start, end)``."""
    filters = [
        Transaction.workspace_id == workspace_id,
        Transaction.type == "debit",
        Transaction.status == "posted",
        Transaction.source != "opening_balance",
        report_date >= start,
        report_date < end,
        counts_as_user_pnl(),
    ]
    if account_ids:
        filters.append(Transaction.account_id.in_(account_ids))
    return filters


# --- Block 5: installments --------------------------------------------------

async def get_installments(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID,
    account_ids: Optional[list[uuid.UUID]] = None,
) -> InstallmentsOutlook:
    primary = await _primary_currency(session, user_id)
    today = app_today()
    stmt = select(Transaction).where(
        Transaction.workspace_id == workspace_id,
        Transaction.type == "debit",
        Transaction.installment_number.is_not(None),
        Transaction.total_installments > 1,
        Transaction.date >= today,
        counts_as_user_pnl(),
    )
    if account_ids:
        stmt = stmt.where(Transaction.account_id.in_(account_ids))
    rows = (await session.execute(stmt)).scalars().all()

    by_month: dict[str, float] = defaultdict(float)
    purchases: set = set()
    for tx in rows:
        amount = (
            float(tx.amount_primary) if tx.amount_primary is not None
            else await _to_primary(session, float(tx.amount), tx.currency, primary)
        )
        by_month[tx.date.strftime("%Y-%m")] += amount
        purchases.add(tx.installment_series_id or (tx.account_id, tx.description, tx.total_installments))

    months = sorted(by_month)
    return InstallmentsOutlook(
        primary_currency=primary,
        remaining_total=sum(by_month.values()),
        active_purchases=len(purchases),
        last_month=months[-1] if months else None,
        timeline=[InstallmentMonth(month=m, amount=by_month[m]) for m in months[:12]],
    )


# --- Block 7: category variance ---------------------------------------------

async def get_category_variance(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID,
    account_ids: Optional[list[uuid.UUID]] = None, reference_months: int = 3, limit: int = 5,
) -> CategoryVarianceReport:
    primary = await _primary_currency(session, user_id)
    today = app_today()
    report_date = reporting_date_col(await get_credit_card_accounting_mode(session))
    month_start = today.replace(day=1)

    async def by_category(start: date, end: date) -> dict:
        rows = await session.execute(
            select(Transaction.category_id, func.sum(_primary_amt()))
            .where(*_expense_filters(workspace_id, report_date, start, end, account_ids))
            .group_by(Transaction.category_id)
        )
        return {cid: float(total or 0) for cid, total in rows.all()}

    # Month-to-date against the same day range of earlier months, so the first
    # week of a month is not compared with three complete months.
    current = await by_category(month_start, today + timedelta(days=1))
    totals: dict = defaultdict(float)
    for back in range(1, reference_months + 1):
        start = _shift_month(month_start, -back)
        nxt = _shift_month(start, 1)
        end = min(start + timedelta(days=today.day), nxt)
        for cid, amount in (await by_category(start, end)).items():
            totals[cid] += amount / reference_months

    cats = {
        c.id: c for c in (await session.execute(
            select(Category).where(Category.workspace_id == workspace_id)
        )).scalars().all()
    }
    items: list[CategoryVariance] = []
    for cid in set(current) | set(totals):
        cat = cats.get(cid)
        cur, avg = current.get(cid, 0.0), totals.get(cid, 0.0)
        items.append(CategoryVariance(
            category_id=str(cid) if cid else None,
            category_name=cat.name if cat else "—",
            category_icon=cat.icon if cat else "circle-help",
            category_color=cat.color if cat else "#6B7280",
            current=cur, average=avg, delta=cur - avg,
            delta_pct=((cur - avg) / avg * 100) if avg > 0 else None,
        ))
    # Noise filter: ignore changes under 1% of total monthly spend.
    floor = 0.01 * (sum(current.values()) or 0)
    up = sorted((i for i in items if i.delta > max(floor, 0.01)), key=lambda i: -i.delta)
    down = sorted((i for i in items if i.delta < -max(floor, 0.01)), key=lambda i: i.delta)
    return CategoryVarianceReport(
        primary_currency=primary, reference_months=reference_months,
        increases=up[:limit], decreases=down[:limit],
    )


# --- Block 8: emergency fund ------------------------------------------------

async def get_emergency_fund(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID,
    account_ids: Optional[list[uuid.UUID]] = None,
) -> EmergencyFund:
    primary = await _primary_currency(session, user_id)
    accounts = await account_service.get_accounts(session, workspace_id)
    if account_ids is not None:
        allowed = set(account_ids)
        accounts = [a for a in accounts if a["id"] in allowed]
    liquid = 0.0
    for acc in accounts:
        if acc["type"] in CASH_TYPES:
            liquid += await _to_primary(session, float(acc["current_balance"]), acc["currency"], primary)
    # Investments count as a reserve; property and vehicles do not.
    if account_ids is None:
        assets = (await session.execute(
            select(Asset).where(
                Asset.workspace_id == workspace_id, Asset.type == "investment",
                Asset.is_archived == False, Asset.sell_date.is_(None),  # noqa: E712
            )
        )).scalars().all()
        for asset in assets:
            value = asset_service._compute_current_value(
                asset, await asset_service._get_latest_value(session, asset.id)
            )
            liquid += await _to_primary(session, value or 0.0, asset.currency, primary)

    trend = await dashboard_service.get_monthly_trend(session, workspace_id, user_id, 4, account_ids)
    current_key = app_today().strftime("%Y-%m")
    past = [t.expenses for t in trend if t.month < current_key and t.expenses > 0]
    avg = sum(past) / len(past) if past else 0.0

    fixed = (await commitments_service.get_commitments(
        session, workspace_id, user_id, app_today(), account_ids
    )).fixed_total
    return EmergencyFund(
        primary_currency=primary, liquid_total=liquid, avg_monthly_expenses=avg,
        months_covered=liquid / avg if avg > 0 else None,
        fixed_monthly=fixed,
        months_covered_fixed=liquid / fixed if fixed > 0 else None,
    )


# --- Block 9: goals pace ----------------------------------------------------

async def get_goals_pace(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID, limit: int = 4,
) -> list[GoalPace]:
    goals = (await session.execute(
        select(Goal).where(Goal.workspace_id == workspace_id, Goal.status == "active")
        .order_by(Goal.position, Goal.created_at).limit(limit)
    )).scalars().all()
    today = app_today()
    result: list[GoalPace] = []
    for goal in goals:
        read = await goal_service._enrich_goal(session, goal, user_id)
        current = float(read.current_amount)
        target = float(goal.target_amount)
        remaining = max(target - current, 0.0)
        created = goal.created_at.date() if goal.created_at else today
        months_elapsed = max((today - created).days / 30.44, 1.0)
        actual = max((current - float(goal.initial_amount)) / months_elapsed, 0.0)
        projected: Optional[date] = None
        if remaining == 0:
            projected = today
        elif actual > 0:
            projected = today + timedelta(days=round(remaining / actual * 30.44))
        result.append(GoalPace(
            id=str(goal.id), name=goal.name, currency=goal.currency,
            icon=goal.icon, color=goal.color, percentage=read.percentage,
            remaining=remaining,
            target_date=goal.target_date.isoformat() if goal.target_date else None,
            required_monthly=read.monthly_contribution, actual_monthly=actual,
            projected_date=projected.isoformat() if projected else None,
            on_track=read.on_track,
        ))
    return result


# --- Block 10: top payees ---------------------------------------------------

async def get_top_payees(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID,
    month: Optional[date] = None, account_ids: Optional[list[uuid.UUID]] = None, limit: int = 8,
) -> TopPayees:
    primary = await _primary_currency(session, user_id)
    month_start, month_end = dashboard_service._month_range(month or app_today())
    report_date = reporting_date_col(await get_credit_card_accounting_mode(session))
    name = func.coalesce(Payee.name, Transaction.payee)
    rows = (await session.execute(
        select(name, func.sum(_primary_amt()), func.count(), func.min(Transaction.payee_id))
        .select_from(Transaction)
        .outerjoin(Payee, Transaction.payee_id == Payee.id)
        .where(*_expense_filters(workspace_id, report_date, month_start, month_end, account_ids),
               name.is_not(None))
        .group_by(name)
        .order_by(func.sum(_primary_amt()).desc())
        .limit(limit)
    )).all()

    names = [r[0] for r in rows]
    seen_before: set = set()
    if names:
        earlier = await session.execute(
            select(func.coalesce(Payee.name, Transaction.payee))
            .select_from(Transaction)
            .outerjoin(Payee, Transaction.payee_id == Payee.id)
            .where(Transaction.workspace_id == workspace_id, Transaction.date < month_start,
                   func.coalesce(Payee.name, Transaction.payee).in_(names))
            .distinct()
        )
        seen_before = {r[0] for r in earlier.all()}

    total = float(await session.scalar(
        select(func.coalesce(func.sum(_primary_amt()), 0)).where(
            *_expense_filters(workspace_id, report_date, month_start, month_end, account_ids)
        )
    ) or 0)
    return TopPayees(
        primary_currency=primary, total_spent=total,
        items=[TopPayee(name=r[0], total=float(r[1] or 0), count=int(r[2]),
                        is_new=r[0] not in seen_before) for r in rows],
    )


# --- Block 11: subscriptions ------------------------------------------------

async def get_subscriptions(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID, limit: int = 8,
) -> Subscriptions:
    primary = await _primary_currency(session, user_id)
    rules = (await session.execute(
        select(RecurringTransaction)
        .outerjoin(Category, RecurringTransaction.category_id == Category.id)
        .where(
            RecurringTransaction.workspace_id == workspace_id,
            RecurringTransaction.is_active == True,  # noqa: E712
            RecurringTransaction.type == "debit",
            (RecurringTransaction.category_id.is_(None))
            | (Category.treat_as_transfer.is_not(True) & Category.is_ignored.is_not(True)),
        )
    )).scalars().all()

    items: list[SubscriptionItem] = []
    for rule in rules:
        factor = MONTHLY_FACTOR.get(rule.frequency)
        if factor is None:
            continue
        monthly = await _to_primary(session, float(rule.amount) * factor, rule.currency, primary)
        # The last two posted charges of this rule tell whether the price moved.
        last = (await session.execute(
            select(Transaction.amount).where(Transaction.recurring_transaction_id == rule.id)
            .order_by(Transaction.date.desc()).limit(2)
        )).scalars().all()
        change = None
        if len(last) == 2 and last[1] > 0 and last[0] != last[1]:
            change = float((last[0] - last[1]) / last[1] * 100)
        items.append(SubscriptionItem(
            description=rule.description, monthly_cost=monthly,
            frequency=rule.frequency, price_change_pct=change,
        ))
    items.sort(key=lambda i: -i.monthly_cost)
    monthly_total = sum(i.monthly_cost for i in items)
    return Subscriptions(
        primary_currency=primary, monthly_total=monthly_total,
        yearly_total=monthly_total * 12, items=items[:limit],
    )


# --- Block 12: investments --------------------------------------------------

async def get_investments(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID,
) -> InvestmentsSummary:
    primary = await _primary_currency(session, user_id)
    assets = (await session.execute(
        select(Asset).where(
            Asset.workspace_id == workspace_id, Asset.type == "investment",
            Asset.is_archived == False, Asset.sell_date.is_(None),  # noqa: E712
        )
    )).scalars().all()
    total_value = total_cost = realized = 0.0
    by_type: dict[str, float] = defaultdict(float)
    for asset in assets:
        value = asset_service._compute_current_value(
            asset, await asset_service._get_latest_value(session, asset.id)
        )
        if value is None:
            continue
        value_p = await _to_primary(session, value, asset.currency, primary)
        total_value += value_p
        by_type[asset.name] += value_p
        if asset.units and asset.average_price:
            cost = float(asset.units) * float(asset.average_price)
        elif asset.purchase_price is not None:
            cost = float(asset.purchase_price)
        else:
            cost = value
        total_cost += await _to_primary(session, cost, asset.currency, primary)
        if asset.realized_gain:
            realized += await _to_primary(session, float(asset.realized_gain), asset.currency, primary)
    gain = total_value - total_cost
    return InvestmentsSummary(
        primary_currency=primary, total_value=total_value, total_cost=total_cost,
        unrealized_gain=gain, unrealized_pct=(gain / total_cost * 100) if total_cost > 0 else None,
        realized_gain=realized,
        allocation=[InvestmentAllocation(name=k, value=v) for k, v in sorted(by_type.items(), key=lambda kv: -kv[1])],
    )


# --- Block 13: anomalies ----------------------------------------------------

async def get_anomalies(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID,
    account_ids: Optional[list[uuid.UUID]] = None, recent_days: int = 30, history_days: int = 180,
    limit: int = 6,
) -> list[Anomaly]:
    today = app_today()
    recent_start = today - timedelta(days=recent_days)
    stmt = (
        select(Transaction, Category.name)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.workspace_id == workspace_id,
            Transaction.type == "debit",
            Transaction.status == "posted",
            Transaction.source != "opening_balance",
            Transaction.date >= today - timedelta(days=history_days),
            Transaction.date <= today,
            counts_as_user_pnl(),
        )
    )
    if account_ids:
        stmt = stmt.where(Transaction.account_id.in_(account_ids))
    rows = (await session.execute(stmt)).all()

    history: dict = defaultdict(list)
    for tx, _ in rows:
        if tx.category_id and tx.date < recent_start:
            history[tx.category_id].append(float(tx.amount_primary if tx.amount_primary is not None else tx.amount))

    found: list[Anomaly] = []
    flagged: set = set()
    for tx, cat_name in rows:
        if tx.date < recent_start:
            continue
        amount = float(tx.amount_primary if tx.amount_primary is not None else tx.amount)
        samples = history.get(tx.category_id, [])
        if len(samples) >= 5:
            mean, sd = statistics.fmean(samples), statistics.pstdev(samples)
            if sd > 0 and amount > mean + 2 * sd and amount > 1.5 * mean:
                flagged.add(tx.id)
                found.append(Anomaly(
                    kind="unusual_amount", transaction_id=str(tx.id), description=tx.description,
                    amount=amount, date=tx.date.isoformat(), category_name=cat_name, detail=mean,
                ))

    # Same account, amount and description within a day of each other.
    recent = [(tx, cat) for tx, cat in rows if tx.date >= recent_start]
    recent.sort(key=lambda r: (r[0].account_id, r[0].description, r[0].amount, r[0].date))
    for (a, _), (b, cat_b) in zip(recent, recent[1:]):
        if (
            a.account_id == b.account_id and a.description == b.description
            and a.amount == b.amount and (b.date - a.date).days <= 1
            and a.recurring_transaction_id is None and b.installment_number is None
            and b.id not in flagged
        ):
            flagged.add(b.id)
            amount = float(b.amount_primary if b.amount_primary is not None else b.amount)
            found.append(Anomaly(
                kind="possible_duplicate", transaction_id=str(b.id), description=b.description,
                amount=amount, date=b.date.isoformat(), category_name=cat_b,
            ))
    found.sort(key=lambda a: (a.date, a.amount), reverse=True)
    return found[:limit]


# --- Block 14: weekday heatmap ----------------------------------------------

async def get_spending_by_weekday(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID,
    account_ids: Optional[list[uuid.UUID]] = None, days: int = 90,
) -> SpendingByWeekday:
    primary = await _primary_currency(session, user_id)
    today = app_today()
    start = today - timedelta(days=days - 1)
    report_date = reporting_date_col(await get_credit_card_accounting_mode(session))
    rows = (await session.execute(
        select(report_date, _primary_amt()).where(
            *_expense_filters(workspace_id, report_date, start, today + timedelta(days=1), account_ids)
        )
    )).all()
    totals = [0.0] * 7
    for d, amount in rows:
        totals[d.weekday()] += float(amount or 0)
    occurrences = [0] * 7
    for i in range(days):
        occurrences[(start + timedelta(days=i)).weekday()] += 1
    return SpendingByWeekday(
        primary_currency=primary, days=days,
        items=[WeekdaySpending(weekday=w, total=totals[w], average=totals[w] / occurrences[w] if occurrences[w] else 0.0)
               for w in range(7)],
    )


# --- Derived health metrics -------------------------------------------------

async def get_health_metrics(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID,
    account_ids: Optional[list[uuid.UUID]] = None,
) -> HealthMetrics:
    from app.services import report_service

    primary = await _primary_currency(session, user_id)
    today = app_today()
    month_key = today.strftime("%Y-%m")
    out = HealthMetrics(primary_currency=primary)

    trend = await dashboard_service.get_monthly_trend(session, workspace_id, user_id, 4, account_ids)
    past = [t for t in trend if t.month < month_key]
    income_3m = sum(t.income for t in past)
    expense_3m = sum(t.expenses for t in past)
    if income_3m > 0:
        out.savings_rate_3m = (income_3m - expense_3m) / income_3m * 100
    if past:
        out.avg_daily_expense = expense_3m / (len(past) * 30.44)

    report_date = reporting_date_col(await get_credit_card_accounting_mode(session))
    month_start = today.replace(day=1)
    cat_rows = (await session.execute(
        select(Transaction.category_id, func.sum(_primary_amt()))
        .where(*_expense_filters(workspace_id, report_date, month_start, today + timedelta(days=1), account_ids))
        .group_by(Transaction.category_id)
    )).all()
    amounts = sorted((float(r[1] or 0) for r in cat_rows), reverse=True)
    if sum(amounts) > 0:
        out.top3_concentration = sum(amounts[:3]) / sum(amounts) * 100

    current = next((t for t in trend if t.month == month_key), None)
    fixed = (await commitments_service.get_commitments(
        session, workspace_id, user_id, today, account_ids
    )).fixed_total
    income_ref = current.income if current and current.income > 0 else (income_3m / len(past) if past else 0)
    if fixed > 0 and income_ref > 0:
        out.fixed_coverage = income_ref / fixed

    accounts = await account_service.get_accounts(session, workspace_id)
    card_debt = 0.0
    for acc in accounts:
        if acc["type"] == "credit_card" and (account_ids is None or acc["id"] in set(account_ids)):
            card_debt += await _to_primary(session, max(-float(acc["current_balance"]), 0.0), acc["currency"], primary)
    if income_ref > 0:
        out.card_debt_to_income = card_debt / income_ref * 100

    if account_ids is None:
        now = await report_service._net_worth_at(session, workspace_id, today, primary)
        for attr, days in (("30d", 30), ("12m", 365)):
            before = await report_service._net_worth_at(session, workspace_id, today - timedelta(days=days), primary)
            diff = now.value - before.value
            setattr(out, f"net_worth_change_{attr}", diff)
            if before.value != 0:
                setattr(out, f"net_worth_change_{attr}_pct", diff / abs(before.value) * 100)

    since = today - timedelta(days=90)
    total_cat = await session.scalar(
        select(func.count()).select_from(Transaction).where(
            Transaction.workspace_id == workspace_id, Transaction.date >= since,
            Transaction.category_id.is_not(None), Transaction.source != "opening_balance",
        )
    ) or 0
    if total_cat:
        ai = await session.scalar(
            select(func.count()).select_from(AiCategorization)
            .join(Transaction, AiCategorization.transaction_id == Transaction.id)
            .where(
                AiCategorization.workspace_id == workspace_id,
                AiCategorization.status.in_(("applied", "approved")),
                Transaction.date >= since, Transaction.category_id.is_not(None),
            )
        ) or 0
        out.ai_categorized_pct = ai / total_cat * 100
    return out
