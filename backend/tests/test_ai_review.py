"""The AI categorization review screen: what it lists, and what it can do."""
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_categorization import AiCategorization
from app.models.rule import Rule
from app.models.transaction import Transaction
from app.models.workspace_ai_settings import WorkspaceAiSettings
from app.services import ai_categorization_service as svc
from app.services import ai_review_service, digest_service
from app.tasks.automation_tasks import run_workspace
from tests.test_ai_categorization import FakeProvider, _enable
from tests.test_digest import RecordingBot, _ready
from tests.test_installment_categorization import _digest_setup, _fresh, _ledger

BASE = "/api/ai-categorization"


@pytest.fixture(autouse=True)
def _no_delays():
    with patch.object(svc, "BATCH_DELAY_SECONDS", 0), patch.object(
        digest_service, "SEND_DELAY_SECONDS", 0
    ):
        yield


@pytest.fixture
def use_provider():
    def _use(provider):
        return patch.object(
            svc.llm_connections, "build_provider_for_connection", return_value=provider
        )

    return _use


async def _rows(session):
    return {
        r.transaction_id: r
        for r in (await session.execute(select(AiCategorization))).scalars().all()
    }


async def _setup(session, user, workspace, categories, transactions):
    """NETFLIX applied by the model (Alimentação); PIX RECEBIDO left for the user."""
    txs = await _ready(session, user, workspace, categories, transactions)
    rows = await _rows(session)
    return txs, rows[txs["NETFLIX"].id], rows[txs["PIX RECEBIDO"].id]


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
def test_merchant_name_drops_the_parcel_suffix_and_prefers_the_payee():
    plain = Transaction(description="AMAZON MARKETPLACE 03/12", payee=None)
    payee = Transaction(description="PIX 99", payee="  Padaria Pão Quente ")

    assert ai_review_service.merchant_of(plain) == "AMAZON MARKETPLACE"
    assert ai_review_service.merchant_of(payee) == "Padaria Pão Quente"
    assert ai_review_service.merchant_key("Padaria Pão-Quente!") == "padaria pao quente"


def test_reasons_are_read_tolerantly():
    text = 'ok [{"t": 1, "r": "  Streaming  "}, {"t": 2}, {"t": 9, "r": "x"}, {"t": 1, "r": "dup"}, {"t": 2, "r": 5}]'
    assert svc.parse_reasons(text, 3) == {1: "Streaming"}
    assert svc.parse_reasons("nope", 3) == {}
    assert len(svc.parse_reasons('[{"t": 1, "r": "' + "a" * 500 + '"}]', 1)[1]) == 160


@pytest.mark.asyncio
async def test_each_tab_lists_its_own_items(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_workspace,
    test_categories, test_transactions,
):
    await _setup(session, test_user, test_workspace, test_categories, test_transactions)

    review = (await client.get(f"{BASE}/items?tab=review", headers=auth_headers)).json()
    applied = (await client.get(f"{BASE}/items?tab=applied", headers=auth_headers)).json()

    assert [i["description"] for i in review["items"]] == ["PIX RECEBIDO"]
    assert review["items"][0]["category"] is None
    assert review["items"][0]["suggested"]["name"] == "Receita"
    assert [i["description"] for i in applied["items"]] == ["NETFLIX"]
    item = applied["items"][0]
    assert item["category"]["name"] == "Alimentação"
    assert item["confidence"] == 0.9 and item["amount"] == -39.9
    assert item["merchant"] == "NETFLIX" and item["account_name"] == "Conta Corrente"
    assert item["pending_telegram"] is True and applied["has_more"] is False


@pytest.mark.asyncio
async def test_the_applied_tab_filters_by_state_confidence_and_text(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_workspace,
    test_categories, test_transactions,
):
    txs, applied, _ = await _setup(session, test_user, test_workspace, test_categories, test_transactions)
    applied.status = "approved"
    await session.commit()

    def ids(response):
        return [i["description"] for i in response.json()["items"]]

    assert ids(await client.get(f"{BASE}/items?tab=applied&status=approved", headers=auth_headers)) == ["NETFLIX"]
    assert ids(await client.get(f"{BASE}/items?tab=applied&status=applied", headers=auth_headers)) == []
    assert ids(await client.get(f"{BASE}/items?tab=applied&min_confidence=0.95", headers=auth_headers)) == []
    assert ids(await client.get(f"{BASE}/items?tab=applied&max_confidence=0.95", headers=auth_headers)) == ["NETFLIX"]
    assert ids(await client.get(f"{BASE}/items?tab=applied&q=netf", headers=auth_headers)) == ["NETFLIX"]
    assert ids(await client.get(f"{BASE}/items?tab=applied&q=zzz", headers=auth_headers)) == []


@pytest.mark.asyncio
async def test_a_purchase_in_parcels_is_listed_once_with_how_many_it_covers(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_workspace,
    test_account, test_categories, use_provider,
):
    await _digest_setup(session, test_user, test_workspace, test_account, test_categories, use_provider)

    items = (await client.get(f"{BASE}/items?tab=applied", headers=auth_headers)).json()["items"]

    assert len(items) == 1
    assert items[0]["installment"] == {"number": 1, "total": 4, "others": 3}
    assert items[0]["merchant"] == "AMAZON"


@pytest.mark.asyncio
async def test_the_summary_counts_what_is_waiting_and_what_telegram_would_still_send(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_workspace,
    test_categories, test_transactions,
):
    await _setup(session, test_user, test_workspace, test_categories, test_transactions)

    s = (await client.get(f"{BASE}/summary", headers=auth_headers)).json()

    assert s["enabled"] is True and s["paused"] is False
    assert (s["to_review"], s["unconfirmed"], s["applied_total"]) == (1, 1, 1)
    assert s["transactions"] == 5 and s["decided"] == 4  # PIX RECEBIDO is still empty
    assert s["decided_percent"] == 80
    assert s["telegram"] == {"connected": True, "digest_enabled": True, "digest_time": "20:00", "pending": 2}


@pytest.mark.asyncio
async def test_the_summary_of_a_workspace_that_never_ran_is_empty_not_an_error(
    client: AsyncClient, auth_headers, test_workspace
):
    s = (await client.get(f"{BASE}/summary", headers=auth_headers)).json()

    assert s["enabled"] is False and s["last_run_at"] is None
    assert s["to_review"] == 0 and s["decided_percent"] == 100
    assert s["telegram"]["connected"] is False


# ---------------------------------------------------------------------------
# The Telegram digest and the screen answer the same question
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_what_is_confirmed_on_screen_never_reaches_telegram(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_workspace,
    test_categories, test_transactions,
):
    _, applied, unsure = await _setup(session, test_user, test_workspace, test_categories, test_transactions)

    ok = await client.post(f"{BASE}/items/approve", headers=auth_headers, json={"ids": [str(applied.id)]})
    chosen = await client.post(
        f"{BASE}/items/category", headers=auth_headers,
        json={"ids": [str(unsure.id)], "category_id": str(test_categories[2].id)},
    )
    bot = RecordingBot()
    sent = await digest_service.send_digest(session, test_workspace.id, client=bot)

    assert ok.json() == {"done": 1, "skipped": 0} and chosen.status_code == 200
    assert sent == 0 and bot.sent == []  # nothing left to ask in the chat


@pytest.mark.asyncio
async def test_what_is_left_unanswered_on_screen_still_goes_to_telegram(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_workspace,
    test_categories, test_transactions,
):
    _, applied, unsure = await _setup(session, test_user, test_workspace, test_categories, test_transactions)
    await client.post(f"{BASE}/items/approve", headers=auth_headers, json={"ids": [str(applied.id)]})
    bot = RecordingBot()

    sent = await digest_service.send_digest(session, test_workspace.id, client=bot)

    assert sent == 1
    assert any("PIX RECEBIDO" in text for _, text, _ in bot.sent)
    assert not any("NETFLIX" in text for _, text, _ in bot.sent)


@pytest.mark.asyncio
async def test_an_item_answered_in_the_chat_leaves_the_screens_queue(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_workspace,
    test_categories, test_transactions,
):
    _, applied, unsure = await _setup(session, test_user, test_workspace, test_categories, test_transactions)

    # A different category from the one the model had suggested (Receita).
    await digest_service.choose(session, test_workspace.id, unsure.id, test_categories[1].id, test_user.id)

    review = (await client.get(f"{BASE}/items?tab=review", headers=auth_headers)).json()
    changed = (await client.get(f"{BASE}/items?tab=applied&status=changed", headers=auth_headers)).json()
    assert review["items"] == []
    assert [i["description"] for i in changed["items"]] == ["PIX RECEBIDO"]


# ---------------------------------------------------------------------------
# Acting
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_confirming_several_at_once_and_skipping_what_has_nothing_to_confirm(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_workspace,
    test_categories, test_transactions,
):
    _, applied, unsure = await _setup(session, test_user, test_workspace, test_categories, test_transactions)

    resp = await client.post(
        f"{BASE}/items/approve", headers=auth_headers,
        json={"ids": [str(applied.id), str(unsure.id), str(uuid.uuid4())]},
    )

    # The unsure one has no category to confirm; the unknown id is not found.
    assert resp.json() == {"done": 1, "skipped": 2}
    await session.refresh(applied)
    assert applied.status == "approved" and applied.reviewed_by_user_id == test_user.id


@pytest.mark.asyncio
async def test_choosing_a_category_for_an_unsure_item_sets_it_on_the_transaction(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_workspace,
    test_categories, test_transactions,
):
    txs, _, unsure = await _setup(session, test_user, test_workspace, test_categories, test_transactions)

    await client.post(
        f"{BASE}/items/category", headers=auth_headers,
        json={"ids": [str(unsure.id)], "category_id": str(test_categories[1].id)},
    )

    await session.refresh(txs["PIX RECEBIDO"])
    await session.refresh(unsure)
    assert txs["PIX RECEBIDO"].category_id == test_categories[1].id
    assert unsure.status == "changed"


@pytest.mark.asyncio
async def test_changing_the_category_of_a_purchase_reaches_its_parcels(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_workspace,
    test_account, test_categories, use_provider,
):
    parcels = await _digest_setup(session, test_user, test_workspace, test_account, test_categories, use_provider)
    rep = [r for r in await _ledger(session) if not r.is_follower][0]

    await client.post(
        f"{BASE}/items/category", headers=auth_headers,
        json={"ids": [str(rep.id)], "category_id": str(test_categories[0].id)},
    )

    assert {p.category_id for p in await _fresh(session, parcels)} == {test_categories[0].id}


@pytest.mark.asyncio
async def test_a_category_from_another_workspace_is_refused(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_workspace,
    test_categories, test_transactions,
):
    _, applied, _ = await _setup(session, test_user, test_workspace, test_categories, test_transactions)

    resp = await client.post(
        f"{BASE}/items/category", headers=auth_headers,
        json={"ids": [str(applied.id)], "category_id": str(uuid.uuid4())},
    )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_undo_takes_the_category_off_and_returns_the_item_to_review(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_workspace,
    test_categories, test_transactions,
):
    txs, applied, _ = await _setup(session, test_user, test_workspace, test_categories, test_transactions)

    resp = await client.post(f"{BASE}/items/undo", headers=auth_headers, json={"ids": [str(applied.id)]})

    assert resp.json() == {"done": 1, "skipped": 0}
    await session.refresh(txs["NETFLIX"])
    assert txs["NETFLIX"].category_id is None
    review = (await client.get(f"{BASE}/items?tab=review", headers=auth_headers)).json()["items"]
    undone = [i for i in review if i["description"] == "NETFLIX"][0]
    assert undone["suggested"]["name"] == "Alimentação"  # what the model had proposed is still shown


@pytest.mark.asyncio
async def test_undo_reverts_the_parcels_that_carried_the_same_category(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_workspace,
    test_account, test_categories, use_provider,
):
    parcels = await _digest_setup(session, test_user, test_workspace, test_account, test_categories, use_provider)
    parcels[2].category_id = test_categories[2].id  # the user set this one apart
    await session.commit()
    rep = [r for r in await _ledger(session) if not r.is_follower][0]

    await client.post(f"{BASE}/items/undo", headers=auth_headers, json={"ids": [str(rep.id)]})

    await _fresh(session, parcels)
    assert [p.category_id for p in parcels] == [None, None, test_categories[2].id, None]


@pytest.mark.asyncio
async def test_an_item_that_is_not_applied_cannot_be_undone(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_workspace,
    test_categories, test_transactions,
):
    _, _, unsure = await _setup(session, test_user, test_workspace, test_categories, test_transactions)

    resp = await client.post(f"{BASE}/items/undo", headers=auth_headers, json={"ids": [str(unsure.id)]})

    assert resp.json() == {"done": 0, "skipped": 1}


@pytest.mark.asyncio
async def test_a_follower_parcel_cannot_be_acted_on_directly(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_workspace,
    test_account, test_categories, use_provider,
):
    await _digest_setup(session, test_user, test_workspace, test_account, test_categories, use_provider)
    follower = [r for r in await _ledger(session) if r.is_follower][0]

    resp = await client.post(f"{BASE}/items/undo", headers=auth_headers, json={"ids": [str(follower.id)]})

    assert resp.json() == {"done": 0, "skipped": 1}


# ---------------------------------------------------------------------------
# A decision becomes a rule
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_decision_can_become_a_rule_that_also_fills_matching_transactions(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_workspace,
    test_account, test_categories, test_transactions,
):
    txs, applied, _ = await _setup(session, test_user, test_workspace, test_categories, test_transactions)
    other = Transaction(
        id=uuid.uuid4(), user_id=test_user.id, account_id=test_account.id, description="NETFLIX.COM 2",
        amount=Decimal("39.90"), date=txs["NETFLIX"].date, type="debit", source="manual",
    )
    session.add(other)
    await session.commit()

    resp = await client.post(
        f"{BASE}/items/rule", headers=auth_headers,
        json={"id": str(applied.id), "category_id": str(test_categories[0].id)},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["applied"] == 1
    rule = (await session.execute(select(Rule))).scalar_one()
    assert rule.conditions == [{"field": "description", "op": "contains", "value": "NETFLIX"}]
    assert rule.actions == [{"op": "set_category", "value": str(test_categories[0].id)}]
    await session.refresh(other)
    assert other.category_id == test_categories[0].id
    await session.refresh(applied)
    assert applied.status in ("approved", "changed")


@pytest.mark.asyncio
async def test_a_second_rule_for_the_same_merchant_and_category_gets_its_own_name(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_workspace,
    test_categories, test_transactions,
):
    _, applied, _ = await _setup(session, test_user, test_workspace, test_categories, test_transactions)
    body = {"id": str(applied.id), "category_id": str(test_categories[0].id)}

    first = await client.post(f"{BASE}/items/rule", headers=auth_headers, json=body)
    second = await client.post(f"{BASE}/items/rule", headers=auth_headers, json=body)

    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["rule_name"] != second.json()["rule_name"]


@pytest.mark.asyncio
async def test_a_merchant_name_too_short_to_match_safely_makes_no_rule(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_workspace,
    test_categories, test_transactions,
):
    txs, applied, _ = await _setup(session, test_user, test_workspace, test_categories, test_transactions)
    txs["NETFLIX"].description = "X1"
    await session.commit()

    resp = await client.post(
        f"{BASE}/items/rule", headers=auth_headers,
        json={"id": str(applied.id), "category_id": str(test_categories[0].id)},
    )

    assert resp.status_code == 400
    assert (await session.execute(select(Rule))).scalars().all() == []


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_viewer_can_look_but_not_change_anything(
    client: AsyncClient, auth_headers, viewer_auth_headers, session: AsyncSession, test_user,
    test_workspace, test_categories, test_transactions,
):
    _, applied, _ = await _setup(session, test_user, test_workspace, test_categories, test_transactions)
    ids = {"ids": [str(applied.id)]}

    assert (await client.get(f"{BASE}/summary", headers=viewer_auth_headers)).status_code == 200
    assert (await client.get(f"{BASE}/items?tab=applied", headers=viewer_auth_headers)).status_code == 200
    for path, body in (
        ("approve", ids),
        ("undo", ids),
        ("category", {**ids, "category_id": str(uuid.uuid4())}),
        ("rule", {"id": str(applied.id), "category_id": str(uuid.uuid4())}),
    ):
        resp = await client.post(f"{BASE}/items/{path}", headers=viewer_auth_headers, json=body)
        assert resp.status_code == 403, path


@pytest.mark.asyncio
async def test_the_request_limits_are_enforced(client: AsyncClient, auth_headers, test_workspace):
    assert (await client.post(f"{BASE}/items/approve", headers=auth_headers, json={"ids": []})).status_code == 422
    too_many = {"ids": [str(uuid.uuid4()) for _ in range(501)]}
    assert (await client.post(f"{BASE}/items/approve", headers=auth_headers, json=too_many)).status_code == 422
    assert (await client.get(f"{BASE}/items?tab=nope", headers=auth_headers)).status_code == 422


# ---------------------------------------------------------------------------
# What the model writes, and when it ran
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_models_reason_is_stored_and_shown(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_workspace,
    test_categories, test_transactions, use_provider,
):
    await _enable(session, test_user, test_workspace)
    only_netflix = FakeProvider(
        raw='[{"t": 1, "c": 1, "conf": 0.9, "r": "Streaming subscription"},'
        ' {"t": 2, "c": null, "conf": 0.1, "r": "Unclear sender"}]'
    )

    with use_provider(only_netflix):
        await svc.categorize_pending(session, test_workspace.id)

    items = (await client.get(f"{BASE}/items?tab=applied", headers=auth_headers)).json()["items"]
    assert "Streaming subscription" in [i["reason"] for i in items] or "Unclear sender" in [
        i["reason"] for i in items
    ]


@pytest.mark.asyncio
async def test_the_prompt_asks_for_a_reason_in_the_users_language(
    session: AsyncSession, test_user, test_workspace, test_categories, test_transactions, use_provider,
):
    await _enable(session, test_user, test_workspace)
    provider = FakeProvider({"NETFLIX": ("Alimentação", 0.9)})

    with use_provider(provider):
        await svc.categorize_pending(session, test_workspace.id)

    system = provider.calls[0][0].content
    assert '"r": <reason>' in system and "language with code pt-BR" in system


@pytest.mark.asyncio
async def test_the_screen_shows_when_the_categorization_last_ran(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_workspace,
    test_categories, test_transactions, use_provider,
):
    await _enable(session, test_user, test_workspace)

    with use_provider(FakeProvider({"NETFLIX": ("Alimentação", 0.9)})):
        resp = await client.post(
            f"/api/workspaces/{test_workspace.id}/automation/run-now", headers=auth_headers
        )
    s = (await client.get(f"{BASE}/summary", headers=auth_headers)).json()

    assert resp.status_code == 200
    assert s["last_run_applied"] == 1
    assert s["last_run_at"] is not None


@pytest.mark.asyncio
async def test_a_scheduled_run_is_recorded_even_when_it_applied_nothing(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    await _enable(session, test_user, test_workspace)
    now = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)

    await run_workspace(session, test_workspace.id, now)

    settings = await session.get(WorkspaceAiSettings, test_workspace.id)
    await session.refresh(settings)
    assert settings is not None and settings.last_run_applied == 0
    assert settings.last_run_at is not None


@pytest.mark.asyncio
async def test_the_summary_counts_transactions_still_waiting_for_the_model(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_workspace,
    test_categories, test_transactions,
):
    await _enable(session, test_user, test_workspace)

    s = (await client.get(f"{BASE}/summary", headers=auth_headers)).json()

    assert s["awaiting_ai"] == 2  # NETFLIX and PIX RECEBIDO have no category yet
