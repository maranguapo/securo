"""remember what chose a transaction's category

- transactions.category_source: 'rule', 'ai', 'user', 'provider' or
  'recurring'. NULL when there is no category, or when it was set before this
  was recorded and cannot be known.
- transactions.category_rule_id: the rule behind a 'rule' category. Set NULL if
  the rule is deleted, so the transaction keeps saying a rule did it.

Plain nullable columns: on PostgreSQL adding them is a catalog change, not a
table rewrite. The partial index keeps deleting a rule from scanning every
transaction to null the reference.

Existing AI categorizations are filled in from the ledger: a category the model
chose and nobody changed is 'ai'; one the user picked over the model's is
'user'. Rules and manual edits made before this carry no record and stay NULL.

Revision ID: 102
Revises: 101
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "102"
down_revision = "101"
branch_labels = None
depends_on = None

SOURCES = ("rule", "ai", "user", "provider", "recurring")

BACKFILL_SQL = """
        UPDATE transactions t
        SET category_source = CASE
            WHEN a.status = 'changed' THEN 'user'
            ELSE 'ai'
        END
        FROM ai_categorizations a
        WHERE a.transaction_id = t.id
          AND t.category_id IS NOT NULL
          AND t.category_source IS NULL
          AND (
              (a.status IN ('applied', 'approved') AND t.category_id = a.suggested_category_id)
              OR a.status = 'changed'
          )
        """


def upgrade() -> None:
    op.add_column("transactions", sa.Column("category_source", sa.String(16), nullable=True))
    op.add_column(
        "transactions",
        sa.Column(
            "category_rule_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rules.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_transactions_category_source",
        "transactions",
        "category_source IS NULL OR category_source IN ("
        + ", ".join(f"'{s}'" for s in SOURCES)
        + ")",
    )
    op.create_index(
        "ix_transactions_category_rule_id",
        "transactions",
        ["category_rule_id"],
        postgresql_where=sa.text("category_rule_id IS NOT NULL"),
    )

    # The model's own choices, from the ledger.
    op.execute(BACKFILL_SQL)


def downgrade() -> None:
    op.drop_index("ix_transactions_category_rule_id", table_name="transactions")
    op.drop_constraint("ck_transactions_category_source", "transactions", type_="check")
    op.drop_column("transactions", "category_rule_id")
    op.drop_column("transactions", "category_source")
