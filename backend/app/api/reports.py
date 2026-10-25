import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import WorkspaceContext, current_workspace
from app.schemas.report import (
    AnnualSummary,
    BudgetReport,
    CategoryReport,
    ReportResponse,
    TaxSummary,
)
from app.services import report_export, report_service, report_tables_service
from app.services.report_filters import ReportFilters, report_filters

router = APIRouter(prefix="/api/reports", tags=["reports"])

# Cap the user-picked custom range so wide windows can't tie up the DB with
# per-day snapshot fan-out. Ten years covers every realistic personal-finance
# question and stays inside the existing daily-interval budget.
_MAX_CUSTOM_RANGE_YEARS = 10


def _add_years(value: date, years: int) -> date:
    """Add whole calendar years to a date.

    Clamps Feb 29 to Feb 28 when the target year isn't a leap year, so the
    result is always a valid calendar date rather than raising.
    """
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return date(value.year + years, 2, 28)


def _financial_year_start_month(tax_jurisdiction: str | None) -> int:
    """Return the first month of the workspace's financial year."""
    return 4 if (tax_jurisdiction or "").upper() == "IN" else 1


def _reject_unsupported_fiscal_year_report(
    period: str | None, interval: str, financial_year_start_month: int
) -> None:
    if period == "ytd" and interval == "yearly" and financial_year_start_month != 1:
        raise HTTPException(
            status_code=422,
            detail="Yearly YTD reports are not supported for non-calendar financial years",
        )


def _resolve_custom_range(
    start_date: date | None, end_date: date | None
) -> tuple[date | None, date | None]:
    """Validate a user-supplied custom range and return the (start, end) pair.

    Both endpoints must be provided together; the range must be non-empty,
    end no later than today and stay within :data:`_MAX_CUSTOM_RANGE_YEARS`
    calendar years of start_date. Returns ``(None, None)`` when neither is
    set, so callers can use presets.
    """
    if start_date is None and end_date is None:
        return None, None
    if start_date is None or end_date is None:
        raise HTTPException(
            status_code=422,
            detail="start_date and end_date must be provided together",
        )
    if end_date < start_date:
        raise HTTPException(
            status_code=422,
            detail="end_date must be on or after start_date",
        )
    if end_date > date.today():
        raise HTTPException(
            status_code=422,
            detail="end_date must be on or before today",
        )
    max_end_date = _add_years(start_date, _MAX_CUSTOM_RANGE_YEARS)
    if end_date > max_end_date:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Custom range is too wide (max {_MAX_CUSTOM_RANGE_YEARS} years)"
            ),
        )
    return start_date, end_date


@router.get("/net-worth", response_model=ReportResponse)
async def get_net_worth(
    months: int = Query(12, ge=1, le=24),
    interval: str = Query("monthly", pattern="^(daily|weekly|monthly|yearly)$"),
    account_ids: Optional[list[uuid.UUID]] = Query(None),
    asset_group_ids: Optional[list[uuid.UUID]] = Query(None),
    period: str | None = Query(None, pattern="^ytd$"),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    financial_year_start_month = _financial_year_start_month(ctx.workspace.tax_jurisdiction)
    custom_start, custom_end = _resolve_custom_range(start_date, end_date)
    effective_period = None if custom_start is not None else period
    _reject_unsupported_fiscal_year_report(
        effective_period, interval, financial_year_start_month
    )
    return await report_service.get_net_worth_report(
        session, ctx.workspace.id, ctx.user_id, months, interval, ctx.user.primary_currency,
        account_ids=account_ids, asset_group_ids=asset_group_ids, period=effective_period,
        financial_year_start_month=financial_year_start_month,
        start_date=custom_start, end_date=custom_end,
    )


@router.get("/income-expenses", response_model=ReportResponse)
async def get_income_expenses(
    months: int = Query(12, ge=1, le=24),
    interval: str = Query("monthly", pattern="^(daily|weekly|monthly|yearly)$"),
    account_ids: Optional[list[uuid.UUID]] = Query(None),
    period: str | None = Query(None, pattern="^ytd$"),
    days: Optional[int] = Query(None, ge=1, le=730),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    filters: ReportFilters = Depends(report_filters),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """`days` overrides `months` with an exact rolling window ending today.

    Alternatively `start_date`/`end_date` (both required together) pin the
    window to an explicit historical calendar range, overriding `months`,
    `period`, and `days`. Custom ranges include actuals only, without estimates.
    """
    financial_year_start_month = _financial_year_start_month(ctx.workspace.tax_jurisdiction)
    custom_start, custom_end = _resolve_custom_range(start_date, end_date)
    effective_period = None if custom_start is not None else period
    _reject_unsupported_fiscal_year_report(
        effective_period, interval, financial_year_start_month
    )
    return await report_service.get_income_expenses_report(
        session, ctx.workspace.id, ctx.user_id, months, interval, ctx.user.primary_currency,
        account_ids=account_ids, period=effective_period, days=days,
        financial_year_start_month=financial_year_start_month,
        start_date=custom_start, end_date=custom_end,
        filters=await filters.resolve(session, ctx.workspace.id),
    )


@router.get("/cash-flow", response_model=ReportResponse)
async def get_cash_flow(
    months: int = Query(6, ge=1, le=12),
    interval: str = Query("daily", pattern="^(daily|weekly|monthly)$"),
    baseline: bool = Query(False),
    account_ids: Optional[list[uuid.UUID]] = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await report_service.get_cash_flow_report(
        session, ctx.workspace.id, ctx.user_id, months, interval, ctx.user.primary_currency,
        baseline=baseline, account_ids=account_ids,
    )


# --- Category, budget, annual and tax reports --------------------------------

@router.get("/categories", response_model=CategoryReport)
async def get_categories_report(
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    kind: str = Query("expenses", pattern="^(expenses|income)$"),
    account_ids: Optional[list[uuid.UUID]] = Query(None),
    filters: ReportFilters = Depends(report_filters),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Ranking of categories for a window, against the window just before it."""
    return await report_tables_service.get_category_report(
        session, ctx.workspace.id, ctx.user_id, start_date, end_date, kind, account_ids,
        await filters.resolve(session, ctx.workspace.id),
    )


@router.get("/budget-vs-actual", response_model=BudgetReport)
async def get_budget_report(
    year: int | None = Query(None, ge=2000, le=2100),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await report_tables_service.get_budget_report(session, ctx.workspace.id, ctx.user_id, year)


@router.get("/annual-summary", response_model=AnnualSummary)
async def get_annual_summary(
    year: int | None = Query(None, ge=2000, le=2100),
    account_ids: Optional[list[uuid.UUID]] = Query(None),
    filters: ReportFilters = Depends(report_filters),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await report_tables_service.get_annual_summary(
        session, ctx.workspace.id, ctx.user_id, year, account_ids,
        await filters.resolve(session, ctx.workspace.id),
    )


@router.get("/tax-summary", response_model=TaxSummary)
async def get_tax_summary(
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    account_ids: Optional[list[uuid.UUID]] = Query(None),
    filters: ReportFilters = Depends(report_filters),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Income and expenses by category, then payee. Defaults to last calendar year."""
    return await report_tables_service.get_tax_summary(
        session, ctx.workspace.id, ctx.user_id, start_date, end_date, account_ids,
        await filters.resolve(session, ctx.workspace.id),
    )


_EXPORTS = "^(income-expenses|categories|annual-summary|tax-summary|budget-vs-actual|transactions)$"


@router.get("/export")
async def export_report(
    report: str = Query(..., pattern=_EXPORTS),
    format: str = Query("csv", pattern="^(csv|pdf)$"),
    lang: str | None = Query(None, max_length=10),
    delimiter: str = Query(",", pattern="^[,;]$"),
    year: int | None = Query(None, ge=2000, le=2100),
    kind: str = Query("expenses", pattern="^(expenses|income)$"),
    months: int = Query(12, ge=1, le=24),
    interval: str = Query("monthly", pattern="^(daily|weekly|monthly|yearly)$"),
    period: str | None = Query(None, pattern="^ytd$"),
    days: Optional[int] = Query(None, ge=1, le=730),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    account_ids: Optional[list[uuid.UUID]] = Query(None),
    filters: ReportFilters = Depends(report_filters),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Download a report as CSV or PDF, with the same filters the screen applies."""
    workspace_id, user_id = ctx.workspace.id, ctx.user_id
    resolved = await filters.resolve(session, workspace_id)

    if report == "categories":
        table = report_export.category_table(
            await report_tables_service.get_category_report(
                session, workspace_id, user_id, start_date, end_date, kind, account_ids, resolved), lang)
    elif report == "annual-summary":
        table = report_export.annual_table(
            await report_tables_service.get_annual_summary(
                session, workspace_id, user_id, year, account_ids, resolved), lang)
    elif report == "tax-summary":
        table = report_export.tax_table(
            await report_tables_service.get_tax_summary(
                session, workspace_id, user_id, start_date, end_date, account_ids, resolved), lang)
    elif report == "budget-vs-actual":
        table = report_export.budget_table(
            await report_tables_service.get_budget_report(session, workspace_id, user_id, year), lang)
    elif report == "transactions":
        table = await report_export.transactions_table(
            session, workspace_id, user_id, start_date, end_date, lang, account_ids, resolved)
    else:
        fy_start = _financial_year_start_month(ctx.workspace.tax_jurisdiction)
        custom_start, custom_end = _resolve_custom_range(start_date, end_date)
        table = report_export.income_expenses_table(
            await report_service.get_income_expenses_report(
                session, workspace_id, user_id, months, interval, ctx.user.primary_currency,
                account_ids=account_ids, period=None if custom_start else period, days=days,
                financial_year_start_month=fy_start, start_date=custom_start, end_date=custom_end,
                filters=resolved),
            lang)

    if format == "pdf":
        body, media = report_export.to_pdf(table), "application/pdf"
    else:
        decimal = "," if delimiter == ";" else "."
        body, media = report_export.to_csv(table, delimiter, decimal), "text/csv; charset=utf-8"
    filename = f"securo-{report}-{date.today().isoformat()}.{format}"
    return Response(content=body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"'})
