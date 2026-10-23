"""Tests for the fixed-commitments summary of a month."""
import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction


def _tx(user, account, desc, amount, **kw):
    today = date.today()
    return Transaction(
        id=uuid.uuid4(), user_id=user.id, account_id=account.id, description=desc,
        amount=Decimal(amount), currency="BRL", date=today, effective_date=today,
        type="debit", source="manual", status="posted", **kw,
    )


@pytest.mark.asyncio
async def test_commitments_splits_recurring_installments_and_ignores_variable(
    client, auth_headers, session, test_user, test_account
):
    rule_id = uuid.uuid4()
    far = date.today().replace(year=date.today().year + 1)
    session.add(RecurringTransaction(
        id=rule_id, user_id=test_user.id, account_id=test_account.id,
        description="NETFLIX", amount=Decimal("40.00"), currency="BRL", type="debit",
        frequency="monthly", start_date=far, next_occurrence=far, is_active=True,
    ))
    await session.flush()
    session.add_all([
        _tx(test_user, test_account, "NETFLIX", "40.00", recurring_transaction_id=rule_id),
        _tx(test_user, test_account, "SOFA", "300.00", installment_number=2, total_installments=10),
        _tx(test_user, test_account, "MERCADO", "120.00"),  # variable
    ])
    await session.commit()

    res = await client.get("/api/dashboard/commitments", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["recurring_total"] == pytest.approx(40.0)
    assert body["installments_total"] == pytest.approx(300.0)
    assert body["fixed_total"] == pytest.approx(340.0)
    assert [i["description"] for i in body["items"]] == ["SOFA", "NETFLIX"]
    assert body["items"][0]["installment"] == "2/10"


@pytest.mark.asyncio
async def test_commitments_empty_month(client, auth_headers):
    res = await client.get(
        "/api/dashboard/commitments", params={"month": "2001-01-01"}, headers=auth_headers
    )
    assert res.status_code == 200
    assert res.json()["fixed_total"] == 0
    assert res.json()["items"] == []
