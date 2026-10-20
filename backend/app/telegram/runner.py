"""Long-polling runner: one task per configured bot.

Polling (rather than a webhook) means no public URL is needed, so it works
behind NAT on a home server. Run it as its own process:

    python -m app.telegram.runner
"""
import asyncio
import hashlib
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents.services.crypto import decrypt
from app.core import redis as redis_core
from app.core.config import get_settings
from app.models.workspace_integration import WorkspaceIntegration
from app.services.integration_service import TELEGRAM
from app.telegram.client import TelegramClient, TelegramError
from app.telegram.handlers import handle_update

logger = logging.getLogger(__name__)

REFRESH_SECONDS = 60
MAX_BACKOFF_SECONDS = 60
_OFFSET_KEY = "telegram:offset:{workspace_id}"


async def _load_offset(workspace_id: uuid.UUID) -> int | None:
    try:
        redis = await redis_core.get_redis()
        raw = await redis.get(_OFFSET_KEY.format(workspace_id=workspace_id))
        return int(raw) if raw else None
    except Exception:
        return None


async def _save_offset(workspace_id: uuid.UUID, offset: int) -> None:
    try:
        redis = await redis_core.get_redis()
        await redis.set(_OFFSET_KEY.format(workspace_id=workspace_id), offset)
    except Exception:
        logger.debug("Could not persist Telegram offset", exc_info=True)


async def poll_bot(
    session_maker: async_sessionmaker[AsyncSession], workspace_id: uuid.UUID, token: str
) -> None:
    client = TelegramClient(token)
    offset = await _load_offset(workspace_id)
    backoff = 1
    while True:
        try:
            updates = await client.get_updates(offset)
            backoff = 1
        except TelegramError as exc:
            if exc.is_auth_error:
                # A revoked token will not heal by retrying. The supervisor
                # starts a fresh task if the owner saves a new one.
                logger.warning("Telegram bot for workspace %s was rejected; stopping", workspace_id)
                return
            logger.warning("Telegram polling error for workspace %s: %s", workspace_id, exc)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
            continue

        for update in updates:
            offset = int(update["update_id"]) + 1
            try:
                await handle_update(session_maker, workspace_id, client, update)
            except Exception:
                logger.exception("Telegram update failed for workspace %s", workspace_id)
        if updates and offset is not None:
            await _save_offset(workspace_id, offset)


async def _configured_bots(
    session_maker: async_sessionmaker[AsyncSession],
) -> dict[uuid.UUID, str]:
    async with session_maker() as session:
        rows = (
            await session.execute(
                select(WorkspaceIntegration).where(WorkspaceIntegration.provider == TELEGRAM)
            )
        ).scalars()
        bots: dict[uuid.UUID, str] = {}
        for row in rows:
            token = decrypt(row.client_secret_encrypted)
            if token:
                bots[row.workspace_id] = token
    return bots


def _fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def main() -> None:
    engine = create_async_engine(get_settings().database_url)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    running: dict[uuid.UUID, tuple[str, asyncio.Task]] = {}
    logger.info("Telegram runner started")
    try:
        while True:
            try:
                bots = await _configured_bots(session_maker)
            except Exception:
                # Keep the bots that are already polling; try again shortly.
                logger.exception("Could not load the configured Telegram bots")
                await asyncio.sleep(REFRESH_SECONDS)
                continue

            for workspace_id, (fp, task) in list(running.items()):
                token = bots.get(workspace_id)
                # Stopped bot, removed bot, or a token that was replaced.
                if token is None or _fingerprint(token) != fp or task.done():
                    task.cancel()
                    del running[workspace_id]
            for workspace_id, token in bots.items():
                if workspace_id not in running:
                    task = asyncio.create_task(poll_bot(session_maker, workspace_id, token))
                    running[workspace_id] = (_fingerprint(token), task)
            await asyncio.sleep(REFRESH_SECONDS)
    finally:
        for _, task in running.values():
            task.cancel()
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(main())
