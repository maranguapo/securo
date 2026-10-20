import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents.config import get_agent_settings
from app.core.config import get_settings
from app.models.workspace_ai_settings import WorkspaceAiSettings
from app.services import ai_categorization_service
from app.worker import celery_app

logger = logging.getLogger(__name__)


def _make_session_maker():
    """Create a fresh engine+session for the Celery worker event loop."""
    engine = create_async_engine(get_settings().database_url)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _run_all() -> int:
    """AI-categorize every workspace that has it switched on."""
    engine, session_maker = _make_session_maker()
    try:
        async with session_maker() as session:
            workspace_ids = (
                await session.execute(
                    select(WorkspaceAiSettings.workspace_id).where(
                        WorkspaceAiSettings.ai_categorization_enabled.is_(True)
                    )
                )
            ).scalars().all()

        applied = 0
        for workspace_id in workspace_ids:
            # One workspace failing must not starve the others.
            try:
                async with session_maker() as session:
                    applied += await ai_categorization_service.categorize_pending(
                        session, workspace_id
                    )
            except Exception:
                logger.exception("AI categorization failed for workspace %s", workspace_id)
        return applied
    finally:
        await engine.dispose()


@celery_app.task(name="app.tasks.automation_tasks.run_workspace_automations")
def run_workspace_automations() -> dict:
    """Celery task: run each workspace's automations (AI categorization)."""
    # The LLM connections live in the optional agents module.
    if not get_agent_settings().enabled:
        return {"skipped": "agents disabled"}
    applied = asyncio.run(_run_all())
    if applied:
        logger.info("AI categorization applied %d categories", applied)
    return {"applied": applied}
