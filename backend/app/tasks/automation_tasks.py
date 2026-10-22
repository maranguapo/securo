import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents.config import get_agent_settings
from app.core.app_clock import get_timezone, get_workspace_timezone, use_resolved_timezone
from app.core.config import get_settings
from app.models.workspace_ai_settings import WorkspaceAiSettings
from app.services import ai_categorization_service, digest_service
from app.worker import celery_app

logger = logging.getLogger(__name__)


def _make_session_maker():
    """Create a fresh engine+session for the Celery worker event loop."""
    engine = create_async_engine(get_settings().database_url)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def run_workspace(
    session: AsyncSession, workspace_id: uuid.UUID, now: Optional[datetime] = None
) -> tuple[int, int]:
    """One workspace's automations: categorize, then send the digest if due.

    Categorizing first means the digest carries what the model has just done.
    Returns (categories applied, transactions sent in a digest).
    """
    now = now or datetime.now(timezone.utc)
    applied = await ai_categorization_service.categorize_pending(session, workspace_id, now=now)
    await ai_categorization_service.record_run(session, workspace_id, applied, now)

    settings = await ai_categorization_service.get_settings(session, workspace_id)
    sent = 0
    if settings is not None and settings.digest_enabled:
        tz = await get_workspace_timezone(session, workspace_id)
        if digest_service.is_due(settings, now, tz):
            sent = await digest_service.send_digest(
                session, workspace_id, now=now, local_date=now.astimezone(tz).date()
            )
    return applied, sent


async def _run_all() -> tuple[int, int]:
    engine, session_maker = _make_session_maker()
    try:
        async with session_maker() as session:
            # Workspace timezones that are unset follow the application one.
            operation_timezone = await get_timezone(session, fresh=True)
            workspace_ids = (
                await session.execute(
                    select(WorkspaceAiSettings.workspace_id).where(
                        or_(
                            WorkspaceAiSettings.ai_categorization_enabled.is_(True),
                            WorkspaceAiSettings.digest_enabled.is_(True),
                        )
                    )
                )
            ).scalars().all()

        applied = sent = 0
        with use_resolved_timezone(operation_timezone):
            for workspace_id in workspace_ids:
                # One workspace failing must not starve the others.
                try:
                    async with session_maker() as session:
                        a, s = await run_workspace(session, workspace_id)
                        applied += a
                        sent += s
                except Exception:
                    logger.exception("Automations failed for workspace %s", workspace_id)
        return applied, sent
    finally:
        await engine.dispose()


@celery_app.task(name="app.tasks.automation_tasks.run_workspace_automations")
def run_workspace_automations() -> dict:
    """Celery task: run each workspace's automations (AI categorization and the
    daily digest)."""
    # The LLM connections live in the optional agents module.
    if not get_agent_settings().enabled:
        return {"skipped": "agents disabled"}
    applied, sent = asyncio.run(_run_all())
    if applied or sent:
        logger.info("Automations: %d categories applied, %d sent in digests", applied, sent)
    return {"applied": applied, "digest_sent": sent}
