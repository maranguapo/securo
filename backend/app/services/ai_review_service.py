"""What the AI categorization review screen reads and does.

The screen and the Telegram digest are two ways to answer the same question,
so they share the ledger (`ai_categorizations`) and the same operations: a
category confirmed here is `approved` there, which is exactly what keeps it out
of the next digest. Nothing in this module talks to Telegram.
"""
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.ai_categorization import AiCategorization
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.rule import Rule
from app.models.transaction import Transaction
from app.schemas.rule import RuleAction, RuleCondition, RuleCreate
from app.services import (
    ai_categorization_service,
    digest_service,
    installment_groups,
    integration_service,
    rule_service,
    telegram_service,
)

MAX_ITEMS = 500

# What the list calls each answer, newest to oldest on screen.
REVIEW = "review"  # the model was unsure: waiting for the user's choice
APPLIED = "applied"  # the model applied a category (confirmed, corrected, or not yet)

_INSTALLMENT_SUFFIX = re.compile(r"\s*(?:parc(?:ela)?\.?\s*)?\d{1,2}\s*/\s*\d{1,2}\s*$", re.I)


def merchant_of(tx: Transaction) -> str:
    """The name the screen groups a transaction under: its payee when it has
    one, otherwise its description without the "03/12" parcel suffix."""
    if tx.payee and tx.payee.strip():
        return tx.payee.strip()
    text = _INSTALLMENT_SUFFIX.sub("", tx.description or "").strip()
    return text or (tx.description or "").strip()


def merchant_key(text: str) -> str:
    """Lowercase, accent-free, punctuation-free form used to group and match."""
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", plain.lower()).strip()


# --- reading -------------------------------------------------------------------
async def _category_index(session: AsyncSession, workspace_id: uuid.UUID) -> dict[uuid.UUID, dict]:
    groups = {
        gid: name
        for gid, name in (
            await session.execute(
                select(CategoryGroup.id, CategoryGroup.name).where(
                    CategoryGroup.workspace_id == workspace_id
                )
            )
        ).all()
    }
    rows = (
        await session.execute(
            select(Category).where(Category.workspace_id == workspace_id)
        )
    ).scalars()
    return {
        c.id: {
            "id": c.id,
            "name": c.name,
            "group": groups.get(c.group_id) if c.group_id else None,
            "icon": c.icon,
            "color": c.color,
        }
        for c in rows
    }


def _signed(tx: Transaction) -> float:
    amount = float(tx.amount)
    return -abs(amount) if tx.type == "debit" else abs(amount)


async def list_items(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    tab: str,
    status: Optional[str] = None,
    min_confidence: Optional[float] = None,
    max_confidence: Optional[float] = None,
    q: Optional[str] = None,
    limit: int = MAX_ITEMS,
) -> tuple[list[dict[str, Any]], bool]:
    """The ledger rows for one tab, newest transaction first. The second value
    says whether more matched than were returned."""
    conditions = [
        AiCategorization.workspace_id == workspace_id,
        AiCategorization.is_follower.is_(False),
        Transaction.workspace_id == workspace_id,
    ]
    if tab == REVIEW:
        conditions += [
            AiCategorization.status == "low_confidence",
            Transaction.category_id.is_(None),
        ]
    else:
        wanted = (status,) if status in ("applied", "approved", "changed") else (
            "applied",
            "approved",
            "changed",
        )
        conditions.append(AiCategorization.status.in_(wanted))
    if min_confidence is not None:
        conditions.append(AiCategorization.confidence >= min_confidence)
    if max_confidence is not None:
        conditions.append(AiCategorization.confidence < max_confidence)
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        conditions.append(
            or_(
                func.lower(Transaction.description).like(like),
                func.lower(func.coalesce(Transaction.payee, "")).like(like),
            )
        )

    rows = (
        await session.execute(
            select(AiCategorization, Transaction, Account.name)
            .join(Transaction, Transaction.id == AiCategorization.transaction_id)
            .join(Account, Account.id == Transaction.account_id)
            .where(*conditions)
            .order_by(Transaction.date.desc(), AiCategorization.created_at.desc())
            .limit(limit + 1)
        )
    ).all()
    more = len(rows) > limit
    rows = rows[:limit]

    categories = await _category_index(session, workspace_id)
    items: list[dict[str, Any]] = []
    for row, tx, account_name in rows:
        merchant = merchant_of(tx)
        parcel = None
        if installment_groups.group_key(tx) is not None:
            parcel = {
                "number": tx.installment_number,
                "total": tx.total_installments,
                "others": await installment_groups.count_siblings(session, tx),
            }
        items.append(
            {
                "id": row.id,
                "transaction_id": tx.id,
                "status": row.status,
                "confidence": row.confidence,
                "reason": row.reason,
                "decided_at": row.created_at,
                "reviewed_at": row.reviewed_at,
                # Still on its way to the next Telegram digest.
                "pending_telegram": row.notified_at is None
                and row.status in ("applied", "low_confidence"),
                "description": tx.description,
                "payee": tx.payee,
                "merchant": merchant,
                "merchant_key": merchant_key(merchant),
                "amount": _signed(tx),
                "currency": tx.currency,
                "date": tx.date,
                "account_name": account_name,
                "category": categories.get(tx.category_id) if tx.category_id else None,
                "suggested": categories.get(row.suggested_category_id)
                if row.suggested_category_id
                else None,
                "installment": parcel,
            }
        )
    return items, more


async def summary(session: AsyncSession, workspace_id: uuid.UUID) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    settings = await ai_categorization_service.get_settings(session, workspace_id)

    countable = (
        Transaction.workspace_id == workspace_id,
        Transaction.is_ignored.is_(False),
        Transaction.transfer_pair_id.is_(None),
    )
    total = int(await session.scalar(select(func.count()).select_from(Transaction).where(*countable)) or 0)
    decided = int(
        await session.scalar(
            select(func.count())
            .select_from(Transaction)
            .where(*countable, Transaction.category_id.is_not(None))
        )
        or 0
    )

    visible = (
        AiCategorization.workspace_id == workspace_id,
        AiCategorization.is_follower.is_(False),
    )
    to_review = int(
        await session.scalar(
            select(func.count())
            .select_from(AiCategorization)
            .join(Transaction, Transaction.id == AiCategorization.transaction_id)
            .where(*visible, AiCategorization.status == "low_confidence", Transaction.category_id.is_(None))
        )
        or 0
    )
    unconfirmed = int(
        await session.scalar(
            select(func.count())
            .select_from(AiCategorization)
            .where(*visible, AiCategorization.status == "applied")
        )
        or 0
    )
    # What the next digest would still list one by one: only what needs a
    # decision. What the model applied goes in a single line.
    on_its_way = int(
        await session.scalar(
            select(func.count())
            .select_from(AiCategorization)
            .where(
                *visible,
                AiCategorization.notified_at.is_(None),
                AiCategorization.status == "low_confidence",
            )
        )
        or 0
    )
    applied_total = int(
        await session.scalar(
            select(func.count())
            .select_from(AiCategorization)
            .where(*visible, AiCategorization.status.in_(("applied", "approved", "changed")))
        )
        or 0
    )

    bot = await integration_service.get_status(session, workspace_id, integration_service.TELEGRAM)
    chats = len(await telegram_service.list_links(session, workspace_id)) if bot.configured else 0
    return {
        "enabled": bool(settings and settings.ai_categorization_enabled),
        "paused": await ai_categorization_service.is_paused(workspace_id),
        "last_run_at": settings.last_run_at if settings else None,
        "last_run_applied": settings.last_run_applied if settings else None,
        "transactions": total,
        "decided": decided,
        "decided_percent": round(decided * 100 / total) if total else 100,
        "to_review": to_review,
        "unconfirmed": unconfirmed,
        "applied_total": applied_total,
        "awaiting_ai": await ai_categorization_service.count_pending(session, workspace_id, now),
        "telegram": {
            "connected": bool(bot.configured and chats),
            "digest_enabled": bool(settings and settings.digest_enabled and bot.configured and chats),
            "digest_time": settings.digest_time.strftime("%H:%M") if settings else None,
            # What the next digest would still send; confirming on screen shrinks it.
            "pending": on_its_way,
        },
    }


# --- acting --------------------------------------------------------------------
@dataclass
class BulkResult:
    done: int = 0
    skipped: int = 0


async def _row(
    session: AsyncSession, workspace_id: uuid.UUID, ai_id: uuid.UUID
) -> Optional[AiCategorization]:
    return (
        await session.execute(
            select(AiCategorization).where(
                AiCategorization.id == ai_id,
                AiCategorization.workspace_id == workspace_id,
                AiCategorization.is_follower.is_(False),
            )
        )
    ).scalar_one_or_none()


async def approve_many(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID, ids: list[uuid.UUID]
) -> BulkResult:
    result = BulkResult()
    for ai_id in ids:
        outcome = await digest_service.approve(session, workspace_id, ai_id, user_id, commit=False)
        # "already" counts as done: the item is settled, which is what was asked.
        if outcome.kind in ("done", "already"):
            result.done += 1
        else:
            result.skipped += 1
    await session.commit()
    return result


async def set_category_many(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    ids: list[uuid.UUID],
    category_id: uuid.UUID,
) -> BulkResult:
    result = BulkResult()
    for ai_id in ids:
        outcome = await digest_service.choose(
            session, workspace_id, ai_id, category_id, user_id, commit=False
        )
        if outcome.kind == "done":
            result.done += 1
        else:
            result.skipped += 1
    await session.commit()
    return result


async def undo_many(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID, ids: list[uuid.UUID]
) -> BulkResult:
    """Take the category back off and return the item to the review queue.

    The parcels of the same purchase that carry the same category go with it,
    mirroring how the category got there. The model's suggestion is kept, so
    the item still shows what it had proposed.
    """
    result = BulkResult()
    for ai_id in ids:
        row = await _row(session, workspace_id, ai_id)
        if row is None or row.status not in ("applied", "approved", "changed"):
            result.skipped += 1
            continue
        tx = (
            await session.execute(
                select(Transaction).where(
                    Transaction.id == row.transaction_id, Transaction.workspace_id == workspace_id
                )
            )
        ).scalar_one_or_none()
        if tx is None:
            result.skipped += 1
            continue

        previous = tx.category_id
        tx.category_id = None
        parcels = await installment_groups.siblings(session, tx)
        for parcel in parcels:
            if parcel.category_id == previous:
                parcel.category_id = None
        row.status = "low_confidence"
        row.reviewed_by_user_id = None
        row.reviewed_at = None
        if parcels:
            followers = (
                await session.execute(
                    select(AiCategorization).where(
                        AiCategorization.transaction_id.in_([p.id for p in parcels]),
                        AiCategorization.status.in_(("applied", "approved", "changed")),
                    )
                )
            ).scalars()
            for follower in followers:
                follower.status = "low_confidence"
                follower.reviewed_by_user_id = None
                follower.reviewed_at = None
        result.done += 1
    await session.commit()
    return result


@dataclass
class RuleResult:
    rule_id: uuid.UUID
    rule_name: str
    applied: int


class MerchantRuleError(ValueError):
    """The rule could not be made; the message says why."""


async def rule_from_item(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    ai_id: uuid.UUID,
    category_id: uuid.UUID,
) -> RuleResult:
    """Turn a decision into a permanent rule: from now on, this merchant gets
    this category at import, before the model is asked. Also fills in the
    uncategorized transactions that already match, and answers this item."""
    row = await _row(session, workspace_id, ai_id)
    if row is None:
        raise MerchantRuleError("Item not found")
    tx = (
        await session.execute(
            select(Transaction).where(
                Transaction.id == row.transaction_id, Transaction.workspace_id == workspace_id
            )
        )
    ).scalar_one_or_none()
    category = (
        await session.execute(
            select(Category).where(Category.id == category_id, Category.workspace_id == workspace_id)
        )
    ).scalar_one_or_none()
    if tx is None or category is None:
        raise MerchantRuleError("Item or category not found")

    text = tx.payee.strip() if tx.payee and tx.payee.strip() else merchant_of(tx)
    field = "payee" if tx.payee and tx.payee.strip() else "description"
    if len(merchant_key(text)) < 3:
        raise MerchantRuleError("This merchant name is too short to make a safe rule")

    taken = {
        name
        for (name,) in (
            await session.execute(select(Rule.name).where(Rule.workspace_id == workspace_id))
        ).all()
    }
    base = f"{text[:120]} → {category.name[:80]}"
    name, n = base, 2
    while name in taken:
        name, n = f"{base} ({n})", n + 1

    rule = await rule_service.create_rule(
        session,
        workspace_id,
        user_id,
        RuleCreate(
            name=name,
            conditions_op="and",
            conditions=[RuleCondition(field=field, op="contains", value=text)],
            actions=[RuleAction(op="set_category", value=str(category.id))],
        ),
    )

    # The rest of the ledger that already matches and is still empty.
    like = f"%{text.lower()}%"
    column = Transaction.payee if field == "payee" else Transaction.description
    matching = (
        await session.execute(
            select(Transaction).where(
                Transaction.workspace_id == workspace_id,
                Transaction.category_id.is_(None),
                Transaction.is_ignored.is_(False),
                Transaction.transfer_pair_id.is_(None),
                Transaction.id != tx.id,
                func.lower(column).like(like),
            )
        )
    ).scalars()
    applied = 0
    for other in matching:
        other.category_id = category.id
        applied += 1

    await digest_service.choose(session, workspace_id, ai_id, category.id, user_id, commit=False)
    await session.commit()
    return RuleResult(rule_id=rule.id, rule_name=rule.name, applied=applied)
