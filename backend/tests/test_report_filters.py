"""Category, payee, kind and amount filters on the income/expenses report (real PostgreSQL)."""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.database import get_async_session
from app.main import app
from app.models.category_group import CategoryGroup
from app.models.payee import Payee
from app.models.transaction import Transaction

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session(postgres_sessions):
    async with postgres_sessions() as session:
        yield session
        await session.rollback()


@pytest.fixture
def clean_db(postgres_sessions):
    """The reused user/data fixtures start in a fresh per-test PostgreSQL schema."""


@pytest_asyncio.fixture
async def client(postgres_sessions, monkeypatch):
    async def pg_session():
        async with postgres_sessions() as session:
            yield session

    monkeypatch.setitem(app.dependency_overrides, get_async_session, pg_session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


def _tx(user, account, desc, amount, **kw):
    today = date.today()
    return Transaction(
        id=uuid.uuid4(), user_id=user.id, account_id=account.id, description=desc,
        amount=Decimal(amount), currency="BRL", date=today, effective_date=today,
        type=kw.pop("type", "debit"), source="manual", status="posted", **kw,
    )


async def _expenses(client, headers, **params):
    res = await client.get(
        "/api/reports/income-expenses",
        params={"months": 1, "interval": "monthly", **params}, headers=headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    return next(b["value"] for b in body["summary"]["breakdowns"] if b["key"] == "expenses"), body


async def test_category_filter_includes_and_excludes(client, auth_headers, session, test_user, test_account, test_categories):
    a, b = test_categories[0], test_categories[1]
    session.add_all([
        _tx(test_user, test_account, "A", "100.00", category_id=a.id),
        _tx(test_user, test_account, "B", "40.00", category_id=b.id),
        _tx(test_user, test_account, "NONE", "7.00"),
    ])
    await session.commit()

    total, _ = await _expenses(client, auth_headers)
    only_a, body = await _expenses(client, auth_headers, category_ids=[str(a.id)])
    without_a, _ = await _expenses(client, auth_headers, category_ids=[str(a.id)], exclude_categories=True)
    uncategorized, _ = await _expenses(client, auth_headers, uncategorized=True)

    assert only_a == pytest.approx(100.0)
    assert without_a == pytest.approx(total - 100.0)
    assert uncategorized == pytest.approx(7.0)
    # composition follows the same filter, so the money map and the totals agree
    assert {c["label"] for c in body["composition"] if c["group"] == "expenses"} == {a.name}


async def test_category_group_filter_expands_to_members(client, auth_headers, session, test_user, test_account, test_categories):
    group = CategoryGroup(id=uuid.uuid4(), user_id=test_user.id, name="Casa")
    session.add(group)
    await session.flush()
    member = test_categories[0]
    member.group_id = group.id
    session.add_all([
        _tx(test_user, test_account, "IN", "30.00", category_id=member.id),
        _tx(test_user, test_account, "OUT", "55.00", category_id=test_categories[1].id),
    ])
    await session.commit()

    total, _ = await _expenses(client, auth_headers, category_group_ids=[str(group.id)])
    assert total == pytest.approx(30.0)


async def test_payee_kind_and_amount_filters(client, auth_headers, session, test_user, test_account):
    payee = Payee(id=uuid.uuid4(), user_id=test_user.id, name="Mercado")
    session.add(payee)
    await session.flush()
    session.add_all([
        _tx(test_user, test_account, "M1", "20.00", payee_id=payee.id),
        _tx(test_user, test_account, "BIG", "500.00"),
        _tx(test_user, test_account, "SOFA", "90.00", installment_number=1, total_installments=6),
    ])
    await session.commit()

    by_payee, _ = await _expenses(client, auth_headers, payee_ids=[str(payee.id)])
    installments, _ = await _expenses(client, auth_headers, kind="installment")
    one_off, _ = await _expenses(client, auth_headers, kind="one_off")
    large, _ = await _expenses(client, auth_headers, min_amount=100)
    small, _ = await _expenses(client, auth_headers, max_amount=100)

    assert by_payee == pytest.approx(20.0)
    assert installments == pytest.approx(90.0)
    assert one_off == pytest.approx(520.0)
    assert large == pytest.approx(500.0)
    assert small == pytest.approx(110.0)


async def test_filters_inactive_by_default():
    from app.services.report_filters import ReportFilters

    assert not ReportFilters().active
    assert ReportFilters().conditions() == []


async def test_comparison_reads_the_previous_window_with_the_same_filters(
    client, auth_headers, session, test_user, test_account, test_categories
):
    today = date.today()
    first = today.replace(day=1)
    keep, other = test_categories[0], test_categories[1]
    session.add_all([
        _tx(test_user, test_account, "NOW", "300.00", category_id=keep.id),
        _tx(test_user, test_account, "NOW2", "999.00", category_id=other.id),
    ])
    tx_prev = _tx(test_user, test_account, "PREV", "100.00", category_id=keep.id)
    tx_prev.date = tx_prev.effective_date = first - timedelta(days=1)
    session.add(tx_prev)
    await session.commit()

    res = await client.get(
        "/api/reports/income-expenses",
        params={"start_date": first.isoformat(), "end_date": today.isoformat(),
                "interval": "monthly", "category_ids": [str(keep.id)]},
        headers=auth_headers,
    )
    comparison = res.json()["comparison"]
    assert comparison["previous_expenses"] == pytest.approx(100.0)
    assert comparison["expenses_change_pct"] == pytest.approx(200.0)
    assert comparison["average_expenses"] == pytest.approx(300.0)
