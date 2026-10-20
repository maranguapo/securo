"""Linking Telegram chats to a workspace."""
import json
import secrets
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import redis as redis_core
from app.models.telegram_link import TelegramLink
from app.models.user import User
from app.services import workspace_service

LINK_TTL_SECONDS = 15 * 60
_CODE_KEY = "telegram:link:{code}"
_WRITE_ROLES = ("owner", "editor", "manager")


async def create_link_code(workspace_id: uuid.UUID, user_id: uuid.UUID) -> str:
    """A one-time code the user sends to the bot to prove the chat is theirs.

    It binds the chat to this user in this workspace and expires quickly, so a
    leaked link is of little use. `token_urlsafe` fits Telegram's /start
    payload alphabet ([A-Za-z0-9_-], up to 64 characters).
    """
    code = secrets.token_urlsafe(16)
    redis = await redis_core.get_redis()
    await redis.set(
        _CODE_KEY.format(code=code),
        json.dumps({"workspace_id": str(workspace_id), "user_id": str(user_id)}),
        ex=LINK_TTL_SECONDS,
    )
    return code


async def consume_link_code(code: str) -> Optional[tuple[uuid.UUID, uuid.UUID]]:
    """(workspace_id, user_id) the code was issued for. Single use."""
    redis = await redis_core.get_redis()
    key = _CODE_KEY.format(code=code)
    raw = await redis.get(key)
    if not raw:
        return None
    await redis.delete(key)
    try:
        data = json.loads(raw)
        return uuid.UUID(data["workspace_id"]), uuid.UUID(data["user_id"])
    except (ValueError, KeyError, TypeError):
        return None


async def can_write(session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    """Whether the user may still change data in the workspace.

    Checked when linking and again whenever the chat acts, because a person
    removed from the workspace (or demoted to viewer) keeps their Telegram.
    """
    member = await workspace_service.get_membership(session, workspace_id, user_id)
    if member is not None:
        return member.role in _WRITE_ROLES
    return await workspace_service.is_workspace_manager(session, workspace_id, user_id)


async def link_chat(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    chat_id: int,
    username: Optional[str],
) -> TelegramLink:
    """Link a chat, replacing the user's previous chat and releasing the chat
    from any other user in this workspace."""
    existing = list(
        (
            await session.execute(
                select(TelegramLink).where(
                    TelegramLink.workspace_id == workspace_id,
                    (TelegramLink.user_id == user_id) | (TelegramLink.chat_id == chat_id),
                )
            )
        ).scalars()
    )
    link = next((link for link in existing if link.user_id == user_id), None)
    for other in existing:
        if other is not link:
            await session.delete(other)
    await session.flush()
    if link is None:
        link = TelegramLink(workspace_id=workspace_id, user_id=user_id)
        session.add(link)
    link.chat_id = chat_id
    link.telegram_username = username
    await session.commit()
    return link


async def list_links(session: AsyncSession, workspace_id: uuid.UUID) -> list[tuple[TelegramLink, str]]:
    """Links with the e-mail of the user each belongs to."""
    links = list(
        (
            await session.execute(
                select(TelegramLink)
                .where(TelegramLink.workspace_id == workspace_id)
                .order_by(TelegramLink.created_at)
            )
        ).scalars()
    )
    if not links:
        return []
    # A second query rather than a join: a workspace has a handful of links,
    # and it keeps the lookup independent of how each table stores its UUIDs.
    rows = await session.execute(
        select(User.id, User.email).where(User.id.in_([link.user_id for link in links]))
    )
    emails = {user_id: email for user_id, email in rows.all()}
    return [(link, emails.get(link.user_id, "")) for link in links]


async def get_link_by_chat(
    session: AsyncSession, workspace_id: uuid.UUID, chat_id: int
) -> Optional[TelegramLink]:
    return (
        await session.execute(
            select(TelegramLink).where(
                TelegramLink.workspace_id == workspace_id, TelegramLink.chat_id == chat_id
            )
        )
    ).scalar_one_or_none()


async def get_link(
    session: AsyncSession, workspace_id: uuid.UUID, link_id: uuid.UUID
) -> Optional[TelegramLink]:
    return (
        await session.execute(
            select(TelegramLink).where(
                TelegramLink.workspace_id == workspace_id, TelegramLink.id == link_id
            )
        )
    ).scalar_one_or_none()


async def delete_link(session: AsyncSession, link: TelegramLink) -> None:
    await session.delete(link)
    await session.commit()
