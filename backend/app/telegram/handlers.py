"""What the bot does with an incoming update: the linking handshake and the
buttons on the daily digest."""
import logging
import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.user import User
from app.models.workspace import Workspace
from app.services import digest_service, telegram_service
from app.telegram.client import TelegramClient, TelegramError

logger = logging.getLogger(__name__)

_MESSAGES = {
    "en": {
        "linked": "✅ Connected to “{workspace}”. You'll get the daily summary of your transactions here.",
        "bad_code": "That code is invalid or has expired. Generate a new one under Workspace settings → Integrations.",
        "not_allowed": "You no longer have permission to edit this workspace, so this chat was not connected.",
        "help": "This bot sends your Securo daily summary. To connect a chat, use the button under Workspace settings → Integrations.",
    },
    "pt": {
        "linked": "✅ Conectado a “{workspace}”. Você receberá aqui o resumo diário das suas transações.",
        "bad_code": "Esse código é inválido ou expirou. Gere um novo em Configurações do workspace → Integrações.",
        "not_allowed": "Você não tem mais permissão de edição neste workspace, então este chat não foi conectado.",
        "help": "Este bot envia o resumo diário do Securo. Para conectar um chat, use o botão em Configurações do workspace → Integrações.",
    },
}


def _lang(user: Optional[User]) -> str:
    language = str(((user.preferences or {}) if user else {}).get("language") or "")
    return "pt" if language.lower().startswith("pt") else "en"


def _text(lang: str, key: str, **kwargs: str) -> str:
    return _MESSAGES[lang][key].format(**kwargs)


async def _reply(client: TelegramClient, chat_id: int, text: str) -> None:
    try:
        await client.send_message(chat_id, text)
    except TelegramError as exc:
        logger.warning("Could not reply on Telegram: %s", exc)


async def handle_update(
    session_maker: async_sessionmaker[AsyncSession],
    workspace_id: uuid.UUID,
    client: TelegramClient,
    update: dict,
) -> None:
    callback = update.get("callback_query")
    if isinstance(callback, dict):
        await _handle_callback(session_maker, workspace_id, client, callback)
        return
    message = update.get("message")
    if not isinstance(message, dict):
        return
    chat = message.get("chat") or {}
    # Groups and channels are ignored: a digest of someone's money belongs in
    # a private chat.
    if chat.get("type") != "private" or not isinstance(chat.get("id"), int):
        return
    chat_id: int = chat["id"]
    text = str(message.get("text") or "").strip()
    sender = message.get("from") or {}

    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        if len(parts) == 2:
            await _handle_link(session_maker, workspace_id, client, chat_id, parts[1].strip(), sender)
            return

    async with session_maker() as session:
        linked = await telegram_service.get_link_by_chat(session, workspace_id, chat_id)
        user = await session.get(User, linked.user_id) if linked else None
    # Strangers get no reply: confirming the bot exists, and what it does, to
    # anyone who finds it is not useful.
    if linked is not None:
        await _reply(client, chat_id, _text(_lang(user), "help"))


async def _handle_link(
    session_maker: async_sessionmaker[AsyncSession],
    workspace_id: uuid.UUID,
    client: TelegramClient,
    chat_id: int,
    code: str,
    sender: dict,
) -> None:
    issued = await telegram_service.consume_link_code(code)
    # The code names a workspace. A bot belongs to exactly one, so a code
    # minted for another workspace must not link here.
    if issued is None or issued[0] != workspace_id:
        await _reply(client, chat_id, _text("en", "bad_code"))
        return
    _, user_id = issued

    async with session_maker() as session:
        user = await session.get(User, user_id)
        lang = _lang(user)
        if user is None or not await telegram_service.can_write(session, workspace_id, user_id):
            await _reply(client, chat_id, _text(lang, "not_allowed"))
            return
        username = sender.get("username")
        await telegram_service.link_chat(
            session, workspace_id, user_id, chat_id, str(username) if username else None
        )
        workspace = await session.get(Workspace, workspace_id)
    await _reply(
        client, chat_id, _text(lang, "linked", workspace=workspace.name if workspace else "")
    )


# --- digest buttons ---------------------------------------------------------
async def _answer(client: TelegramClient, callback_id: str, text: Optional[str] = None) -> None:
    try:
        await client.answer_callback_query(callback_id, text)
    except TelegramError as exc:
        logger.debug("Could not answer callback: %s", exc)


async def _edit(
    client: TelegramClient,
    chat_id: int,
    message_id: int,
    text: str,
    keyboard: Optional[dict],
) -> None:
    try:
        await client.edit_message_text(chat_id, message_id, text, reply_markup=keyboard)
    except TelegramError as exc:
        # "message is not modified" is routine when a button is tapped twice.
        logger.debug("Could not edit message: %s", exc)


async def _handle_callback(
    session_maker: async_sessionmaker[AsyncSession],
    workspace_id: uuid.UUID,
    client: TelegramClient,
    callback: dict,
) -> None:
    callback_id = str(callback.get("id") or "")
    data = str(callback.get("data") or "")
    message = callback.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")
    if not callback_id or not isinstance(chat_id, int) or not isinstance(message_id, int):
        return

    async with session_maker() as session:
        link = await telegram_service.get_link_by_chat(session, workspace_id, chat_id)
        if link is None:
            # Not one of ours: acknowledge so the button stops spinning, nothing more.
            await _answer(client, callback_id)
            return
        user = await session.get(User, link.user_id)
        lang = _lang(user)
        user_id = link.user_id
        if not await telegram_service.can_write(session, workspace_id, user_id):
            await _answer(client, callback_id, digest_service.t(lang, "no_permission"))
            return

        feedback: Optional[str] = None
        try:
            feedback = await _dispatch(
                session, workspace_id, user_id, lang, client, chat_id, message_id, data
            )
        except Exception:
            logger.exception("Digest button failed for workspace %s", workspace_id)
            await session.rollback()
            feedback = digest_service.t(lang, "error")
        await _answer(client, callback_id, feedback)


async def _dispatch(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    lang: str,
    client: TelegramClient,
    chat_id: int,
    message_id: int,
    data: str,
) -> Optional[str]:
    action, _, rest = data.partition(":")
    t = digest_service.t

    if action == "noop":
        return None

    if action == "A":
        count = await digest_service.approve_all(session, workspace_id, user_id)
        await _edit(client, chat_id, message_id, t(lang, "approved_all", n=count), None)
        return t(lang, "approved_all", n=count) if count else t(lang, "nothing")

    ref_text, _, extra = rest.partition(":")
    ref = digest_service.unpack(ref_text)
    if ref is None:
        return None

    if action == "a":
        outcome = await digest_service.approve(session, workspace_id, ref, user_id)
        return await _show_outcome(client, chat_id, message_id, lang, outcome)

    if action == "e":
        item = await digest_service.load_item(session, workspace_id, ref)
        if item is None:
            await _edit(client, chat_id, message_id, t(lang, "gone"), None)
            return t(lang, "gone")
        if item.row.status in ("approved", "changed"):
            return await _show_outcome(
                client, chat_id, message_id, lang,
                digest_service.Outcome(
                    "already", item, "confirmed" if item.row.status == "approved" else "changed"
                ),
            )
        try:
            page = int(extra or 0)
        except ValueError:
            page = 0
        categories, has_next, pages = await digest_service.category_page(
            session, workspace_id, page
        )
        page = max(0, min(page, pages - 1))
        text, keyboard = digest_service.render_category_picker(
            ref, categories, page, has_next, pages, lang
        )
        await _edit(client, chat_id, message_id, text, keyboard)
        return None

    if action == "x":
        item = await digest_service.load_item(session, workspace_id, ref)
        if item is None:
            await _edit(client, chat_id, message_id, t(lang, "gone"), None)
            return t(lang, "gone")
        state = {"approved": "confirmed", "changed": "changed"}.get(item.row.status)
        text, keyboard = digest_service.render_item(item, lang, state=state)
        await _edit(client, chat_id, message_id, text, keyboard)
        return None

    if action == "c":
        category_id = digest_service.unpack(extra)
        if category_id is None:
            return None
        outcome = await digest_service.choose(session, workspace_id, ref, category_id, user_id)
        return await _show_outcome(client, chat_id, message_id, lang, outcome)

    return None


async def _show_outcome(
    client: TelegramClient,
    chat_id: int,
    message_id: int,
    lang: str,
    outcome: "digest_service.Outcome",
) -> Optional[str]:
    t = digest_service.t
    if outcome.kind == "gone" or outcome.item is None:
        await _edit(client, chat_id, message_id, t(lang, "gone"), None)
        return t(lang, "gone")
    if outcome.kind == "needs_choice":
        # A suggestion the model was unsure of has nothing to confirm.
        return t(lang, "needs_choice")
    text, keyboard = digest_service.render_item(outcome.item, lang, state=outcome.state)
    await _edit(client, chat_id, message_id, text, keyboard)
    return t(lang, "already") if outcome.kind == "already" else None
