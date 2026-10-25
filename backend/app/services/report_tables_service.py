"""Tabular reports: spending by category, budget vs actual, annual summary, tax summary.

All of them count what the income/expenses report counts (posted rows that
``counts_as_user_pnl`` accepts, bucketed by the reporting date) in the user's
primary currency. Group-split offsets are not applied: amounts are the full
transaction value, and a report narrowed by any filter behaves the same way in
``report_service``.
"""
import uuid
from collections import defaultdict
from datetime import date, timedelta
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.core.config import get_settings
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.payee import Payee
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.report import (
    AnnualRow,
    AnnualSummary,
    BudgetReport,
    BudgetReportRow,
    CategoryReport,
    CategoryReportItem,
    TaxCategory,
    TaxPayee,
    TaxSummary,
)
from app.services import budget_service
from app.services._query_filters import counts_as_user_pnl, reporting_date_col
from app.services.admin_service import get_credit_card_accounting_mode
from app.services.report_filters import ReportFilters

MAX_RANGE_MONTHS = 24
UNCATEGORIZED_COLOR = "#6B7280"


def _amount():
    return func.coalesce(Transaction.amount_primary, Transaction.amount)


def _month_key(d: date) -> str:
    return d.strftime("%Y-%m")


def _months_between(start: date, end: date) -> list[str]:
    out, cursor = [], start.replace(day=1)
    while cursor <= end:
        out.append(_month_key(cursor))
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    return out


def resolve_window(start: Optional[date], end: Optional[date]) -> tuple[date, date]:
    """Default to the current month so far; reject empty or very wide windows."""
    today = app_today()
    end = end or today
    start = start or end.replace(day=1)
    if end < start:
        raise HTTPException(status_code=422, detail="end_date must be on or after start_date")
    if len(_months_between(start, end)) > MAX_RANGE_MONTHS:
        raise HTTPException(status_code=422, detail=f"Range is too wide (max {MAX_RANGE_MONTHS} months)")
    return start, end


async def _context(session: AsyncSession, user_id: uuid.UUID):
    user = await session.get(User, user_id)
    primary = user.primary_currency if user else get_settings().default_currency
    report_date = reporting_date_col(await get_credit_card_accounting_mode(session))
    return primary, report_date


def _conditions(workspace_id, report_date, start, end, account_ids, filters: ReportFilters, kind: Optional[str]):
    conds = [
        Transaction.workspace_id == workspace_id,
        report_date >= start,
        report_date <= end,
        Transaction.source != "opening_balance",
        Transaction.status == "posted",
        counts_as_user_pnl(),
        *filters.conditions(),
    ]
    if account_ids is not None:
        conds.append(Transaction.account_id.in_(account_ids))
    if kind == "expenses":
        conds.append(Transaction.type == "debit")
    elif kind == "income":
        conds.append(Transaction.type == "credit")
    return conds


def _pct(delta: float, base: float) -> Optional[float]:
    return round(delta / base * 100, 2) if base else None


# --- Spending (or income) by category ---------------------------------------

async def get_category_report(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID,
    start: Optional[date], end: Optional[date], kind: str = "expenses",
    account_ids: Optional[list[uuid.UUID]] = None, filters: Optional[ReportFilters] = None,
) -> CategoryReport:
    filters = filters or ReportFilters()
    start, end = resolve_window(start, end)
    primary, report_date = await _context(session, user_id)
    length = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=length - 1)

    async def totals(a: date, b: date):
        rows = await session.execute(
            select(Transaction.category_id, func.sum(_amount()), func.count())
            .where(*_conditions(workspace_id, report_date, a, b, account_ids, filters, kind))
            .group_by(Transaction.category_id)
        )
        return {cid: (abs(float(total or 0)), int(n)) for cid, total, n in rows.all()}

    current = await totals(start, end)
    previous = await totals(prev_start, prev_end)

    label = func.to_char(report_date, "YYYY-MM")
    series_rows = await session.execute(
        select(Transaction.category_id, label, func.sum(_amount()))
        .where(*_conditions(workspace_id, report_date, start, end, account_ids, filters, kind))
        .group_by(Transaction.category_id, label)
    )
    months = _months_between(start, end)
    series: dict = defaultdict(lambda: {m: 0.0 for m in months})
    for cid, month, total in series_rows.all():
        series[cid][month] = abs(float(total or 0))

    cats = {
        c.id: (c, g.name if g else None)
        for c, g in (await session.execute(
            select(Category, CategoryGroup)
            .outerjoin(CategoryGroup, Category.group_id == CategoryGroup.id)
            .where(Category.workspace_id == workspace_id)
        )).all()
    }
    grand = sum(t for t, _ in current.values())
    prev_grand = sum(t for t, _ in previous.values())

    items: list[CategoryReportItem] = []
    for cid, (total, count) in current.items():
        cat, group_name = cats.get(cid, (None, None))
        prev_total = previous.get(cid, (0.0, 0))[0]
        items.append(CategoryReportItem(
            category_id=str(cid) if cid else None,
            name=cat.name if cat else "",
            color=cat.color if cat else UNCATEGORIZED_COLOR,
            icon=cat.icon if cat else "circle-help",
            group_name=group_name,
            total=round(total, 2),
            share_pct=round(total / grand * 100, 2) if grand else 0.0,
            count=count,
            previous_total=round(prev_total, 2),
            delta=round(total - prev_total, 2),
            delta_pct=_pct(total - prev_total, prev_total),
            series=[round(series[cid][m], 2) for m in months],
        ))
    items.sort(key=lambda i: -i.total)
    return CategoryReport(
        primary_currency=primary, kind=kind, start=start.isoformat(), end=end.isoformat(),
        previous_start=prev_start.isoformat(), previous_end=prev_end.isoformat(), months=months,
        total=round(grand, 2), previous_total=round(prev_grand, 2), items=items,
    )


# --- Annual summary ---------------------------------------------------------

async def get_annual_summary(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID, year: Optional[int],
    account_ids: Optional[list[uuid.UUID]] = None, filters: Optional[ReportFilters] = None,
) -> AnnualSummary:
    filters = filters or ReportFilters()
    year = year or app_today().year
    primary, report_date = await _context(session, user_id)
    label = func.to_char(report_date, "YYYY-MM")
    rows = await session.execute(
        select(Transaction.category_id, Transaction.type, label, func.sum(_amount()))
        .where(*_conditions(workspace_id, report_date, date(year, 1, 1), date(year, 12, 31), account_ids, filters, None))
        .group_by(Transaction.category_id, Transaction.type, label)
    )
    cells: dict = defaultdict(lambda: [0.0] * 12)
    for cid, tx_type, month, total in rows.all():
        kind = "income" if tx_type == "credit" else "expenses"
        cells[(cid, kind)][int(month[5:7]) - 1] += abs(float(total or 0))

    cats = {c.id: c for c in (await session.execute(
        select(Category).where(Category.workspace_id == workspace_id)
    )).scalars().all()}
    out: list[AnnualRow] = []
    income_months, expense_months = [0.0] * 12, [0.0] * 12
    for (cid, kind), months in cells.items():
        cat = cats.get(cid)
        out.append(AnnualRow(
            category_id=str(cid) if cid else None, name=cat.name if cat else "",
            color=cat.color if cat else UNCATEGORIZED_COLOR, kind=kind,
            months=[round(m, 2) for m in months], total=round(sum(months), 2),
        ))
        target = income_months if kind == "income" else expense_months
        for i, value in enumerate(months):
            target[i] += value
    out.sort(key=lambda r: (r.kind != "income", -r.total))
    return AnnualSummary(
        primary_currency=primary, year=year, rows=out,
        income_months=[round(v, 2) for v in income_months],
        expense_months=[round(v, 2) for v in expense_months],
        net_months=[round(i - e, 2) for i, e in zip(income_months, expense_months)],
    )


# --- Tax summary (income and expenses by category, then payee) --------------

async def get_tax_summary(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID,
    start: Optional[date], end: Optional[date],
    account_ids: Optional[list[uuid.UUID]] = None, filters: Optional[ReportFilters] = None,
) -> TaxSummary:
    filters = filters or ReportFilters()
    today = app_today()
    start = start or date(today.year - 1, 1, 1)
    end = end or date(today.year - 1, 12, 31)
    start, end = resolve_window(start, end)
    primary, report_date = await _context(session, user_id)
    payee_name = func.coalesce(Payee.name, Transaction.payee)
    rows = await session.execute(
        select(Transaction.category_id, Transaction.type, payee_name, func.sum(_amount()), func.count())
        .select_from(Transaction)
        .outerjoin(Payee, Transaction.payee_id == Payee.id)
        .where(*_conditions(workspace_id, report_date, start, end, account_ids, filters, None))
        .group_by(Transaction.category_id, Transaction.type, payee_name)
    )
    grouped: dict = defaultdict(list)
    for cid, tx_type, name, total, count in rows.all():
        kind = "income" if tx_type == "credit" else "expenses"
        grouped[(cid, kind)].append(TaxPayee(name=name or None, total=round(abs(float(total or 0)), 2), count=int(count)))

    cats = {c.id: c for c in (await session.execute(
        select(Category).where(Category.workspace_id == workspace_id)
    )).scalars().all()}
    categories: list[TaxCategory] = []
    for (cid, kind), payees in grouped.items():
        payees.sort(key=lambda p: -p.total)
        categories.append(TaxCategory(
            kind=kind, category_id=str(cid) if cid else None,
            name=cats[cid].name if cid in cats else "",
            total=round(sum(p.total for p in payees), 2), count=sum(p.count for p in payees),
            payees=payees,
        ))
    categories.sort(key=lambda c: (c.kind != "income", -c.total))
    return TaxSummary(
        primary_currency=primary, start=start.isoformat(), end=end.isoformat(),
        income_total=round(sum(c.total for c in categories if c.kind == "income"), 2),
        expense_total=round(sum(c.total for c in categories if c.kind == "expenses"), 2),
        categories=categories,
    )


# --- Budget vs actual -------------------------------------------------------

async def get_budget_report(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID, year: Optional[int],
) -> BudgetReport:
    today = app_today()
    year = year or today.year
    primary, _ = await _context(session, user_id)
    through = 12 if year < today.year else (today.month if year == today.year else 0)

    rows: dict = {}
    for month in range(1, 13):
        # Same function the budgets screen uses, so both show the same numbers.
        for item in await budget_service.get_budget_vs_actual(session, workspace_id, user_id, date(year, month, 1)):
            row = rows.setdefault(str(item.category_id), {
                "item": item, "budget": [None] * 12, "actual": [0.0] * 12,
            })
            if item.budget_amount is not None:
                row["budget"][month - 1] = float(item.budget_amount)
            row["actual"][month - 1] = float(item.actual_amount)

    out: list[BudgetReportRow] = []
    for cid, row in rows.items():
        item = row["item"]
        if not any(b for b in row["budget"]) and not any(row["actual"]):
            continue
        out.append(BudgetReportRow(
            category_id=cid, name=item.category_name, color=item.category_color, icon=item.category_icon,
            group_name=item.group_name, budget=row["budget"],
            actual=[round(v, 2) for v in row["actual"]],
            budget_total=round(sum(b or 0 for b in row["budget"]), 2),
            actual_total=round(sum(row["actual"]), 2),
        ))
    out.sort(key=lambda r: (-r.budget_total, -r.actual_total))
    return BudgetReport(primary_currency=primary, year=year, through_month=through, rows=out)
