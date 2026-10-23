"""Tests for the dashboard insight endpoints."""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.asset import Asset
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.schemas.dashboard import MonthlyTrend
from app.services import dashboard_service


@pytest.fixture(autouse=True)
def stub_monthly_trend(monkeypatch):
    """get_monthly_trend uses Postgres to_char; the SQLite test backend has no such function."""
    async def _trend(*args, **kwargs):
        first = date.today().replace(day=1)
        prev = (first - timedelta(days=1)).strftime("%Y-%m")
        return [
            MonthlyTrend(month=prev, income=5000.0, expenses=3000.0),
            MonthlyTrend(month=first.strftime("%Y-%m"), income=2000.0, expenses=500.0),
        ]

    monkeypatch.setattr(dashboard_service, "get_monthly_trend", _trend)


def _tx(user, account, desc, amount, when, **kw):
    return Transaction(
        id=uuid.uuid4(), user_id=user.id, account_id=account.id, description=desc,
        amount=Decimal(amount), currency="BRL", date=when, effective_date=when,
        type="debit", source="manual", status="posted", **kw,
    )


@pytest.mark.asyncio
async def test_installments_sum_future_rows_by_month(client, auth_headers, session, test_user, test_account):
    today = date.today()
    series = uuid.uuid4()
    for n in (1, 2, 3):
        when = today + timedelta(days=31 * n)
        session.add(_tx(test_user, test_account, "SOFA", "100.00", when,
                        installment_number=n, total_installments=3, installment_series_id=series))
    await session.commit()
    body = (await client.get("/api/dashboard/installments", headers=auth_headers)).json()
    assert body["remaining_total"] == pytest.approx(300.0)
    assert body["active_purchases"] == 1
    assert sum(m["amount"] for m in body["timeline"]) == pytest.approx(300.0)


@pytest.mark.asyncio
async def test_top_payees_marks_new_and_known(client, auth_headers, session, test_user, test_account):
    today = date.today()
    session.add_all([
        _tx(test_user, test_account, "A", "50.00", today, payee="Padaria"),
        _tx(test_user, test_account, "B", "200.00", today, payee="Mercado"),
        _tx(test_user, test_account, "C", "20.00", today.replace(day=1) - timedelta(days=40), payee="Mercado"),
    ])
    await session.commit()
    body = (await client.get("/api/dashboard/top-payees", headers=auth_headers)).json()
    names = [i["name"] for i in body["items"]]
    assert names == ["Mercado", "Padaria"]
    assert body["items"][0]["is_new"] is False
    assert body["items"][1]["is_new"] is True


@pytest.mark.asyncio
async def test_subscriptions_monthly_equivalent_and_price_change(client, auth_headers, session, test_user, test_account):
    rid = uuid.uuid4()
    today = date.today()
    session.add(RecurringTransaction(
        id=rid, user_id=test_user.id, account_id=test_account.id, description="SEGURO",
        amount=Decimal("1200.00"), currency="BRL", type="debit", frequency="yearly",
        start_date=today, next_occurrence=today + timedelta(days=300), is_active=True,
    ))
    await session.flush()
    session.add_all([
        _tx(test_user, test_account, "SEGURO", "1000.00", today - timedelta(days=365), recurring_transaction_id=rid),
        _tx(test_user, test_account, "SEGURO", "1200.00", today - timedelta(days=1), recurring_transaction_id=rid),
    ])
    await session.commit()
    body = (await client.get("/api/dashboard/subscriptions", headers=auth_headers)).json()
    assert body["monthly_total"] == pytest.approx(100.0)
    assert body["yearly_total"] == pytest.approx(1200.0)
    assert body["items"][0]["price_change_pct"] == pytest.approx(20.0)


@pytest.mark.asyncio
async def test_weekday_spending_totals_match(client, auth_headers, session, test_user, test_account):
    session.add(_tx(test_user, test_account, "X", "90.00", date.today()))
    await session.commit()
    body = (await client.get("/api/dashboard/spending-by-weekday", headers=auth_headers)).json()
    assert len(body["items"]) == 7
    assert sum(i["total"] for i in body["items"]) == pytest.approx(90.0)


@pytest.mark.asyncio
async def test_anomalies_flag_outlier_and_duplicate(client, auth_headers, session, test_user, test_account, test_categories):
    today = date.today()
    cat = test_categories[0].id
    for i in range(8):
        session.add(_tx(test_user, test_account, f"LUNCH{i}", f"{40 + i}.00", today - timedelta(days=60 + i * 3), category_id=cat))
    session.add(_tx(test_user, test_account, "FANCY DINNER", "900.00", today - timedelta(days=2), category_id=cat))
    session.add(_tx(test_user, test_account, "PHARMACY", "35.00", today - timedelta(days=5)))
    session.add(_tx(test_user, test_account, "PHARMACY", "35.00", today - timedelta(days=5)))
    await session.commit()
    kinds = {a["description"]: a["kind"] for a in (await client.get("/api/dashboard/anomalies", headers=auth_headers)).json()}
    assert kinds["FANCY DINNER"] == "unusual_amount"
    assert kinds["PHARMACY"] == "possible_duplicate"


@pytest.mark.asyncio
async def test_category_variance_reports_increase(client, auth_headers, session, test_user, test_account, test_categories):
    today = date.today()
    cat = test_categories[0].id
    prev = today.replace(day=1) - timedelta(days=1)
    session.add(_tx(test_user, test_account, "NOW", "500.00", today, category_id=cat))
    session.add(_tx(test_user, test_account, "BEFORE", "100.00", prev.replace(day=1), category_id=cat))
    await session.commit()
    body = (await client.get("/api/dashboard/category-variance", headers=auth_headers)).json()
    assert body["increases"] and body["increases"][0]["delta"] > 0


@pytest.mark.asyncio
async def test_emergency_fund_months_covered(client, auth_headers, test_account):
    body = (await client.get("/api/dashboard/emergency-fund", headers=auth_headers)).json()
    # 1500 in the checking account against the stubbed 3000 average expense.
    assert body["avg_monthly_expenses"] == pytest.approx(3000.0)
    assert body["months_covered"] == pytest.approx(body["liquid_total"] / 3000.0)


@pytest.mark.asyncio
async def test_investments_unrealized_gain(client, auth_headers, session, test_user):
    session.add(Asset(
        id=uuid.uuid4(), user_id=test_user.id, name="ACME", type="investment", currency="BRL",
        valuation_method="market_price", units=Decimal("10"), last_price=Decimal("15"),
        average_price=Decimal("10"),
    ))
    await session.commit()
    body = (await client.get("/api/dashboard/investments", headers=auth_headers)).json()
    assert body["total_value"] == pytest.approx(150.0)
    assert body["unrealized_gain"] == pytest.approx(50.0)
    assert body["unrealized_pct"] == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_goals_pace_and_health_metrics_respond(client, auth_headers, test_account):
    assert (await client.get("/api/dashboard/goals-pace", headers=auth_headers)).status_code == 200
    res = await client.get("/api/dashboard/health-metrics", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["savings_rate_3m"] == pytest.approx(40.0)
