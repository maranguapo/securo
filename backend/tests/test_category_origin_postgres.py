"""Migration 102's backfill, run against real PostgreSQL (the SQL is not SQLite's)."""
import importlib.util
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.models.ai_categorization import AiCategorization
from app.models.transaction import Transaction
from app.services.category_origin import set_category

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session(postgres_sessions):
    async with postgres_sessions() as session:
        yield session
        await session.rollback()


@pytest.fixture
def clean_db(postgres_sessions):
    """The reused fixtures start in a fresh per-test PostgreSQL schema."""


async def _run_backfill(session):
    """Raw SQL ignores the per-test schema mapping, so point the search path at it."""
    schema = session.get_bind().get_execution_options()["schema_translate_map"][None]
    await session.execute(text(f'SET LOCAL search_path TO "{schema}"'))
    await session.execute(text(_migration().BACKFILL_SQL))
    await session.commit()


def _migration():
    path = Path(__file__).parent.parent / "alembic" / "versions" / "102_category_origin.py"
    spec = importlib.util.spec_from_file_location("migration_102", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_the_backfill_labels_only_what_the_ledger_proves(
    session, test_workspace, test_categories, test_transactions
):
    by_name = {t.description: t for t in test_transactions}
    ai_chose, user_changed, never_ai, low = (
        by_name["NETFLIX"], by_name["PIX RECEBIDO"], by_name["UBER TRIP"], by_name["IFOOD RESTAURANTE"],
    )
    ai_chose.category_id = test_categories[0].id
    user_changed.category_id = test_categories[2].id
    low.category_id = test_categories[1].id
    for tx, status, suggested in (
        (ai_chose, "applied", test_categories[0].id),
        (user_changed, "changed", test_categories[0].id),
        # Confirmed, but the category has moved on since: not provably the model's.
        (low, "approved", test_categories[0].id),
    ):
        session.add(
            AiCategorization(
                transaction_id=tx.id, workspace_id=test_workspace.id,
                suggested_category_id=suggested, confidence=0.9, status=status,
            )
        )
    await session.commit()

    await _run_backfill(session)

    for tx in (ai_chose, user_changed, never_ai, low):
        await session.refresh(tx)
    assert ai_chose.category_source == "ai"
    assert user_changed.category_source == "user"
    assert never_ai.category_source is None  # no ledger row: unknown, not guessed
    assert low.category_source is None


async def test_the_backfill_never_overwrites_a_recorded_origin(
    session, test_workspace, test_categories, test_transactions
):
    tx = {t.description: t for t in test_transactions}["NETFLIX"]
    set_category(tx, test_categories[0].id, "rule")
    session.add(
        AiCategorization(
            transaction_id=tx.id, workspace_id=test_workspace.id,
            suggested_category_id=test_categories[0].id, confidence=0.9, status="applied",
        )
    )
    await session.commit()

    await _run_backfill(session)

    rows = (await session.execute(select(Transaction.category_source).where(Transaction.id == tx.id))).scalars().all()
    assert rows == ["rule"]
