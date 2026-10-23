"""Tests for the month outlook (safe to spend + spending pace)."""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.schemas.dashboard import MonthlyTrend
from app.services import dashboard_service
from app.services.month_outlook_service import compute_pace


@pytest.fixture(autouse=True)
def stub_monthly_trend(monkeypatch):
    """get_monthly_trend uses Postgres to_char; the SQLite test backend has no such function."""
    async def _trend(*args, **kwargs):
        return [MonthlyTrend(month=date.today().strftime("%Y-%m"), income=0.0, expenses=0.0)]

    monkeypatch.setattr(dashboard_service, "get_monthly_trend", _trend)


def test_pace_on_track_when_projection_stays_under_limit():
    pace = compute_pace(spent=300, limit=1000, day_of_month=10, days_in_month=30)
    assert pace["status"] == "on_track"
    assert pace["projected"] == pytest.approx(900)
    assert pace["exhaustion_day"] is None


def test_pace_warns_with_exhaustion_day_when_limit_runs_out_early():
    # 50/day against 1000 -> the limit is reached on day 20.
    pace = compute_pace(spent=500, limit=1000, day_of_month=10, days_in_month=30)
    assert pace["status"] == "warning"
    assert pace["exhaustion_day"] == 20


def test_pace_over_once_limit_is_crossed():
    pace = compute_pace(spent=1200, limit=1000, day_of_month=15, days_in_month=30)
    assert pace["status"] == "over"
    assert pace["exhaustion_day"] is None


def test_pace_with_no_spending_is_on_track():
    pace = compute_pace(spent=0, limit=1000, day_of_month=1, days_in_month=31)
    assert pace["status"] == "on_track"
    assert pace["projected"] == 0


@pytest.mark.asyncio
async def test_outlook_other_month_is_empty(client, auth_headers):
    first = date.today().replace(day=1)
    prev = (first - timedelta(days=1)).replace(day=1)
    res = await client.get(
        "/api/dashboard/month-outlook", params={"month": prev.isoformat()}, headers=auth_headers
    )
    assert res.status_code == 200
    body = res.json()
    assert body["is_current_month"] is False
    assert body["safe_to_spend"] is None
    assert body["pace"] is None


@pytest.mark.asyncio
async def test_outlook_current_month_subtracts_upcoming_expenses(
    client, auth_headers, session, test_user, test_account
):
    today = date.today()
    next_month = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
    if today.day == (next_month - timedelta(days=1)).day:
        pytest.skip("last day of month: nothing is left to forecast")
    session.add(Transaction(
        id=uuid.uuid4(), user_id=test_user.id, account_id=test_account.id,
        description="FUTURE BILL", amount=Decimal("200.00"), currency="BRL",
        date=next_month - timedelta(days=1), effective_date=next_month - timedelta(days=1),
        type="debit", source="manual", status="pending",
    ))
    await session.commit()

    res = await client.get("/api/dashboard/month-outlook", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["is_current_month"] is True
    sts = body["safe_to_spend"]
    assert sts["upcoming_expenses"] == pytest.approx(200.0)
    assert sts["safe_to_spend"] == pytest.approx(
        sts["available_now"] + sts["upcoming_income"] - 200.0 - sts["card_bills_due"]
    )
    assert body["pace"]["status"] in {"on_track", "warning", "over", "no_reference"}


@pytest.mark.asyncio
async def test_outlook_includes_recurring_debits_not_yet_posted(
    client, auth_headers, session, test_user, test_account
):
    today = date.today()
    next_month = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
    due = next_month - timedelta(days=1)
    if due <= today:
        pytest.skip("last day of month: nothing is left to forecast")
    session.add(RecurringTransaction(
        id=uuid.uuid4(), user_id=test_user.id, account_id=test_account.id,
        description="RENT", amount=Decimal("500.00"), currency="BRL", type="debit",
        frequency="monthly", start_date=due, next_occurrence=due,
        day_of_month=due.day, is_active=True,
    ))
    await session.commit()

    res = await client.get("/api/dashboard/month-outlook", headers=auth_headers)
    assert res.json()["safe_to_spend"]["upcoming_expenses"] == pytest.approx(500.0)
