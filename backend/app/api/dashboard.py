import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import WorkspaceContext, current_workspace
from app.schemas.dashboard import DashboardSummary, SpendingByCategory, MonthlyTrend, ProjectedTransaction, BalanceHistory, MonthOutlook, Commitments, InstallmentsOutlook, CategoryVarianceReport, EmergencyFund, GoalPace, TopPayees, Subscriptions, InvestmentsSummary, Anomaly, SpendingByWeekday, HealthMetrics
from app.services import commitments_service, dashboard_service, insights_service, month_outlook_service

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummary)
async def get_summary(
    month: Optional[date] = Query(None),
    balance_date: Optional[date] = Query(None),
    account_ids: Optional[list[uuid.UUID]] = Query(None),
    asset_group_ids: Optional[list[uuid.UUID]] = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await dashboard_service.get_summary(
        session, ctx.workspace.id, ctx.user_id, month, balance_date, account_ids,
        asset_group_ids,
    )


@router.get("/spending-by-category", response_model=list[SpendingByCategory])
async def get_spending_by_category(
    month: Optional[date] = Query(None),
    account_ids: Optional[list[uuid.UUID]] = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await dashboard_service.get_spending_by_category(
        session, ctx.workspace.id, ctx.user_id, month, account_ids
    )


@router.get("/monthly-trend", response_model=list[MonthlyTrend])
async def get_monthly_trend(
    months: int = Query(6, ge=1, le=12),
    account_ids: Optional[list[uuid.UUID]] = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await dashboard_service.get_monthly_trend(
        session, ctx.workspace.id, ctx.user_id, months, account_ids
    )


@router.get("/balance-history", response_model=BalanceHistory)
async def get_balance_history(
    month: Optional[date] = Query(None),
    account_ids: Optional[list[uuid.UUID]] = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await dashboard_service.get_balance_history(
        session, ctx.workspace.id, ctx.user_id, month, account_ids
    )


@router.get("/projected-transactions", response_model=list[ProjectedTransaction])
async def get_projected_transactions(
    month: Optional[date] = Query(None),
    account_id: Optional[uuid.UUID] = Query(None),
    from_date: Optional[date] = Query(None, alias="from"),
    to_date: Optional[date] = Query(None, alias="to"),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        return await dashboard_service.get_projected_transactions(
            session,
            ctx.workspace.id,
            ctx.user_id,
            month,
            account_id=account_id,
            from_date=from_date,
            to_date=to_date,
        )
    except ValueError as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/month-outlook", response_model=MonthOutlook)
async def get_month_outlook(
    month: Optional[date] = Query(None),
    account_ids: Optional[list[uuid.UUID]] = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await month_outlook_service.get_month_outlook(
        session, ctx.workspace.id, ctx.user_id, month, account_ids
    )


@router.get("/commitments", response_model=Commitments)
async def get_commitments(
    month: Optional[date] = Query(None),
    account_ids: Optional[list[uuid.UUID]] = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await commitments_service.get_commitments(
        session, ctx.workspace.id, ctx.user_id, month, account_ids
    )


# --- Insights ---------------------------------------------------------------

@router.get("/installments", response_model=InstallmentsOutlook)
async def get_installments(
    account_ids: Optional[list[uuid.UUID]] = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await insights_service.get_installments(session, ctx.workspace.id, ctx.user_id, account_ids)


@router.get("/category-variance", response_model=CategoryVarianceReport)
async def get_category_variance(
    account_ids: Optional[list[uuid.UUID]] = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await insights_service.get_category_variance(session, ctx.workspace.id, ctx.user_id, account_ids)


@router.get("/emergency-fund", response_model=EmergencyFund)
async def get_emergency_fund(
    account_ids: Optional[list[uuid.UUID]] = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await insights_service.get_emergency_fund(session, ctx.workspace.id, ctx.user_id, account_ids)


@router.get("/goals-pace", response_model=list[GoalPace])
async def get_goals_pace(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await insights_service.get_goals_pace(session, ctx.workspace.id, ctx.user_id)


@router.get("/top-payees", response_model=TopPayees)
async def get_top_payees(
    month: Optional[date] = Query(None),
    account_ids: Optional[list[uuid.UUID]] = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await insights_service.get_top_payees(session, ctx.workspace.id, ctx.user_id, month, account_ids)


@router.get("/subscriptions", response_model=Subscriptions)
async def get_subscriptions(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await insights_service.get_subscriptions(session, ctx.workspace.id, ctx.user_id)


@router.get("/investments", response_model=InvestmentsSummary)
async def get_investments(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await insights_service.get_investments(session, ctx.workspace.id, ctx.user_id)


@router.get("/anomalies", response_model=list[Anomaly])
async def get_anomalies(
    account_ids: Optional[list[uuid.UUID]] = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await insights_service.get_anomalies(session, ctx.workspace.id, ctx.user_id, account_ids)


@router.get("/spending-by-weekday", response_model=SpendingByWeekday)
async def get_spending_by_weekday(
    account_ids: Optional[list[uuid.UUID]] = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await insights_service.get_spending_by_weekday(session, ctx.workspace.id, ctx.user_id, account_ids)


@router.get("/health-metrics", response_model=HealthMetrics)
async def get_health_metrics(
    account_ids: Optional[list[uuid.UUID]] = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await insights_service.get_health_metrics(session, ctx.workspace.id, ctx.user_id, account_ids)
