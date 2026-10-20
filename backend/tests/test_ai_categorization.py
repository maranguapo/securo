"""AI categorization of uncategorized transactions."""
import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.models.connection import LlmConnection
from app.agents.providers.base import ChatResponse, LLMUnavailableError
from app.models.ai_categorization import AiCategorization
from app.models.transaction import Transaction
from app.models.workspace_ai_settings import WorkspaceAiSettings
from app.services import ai_categorization_service as svc
from app.services.ai_categorization_service import parse_suggestions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
class FakeProvider:
    """Answers from a `{description fragment: (category name, confidence)}` map.

    Reads the prompt the way a model would: category numbers come from the
    "Categories:" list, transaction numbers from the "Transactions:" list.
    """

    def __init__(self, answers=None, *, raw=None, error=None, on_call=None):
        self.answers = answers or {}
        self.raw = raw
        self.error = error
        self.on_call = on_call
        self.calls: list[list] = []

    async def chat(self, messages, **kwargs):
        self.calls.append(messages)
        if self.error:
            raise self.error
        if self.on_call:
            await self.on_call()
        if self.raw is not None:
            return ChatResponse(content=self.raw)
        prompt = messages[-1].content
        cat_section = prompt.split("Categories:")[1].split("Transactions:")[0]
        cats = {
            m.group(2).strip(): int(m.group(1))
            for m in re.finditer(r"^(\d+)\. (.+)$", cat_section, re.M)
            if "->" not in m.group(2)
        }
        tx_section = prompt.split("Transactions:")[1]
        out = []
        for m in re.finditer(r'^(\d+)\. "(.*?)"', tx_section, re.M):
            for fragment, (name, conf) in self.answers.items():
                if fragment in m.group(2):
                    out.append(
                        {"t": int(m.group(1)), "c": cats.get(name) if name else None, "conf": conf}
                    )
        return ChatResponse(content="```json\n" + str(out).replace("'", '"').replace("None", "null") + "\n```")


@pytest.fixture
def use_provider():
    """Route the service to a fake provider instead of a real endpoint."""

    def _use(provider):
        return patch.object(
            svc.llm_connections, "build_provider_for_connection", return_value=provider
        )

    return _use


async def _enable(session, user, workspace, *, min_confidence=0.6, model="m"):
    conn = LlmConnection(
        id=uuid.uuid4(), user_id=user.id, name="local", kind="ollama", default_model=model
    )
    session.add(conn)
    await session.flush()
    session.add(
        WorkspaceAiSettings(
            workspace_id=workspace.id,
            ai_categorization_enabled=True,
            llm_connection_id=conn.id,
            min_confidence=min_confidence,
            configured_by_user_id=user.id,
        )
    )
    await session.commit()
    return conn


def _by_description(transactions):
    return {t.description: t for t in transactions}


async def _category_of(session, tx):
    await session.refresh(tx)
    return tx.category_id


# ---------------------------------------------------------------------------
# categorize_pending
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_fills_only_empty_categories_and_records_each_decision(
    session: AsyncSession, test_user, test_workspace, test_categories, test_transactions, use_provider
):
    await _enable(session, test_user, test_workspace)
    txs = _by_description(test_transactions)
    uber_before = txs["UBER TRIP"].category_id
    provider = FakeProvider(
        {
            "NETFLIX": ("Alimentação", 0.9),
            "PIX RECEBIDO": ("Receita", 0.8),
            # Already categorized: never sent to the model at all.
            "UBER TRIP": ("Alimentação", 0.99),
        }
    )

    with use_provider(provider):
        applied = await svc.categorize_pending(session, test_workspace.id)

    assert applied == 2
    assert await _category_of(session, txs["NETFLIX"]) == test_categories[0].id
    assert await _category_of(session, txs["PIX RECEBIDO"]) == test_categories[2].id
    assert await _category_of(session, txs["UBER TRIP"]) == uber_before

    rows = (await session.execute(select(AiCategorization))).scalars().all()
    assert {r.status for r in rows} == {"applied"}
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_low_confidence_is_recorded_but_not_applied(
    session: AsyncSession, test_user, test_workspace, test_categories, test_transactions, use_provider
):
    await _enable(session, test_user, test_workspace, min_confidence=0.7)
    txs = _by_description(test_transactions)

    with use_provider(FakeProvider({"NETFLIX": ("Alimentação", 0.4)})):
        applied = await svc.categorize_pending(session, test_workspace.id)

    assert applied == 0
    assert await _category_of(session, txs["NETFLIX"]) is None
    row = (await session.execute(select(AiCategorization))).scalar_one()
    assert row.status == "low_confidence"
    assert row.suggested_category_id == test_categories[0].id


@pytest.mark.asyncio
async def test_a_null_category_is_not_applied(
    session: AsyncSession, test_user, test_workspace, test_categories, test_transactions, use_provider
):
    await _enable(session, test_user, test_workspace)
    txs = _by_description(test_transactions)

    with use_provider(FakeProvider({"NETFLIX": (None, 0.95)})):
        applied = await svc.categorize_pending(session, test_workspace.id)

    assert applied == 0
    assert await _category_of(session, txs["NETFLIX"]) is None


@pytest.mark.asyncio
async def test_disabled_workspace_never_calls_the_model(
    session: AsyncSession, test_user, test_workspace, test_categories, test_transactions, use_provider
):
    await _enable(session, test_user, test_workspace)
    settings = await session.get(WorkspaceAiSettings, test_workspace.id)
    assert settings is not None
    settings.ai_categorization_enabled = False
    await session.commit()
    provider = FakeProvider({"NETFLIX": ("Alimentação", 0.9)})

    with use_provider(provider):
        assert await svc.categorize_pending(session, test_workspace.id) == 0

    assert provider.calls == []


@pytest.mark.asyncio
async def test_workspace_without_settings_does_nothing(
    session: AsyncSession, test_workspace, test_categories, test_transactions
):
    assert await svc.categorize_pending(session, test_workspace.id) == 0


@pytest.mark.asyncio
async def test_a_transaction_is_never_sent_twice(
    session: AsyncSession, test_user, test_workspace, test_categories, test_transactions, use_provider
):
    await _enable(session, test_user, test_workspace, min_confidence=0.9)
    provider = FakeProvider({"NETFLIX": ("Alimentação", 0.5), "PIX RECEBIDO": ("Receita", 0.5)})

    with use_provider(provider):
        await svc.categorize_pending(session, test_workspace.id)
        await svc.categorize_pending(session, test_workspace.id)

    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_an_unavailable_model_records_nothing_so_the_next_run_retries(
    session: AsyncSession, test_user, test_workspace, test_categories, test_transactions, use_provider
):
    await _enable(session, test_user, test_workspace)

    with use_provider(FakeProvider(error=LLMUnavailableError("down"))):
        assert await svc.categorize_pending(session, test_workspace.id) == 0
    assert (await session.execute(select(AiCategorization))).scalars().all() == []

    with use_provider(FakeProvider({"NETFLIX": ("Alimentação", 0.9)})):
        assert await svc.categorize_pending(session, test_workspace.id) == 1


@pytest.mark.asyncio
async def test_unparseable_answers_record_nothing(
    session: AsyncSession, test_user, test_workspace, test_categories, test_transactions, use_provider
):
    await _enable(session, test_user, test_workspace)

    with use_provider(FakeProvider(raw="Sorry, I can't help with that.")):
        assert await svc.categorize_pending(session, test_workspace.id) == 0
    assert (await session.execute(select(AiCategorization))).scalars().all() == []


@pytest.mark.asyncio
async def test_an_invented_category_number_is_treated_as_no_answer(
    session: AsyncSession, test_user, test_workspace, test_categories, test_transactions, use_provider
):
    await _enable(session, test_user, test_workspace)
    txs = _by_description(test_transactions)

    with use_provider(FakeProvider(raw='[{"t": 1, "c": 99, "conf": 0.99}]')):
        assert await svc.categorize_pending(session, test_workspace.id) == 0
    assert await _category_of(session, txs["NETFLIX"]) is None


@pytest.mark.asyncio
async def test_a_category_the_user_set_meanwhile_is_not_overwritten(
    session: AsyncSession, test_user, test_workspace, test_categories, test_transactions, use_provider
):
    await _enable(session, test_user, test_workspace)
    txs = _by_description(test_transactions)
    netflix = txs["NETFLIX"]

    async def user_categorizes_while_the_model_thinks():
        await session.execute(
            update(Transaction)
            .where(Transaction.id == netflix.id)
            .values(category_id=test_categories[1].id)
        )
        await session.commit()

    provider = FakeProvider(
        {"NETFLIX": ("Alimentação", 0.95)}, on_call=user_categorizes_while_the_model_thinks
    )
    with use_provider(provider):
        applied = await svc.categorize_pending(session, test_workspace.id)

    assert applied == 0
    assert await _category_of(session, netflix) == test_categories[1].id


@pytest.mark.asyncio
async def test_ignored_transfer_and_old_transactions_are_left_alone(
    session: AsyncSession, test_user, test_workspace, test_categories, test_transactions, use_provider
):
    await _enable(session, test_user, test_workspace)
    txs = _by_description(test_transactions)
    await session.execute(
        update(Transaction).where(Transaction.id == txs["NETFLIX"].id).values(is_ignored=True)
    )
    await session.execute(
        update(Transaction)
        .where(Transaction.id == txs["PIX RECEBIDO"].id)
        .values(created_at=datetime.now(timezone.utc) - timedelta(days=90))
    )
    session.add(
        Transaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            account_id=txs["UBER TRIP"].account_id,
            description="TRANSFER OUT",
            amount=Decimal("10.00"),
            date=txs["UBER TRIP"].date,
            type="debit",
            source="manual",
            transfer_pair_id=uuid.uuid4(),
        )
    )
    await session.commit()
    provider = FakeProvider({"": ("Alimentação", 0.99)})

    with use_provider(provider):
        applied = await svc.categorize_pending(session, test_workspace.id)

    assert applied == 0
    assert provider.calls == []


@pytest.mark.asyncio
async def test_only_what_is_needed_leaves_the_server(
    session: AsyncSession, test_user, test_workspace, test_categories, test_transactions, use_provider
):
    await _enable(session, test_user, test_workspace)
    txs = _by_description(test_transactions)
    txs["NETFLIX"].notes = "my private note"
    txs["NETFLIX"].payee = "Netflix Inc"
    await session.commit()
    provider = FakeProvider({"NETFLIX": ("Alimentação", 0.9)})

    with use_provider(provider):
        await svc.categorize_pending(session, test_workspace.id)

    prompt = "\n".join(m.content for m in provider.calls[0])
    assert "NETFLIX" in prompt and "Netflix Inc" in prompt and "-39.90 " in prompt
    # The user's own conventions are passed along as examples...
    assert '"UBER TRIP" -> Transporte' in prompt
    # ...but nothing that identifies the user or their accounts.
    assert "my private note" not in prompt
    assert test_user.email not in prompt
    assert "Conta Corrente" not in prompt
    assert str(test_categories[0].id) not in prompt
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", prompt)


@pytest.mark.asyncio
async def test_corrections_the_user_made_come_first_among_the_examples(
    session: AsyncSession, test_user, test_workspace, test_categories, test_transactions, use_provider
):
    await _enable(session, test_user, test_workspace)
    txs = _by_description(test_transactions)
    uber = txs["UBER TRIP"]
    session.add(
        AiCategorization(
            transaction_id=uber.id,
            workspace_id=test_workspace.id,
            suggested_category_id=test_categories[0].id,
            confidence=0.9,
            status="changed",
        )
    )
    await session.commit()

    examples = await svc._examples(session, test_workspace.id)

    assert examples[0] == ("UBER TRIP", "Transporte")


# ---------------------------------------------------------------------------
# parse_suggestions
# ---------------------------------------------------------------------------
def test_parse_reads_json_inside_prose_and_fences():
    text = 'Sure!\n```json\n[{"t": 1, "c": 2, "conf": 0.8}]\n```\nHope that helps.'
    assert parse_suggestions(text, 3, 5) == {1: (2, 0.8)}


@pytest.mark.parametrize(
    "text",
    ["", "no json here", "[1, 2, 3]", '{"t": 1}', '[{"t": "1", "c": 1, "conf": 1}]', "[{"],
)
def test_parse_drops_what_it_cannot_trust(text):
    assert parse_suggestions(text, 3, 5) == {}


def test_parse_clamps_confidence_and_nulls_out_of_range_categories():
    text = (
        '[{"t": 1, "c": 1, "conf": 7}, {"t": 2, "c": 0, "conf": 0.9},'
        ' {"t": 3, "c": true, "conf": 0.9}, {"t": 4, "c": 1, "conf": 1},'
        ' {"t": 1, "c": 2, "conf": 0.1}]'
    )
    assert parse_suggestions(text, 3, 5) == {
        1: (1, 1.0),  # first answer wins, confidence clamped
        2: (None, 0.9),  # category 0 is not a category
        3: (None, 0.9),  # a boolean is not a position
    }  # t=4 is past the batch and is dropped


def test_parse_treats_unreadable_confidence_as_zero():
    assert parse_suggestions('[{"t": 1, "c": 1, "conf": "high"}]', 1, 1) == {1: (1, 0.0)}


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
def _url(workspace_id, suffix="automation"):
    return f"/api/workspaces/{workspace_id}/{suffix}"


async def _connection(session, user, model="m"):
    conn = LlmConnection(
        id=uuid.uuid4(), user_id=user.id, name="c", kind="ollama", default_model=model
    )
    session.add(conn)
    await session.commit()
    return conn


@pytest.mark.asyncio
async def test_defaults_are_off(client: AsyncClient, auth_headers, test_workspace):
    resp = await client.get(_url(test_workspace.id), headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {
        "ai_categorization_enabled": False,
        "llm_connection_id": None,
        "llm_model": None,
        "min_confidence": 0.6,
        "digest_enabled": False,
        "digest_time": "20:00",
        "agents_enabled": True,
    }


@pytest.mark.asyncio
async def test_owner_can_turn_it_on(
    client: AsyncClient, auth_headers, test_workspace, test_user, session: AsyncSession
):
    conn = await _connection(session, test_user)

    resp = await client.put(
        _url(test_workspace.id),
        headers=auth_headers,
        json={
            "ai_categorization_enabled": True,
            "llm_connection_id": str(conn.id),
            "min_confidence": 0.8,
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ai_categorization_enabled"] is True
    assert body["llm_connection_id"] == str(conn.id)
    assert body["min_confidence"] == 0.8
    saved = await session.get(WorkspaceAiSettings, test_workspace.id)
    assert saved is not None and saved.configured_by_user_id == test_user.id


@pytest.mark.asyncio
async def test_turning_it_on_needs_a_connection_and_a_model(
    client: AsyncClient, auth_headers, test_workspace, test_user, session: AsyncSession
):
    no_connection = await client.put(
        _url(test_workspace.id),
        headers=auth_headers,
        json={"ai_categorization_enabled": True},
    )
    assert no_connection.status_code == 400

    conn = await _connection(session, test_user, model=None)  # type: ignore[arg-type]
    no_model = await client.put(
        _url(test_workspace.id),
        headers=auth_headers,
        json={"ai_categorization_enabled": True, "llm_connection_id": str(conn.id)},
    )
    assert no_model.status_code == 400

    with_model = await client.put(
        _url(test_workspace.id),
        headers=auth_headers,
        json={
            "ai_categorization_enabled": True,
            "llm_connection_id": str(conn.id),
            "llm_model": "llama3",
        },
    )
    assert with_model.status_code == 200


@pytest.mark.asyncio
async def test_someone_elses_connection_is_refused(
    client: AsyncClient, auth_headers, test_workspace, session: AsyncSession, viewer_auth_headers
):
    from app.models.user import User

    other = (
        await session.execute(select(User).where(User.email == "viewer-role@example.com"))
    ).scalar_one()
    theirs = await _connection(session, other)

    resp = await client.put(
        _url(test_workspace.id),
        headers=auth_headers,
        json={"ai_categorization_enabled": True, "llm_connection_id": str(theirs.id)},
    )

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_it_cannot_be_turned_on_without_the_agents_module(
    client: AsyncClient, auth_headers, test_workspace, test_user, session: AsyncSession
):
    conn = await _connection(session, test_user)
    with patch("app.api.workspaces.get_agent_settings") as agent_settings:
        agent_settings.return_value.enabled = False
        resp = await client.put(
            _url(test_workspace.id),
            headers=auth_headers,
            json={"ai_categorization_enabled": True, "llm_connection_id": str(conn.id)},
        )
        status = await client.get(_url(test_workspace.id), headers=auth_headers)

    assert resp.status_code == 400
    assert status.json()["agents_enabled"] is False


@pytest.mark.asyncio
async def test_confidence_must_be_in_range(client: AsyncClient, auth_headers, test_workspace):
    for bad in (0.1, 1.5):
        resp = await client.put(
            _url(test_workspace.id),
            headers=auth_headers,
            json={"ai_categorization_enabled": False, "min_confidence": bad},
        )
        assert resp.status_code == 422


@pytest.mark.asyncio
async def test_viewers_can_read_but_not_change_or_run(
    client: AsyncClient, viewer_auth_headers, test_workspace
):
    assert (await client.get(_url(test_workspace.id), headers=viewer_auth_headers)).status_code == 200
    put = await client.put(
        _url(test_workspace.id),
        headers=viewer_auth_headers,
        json={"ai_categorization_enabled": False},
    )
    assert put.status_code == 403
    run = await client.post(_url(test_workspace.id, "automation/run-now"), headers=viewer_auth_headers)
    assert run.status_code == 403


@pytest.mark.asyncio
async def test_run_now_categorizes_immediately(
    client: AsyncClient,
    auth_headers,
    test_workspace,
    test_user,
    test_categories,
    test_transactions,
    session: AsyncSession,
    use_provider,
):
    await _enable(session, test_user, test_workspace)

    with use_provider(FakeProvider({"NETFLIX": ("Alimentação", 0.9)})):
        resp = await client.post(
            _url(test_workspace.id, "automation/run-now"), headers=auth_headers
        )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"categorized": 1, "digest_sent": 0}
