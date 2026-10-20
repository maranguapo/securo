"""Categorize uncategorized transactions with an LLM.

Runs after the deterministic sources have had their turn (installment
inheritance, the provider's category, the user's rules), so it only ever
fills what is still empty and never overrides any of them.

What leaves the server, per batch: transaction description, payee, signed
amount, currency and date, plus category names and a few already-categorized
examples. No notes, account data, e-mail or balances. Category ids never go
to the model either: it answers with list positions, which are mapped back
here, so it cannot invent an id.
"""
import json
import logging
import uuid
from datetime import datetime, time, timedelta, timezone
from typing import Optional

from sqlalchemy import exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.providers.base import ChatMessage, LLMError, LLMProvider
from app.agents.services import connection_service as llm_connections
from app.models.ai_categorization import AiCategorization
from app.models.category import Category
from app.models.transaction import Transaction
from app.models.workspace_ai_settings import WorkspaceAiSettings

logger = logging.getLogger(__name__)

# Transactions older than this are left alone, so switching the feature on
# does not send a whole history to the model.
LOOKBACK = timedelta(days=30)
BATCH_SIZE = 50
# Bounds one run, so a large backlog is worked off over several runs.
MAX_PER_RUN = 200
MAX_EXAMPLES = 40
_DESCRIPTION_LIMIT = 120

_SYSTEM_PROMPT = (
    "You categorize personal finance transactions.\n"
    "You receive a numbered list of categories, some examples of how this "
    "user categorizes, and a numbered list of transactions.\n"
    "Answer with ONLY a JSON array, one object per transaction: "
    '{"t": <transaction number>, "c": <category number or null>, '
    '"conf": <confidence between 0 and 1>}.\n'
    "Use null for c when no category fits or you are unsure. "
    "Negative amounts are money spent, positive amounts are money received. "
    "Follow the examples: they show this user's own conventions."
)


async def get_settings(
    session: AsyncSession, workspace_id: uuid.UUID
) -> Optional[WorkspaceAiSettings]:
    return await session.get(WorkspaceAiSettings, workspace_id)


async def resolve_llm(
    session: AsyncSession, settings: WorkspaceAiSettings
) -> Optional[tuple[LLMProvider, str]]:
    """The provider and model configured for the workspace, if usable."""
    if not settings.llm_connection_id or not settings.configured_by_user_id:
        return None
    conn = await llm_connections.get_connection(
        session, settings.llm_connection_id, settings.configured_by_user_id
    )
    if conn is None:
        return None
    model = settings.llm_model or conn.default_model
    if not model:
        return None
    return llm_connections.build_provider_for_connection(conn), model


class InvalidAutomationSettings(ValueError):
    """The requested configuration cannot work; the message says why."""


async def save_settings(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    enabled: bool,
    llm_connection_id: Optional[uuid.UUID],
    llm_model: Optional[str],
    min_confidence: float,
    digest_enabled: bool = False,
    digest_time: Optional[time] = None,
) -> WorkspaceAiSettings:
    """Create or update the workspace's AI settings.

    The connection must be the saving user's own: the background job decrypts
    it with that user's ownership, so a workspace must never be pointed at
    someone else's API key.
    """
    model = (llm_model or "").strip() or None
    if llm_connection_id is not None:
        conn = await llm_connections.get_connection(session, llm_connection_id, user_id)
        if conn is None:
            raise InvalidAutomationSettings("LLM connection not found")
        effective_model = model or conn.default_model
    else:
        effective_model = None
    if enabled:
        if llm_connection_id is None:
            raise InvalidAutomationSettings("Choose an LLM connection to turn this on")
        if not effective_model:
            raise InvalidAutomationSettings("Choose a model to turn this on")

    if digest_enabled and not enabled:
        # The digest is the review of what the model did; without it there is
        # nothing to send.
        raise InvalidAutomationSettings("Turn on AI categorization to get a daily summary")

    row = await session.get(WorkspaceAiSettings, workspace_id)
    if row is None:
        row = WorkspaceAiSettings(workspace_id=workspace_id)
        session.add(row)
    if digest_enabled and not row.digest_enabled:
        # Starting (or restarting) the summary: begin from tomorrow's clean
        # slate rather than whatever date a previous run left behind.
        row.last_digest_on = None
    row.digest_enabled = digest_enabled
    if digest_time is not None:
        row.digest_time = digest_time
    row.ai_categorization_enabled = enabled
    row.llm_connection_id = llm_connection_id
    row.llm_model = model
    row.min_confidence = min_confidence
    row.configured_by_user_id = user_id
    await session.commit()
    return row


def _signed_amount(tx: Transaction) -> float:
    amount = float(tx.amount)
    return -abs(amount) if tx.type == "debit" else abs(amount)


async def _pending_transactions(
    session: AsyncSession, workspace_id: uuid.UUID, now: datetime
) -> list[Transaction]:
    already_seen = exists().where(AiCategorization.transaction_id == Transaction.id)
    result = await session.execute(
        select(Transaction)
        .where(
            Transaction.workspace_id == workspace_id,
            Transaction.category_id.is_(None),
            Transaction.is_ignored.is_(False),
            Transaction.transfer_pair_id.is_(None),
            Transaction.created_at >= now - LOOKBACK,
            ~already_seen,
        )
        .order_by(Transaction.date.desc(), Transaction.created_at.desc())
        .limit(MAX_PER_RUN)
    )
    return list(result.scalars().all())


async def _categories(session: AsyncSession, workspace_id: uuid.UUID) -> list[Category]:
    result = await session.execute(
        select(Category)
        .where(Category.workspace_id == workspace_id, Category.is_hidden.is_(False))
        .order_by(Category.name)
    )
    return list(result.scalars().all())


async def _examples(
    session: AsyncSession, workspace_id: uuid.UUID
) -> list[tuple[str, str]]:
    """(description, category name) pairs showing how this user categorizes.

    Corrections the user made to the model's suggestions come first: they are
    the clearest signal of what it got wrong.
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(description: str, category: str) -> None:
        key = description.strip().lower()
        if key and key not in seen and len(out) < MAX_EXAMPLES:
            seen.add(key)
            out.append((description.strip()[:_DESCRIPTION_LIMIT], category))

    corrected = await session.execute(
        select(Transaction.description, Category.name)
        .join(AiCategorization, AiCategorization.transaction_id == Transaction.id)
        .join(Category, Category.id == Transaction.category_id)
        .where(
            AiCategorization.workspace_id == workspace_id,
            AiCategorization.status == "changed",
        )
        .order_by(AiCategorization.created_at.desc())
        .limit(MAX_EXAMPLES)
    )
    for description, category in corrected.all():
        add(description, category)

    recent = await session.execute(
        select(Transaction.description, Category.name)
        .join(Category, Category.id == Transaction.category_id)
        .where(Transaction.workspace_id == workspace_id)
        .order_by(Transaction.created_at.desc())
        .limit(MAX_EXAMPLES * 5)
    )
    for description, category in recent.all():
        add(description, category)
    return out


def _build_prompt(
    categories: list[Category],
    examples: list[tuple[str, str]],
    batch: list[Transaction],
) -> str:
    lines = ["Categories:"]
    lines += [f"{i}. {c.name}" for i, c in enumerate(categories, start=1)]
    if examples:
        lines += ["", "Examples of how this user categorizes:"]
        lines += [f'- "{d}" -> {c}' for d, c in examples]
    lines += ["", "Transactions:"]
    for i, tx in enumerate(batch, start=1):
        payee = f" | payee: {tx.payee}" if tx.payee else ""
        lines.append(
            f'{i}. "{tx.description.strip()[:_DESCRIPTION_LIMIT]}"{payee} | '
            f"{_signed_amount(tx):.2f} {tx.currency} | {tx.date.isoformat()}"
        )
    return "\n".join(lines)


def _extract_json_array(text: str) -> list:
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def parse_suggestions(
    text: str, n_transactions: int, n_categories: int
) -> dict[int, tuple[Optional[int], float]]:
    """{transaction position: (category position or None, confidence)}.

    Anything malformed, out of range or repeated is dropped rather than
    guessed at: a wrong category applied silently is worse than none.
    """
    out: dict[int, tuple[Optional[int], float]] = {}
    for item in _extract_json_array(text):
        if not isinstance(item, dict):
            continue
        t, c, conf = item.get("t"), item.get("c"), item.get("conf")
        if not isinstance(t, int) or isinstance(t, bool) or not 1 <= t <= n_transactions:
            continue
        if t in out:
            continue
        if c is not None and (
            not isinstance(c, int) or isinstance(c, bool) or not 1 <= c <= n_categories
        ):
            c = None
        try:
            confidence = max(0.0, min(1.0, float(conf)))
        except (TypeError, ValueError):
            confidence = 0.0
        out[t] = (c, confidence)
    return out


async def _record_and_apply(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    batch: list[Transaction],
    categories: list[Category],
    suggestions: dict[int, tuple[Optional[int], float]],
    min_confidence: float,
) -> int:
    applied = 0
    for position, tx in enumerate(batch, start=1):
        suggestion = suggestions.get(position)
        if suggestion is None:
            # The model skipped it. Leave it unrecorded so a later run retries.
            continue
        category_position, confidence = suggestion
        category = categories[category_position - 1] if category_position else None
        confident = category is not None and confidence >= min_confidence

        if confident:
            assert category is not None
            # Only fill an empty category: the user may have set one while the
            # model was thinking.
            result = await session.execute(
                update(Transaction)
                .where(
                    Transaction.id == tx.id,
                    Transaction.workspace_id == workspace_id,
                    Transaction.category_id.is_(None),
                )
                .values(category_id=category.id)
            )
            if (getattr(result, "rowcount", 0) or 0) == 0:
                continue
            applied += 1

        session.add(
            AiCategorization(
                transaction_id=tx.id,
                workspace_id=workspace_id,
                suggested_category_id=category.id if category else None,
                confidence=confidence,
                status="applied" if confident else "low_confidence",
            )
        )
    try:
        await session.commit()
    except IntegrityError:
        # A concurrent run recorded the same transactions first.
        await session.rollback()
        logger.warning("AI categorization batch collided with another run")
        return 0
    return applied


async def categorize_pending(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    now: Optional[datetime] = None,
) -> int:
    """Categorize this workspace's uncategorized transactions. Returns how many
    categories were applied."""
    settings = await get_settings(session, workspace_id)
    if settings is None or not settings.ai_categorization_enabled:
        return 0
    llm = await resolve_llm(session, settings)
    if llm is None:
        logger.warning("AI categorization on for workspace %s but no usable LLM", workspace_id)
        return 0
    provider, model = llm

    now = now or datetime.now(timezone.utc)
    pending = await _pending_transactions(session, workspace_id, now)
    if not pending:
        return 0
    categories = await _categories(session, workspace_id)
    if not categories:
        return 0
    examples = await _examples(session, workspace_id)

    applied = 0
    for i in range(0, len(pending), BATCH_SIZE):
        batch = pending[i : i + BATCH_SIZE]
        messages = [
            ChatMessage(role="system", content=_SYSTEM_PROMPT),
            ChatMessage(role="user", content=_build_prompt(categories, examples, batch)),
        ]
        try:
            response = await provider.chat(
                messages, model=model, temperature=0.1, max_tokens=2000
            )
        except LLMError as exc:
            # Nothing is recorded, so the next run retries these.
            logger.warning("AI categorization stopped for workspace %s: %s", workspace_id, exc)
            break
        suggestions = parse_suggestions(response.content, len(batch), len(categories))
        applied += await _record_and_apply(
            session,
            workspace_id,
            batch,
            categories,
            suggestions,
            settings.min_confidence,
        )
    return applied


__all__ = [
    "categorize_pending",
    "parse_suggestions",
    "get_settings",
    "resolve_llm",
    "save_settings",
    "InvalidAutomationSettings",
]
