"""What chose a transaction's category, and the API that tells the detail view."""
import uuid
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_categorization import AiCategorization
from app.models.transaction import Transaction
from app.services import ai_categorization_service as svc
from app.services import digest_service
from app.services.category_origin import set_category
from tests.test_ai_categorization import FakeProvider, _enable


def _by_description(transactions):
    return {t.description: t for t in transactions}


async def _origin(client, headers, tx_id):
    response = await client.get(f"/api/transactions/{tx_id}/category-origin", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def _create(client, headers, account, description, category=None):
    payload = {
        "account_id": str(account.id),
        "description": description,
        "amount": "10.00",
        "date": "2026-02-20",
        "type": "debit",
    }
    if category is not None:
        payload["category_id"] = str(category.id)
    response = await client.post("/api/transactions", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _rule(client, headers, category, contains, name="Regra de teste"):
    response = await client.post(
        "/api/rules",
        headers=headers,
        json={
            "name": name,
            "conditions_op": "and",
            "conditions": [{"field": "description", "op": "contains", "value": contains}],
            "actions": [{"op": "set_category", "value": str(category.id)}],
            "priority": 1,
            "is_active": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# set_category
# ---------------------------------------------------------------------------
def test_set_category_records_the_source_and_the_rule():
    tx, category, rule = SimpleNamespace(), uuid.uuid4(), uuid.uuid4()

    set_category(tx, category, "rule", rule)

    assert (tx.category_id, tx.category_source, tx.category_rule_id) == (category, "rule", rule)


def test_only_a_rule_keeps_a_rule_id():
    tx = SimpleNamespace()

    set_category(tx, uuid.uuid4(), "user", uuid.uuid4())

    assert tx.category_source == "user" and tx.category_rule_id is None


def test_removing_the_category_removes_its_origin():
    tx = SimpleNamespace()
    set_category(tx, uuid.uuid4(), "ai")

    set_category(tx, None, "ai")

    assert (tx.category_id, tx.category_source, tx.category_rule_id) == (None, None, None)


def test_an_unknown_source_is_refused():
    with pytest.raises(ValueError):
        set_category(SimpleNamespace(), uuid.uuid4(), "magic")


# ---------------------------------------------------------------------------
# The safety net: a path that forgets to say who chose is never wrong
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_category_changed_without_saying_who_loses_its_stale_origin(
    session: AsyncSession, test_transactions, test_categories
):
    tx = _by_description(test_transactions)["NETFLIX"]
    set_category(tx, test_categories[0].id, "ai")
    await session.commit()
    await session.refresh(tx)
    assert tx.category_source == "ai"

    tx.category_id = test_categories[1].id  # a path that does not know about origins
    await session.commit()
    await session.refresh(tx)

    assert tx.category_id == test_categories[1].id
    assert tx.category_source is None  # unknown, rather than a lie


@pytest.mark.asyncio
async def test_choosing_twice_as_a_person_keeps_the_origin(
    session: AsyncSession, test_transactions, test_categories
):
    tx = _by_description(test_transactions)["NETFLIX"]
    set_category(tx, test_categories[0].id, "user")
    await session.commit()

    set_category(tx, test_categories[1].id, "user")
    await session.commit()
    await session.refresh(tx)

    assert (tx.category_id, tx.category_source) == (test_categories[1].id, "user")


@pytest.mark.asyncio
async def test_changing_something_else_keeps_the_origin(
    session: AsyncSession, test_transactions, test_categories
):
    tx = _by_description(test_transactions)["NETFLIX"]
    set_category(tx, test_categories[0].id, "ai")
    await session.commit()

    tx.notes = "anotação"
    await session.commit()
    await session.refresh(tx)

    assert tx.category_source == "ai"


# ---------------------------------------------------------------------------
# API: who chose
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_category_picked_while_creating_is_the_users(
    client: AsyncClient, auth_headers, test_account, test_categories
):
    created = await _create(client, auth_headers, test_account, "Padaria", test_categories[0])

    origin = await _origin(client, auth_headers, created["id"])

    assert origin["source"] == "user" and origin["rule"] is None


@pytest.mark.asyncio
async def test_a_transaction_without_category_has_no_origin(
    client: AsyncClient, auth_headers, test_account
):
    created = await _create(client, auth_headers, test_account, "Sem categoria zzz")

    assert (await _origin(client, auth_headers, created["id"]))["source"] is None


@pytest.mark.asyncio
async def test_a_rule_that_matches_a_new_transaction_is_named(
    client: AsyncClient, auth_headers, test_account, test_categories
):
    rule = await _rule(client, auth_headers, test_categories[1], "PADARIA", name="Padarias")

    created = await _create(client, auth_headers, test_account, "PADARIA DO ZE")
    origin = await _origin(client, auth_headers, created["id"])

    assert created["category_id"] == str(test_categories[1].id)
    assert origin["source"] == "rule"
    assert origin["rule"] == {"id": rule["id"], "name": "Padarias"}
    assert origin["rule_removed"] is False


@pytest.mark.asyncio
async def test_a_rule_created_later_marks_the_transactions_it_categorized(
    client: AsyncClient, auth_headers, test_transactions, test_categories
):
    netflix = _by_description(test_transactions)["NETFLIX"]
    rule = await _rule(client, auth_headers, test_categories[0], "NETFLIX", name="Streaming")

    origin = await _origin(client, auth_headers, netflix.id)

    assert origin["source"] == "rule" and origin["rule"]["id"] == rule["id"]


@pytest.mark.asyncio
async def test_deleting_the_rule_keeps_saying_a_rule_did_it(
    client: AsyncClient, auth_headers, test_account, test_categories
):
    rule = await _rule(client, auth_headers, test_categories[1], "PADARIA")
    created = await _create(client, auth_headers, test_account, "PADARIA DO ZE")

    deleted = await client.delete(f"/api/rules/{rule['id']}", headers=auth_headers)
    assert deleted.status_code == 204
    origin = await _origin(client, auth_headers, created["id"])

    assert origin["source"] == "rule"
    assert origin["rule"] is None and origin["rule_removed"] is True


@pytest.mark.asyncio
async def test_changing_the_category_by_hand_takes_it_over_from_the_rule(
    client: AsyncClient, auth_headers, test_account, test_categories
):
    await _rule(client, auth_headers, test_categories[1], "PADARIA")
    created = await _create(client, auth_headers, test_account, "PADARIA DO ZE")

    patched = await client.patch(
        f"/api/transactions/{created['id']}",
        headers=auth_headers,
        json={"category_id": str(test_categories[2].id)},
    )
    assert patched.status_code == 200, patched.text
    origin = await _origin(client, auth_headers, created["id"])

    assert origin["source"] == "user" and origin["rule"] is None


@pytest.mark.asyncio
async def test_saving_the_same_category_again_does_not_claim_it(
    client: AsyncClient, auth_headers, test_account, test_categories
):
    await _rule(client, auth_headers, test_categories[1], "PADARIA")
    created = await _create(client, auth_headers, test_account, "PADARIA DO ZE")

    await client.patch(
        f"/api/transactions/{created['id']}",
        headers=auth_headers,
        json={"category_id": created["category_id"], "notes": "só uma nota"},
    )

    assert (await _origin(client, auth_headers, created["id"]))["source"] == "rule"


@pytest.mark.asyncio
async def test_clearing_the_category_clears_the_origin(
    client: AsyncClient, auth_headers, test_account, test_categories
):
    created = await _create(client, auth_headers, test_account, "Mercado", test_categories[0])

    await client.patch(
        f"/api/transactions/{created['id']}", headers=auth_headers, json={"category_id": None}
    )

    assert (await _origin(client, auth_headers, created["id"]))["source"] is None


@pytest.mark.asyncio
async def test_bulk_categorize_is_the_users(
    client: AsyncClient, auth_headers, test_transactions, test_categories
):
    ids = [str(t.id) for t in test_transactions[:2]]

    response = await client.patch(
        "/api/transactions/bulk-categorize",
        headers=auth_headers,
        json={"transaction_ids": ids, "category_id": str(test_categories[2].id)},
    )
    assert response.status_code == 200, response.text

    for tx_id in ids:
        assert (await _origin(client, auth_headers, tx_id))["source"] == "user"


@pytest.mark.asyncio
async def test_an_unknown_transaction_is_a_404(client: AsyncClient, auth_headers):
    response = await client.get(
        f"/api/transactions/{uuid.uuid4()}/category-origin", headers=auth_headers
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_the_origin_needs_a_login(client: AsyncClient, test_transactions):
    response = await client.get(f"/api/transactions/{test_transactions[0].id}/category-origin")
    assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# AI and the review flow
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_ai_is_named_with_its_confidence(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_workspace,
    test_categories, test_transactions,
):
    from unittest.mock import patch

    await _enable(session, test_user, test_workspace)
    netflix = _by_description(test_transactions)["NETFLIX"]
    provider = FakeProvider({"NETFLIX": ("Alimentação", 0.9)})

    with patch.object(
        svc.llm_connections, "build_provider_for_connection", return_value=provider
    ):
        await svc.categorize_pending(session, test_workspace.id)

    origin = await _origin(client, auth_headers, netflix.id)
    assert origin["source"] == "ai"
    assert origin["ai"]["status"] == "applied" and origin["ai"]["confidence"] == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_confirming_the_ai_keeps_it_the_ais_and_changing_it_makes_it_the_users(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_workspace,
    test_categories, test_transactions,
):
    from unittest.mock import patch

    await _enable(session, test_user, test_workspace)
    txs = _by_description(test_transactions)
    provider = FakeProvider({"NETFLIX": ("Alimentação", 0.9), "PIX RECEBIDO": ("Receita", 0.9)})
    with patch.object(
        svc.llm_connections, "build_provider_for_connection", return_value=provider
    ):
        await svc.categorize_pending(session, test_workspace.id)
    rows = {
        r.transaction_id: r for r in (await session.execute(select(AiCategorization))).scalars()
    }

    await digest_service.approve(session, test_workspace.id, rows[txs["NETFLIX"].id].id, test_user.id)
    await digest_service.choose(
        session, test_workspace.id, rows[txs["PIX RECEBIDO"].id].id, test_categories[1].id, test_user.id
    )

    assert (await _origin(client, auth_headers, txs["NETFLIX"].id))["source"] == "ai"
    assert (await _origin(client, auth_headers, txs["PIX RECEBIDO"].id))["source"] == "user"


@pytest.mark.asyncio
async def test_an_installment_series_picks_up_the_category_as_the_users(
    client: AsyncClient, auth_headers, test_account, test_categories
):
    created = await client.post(
        "/api/transactions/installments",
        headers=auth_headers,
        json={
            "base": {
                "account_id": str(test_account.id),
                "category_id": str(test_categories[0].id),
                "description": "Geladeira",
                "amount": "100.00",
                "date": "2026-02-10",
                "type": "debit",
            },
            "installments": 3,
        },
    )
    assert created.status_code == 201, created.text

    for tx in created.json():
        assert (await _origin(client, auth_headers, tx["id"]))["source"] == "user"


@pytest.mark.asyncio
async def test_origin_columns_exist_on_the_model(session: AsyncSession):
    # Guards the migration/model pair: a plain select touches both columns.
    await session.execute(select(Transaction.category_source, Transaction.category_rule_id).limit(1))


@pytest.mark.asyncio
async def test_a_generated_recurring_transaction_says_it_came_from_the_recurrence(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_workspace,
    test_account, test_categories,
):
    from datetime import date, timedelta

    from app.schemas.recurring_transaction import RecurringTransactionCreate
    from app.services.recurring_transaction_service import (
        create_recurring_transaction,
        generate_pending,
    )

    await create_recurring_transaction(
        session,
        test_workspace.id,
        test_user.id,
        RecurringTransactionCreate(
            description="Assinatura mensal zz",
            amount=20,
            type="debit",
            frequency="monthly",
            start_date=date.today() - timedelta(days=1),
            account_id=test_account.id,
            category_id=test_categories[1].id,
            auto_generate=True,
        ),
    )

    assert await generate_pending(session, test_user.id) == 1

    tx = (
        await session.execute(
            select(Transaction).where(Transaction.description == "Assinatura mensal zz")
        )
    ).scalar_one()
    origin = await _origin(client, auth_headers, tx.id)
    assert origin["source"] == "recurring"
