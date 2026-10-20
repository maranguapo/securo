"""The daily digest of AI categorizations, and answering it.

Once a day, at a time the owner picks in the workspace's own timezone, the
transactions the model has categorized since the last digest go out to the
workspace's linked Telegram chats. Each one can be confirmed or changed from
the chat. Doing nothing is also an answer: the category the model applied
stays.
"""
import asyncio
import base64
import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_categorization import AiCategorization
from app.models.category import Category
from app.models.transaction import Transaction
from app.models.workspace_ai_settings import WorkspaceAiSettings
from app.services import integration_service, telegram_service
from app.telegram.client import TelegramClient, TelegramError

logger = logging.getLogger(__name__)

# One message per transaction; the rest wait for tomorrow's digest.
MAX_ITEMS = 40
CATEGORIES_PER_PAGE = 8
# Telegram tolerates short bursts but asks for roughly a message a second per
# chat; this keeps a full digest comfortably inside the limits.
SEND_DELAY_SECONDS = 0.05

_SYMBOLS = {"BRL": "R$", "USD": "$", "EUR": "€", "GBP": "£"}

_TEXT = {
    "en": {
        "header": "🗂 Daily summary — {n} transaction(s) to review",
        "header_hint": "Tap ✅ to confirm or ✏️ to change. If you do nothing, the suggested category stays.",
        "confirm_all": "✅ Confirm all",
        "confirm": "✅ Confirm",
        "change": "✏️ Change",
        "choose": "✏️ Choose",
        "cancel": "↩ Back",
        "category": "Category",
        "unsure": "no category — the AI wasn't sure",
        "confirmed": "confirmed",
        "changed": "changed",
        "more": "…and {n} more. They'll be in the next summary, or see them in the app.",
        "pick": "Pick a category:",
        "approved_all": "Confirmed {n} categorization(s).",
        "already": "Already reviewed.",
        "gone": "That transaction no longer exists.",
        "no_permission": "You can't edit this workspace.",
        "nothing": "Nothing to confirm.",
        "needs_choice": "Nothing to confirm yet. Tap ✏️ Choose to pick a category.",
        "error": "Something went wrong. Try again from the app.",
        "page": "{page}/{pages}",
        "ai": "AI",
    },
    "pt": {
        "header": "🗂 Resumo diário — {n} transação(ões) para revisar",
        "header_hint": "Toque em ✅ para confirmar ou ✏️ para alterar. Se não fizer nada, a categoria sugerida permanece.",
        "confirm_all": "✅ Confirmar todas",
        "confirm": "✅ Confirmar",
        "change": "✏️ Alterar",
        "choose": "✏️ Escolher",
        "cancel": "↩ Voltar",
        "category": "Categoria",
        "unsure": "sem categoria — a IA não teve certeza",
        "confirmed": "confirmada",
        "changed": "alterada",
        "more": "…e mais {n}. Entram no próximo resumo, ou veja no app.",
        "pick": "Escolha uma categoria:",
        "approved_all": "{n} categorização(ões) confirmada(s).",
        "already": "Já revisada.",
        "gone": "Essa transação não existe mais.",
        "no_permission": "Você não pode editar este workspace.",
        "nothing": "Nada para confirmar.",
        "needs_choice": "Ainda não há o que confirmar. Toque em ✏️ Escolher para definir a categoria.",
        "error": "Algo deu errado. Tente de novo pelo app.",
        "page": "{page}/{pages}",
        "ai": "IA",
    },
}


def t(lang: str, key: str, **kwargs) -> str:
    return _TEXT["pt" if lang == "pt" else "en"][key].format(**kwargs)


# --- when to send -----------------------------------------------------------
def is_due(
    settings: WorkspaceAiSettings, now_utc: datetime, tz, *, force: bool = False
) -> bool:
    """Whether today's digest should go out now.

    Due once the workspace's local clock has passed `digest_time` and none has
    gone out on the workspace's local date. Judging by the local date, not the
    UTC one, is what keeps a workspace far from UTC from getting two digests
    or none around midnight.
    """
    if not settings.digest_enabled:
        return False
    if force:
        return True
    local = now_utc.astimezone(tz)
    if settings.last_digest_on is not None and settings.last_digest_on >= local.date():
        return False
    return local.time().replace(second=0, microsecond=0) >= settings.digest_time


def parse_time(value: str) -> time:
    """'HH:MM' -> time. Raises ValueError for anything else."""
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


# --- callback data ----------------------------------------------------------
# Telegram caps callback_data at 64 bytes, so UUIDs travel as 22 characters.
def pack(value: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(value.bytes).rstrip(b"=").decode("ascii")


def unpack(text: str) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(bytes=base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)))
    except (ValueError, TypeError):
        return None


# --- rendering ---------------------------------------------------------------
@dataclass
class Item:
    row: AiCategorization
    transaction: Transaction
    category_name: Optional[str]


def format_money(amount: float, currency: str, lang: str) -> str:
    symbol = _SYMBOLS.get(currency, currency)
    text = f"{abs(amount):,.2f}"
    if lang == "pt":
        text = text.replace(",", "§").replace(".", ",").replace("§", ".")
    sign = "-" if amount < 0 else ""
    return f"{sign}{symbol} {text}" if len(symbol) <= 2 else f"{sign}{text} {symbol}"


def _signed(tx: Transaction) -> float:
    amount = float(tx.amount)
    return -abs(amount) if tx.type == "debit" else abs(amount)


def render_item(item: Item, lang: str, *, state: Optional[str] = None) -> tuple[str, Optional[dict]]:
    """(text, keyboard) for one transaction. `state` is 'confirmed' or
    'changed' once it has been answered, which also drops the buttons."""
    tx = item.transaction
    first = f"{tx.date.strftime('%d/%m')} · {tx.description.strip()[:80]}"
    money = format_money(_signed(tx), tx.currency, lang)
    if item.category_name:
        confidence = (
            f" · {t(lang, 'ai')} {round(item.row.confidence * 100)}%"
            if item.row.confidence is not None and state is None
            else ""
        )
        suffix = f" ({t(lang, state)})" if state else confidence
        body = f"{t(lang, 'category')}: {item.category_name}{suffix}"
    else:
        body = f"{t(lang, 'category')}: {t(lang, 'unsure')}"
    text = f"{first}\n{money}\n{body}"
    if state is not None:
        return text, None

    ref = pack(item.row.id)
    buttons = []
    if item.category_name:
        buttons.append({"text": t(lang, "confirm"), "callback_data": f"a:{ref}"})
        buttons.append({"text": t(lang, "change"), "callback_data": f"e:{ref}:0"})
    else:
        buttons.append({"text": t(lang, "choose"), "callback_data": f"e:{ref}:0"})
    return text, {"inline_keyboard": [buttons]}


def render_category_picker(
    ref: uuid.UUID,
    categories: list[tuple[uuid.UUID, str]],
    page: int,
    has_next: bool,
    pages: int,
    lang: str,
) -> tuple[str, dict]:
    rows = [
        [{"text": name[:30], "callback_data": f"c:{pack(ref)}:{pack(cid)}"}]
        for cid, name in categories
    ]
    nav = []
    if page > 0:
        nav.append({"text": "◀", "callback_data": f"e:{pack(ref)}:{page - 1}"})
    nav.append({"text": t(lang, "page", page=page + 1, pages=pages), "callback_data": "noop"})
    if has_next:
        nav.append({"text": "▶", "callback_data": f"e:{pack(ref)}:{page + 1}"})
    rows.append(nav)
    rows.append([{"text": t(lang, "cancel"), "callback_data": f"x:{pack(ref)}"}])
    return t(lang, "pick"), {"inline_keyboard": rows}


# --- reading the ledger ------------------------------------------------------
async def load_item(
    session: AsyncSession, workspace_id: uuid.UUID, ai_id: uuid.UUID
) -> Optional[Item]:
    row = (
        await session.execute(
            select(AiCategorization).where(
                AiCategorization.id == ai_id, AiCategorization.workspace_id == workspace_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    tx = (
        await session.execute(
            select(Transaction).where(
                Transaction.id == row.transaction_id, Transaction.workspace_id == workspace_id
            )
        )
    ).scalar_one_or_none()
    if tx is None:
        return None
    name = None
    if tx.category_id is not None:
        name = await session.scalar(select(Category.name).where(Category.id == tx.category_id))
    return Item(row=row, transaction=tx, category_name=name)


async def _pending_items(session: AsyncSession, workspace_id: uuid.UUID) -> list[Item]:
    """Unsent evaluations that still need the user's eye, newest first.

    Anything the user already settled in the app is closed out here as
    'changed' instead of being sent: asking about a category they picked
    themselves would be noise, and the correction is worth learning from.
    """
    rows = (
        await session.execute(
            select(AiCategorization, Transaction)
            .join(Transaction, Transaction.id == AiCategorization.transaction_id)
            .where(
                AiCategorization.workspace_id == workspace_id,
                AiCategorization.notified_at.is_(None),
                AiCategorization.status.in_(("applied", "low_confidence")),
            )
            .order_by(Transaction.date.desc(), AiCategorization.created_at.desc())
        )
    ).all()

    name_rows = await session.execute(
        select(Category.id, Category.name).where(Category.workspace_id == workspace_id)
    )
    names = {category_id: name for category_id, name in name_rows.all()}
    items: list[Item] = []
    for row, tx in rows:
        if row.status == "applied":
            settled_elsewhere = tx.category_id != row.suggested_category_id
        else:
            settled_elsewhere = tx.category_id is not None
        if settled_elsewhere:
            row.status = "changed"
            continue
        items.append(
            Item(row=row, transaction=tx, category_name=names.get(tx.category_id))
        )
    await session.flush()
    return items


async def category_page(
    session: AsyncSession, workspace_id: uuid.UUID, page: int
) -> tuple[list[tuple[uuid.UUID, str]], bool, int]:
    """Categories to pick from, most used first. (page items, has_next, pages)."""
    usage_rows = await session.execute(
        select(Transaction.category_id, func.count())
        .where(
            Transaction.workspace_id == workspace_id,
            Transaction.category_id.is_not(None),
        )
        .group_by(Transaction.category_id)
    )
    usage = {category_id: count for category_id, count in usage_rows.all()}
    cats = (
        await session.execute(
            select(Category.id, Category.name).where(
                Category.workspace_id == workspace_id, Category.is_hidden.is_(False)
            )
        )
    ).all()
    ordered = sorted(cats, key=lambda c: (-usage.get(c[0], 0), c[1].lower()))
    pages = max(1, -(-len(ordered) // CATEGORIES_PER_PAGE))
    page = max(0, min(page, pages - 1))
    start = page * CATEGORIES_PER_PAGE
    chunk = ordered[start : start + CATEGORIES_PER_PAGE]
    return [(cid, name) for cid, name in chunk], page < pages - 1, pages


# --- sending -----------------------------------------------------------------
async def send_digest(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    client: Optional[TelegramClient] = None,
    now: Optional[datetime] = None,
    local_date: Optional[date] = None,
) -> int:
    """Send the pending evaluations to every linked chat. Returns how many
    transactions were included (0 when there was nothing to send).

    The ledger rows are marked as sent only if at least one chat received
    them, so a Telegram outage or an unlinked workspace loses nothing: the
    same items are simply sent next time.
    """
    now = now or datetime.now(timezone.utc)
    if client is None:
        bot = await integration_service.get_telegram_bot(session, workspace_id)
        if bot is None:
            return 0
        client = TelegramClient(bot[1])

    from app.models.user import User

    recipients = []
    for link, _ in await telegram_service.list_links(session, workspace_id):
        if await telegram_service.can_write(session, workspace_id, link.user_id):
            user = await session.get(User, link.user_id)
            language = str(((user.preferences or {}) if user else {}).get("language") or "")
            recipients.append((link.chat_id, "pt" if language.lower().startswith("pt") else "en"))
    if not recipients:
        return 0

    items = await _pending_items(session, workspace_id)
    sendable = items[:MAX_ITEMS]
    overflow = len(items) - len(sendable)
    delivered = False

    if sendable:
        for chat_id, lang in recipients:
            try:
                header_keyboard = None
                if any(i.category_name for i in sendable):
                    header_keyboard = {
                        "inline_keyboard": [
                            [{"text": t(lang, "confirm_all"), "callback_data": "A"}]
                        ]
                    }
                await client.send_message(
                    chat_id,
                    f"{t(lang, 'header', n=len(items))}\n{t(lang, 'header_hint')}",
                    reply_markup=header_keyboard,
                )
                for item in sendable:
                    text, keyboard = render_item(item, lang)
                    await client.send_message(chat_id, text, reply_markup=keyboard)
                    await asyncio.sleep(SEND_DELAY_SECONDS)
                if overflow:
                    await client.send_message(chat_id, t(lang, "more", n=overflow))
                delivered = True
            except TelegramError as exc:
                logger.warning("Digest to chat failed for workspace %s: %s", workspace_id, exc)

    if delivered:
        for item in sendable:
            item.row.notified_at = now
    settings = await session.get(WorkspaceAiSettings, workspace_id)
    # An empty day still counts as the day's digest, so it is not retried every
    # five minutes until midnight. A day where every chat failed is retried.
    if settings is not None and (delivered or not sendable) and local_date is not None:
        settings.last_digest_on = local_date
    await session.commit()
    return len(sendable) if delivered else 0


# --- answering ----------------------------------------------------------------
@dataclass
class Outcome:
    kind: str  # 'done' | 'already' | 'gone' | 'needs_choice'
    item: Optional[Item] = None
    state: Optional[str] = None  # 'confirmed' | 'changed'


def _mark(row: AiCategorization, user_id: uuid.UUID, status: str) -> None:
    row.status = status
    row.reviewed_by_user_id = user_id
    row.reviewed_at = datetime.now(timezone.utc)


async def approve(
    session: AsyncSession, workspace_id: uuid.UUID, ai_id: uuid.UUID, user_id: uuid.UUID
) -> Outcome:
    item = await load_item(session, workspace_id, ai_id)
    if item is None:
        return Outcome("gone")
    if item.row.status in ("approved", "changed"):
        return Outcome("already", item, "confirmed" if item.row.status == "approved" else "changed")
    if item.transaction.category_id != item.row.suggested_category_id or not item.category_name:
        # Nothing to confirm: either the category moved on, or there never was one.
        if item.transaction.category_id is None:
            return Outcome("needs_choice", item)
        _mark(item.row, user_id, "changed")
        await session.commit()
        return Outcome("already", item, "changed")
    _mark(item.row, user_id, "approved")
    await session.commit()
    return Outcome("done", item, "confirmed")


async def choose(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    ai_id: uuid.UUID,
    category_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Outcome:
    item = await load_item(session, workspace_id, ai_id)
    if item is None:
        return Outcome("gone")
    category = (
        await session.execute(
            select(Category).where(
                Category.id == category_id, Category.workspace_id == workspace_id
            )
        )
    ).scalar_one_or_none()
    if category is None:
        return Outcome("gone")
    item.transaction.category_id = category.id
    item.category_name = category.name
    same = category.id == item.row.suggested_category_id
    _mark(item.row, user_id, "approved" if same else "changed")
    await session.commit()
    return Outcome("done", item, "confirmed" if same else "changed")


async def approve_all(session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID) -> int:
    """Confirm every sent suggestion that is still waiting and still in place."""
    rows = (
        await session.execute(
            select(AiCategorization, Transaction)
            .join(Transaction, Transaction.id == AiCategorization.transaction_id)
            .where(
                AiCategorization.workspace_id == workspace_id,
                AiCategorization.notified_at.is_not(None),
                AiCategorization.status == "applied",
            )
        )
    ).all()
    count = 0
    for row, tx in rows:
        if tx.category_id == row.suggested_category_id:
            _mark(row, user_id, "approved")
            count += 1
        else:
            row.status = "changed"
    await session.commit()
    return count
