"""Category, annual, tax and budget reports, plus CSV and PDF export (real PostgreSQL)."""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.database import get_async_session
from app.main import app

from app.models.budget import Budget
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


def _tx(user, account, desc, amount, when=None, **kw):
    when = when or date.today()
    return Transaction(
        id=uuid.uuid4(), user_id=user.id, account_id=account.id, description=desc,
        amount=Decimal(amount), currency="BRL", date=when, effective_date=when,
        type=kw.pop("type", "debit"), source="manual", status="posted", **kw,
    )


async def test_category_report_ranks_and_compares_to_previous_window(
    client, auth_headers, session, test_user, test_account, test_categories
):
    today = date.today()
    a, b = test_categories[0], test_categories[1]
    window_start = today.replace(day=1)
    before = window_start - timedelta(days=3)
    session.add_all([
        _tx(test_user, test_account, "BIG", "300.00", today, category_id=a.id),
        _tx(test_user, test_account, "SMALL", "100.00", today, category_id=b.id),
        _tx(test_user, test_account, "OLD", "150.00", before, category_id=a.id),
    ])
    await session.commit()

    res = await client.get(
        "/api/reports/categories",
        params={"start_date": window_start.isoformat(), "end_date": today.isoformat()},
        headers=auth_headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert [i["name"] for i in body["items"][:2]] == [a.name, b.name]
    assert body["total"] == pytest.approx(400.0)
    assert body["items"][0]["share_pct"] == pytest.approx(75.0)
    assert body["items"][0]["previous_total"] == pytest.approx(150.0)
    assert body["items"][0]["delta"] == pytest.approx(150.0)
    assert body["items"][0]["delta_pct"] == pytest.approx(100.0)
    assert sum(body["items"][0]["series"]) == pytest.approx(300.0)


async def test_annual_summary_places_amounts_in_their_month(
    client, auth_headers, session, test_user, test_account, test_categories
):
    year = date.today().year
    cat = test_categories[0]
    session.add_all([
        _tx(test_user, test_account, "JAN", "10.00", date(year, 1, 15), category_id=cat.id),
        _tx(test_user, test_account, "MAR", "25.00", date(year, 3, 2), category_id=cat.id),
        _tx(test_user, test_account, "PAY", "1000.00", date(year, 3, 5), type="credit"),
    ])
    await session.commit()

    body = (await client.get("/api/reports/annual-summary", params={"year": year}, headers=auth_headers)).json()
    row = next(r for r in body["rows"] if r["kind"] == "expenses" and r["name"] == cat.name)
    assert row["months"][0] == pytest.approx(10.0)
    assert row["months"][2] == pytest.approx(25.0)
    assert body["income_months"][2] == pytest.approx(1000.0)
    assert body["net_months"][2] == pytest.approx(975.0)


async def test_tax_summary_groups_by_category_then_payee(
    client, auth_headers, session, test_user, test_account, test_categories
):
    year = date.today().year
    cat = test_categories[0]
    clinic = Payee(id=uuid.uuid4(), user_id=test_user.id, name="Clinica")
    session.add(clinic)
    await session.flush()
    session.add_all([
        _tx(test_user, test_account, "C1", "200.00", date(year, 2, 1), category_id=cat.id, payee_id=clinic.id),
        _tx(test_user, test_account, "C2", "300.00", date(year, 4, 1), category_id=cat.id, payee_id=clinic.id),
        _tx(test_user, test_account, "C3", "50.00", date(year, 5, 1), category_id=cat.id, payee="Farmacia"),
    ])
    await session.commit()

    body = (await client.get(
        "/api/reports/tax-summary",
        params={"start_date": f"{year}-01-01", "end_date": f"{year}-12-31", "category_ids": [str(cat.id)]},
        headers=auth_headers,
    )).json()
    assert body["expense_total"] == pytest.approx(550.0)
    payees = {p["name"]: p for p in body["categories"][0]["payees"]}
    assert payees["Clinica"]["total"] == pytest.approx(500.0) and payees["Clinica"]["count"] == 2
    assert payees["Farmacia"]["total"] == pytest.approx(50.0)


async def test_budget_report_pairs_budget_with_actual(
    client, auth_headers, session, test_user, test_account, test_categories
):
    today = date.today()
    cat = test_categories[0]
    session.add(Budget(
        id=uuid.uuid4(), user_id=test_user.id, category_id=cat.id, amount=Decimal("500.00"),
        month=today.replace(day=1), is_recurring=False, currency="BRL",
    ))
    session.add(_tx(test_user, test_account, "SPEND", "120.00", today, category_id=cat.id))
    await session.commit()

    body = (await client.get("/api/reports/budget-vs-actual", params={"year": today.year}, headers=auth_headers)).json()
    row = next(r for r in body["rows"] if r["name"] == cat.name)
    assert row["budget"][today.month - 1] == pytest.approx(500.0)
    assert row["actual"][today.month - 1] == pytest.approx(120.0)
    assert body["through_month"] == today.month


async def test_export_csv_and_pdf_respect_filters(
    client, auth_headers, session, test_user, test_account, test_categories
):
    cat, other = test_categories[0], test_categories[1]
    session.add_all([
        _tx(test_user, test_account, "KEEP ME", "80.00", category_id=cat.id),
        _tx(test_user, test_account, "DROP ME", "20.00", category_id=other.id),
    ])
    await session.commit()
    params = {"report": "transactions", "category_ids": [str(cat.id)], "delimiter": ";", "lang": "pt-BR"}

    csv_res = await client.get("/api/reports/export", params={**params, "format": "csv"}, headers=auth_headers)
    assert csv_res.status_code == 200
    assert csv_res.headers["content-type"].startswith("text/csv")
    text = csv_res.content.decode("utf-8-sig")
    assert text.splitlines()[0].startswith("Data;Descrição")
    assert "KEEP ME" in text and "DROP ME" not in text
    assert "-80,00" in text  # decimal comma goes with the semicolon delimiter

    pdf_res = await client.get("/api/reports/export", params={**params, "format": "pdf"}, headers=auth_headers)
    assert pdf_res.status_code == 200
    assert pdf_res.content.startswith(b"%PDF")


async def test_income_expenses_export_matches_screen_totals(client, auth_headers, test_transactions):
    res = await client.get(
        "/api/reports/export",
        params={"report": "income-expenses", "months": 1, "interval": "monthly", "format": "csv"},
        headers=auth_headers,
    )
    assert res.status_code == 200
    lines = res.content.decode("utf-8-sig").splitlines()
    assert lines[0] == "Period,Income,Expenses,Net"
    assert lines[-1].startswith("Total,")
