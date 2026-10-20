"""What the bot does with an incoming update.

Only the linking handshake lives here for now; the digest buttons are added
alongside the digest itself.
"""
import logging
import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.user import User
from app.models.workspace import Workspace
from app.services import telegram_service
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
