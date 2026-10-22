"""The daily digest of AI categorizations and the Telegram buttons that answer it."""
import uuid
from datetime import date, datetime, time, timezone
from typing import Optional
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.models.connection import LlmConnection
from app.agents.services.crypto import encrypt
from app.models.ai_categorization import AiCategorization
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace_ai_settings import WorkspaceAiSettings
from app.models.workspace_integration import WorkspaceIntegration
from app.services import digest_service, telegram_service
from app.services.digest_service import Item
from app.tasks.automation_tasks import run_workspace
from app.telegram import handlers
from app.telegram.client import TelegramClient, TelegramError
from tests.conftest import TestSessionLocal

SAO_PAULO = ZoneInfo("America/Sao_Paulo")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
class RecordingBot(TelegramClient):
    def __init__(self, fail_chats=()):
        super().__init__("123:token")
        self.sent: list[tuple[int, str, Optional[dict]]] = []
        self.edits: list[tuple[int, int, str, Optional[dict]]] = []
        self.answers: list[tuple[str, Optional[str]]] = []
        self.fail_chats = set(fail_chats)

    async def send_message(self, chat_id, text, *, reply_markup=None):
        if chat_id in self.fail_chats:
            raise TelegramError("blocked", code=403)
        self.sent.append((chat_id, text, reply_markup))
        return {}

    async def edit_message_text(self, chat_id, message_id, text, *, reply_markup=None):
        self.edits.append((chat_id, message_id, text, reply_markup))
        return {}

    async def answer_callback_query(self, callback_id, text=None):
        self.answers.append((callback_id, text))


@pytest.fixture(autouse=True)
def _no_send_delay():
    with patch.object(digest_service, "SEND_DELAY_SECONDS", 0):
        yield


def _settings(**overrides):
    base = dict(
        workspace_id=uuid.uuid4(),
        ai_categorization_enabled=True,
        digest_enabled=True,
        digest_time=time(20, 0),
        last_digest_on=None,
    )
    base.update(overrides)
    return WorkspaceAiSettings(**base)


async def _ledger(session, workspace, tx, category, status="applied", confidence=0.9):
    """Record that the model looked at `tx`, applying `category` when confident."""
    if status == "applied":
        tx.category_id = category.id
    row = AiCategorization(
        transaction_id=tx.id,
        workspace_id=workspace.id,
        suggested_category_id=category.id,
        confidence=confidence,
        status=status,
    )
    session.add(row)
    await session.commit()
    return row


async def _ready_with_unsure_answered(session, user, workspace, categories, transactions):
    """Like `_ready`, but the unsure one was already answered, so only what the
    model applied with confidence is left to report."""
    txs = await _ready(session, user, workspace, categories, transactions)
    unsure = [r for r in (await session.execute(select(AiCategorization))).scalars() if r.status == "low_confidence"][0]
    txs["PIX RECEBIDO"].category_id = categories[2].id
    unsure.status = "changed"
    await session.commit()
    return txs, None, unsure


async def _enable_digest(session, user, workspace, *, tz=None):
    conn = LlmConnection(id=uuid.uuid4(), user_id=user.id, name="c", kind="ollama", default_model="m")
    session.add(conn)
    await session.flush()
    session.add(
        WorkspaceAiSettings(
            workspace_id=workspace.id,
            ai_categorization_enabled=True,
            llm_connection_id=conn.id,
            configured_by_user_id=user.id,
            digest_enabled=True,
            digest_time=time(20, 0),
        )
    )
    if tz:
        workspace.timezone = tz
    session.add(
        WorkspaceIntegration(
            workspace_id=workspace.id,
            provider="telegram",
            client_id="bot",
            client_secret_encrypted=encrypt("123:token") or "",
        )
    )
    await session.commit()


def _by_description(transactions):
    return {t.description: t for t in transactions}


def _callback(chat_id, data, *, message_id=77, cid="cb1"):
    return {
        "update_id": 1,
        "callback_query": {
            "id": cid,
            "data": data,
            "message": {"message_id": message_id, "chat": {"id": chat_id, "type": "private"}},
        },
    }


def _buttons(keyboard):
    return [b["callback_data"] for row in keyboard["inline_keyboard"] for b in row]


# ---------------------------------------------------------------------------
# is_due
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "utc, last, expected",
    [
        # 19:59 and 20:00 local (UTC-3) on the 19th.
        (datetime(2026, 10, 19, 22, 59, tzinfo=timezone.utc), None, False),
        (datetime(2026, 10, 19, 23, 0, tzinfo=timezone.utc), None, True),
        (datetime(2026, 10, 19, 23, 5, tzinfo=timezone.utc), date(2026, 10, 19), False),
        (datetime(2026, 10, 19, 23, 5, tzinfo=timezone.utc), date(2026, 10, 18), True),
        # 23:30 local on the 19th, but already the 20th in UTC: still the 19th's
        # digest day, and it already went out.
        (datetime(2026, 10, 20, 2, 30, tzinfo=timezone.utc), date(2026, 10, 19), False),
        # Next morning is a new local day, before the time: not yet.
        (datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc), date(2026, 10, 19), False),
        (datetime(2026, 10, 20, 23, 0, tzinfo=timezone.utc), date(2026, 10, 19), True),
    ],
)
def test_is_due_follows_the_workspaces_own_calendar(utc, last, expected):
    settings = _settings(last_digest_on=last)
    assert digest_service.is_due(settings, utc, SAO_PAULO) is expected


def test_is_due_respects_the_switch_and_force():
    now = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
    off = _settings(digest_enabled=False)
    assert digest_service.is_due(off, now, SAO_PAULO) is False
    assert digest_service.is_due(off, now, SAO_PAULO, force=True) is False
    on = _settings()
    assert digest_service.is_due(on, now, SAO_PAULO) is False
    assert digest_service.is_due(on, now, SAO_PAULO, force=True) is True


def test_parse_time():
    assert digest_service.parse_time("07:05") == time(7, 5)
    with pytest.raises(ValueError):
        digest_service.parse_time("7pm")


# ---------------------------------------------------------------------------
# Callback data and rendering
# ---------------------------------------------------------------------------
def test_ids_round_trip_in_22_characters():
    value = uuid.uuid4()
    packed = digest_service.pack(value)
    assert len(packed) == 22
    assert digest_service.unpack(packed) == value
    assert digest_service.unpack("###") is None
    assert digest_service.unpack("") is None


def test_the_longest_callback_fits_telegrams_64_byte_limit():
    a, b = digest_service.pack(uuid.uuid4()), digest_service.pack(uuid.uuid4())
    assert len(f"c:{a}:{b}".encode()) <= 64


def test_money_is_formatted_for_the_readers_language():
    assert digest_service.format_money(-1234.5, "BRL", "pt") == "-R$ 1.234,50"
    assert digest_service.format_money(1234.5, "USD", "en") == "$ 1,234.50"
    assert digest_service.format_money(-10, "JPY", "en") == "-10.00 JPY"


async def _item(session, workspace, category, tx, status="applied", confidence=0.9):
    row = await _ledger(session, workspace, tx, category, status, confidence)
    return Item(row=row, transaction=tx, category_name=category.name if tx.category_id else None)


@pytest.mark.asyncio
async def test_a_suggestion_offers_confirm_and_change(
    session: AsyncSession, test_workspace, test_categories, test_transactions
):
    tx = _by_description(test_transactions)["NETFLIX"]
    item = await _item(session, test_workspace, test_categories[0], tx)

    text, keyboard = digest_service.render_item(item, "en")

    assert keyboard is not None
    assert "NETFLIX" in text and "Alimentação" in text and "AI 90%" in text
    assert "-$ 39.90" in text  # the fixtures default to USD
    assert [b["text"] for b in keyboard["inline_keyboard"][0]] == ["✅ Confirm", "✏️ Change"]


@pytest.mark.asyncio
async def test_an_unsure_one_can_only_be_chosen_not_confirmed(
    session: AsyncSession, test_workspace, test_categories, test_transactions
):
    tx = _by_description(test_transactions)["PIX RECEBIDO"]
    item = await _item(session, test_workspace, test_categories[2], tx, "low_confidence", 0.3)

    text, keyboard = digest_service.render_item(item, "pt")

    assert keyboard is not None
    assert "a IA não teve certeza" in text
    assert [b["text"] for b in keyboard["inline_keyboard"][0]] == ["✏️ Escolher"]


@pytest.mark.asyncio
async def test_an_answered_item_loses_its_buttons(
    session: AsyncSession, test_workspace, test_categories, test_transactions
):
    tx = _by_description(test_transactions)["NETFLIX"]
    item = await _item(session, test_workspace, test_categories[0], tx)

    text, keyboard = digest_service.render_item(item, "en", state="confirmed")

    assert keyboard is None
    assert "(confirmed)" in text and "AI 90%" not in text


# ---------------------------------------------------------------------------
# send_digest
# ---------------------------------------------------------------------------
async def _ready(session, user, workspace, categories, transactions, *, chat_id=555):
    """Two evaluations waiting, a bot, and one linked chat."""
    await _enable_digest(session, user, workspace)
    txs = _by_description(transactions)
    await _ledger(session, workspace, txs["NETFLIX"], categories[0])
    await _ledger(session, workspace, txs["PIX RECEBIDO"], categories[2], "low_confidence", 0.3)
    await telegram_service.link_chat(session, workspace.id, user.id, chat_id, None)
    return txs


@pytest.mark.asyncio
async def test_only_what_needs_a_decision_gets_its_own_message(
    session: AsyncSession, test_user, test_workspace, test_categories, test_transactions
):
    await _ready(session, test_user, test_workspace, test_categories, test_transactions)
    bot = RecordingBot()

    sent = await digest_service.send_digest(session, test_workspace.id, client=bot)

    # One the model was unsure about, one it applied with confidence.
    assert sent == 2
    assert [chat for chat, _, _ in bot.sent] == [555, 555]
    header = bot.sent[0]
    assert "1 para escolher" in header[1]  # the test user reads Portuguese
    assert "1 categorizada(s) automaticamente" in header[1]
    assert _buttons(header[2]) == ["A"]  # confirm everything the model applied
    assert any("PIX RECEBIDO" in text for _, text, _ in bot.sent)
    # What the model applied is a line in the header, not a message to answer.
    assert not any("NETFLIX" in text for _, text, _ in bot.sent)


@pytest.mark.asyncio
async def test_a_day_with_only_automatic_categorizations_is_one_message(
    session: AsyncSession, test_user, test_workspace, test_categories, test_transactions
):
    txs, _, unsure = await _ready_with_unsure_answered(
        session, test_user, test_workspace, test_categories, test_transactions
    )
    bot = RecordingBot()

    sent = await digest_service.send_digest(session, test_workspace.id, client=bot)

    assert sent == 1 and len(bot.sent) == 1
    text, keyboard = bot.sent[0][1], bot.sent[0][2]
    assert "para escolher" not in text and "1 categorizada(s) automaticamente" in text
    assert _buttons(keyboard) == ["A"]


@pytest.mark.asyncio
async def test_a_day_with_nothing_automatic_has_no_confirm_all_button(
    session: AsyncSession, test_user, test_workspace, test_categories, test_transactions
):
    await _ready(session, test_user, test_workspace, test_categories, test_transactions)
    rows = (await session.execute(select(AiCategorization))).scalars().all()
    for row in rows:
        row.status = "low_confidence"
    await session.commit()
    bot = RecordingBot()

    await digest_service.send_digest(session, test_workspace.id, client=bot)

    header = bot.sent[0]
    assert "categorizada(s) automaticamente" not in header[1]
    assert header[2] is None


@pytest.mark.asyncio
async def test_each_transaction_goes_out_once(
    session: AsyncSession, test_user, test_workspace, test_categories, test_transactions
):
    await _ready(session, test_user, test_workspace, test_categories, test_transactions)
    bot = RecordingBot()

    await digest_service.send_digest(session, test_workspace.id, client=bot)
    again = await digest_service.send_digest(session, test_workspace.id, client=bot)

    assert again == 0
    assert len(bot.sent) == 2
    rows = (await session.execute(select(AiCategorization))).scalars().all()
    assert all(r.notified_at is not None for r in rows)


@pytest.mark.asyncio
async def test_everyone_linked_gets_the_digest(
    session: AsyncSession, test_user, test_workspace, test_categories, test_transactions
):
    await _ready(session, test_user, test_workspace, test_categories, test_transactions)
    await telegram_service.link_chat(session, test_workspace.id, test_user.id, 555, None)
    bot = RecordingBot()

    await digest_service.send_digest(session, test_workspace.id, client=bot)

    assert {chat for chat, _, _ in bot.sent} == {555}


@pytest.mark.asyncio
async def test_a_chat_that_lost_write_access_is_skipped(
    session: AsyncSession, test_user, test_workspace, test_categories, test_transactions, viewer_auth_headers
):
    await _ready(session, test_user, test_workspace, test_categories, test_transactions)
    viewer = (
        await session.execute(select(User).where(User.email == "viewer-role@example.com"))
    ).scalar_one()
    await telegram_service.link_chat(session, test_workspace.id, viewer.id, 999, None)
    bot = RecordingBot()

    await digest_service.send_digest(session, test_workspace.id, client=bot)

    assert {chat for chat, _, _ in bot.sent} == {555}


@pytest.mark.asyncio
async def test_with_no_chat_nothing_is_marked_sent(
    session: AsyncSession, test_user, test_workspace, test_categories, test_transactions
):
    txs = await _ready(session, test_user, test_workspace, test_categories, test_transactions)
    for link, _ in await telegram_service.list_links(session, test_workspace.id):
        await telegram_service.delete_link(session, link)
    bot = RecordingBot()

    assert await digest_service.send_digest(session, test_workspace.id, client=bot) == 0

    assert bot.sent == []
    rows = (await session.execute(select(AiCategorization))).scalars().all()
    assert all(r.notified_at is None for r in rows)
    assert txs  # the items are still waiting for someone to connect


@pytest.mark.asyncio
async def test_if_telegram_fails_everywhere_nothing_is_lost_and_the_day_is_not_spent(
    session: AsyncSession, test_user, test_workspace, test_categories, test_transactions
):
    await _ready(session, test_user, test_workspace, test_categories, test_transactions)
    bot = RecordingBot(fail_chats={555})

    sent = await digest_service.send_digest(
        session, test_workspace.id, client=bot, local_date=date(2026, 10, 19)
    )

    assert sent == 0
    rows = (await session.execute(select(AiCategorization))).scalars().all()
    assert all(r.notified_at is None for r in rows)
    settings = await session.get(WorkspaceAiSettings, test_workspace.id)
    assert settings is not None and settings.last_digest_on is None


@pytest.mark.asyncio
async def test_a_day_with_nothing_to_review_still_counts_as_sent(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    await _enable_digest(session, test_user, test_workspace)
    await telegram_service.link_chat(session, test_workspace.id, test_user.id, 555, None)
    bot = RecordingBot()

    sent = await digest_service.send_digest(
        session, test_workspace.id, client=bot, local_date=date(2026, 10, 19)
    )

    assert sent == 0 and bot.sent == []
    settings = await session.get(WorkspaceAiSettings, test_workspace.id)
    assert settings is not None and settings.last_digest_on == date(2026, 10, 19)


@pytest.mark.asyncio
async def test_what_the_user_settled_in_the_app_is_not_asked_about(
    session: AsyncSession, test_user, test_workspace, test_categories, test_transactions
):
    txs = await _ready(session, test_user, test_workspace, test_categories, test_transactions)
    # The user recategorizes one in the app and categorizes the unsure one.
    txs["NETFLIX"].category_id = test_categories[1].id
    txs["PIX RECEBIDO"].category_id = test_categories[2].id
    await session.commit()
    bot = RecordingBot()

    sent = await digest_service.send_digest(session, test_workspace.id, client=bot)

    assert sent == 0 and bot.sent == []
    statuses = {r.status for r in (await session.execute(select(AiCategorization))).scalars()}
    assert statuses == {"changed"}


@pytest.mark.asyncio
async def test_the_overflow_waits_for_the_next_digest(
    session: AsyncSession, test_user, test_workspace, test_categories, test_transactions
):
    await _ready(session, test_user, test_workspace, test_categories, test_transactions)
    # Two that need a decision, so one of them can wait.
    netflix = (
        await session.execute(
            select(AiCategorization).where(AiCategorization.status == "applied")
        )
    ).scalar_one()
    tx = await session.get(Transaction, netflix.transaction_id)
    assert tx is not None
    tx.category_id = None
    netflix.status = "low_confidence"
    await session.commit()
    bot = RecordingBot()

    with patch.object(digest_service, "MAX_ITEMS", 1):
        sent = await digest_service.send_digest(session, test_workspace.id, client=bot)
        assert sent == 1
        assert "1 more" in bot.sent[-1][1] or "mais 1" in bot.sent[-1][1]
        second = await digest_service.send_digest(session, test_workspace.id, client=bot)

    assert second == 1


@pytest.mark.asyncio
async def test_without_a_bot_there_is_nothing_to_send(
    session: AsyncSession, test_user, test_workspace, test_categories, test_transactions
):
    await _ready(session, test_user, test_workspace, test_categories, test_transactions)
    await session.execute(
        delete(WorkspaceIntegration).where(WorkspaceIntegration.workspace_id == test_workspace.id)
    )
    await session.commit()

    assert await digest_service.send_digest(session, test_workspace.id) == 0


# ---------------------------------------------------------------------------
# Answering
# ---------------------------------------------------------------------------
async def _sent_ids(session):
    rows = (await session.execute(select(AiCategorization))).scalars().all()
    return {r.status: r.id for r in rows}


@pytest.mark.asyncio
async def test_confirming_closes_the_item_and_edits_the_message(
    session: AsyncSession, test_user, test_workspace, test_categories, test_transactions
):
    await _ready(session, test_user, test_workspace, test_categories, test_transactions)
    ai_id = (await _sent_ids(session))["applied"]
    bot = RecordingBot()

    await handlers.handle_update(
        TestSessionLocal, test_workspace.id, bot, _callback(555, f"a:{digest_service.pack(ai_id)}")
    )

    row = await session.get(AiCategorization, ai_id)
    await session.refresh(row)
    assert row is not None and row.status == "approved" and row.reviewed_by_user_id == test_user.id
    chat, message_id, text, keyboard = bot.edits[-1]
    assert (chat, message_id, keyboard) == (555, 77, None)
    assert "confirmada" in text
    assert bot.answers[-1] == ("cb1", None)


@pytest.mark.asyncio
async def test_a_second_tap_says_it_was_already_answered(
    session: AsyncSession, test_user, test_workspace, test_categories, test_transactions
):
    await _ready(session, test_user, test_workspace, test_categories, test_transactions)
    ref = digest_service.pack((await _sent_ids(session))["applied"])
    bot = RecordingBot()

    for _ in range(2):
        await handlers.handle_update(
            TestSessionLocal, test_workspace.id, bot, _callback(555, f"a:{ref}")
        )

    assert bot.answers[-1][1] == "Já revisada."


@pytest.mark.asyncio
async def test_change_shows_categories_and_choosing_one_recategorizes(
    session: AsyncSession, test_user, test_workspace, test_categories, test_transactions
):
    txs = await _ready(session, test_user, test_workspace, test_categories, test_transactions)
    ai_id = (await _sent_ids(session))["applied"]
    ref = digest_service.pack(ai_id)
    bot = RecordingBot()

    await handlers.handle_update(TestSessionLocal, test_workspace.id, bot, _callback(555, f"e:{ref}:0"))
    picker = bot.edits[-1][3]
    assert picker is not None
    chosen = f"c:{ref}:{digest_service.pack(test_categories[1].id)}"
    assert chosen in _buttons(picker)

    await handlers.handle_update(TestSessionLocal, test_workspace.id, bot, _callback(555, chosen))

    await session.refresh(txs["NETFLIX"])
    assert txs["NETFLIX"].category_id == test_categories[1].id
    row = await session.get(AiCategorization, ai_id)
    await session.refresh(row)
    assert row is not None and row.status == "changed"
    assert "alterada" in bot.edits[-1][2]


@pytest.mark.asyncio
async def test_choosing_the_suggestion_itself_counts_as_a_confirmation(
    session: AsyncSession, test_user, test_workspace, test_categories, test_transactions
):
    await _ready(session, test_user, test_workspace, test_categories, test_transactions)
    ai_id = (await _sent_ids(session))["applied"]
    ref = digest_service.pack(ai_id)

    await handlers.handle_update(
        TestSessionLocal,
        test_workspace.id,
        RecordingBot(),
        _callback(555, f"c:{ref}:{digest_service.pack(test_categories[0].id)}"),
    )

    row = await session.get(AiCategorization, ai_id)
    await session.refresh(row)
    assert row is not None and row.status == "approved"


@pytest.mark.asyncio
async def test_an_unsure_suggestion_can_be_chosen_but_not_confirmed(
    session: AsyncSession, test_user, test_workspace, test_categories, test_transactions
):
    txs = await _ready(session, test_user, test_workspace, test_categories, test_transactions)
    ai_id = (await _sent_ids(session))["low_confidence"]
    ref = digest_service.pack(ai_id)
    bot = RecordingBot()

    await handlers.handle_update(TestSessionLocal, test_workspace.id, bot, _callback(555, f"a:{ref}"))
    assert "Escolher" in (bot.answers[-1][1] or "")
    await session.refresh(txs["PIX RECEBIDO"])
    assert txs["PIX RECEBIDO"].category_id is None

    await handlers.handle_update(
        TestSessionLocal,
        test_workspace.id,
        bot,
        _callback(555, f"c:{ref}:{digest_service.pack(test_categories[2].id)}"),
    )
    await session.refresh(txs["PIX RECEBIDO"])
    assert txs["PIX RECEBIDO"].category_id == test_categories[2].id


@pytest.mark.asyncio
async def test_back_restores_the_original_buttons(
    session: AsyncSession, test_user, test_workspace, test_categories, test_transactions
):
    await _ready(session, test_user, test_workspace, test_categories, test_transactions)
    ref = digest_service.pack((await _sent_ids(session))["applied"])
    bot = RecordingBot()

    await handlers.handle_update(TestSessionLocal, test_workspace.id, bot, _callback(555, f"x:{ref}"))

    _, _, text, keyboard = bot.edits[-1]
    assert "NETFLIX" in text and keyboard is not None
    assert f"a:{ref}" in _buttons(keyboard)


@pytest.mark.asyncio
async def test_confirm_all_confirms_only_what_is_still_in_place(
    session: AsyncSession, test_user, test_workspace, test_categories, test_transactions
):
    txs = await _ready(session, test_user, test_workspace, test_categories, test_transactions)
    await digest_service.send_digest(session, test_workspace.id, client=RecordingBot())
    bot = RecordingBot()

    await handlers.handle_update(TestSessionLocal, test_workspace.id, bot, _callback(555, "A"))

    statuses = {r.status for r in (await session.execute(select(AiCategorization))).scalars()}
    # The confident one is confirmed; the unsure one was never a suggestion.
    assert statuses == {"approved", "low_confidence"}
    assert "1 categorização" in bot.edits[-1][2]
    assert txs  # silence unused


@pytest.mark.asyncio
async def test_a_chat_that_is_not_linked_gets_an_ack_and_nothing_else(
    session: AsyncSession, test_user, test_workspace, test_categories, test_transactions
):
    await _ready(session, test_user, test_workspace, test_categories, test_transactions)
    ref = digest_service.pack((await _sent_ids(session))["applied"])
    bot = RecordingBot()

    await handlers.handle_update(TestSessionLocal, test_workspace.id, bot, _callback(4242, f"a:{ref}"))

    assert bot.answers == [("cb1", None)]
    assert bot.edits == []
    assert "applied" in (await _sent_ids(session))


@pytest.mark.asyncio
async def test_someone_demoted_since_linking_cannot_answer(
    session: AsyncSession, test_user, test_workspace, test_categories,
    test_transactions, viewer_auth_headers,
):
    await _ready(session, test_user, test_workspace, test_categories, test_transactions)
    viewer = (
        await session.execute(select(User).where(User.email == "viewer-role@example.com"))
    ).scalar_one()
    await telegram_service.link_chat(session, test_workspace.id, viewer.id, 999, None)
    ref = digest_service.pack((await _sent_ids(session))["applied"])
    bot = RecordingBot()

    await handlers.handle_update(TestSessionLocal, test_workspace.id, bot, _callback(999, f"a:{ref}"))

    assert bot.edits == []
    assert "applied" in (await _sent_ids(session))
    assert "não pode" in (bot.answers[-1][1] or "").lower() or "can't" in (bot.answers[-1][1] or "")


@pytest.mark.asyncio
async def test_a_button_from_another_workspace_finds_nothing(
    session: AsyncSession, test_user, test_workspace, test_categories, test_transactions
):
    from app.models.workspace import Workspace, WorkspaceMember

    await _ready(session, test_user, test_workspace, test_categories, test_transactions)
    ref = digest_service.pack((await _sent_ids(session))["applied"])
    other = Workspace(id=uuid.uuid4(), name="Other", created_by_user_id=test_user.id)
    session.add(other)
    session.add(WorkspaceMember(workspace_id=other.id, user_id=test_user.id, role="owner"))
    await session.commit()
    await telegram_service.link_chat(session, other.id, test_user.id, 888, None)
    bot = RecordingBot()

    await handlers.handle_update(TestSessionLocal, other.id, bot, _callback(888, f"a:{ref}"))

    assert "applied" in (await _sent_ids(session))
    assert "não existe" in bot.edits[-1][2]


@pytest.mark.asyncio
async def test_garbage_callback_data_is_ignored(
    session: AsyncSession, test_user, test_workspace, test_categories, test_transactions
):
    await _ready(session, test_user, test_workspace, test_categories, test_transactions)
    bot = RecordingBot()

    for data in ("noop", "a:", "a:###", "c:abc", "zzz", ""):
        await handlers.handle_update(TestSessionLocal, test_workspace.id, bot, _callback(555, data))

    assert bot.edits == []
    assert len(bot.answers) == 6


@pytest.mark.asyncio
async def test_category_pages_put_the_most_used_first(
    session: AsyncSession, test_workspace, test_categories, test_transactions
):
    categories, has_next, pages = await digest_service.category_page(session, test_workspace.id, 0)

    assert (has_next, pages) == (False, 1)
    assert {name for _, name in categories} == {"Alimentação", "Transporte", "Receita"}


# ---------------------------------------------------------------------------
# The scheduled run
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_run_sends_the_digest_once_the_workspace_clock_passes_the_time(
    session: AsyncSession, test_user, test_workspace, test_categories, test_transactions
):
    await _ready(session, test_user, test_workspace, test_categories, test_transactions)
    test_workspace.timezone = "America/Sao_Paulo"
    await session.commit()
    bot = RecordingBot()

    with patch.object(digest_service, "TelegramClient", lambda token: bot), patch(
        "app.services.ai_categorization_service.resolve_llm", return_value=None
    ):
        early = datetime(2026, 10, 19, 22, 0, tzinfo=timezone.utc)  # 19:00 local
        assert await run_workspace(session, test_workspace.id, early) == (0, 0)
        assert bot.sent == []

        due = datetime(2026, 10, 19, 23, 30, tzinfo=timezone.utc)  # 20:30 local
        _, sent = await run_workspace(session, test_workspace.id, due)
        assert sent == 2

        later = datetime(2026, 10, 20, 0, 30, tzinfo=timezone.utc)  # 21:30 local, same day
        assert await run_workspace(session, test_workspace.id, later) == (0, 0)

    settings = await session.get(WorkspaceAiSettings, test_workspace.id)
    await session.refresh(settings)
    assert settings is not None and settings.last_digest_on == date(2026, 10, 19)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
def _url(workspace_id, suffix="automation"):
    return f"/api/workspaces/{workspace_id}/{suffix}"


@pytest.mark.asyncio
async def test_the_summary_time_is_saved_as_wall_clock_time(
    client: AsyncClient, auth_headers, test_workspace, test_user, session: AsyncSession
):
    conn = LlmConnection(id=uuid.uuid4(), user_id=test_user.id, name="c", kind="ollama", default_model="m")
    session.add(conn)
    await session.commit()

    resp = await client.put(
        _url(test_workspace.id),
        headers=auth_headers,
        json={
            "ai_categorization_enabled": True,
            "llm_connection_id": str(conn.id),
            "digest_enabled": True,
            "digest_time": "07:30",
        },
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["digest_enabled"] is True
    assert resp.json()["digest_time"] == "07:30"


@pytest.mark.asyncio
async def test_a_summary_needs_ai_categorization_on(
    client: AsyncClient, auth_headers, test_workspace
):
    resp = await client.put(
        _url(test_workspace.id),
        headers=auth_headers,
        json={"ai_categorization_enabled": False, "digest_enabled": True},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["25:00", "7:30", "07:60", "noon", ""])
async def test_a_malformed_time_is_rejected(client: AsyncClient, auth_headers, test_workspace, bad):
    resp = await client.put(
        _url(test_workspace.id),
        headers=auth_headers,
        json={"ai_categorization_enabled": False, "digest_time": bad},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_run_now_sends_the_pending_digest_without_spending_the_day(
    client: AsyncClient, auth_headers, test_user, test_workspace, test_categories,
    test_transactions, session: AsyncSession,
):
    await _ready(session, test_user, test_workspace, test_categories, test_transactions)
    bot = RecordingBot()

    with patch.object(digest_service, "TelegramClient", lambda token: bot), patch(
        "app.services.ai_categorization_service.resolve_llm", return_value=None
    ):
        resp = await client.post(_url(test_workspace.id, "automation/run-now"), headers=auth_headers)

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"categorized": 0, "digest_sent": 2}
    settings = await session.get(WorkspaceAiSettings, test_workspace.id)
    await session.refresh(settings)
    assert settings is not None and settings.last_digest_on is None
