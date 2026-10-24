"""Who chose a transaction's category.

Every place that writes a category goes through `set_category`, so the origin is
recorded in the same UPDATE as the category and the two cannot disagree. A
change that bypasses it is caught by a safety net on the model, which clears
the origin instead of leaving a stale one behind: the record is either right or
absent, never wrong.
"""
import uuid
from typing import Any, Optional

# 'rule'      a rule's "set category" action (category_rule_id says which)
# 'ai'        the AI categorization, applied or confirmed as it was
# 'user'      picked by a person: in the app, on the review screen, in Telegram
# 'provider'  the category the bank connection delivered with the transaction
# 'recurring' copied from the recurring transaction it was generated from
SOURCES = ("rule", "ai", "user", "provider", "recurring")

_DECLARED = "_category_origin_declared"


def set_category(
    tx: Any,
    category_id: Optional[uuid.UUID],
    source: Optional[str],
    rule_id: Optional[uuid.UUID] = None,
) -> None:
    """Set a transaction's category together with what chose it."""
    if source is not None and source not in SOURCES:
        raise ValueError(f"unknown category source: {source!r}")
    if category_id is None:
        source = None
    tx.category_id = category_id
    tx.category_source = source
    tx.category_rule_id = rule_id if source == "rule" else None
    # Tells the model's safety net this change came through here even when the
    # origin keeps its old value (a person changing a category twice stays "user").
    object.__setattr__(tx, _DECLARED, True)


def was_declared(tx: Any) -> bool:
    return bool(getattr(tx, _DECLARED, False))


def reset_declared(tx: Any) -> None:
    object.__setattr__(tx, _DECLARED, False)
