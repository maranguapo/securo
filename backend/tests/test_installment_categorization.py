"""A purchase paid in parcels is one decision, not one per parcel."""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_categorization import AiCategorization
from app.models.transaction import Transaction
from app.services import ai_categorization_service as svc
from app.services import digest_service, installment_groups
from tests.test_ai_categorization import FakeProvider, _enable
from tests.test_digest import RecordingBot

PURCHASE_DATE = date(2026, 9, 12)


@pytest.fixture
def use_provider():
    def _use(provider):
        return patch.object(
            svc.llm_connections, "build_provider_for_connection", return_value=provider
        )

    return _use


@pytest.fixture(autouse=True)
def _no_delays():
    with patch.object(svc, "BATCH_DELAY_SECONDS", 0), patch.object(
        digest_service, "SEND_DELAY_SECONDS", 0
    ):
        yield


async def _parcels(
    session, user, account, *, count=4, description="AMAZON", total=Decimal("400.00"),
    numbers=None, category_id=None, source="sync", created_at=None,
):
    """`count` parcels of one purchase, one per month, as a bank would send them."""
    rows = []
    for number in numbers or range(1, count + 1):
        row = Transaction(
            id=uuid.uuid4(),
            user_id=user.id,
            account_id=account.id,
            description=f"{description} {number:02d}/{count:02d}",
            amount=total / count,
            date=PURCHASE_DATE + timedelta(days=30 * (number - 1)),
            type="debit",
            source=source,
            installment_number=number,
            total_installments=count,
            installment_total_amount=total,
            installment_purchase_date=PURCHASE_DATE,
            category_id=category_id,
            created_at=created_at or datetime.now(timezone.utc),
        )
        session.add(row)
        rows.append(row)
    await session.commit()
    return rows


async def _ledger(session):
    return (await session.execute(select(AiCategorization))).scalars().all()


async def _fresh(session, rows):
    for row in rows:
        await session.refresh(row)
    return rows


# ---------------------------------------------------------------------------
# What counts as the same purchase
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_parcels_of_one_purchase_share_a_key_and_others_do_not(
    session: AsyncSession, test_user, test_account, test_workspace
):
    amazon = await _parcels(session, test_user, test_account, description="AMAZON")
    other_total = await _parcels(session, test_user, test_account, description="X", total=Decimal("800.00"))
    key = installment_groups.group_key(amazon[0])

    assert key is not None
    assert {installment_groups.group_key(p) for p in amazon} == {key}
    assert installment_groups.group_key(other_total[0]) != key


@pytest.mark.asyncio
async def test_a_transaction_that_is_not_a_parcel_has_no_group(
    session: AsyncSession, test_user, test_account, test_workspace
):
    single = Transaction(
        id=uuid.uuid4(), user_id=test_user.id, account_id=test_account.id, description="CAFE",
        amount=Decimal("9.00"), date=PURCHASE_DATE, type="debit", source="sync",
    )
    one_of_one = (await _parcels(session, test_user, test_account, count=1))[0]

    assert installment_groups.group_key(single) is None
    assert installment_groups.group_key(one_of_one) is None


@pytest.mark.asyncio
async def test_siblings_are_the_other_parcels_only(
    session: AsyncSession, test_user, test_account, test_workspace
):
    amazon = await _parcels(session, test_user, test_account, description="AMAZON")
    await _parcels(session, test_user, test_account, description="OTHER", total=Decimal("900.00"))

    found = await installment_groups.siblings(session, amazon[1])

    assert [p.installment_number for p in found] == [1, 3, 4]
    assert await installment_groups.count_siblings(session, amazon[1]) == 3


@pytest.mark.asyncio
async def test_parcels_the_user_created_as_a_series_group_by_series_id(
    session: AsyncSession, test_user, test_account, test_workspace
):
    series = uuid.uuid4()
    rows = []
    for n in (1, 2, 3):
        rows.append(
            Transaction(
                id=uuid.uuid4(), user_id=test_user.id, account_id=test_account.id,
                description=f"NOTEBOOK {n}/3", amount=Decimal("100.00"),
                date=PURCHASE_DATE + timedelta(days=30 * n), type="debit", source="manual",
                installment_series_id=series,
            )
        )
    session.add_all(rows)
    await session.commit()

    assert installment_groups.group_key(rows[0]) == ("series", series)
    assert len(await installment_groups.siblings(session, rows[0])) == 2


# ---------------------------------------------------------------------------
# Categorizing
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_model_sees_one_line_for_the_whole_purchase_and_all_parcels_take_its_answer(
    session: AsyncSession, test_user, test_workspace, test_account, test_categories, use_provider
):
    await _enable(session, test_user, test_workspace)
    parcels = await _parcels(session, test_user, test_account)
    provider = FakeProvider({"AMAZON": ("Transporte", 0.9)})

    with use_provider(provider):
        applied = await svc.categorize_pending(session, test_workspace.id)

    prompt = provider.calls[0][-1].content
    assert prompt.count("AMAZON") == 1
    assert "one of 4 installments of one purchase" in prompt
    assert applied == 4
    assert {p.category_id for p in await _fresh(session, parcels)} == {test_categories[1].id}


@pytest.mark.asyncio
async def test_only_the_representative_is_visible_to_the_digest(
    session: AsyncSession, test_user, test_workspace, test_account, test_categories, use_provider
):
    await _enable(session, test_user, test_workspace)
    await _parcels(session, test_user, test_account)
    with use_provider(FakeProvider({"AMAZON": ("Transporte", 0.9)})):
        await svc.categorize_pending(session, test_workspace.id)

    rows = await _ledger(session)

    assert len(rows) == 4
    assert sum(1 for r in rows if r.notified_at is None) == 1  # the representative
    assert {r.status for r in rows} == {"applied"}


@pytest.mark.asyncio
async def test_the_earliest_parcel_is_the_one_that_represents_the_purchase(
    session: AsyncSession, test_user, test_workspace, test_account, test_categories, use_provider
):
    await _enable(session, test_user, test_workspace)
    parcels = await _parcels(session, test_user, test_account)
    with use_provider(FakeProvider({"AMAZON": ("Transporte", 0.9)})):
        await svc.categorize_pending(session, test_workspace.id)

    visible = [r for r in await _ledger(session) if r.notified_at is None]

    assert visible[0].transaction_id == parcels[0].id


@pytest.mark.asyncio
async def test_a_purchase_is_never_put_to_the_model_twice(
    session: AsyncSession, test_user, test_workspace, test_account, test_categories, use_provider
):
    await _enable(session, test_user, test_workspace)
    await _parcels(session, test_user, test_account)
    provider = FakeProvider({"AMAZON": ("Transporte", 0.9)})

    with use_provider(provider):
        await svc.categorize_pending(session, test_workspace.id)
        await svc.categorize_pending(session, test_workspace.id)

    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_a_parcel_the_user_already_categorized_settles_the_rest_without_asking(
    session: AsyncSession, test_user, test_workspace, test_account, test_categories, use_provider
):
    await _enable(session, test_user, test_workspace)
    parcels = await _parcels(session, test_user, test_account)
    parcels[0].category_id = test_categories[0].id  # the user did the first one
    await session.commit()
    provider = FakeProvider({"AMAZON": ("Transporte", 0.9)})

    with use_provider(provider):
        applied = await svc.categorize_pending(session, test_workspace.id)

    assert provider.calls == []
    assert applied == 3
    assert {p.category_id for p in await _fresh(session, parcels)} == {test_categories[0].id}
    assert await _ledger(session) == []  # nothing to review: the user already decided


@pytest.mark.asyncio
async def test_parcels_outside_the_lookback_get_the_answer_too(
    session: AsyncSession, test_user, test_workspace, test_account, test_categories, use_provider
):
    await _enable(session, test_user, test_workspace)
    old = datetime.now(timezone.utc) - timedelta(days=90)
    stale = await _parcels(session, test_user, test_account, numbers=[1, 2], count=4, created_at=old)
    fresh = await _parcels(session, test_user, test_account, numbers=[3, 4], count=4)
    with use_provider(FakeProvider({"AMAZON": ("Transporte", 0.9)})):
        await svc.categorize_pending(session, test_workspace.id)

    assert {p.category_id for p in await _fresh(session, stale + fresh)} == {test_categories[1].id}


@pytest.mark.asyncio
async def test_an_unsure_answer_leaves_every_parcel_waiting_and_unlisted_but_one(
    session: AsyncSession, test_user, test_workspace, test_account, test_categories, use_provider
):
    await _enable(session, test_user, test_workspace)
    parcels = await _parcels(session, test_user, test_account)
    with use_provider(FakeProvider({"AMAZON": ("Transporte", 0.2)})):
        applied = await svc.categorize_pending(session, test_workspace.id)

    rows = await _ledger(session)

    assert applied == 0
    assert {p.category_id for p in await _fresh(session, parcels)} == {None}
    assert {r.status for r in rows} == {"low_confidence"}
    assert sum(1 for r in rows if r.notified_at is None) == 1


@pytest.mark.asyncio
async def test_a_parcel_that_arrives_later_joins_a_purchase_awaiting_the_users_choice(
    session: AsyncSession, test_user, test_workspace, test_account, test_categories, use_provider
):
    await _enable(session, test_user, test_workspace)
    await _parcels(session, test_user, test_account, count=5, numbers=[1, 2, 3, 4])
    provider = FakeProvider({"AMAZON": ("Transporte", 0.2)})
    with use_provider(provider):
        await svc.categorize_pending(session, test_workspace.id)
        await _parcels(session, test_user, test_account, count=5, numbers=[5])
        await svc.categorize_pending(session, test_workspace.id)

    assert len(provider.calls) == 1  # the late parcel did not trigger a second question
    rows = await _ledger(session)
    assert len(rows) == 5 and sum(1 for r in rows if r.notified_at is None) == 1


@pytest.mark.asyncio
async def test_a_parcel_that_arrives_later_inherits_the_answer(
    session: AsyncSession, test_user, test_workspace, test_account, test_categories, use_provider
):
    await _enable(session, test_user, test_workspace)
    await _parcels(session, test_user, test_account, count=5, numbers=[1, 2, 3, 4])
    provider = FakeProvider({"AMAZON": ("Transporte", 0.9)})
    with use_provider(provider):
        await svc.categorize_pending(session, test_workspace.id)
        late = await _parcels(session, test_user, test_account, count=5, numbers=[5])
        await svc.categorize_pending(session, test_workspace.id)

    assert len(provider.calls) == 1
    assert (await _fresh(session, late))[0].category_id == test_categories[1].id


@pytest.mark.asyncio
async def test_other_purchases_and_plain_transactions_are_still_asked_about_separately(
    session: AsyncSession, test_user, test_workspace, test_account, test_categories,
    test_transactions, use_provider,
):
    await _enable(session, test_user, test_workspace)
    await _parcels(session, test_user, test_account, description="AMAZON")
    await _parcels(session, test_user, test_account, description="LOJAX", total=Decimal("600.00"), count=3)
    provider = FakeProvider(
        {"AMAZON": ("Transporte", 0.9), "LOJAX": ("Alimentação", 0.9), "NETFLIX": ("Receita", 0.9)}
    )

    with use_provider(provider):
        await svc.categorize_pending(session, test_workspace.id)

    prompt = provider.calls[0][-1].content
    assert prompt.count("AMAZON") == 1 and prompt.count("LOJAX") == 1
    assert "NETFLIX" in prompt and "PIX RECEBIDO" in prompt


# ---------------------------------------------------------------------------
# Reviewing
# ---------------------------------------------------------------------------
async def _digest_setup(session, user, workspace, account, categories, use_provider):
    from app.services import telegram_service
    from tests.test_digest import _enable_digest

    await _enable_digest(session, user, workspace)
    parcels = await _parcels(session, user, account)
    with use_provider(FakeProvider({"AMAZON": ("Transporte", 0.9)})):
        await svc.categorize_pending(session, workspace.id)
    await telegram_service.link_chat(session, workspace.id, user.id, 555, None)
    return parcels


@pytest.mark.asyncio
async def test_the_digest_asks_about_the_purchase_once_and_says_how_many_parcels_it_covers(
    session: AsyncSession, test_user, test_workspace, test_account, test_categories, use_provider
):
    await _digest_setup(session, test_user, test_workspace, test_account, test_categories, use_provider)
    bot = RecordingBot()

    sent = await digest_service.send_digest(session, test_workspace.id, client=bot)

    assert sent == 1
    texts = [text for _, text, _ in bot.sent]
    assert sum("AMAZON" in t for t in texts) == 1
    assert any("mais 3 parcela" in t for t in texts)  # the test user reads Portuguese


@pytest.mark.asyncio
async def test_choosing_a_category_for_the_representative_changes_the_whole_purchase(
    session: AsyncSession, test_user, test_workspace, test_account, test_categories, use_provider
):
    parcels = await _digest_setup(
        session, test_user, test_workspace, test_account, test_categories, use_provider
    )
    rep = [r for r in await _ledger(session) if r.notified_at is None][0]

    outcome = await digest_service.choose(
        session, test_workspace.id, rep.id, test_categories[0].id, test_user.id
    )

    assert outcome.state == "changed"
    assert {p.category_id for p in await _fresh(session, parcels)} == {test_categories[0].id}
    for row in await _ledger(session):
        await session.refresh(row)
        assert row.status == "changed" and row.reviewed_by_user_id == test_user.id


@pytest.mark.asyncio
async def test_a_parcel_the_user_set_differently_is_left_alone_when_the_purchase_changes(
    session: AsyncSession, test_user, test_workspace, test_account, test_categories, use_provider
):
    parcels = await _digest_setup(
        session, test_user, test_workspace, test_account, test_categories, use_provider
    )
    parcels[2].category_id = test_categories[2].id  # the user set this one on purpose
    await session.commit()
    rep = [r for r in await _ledger(session) if r.notified_at is None][0]

    await digest_service.choose(session, test_workspace.id, rep.id, test_categories[0].id, test_user.id)

    await _fresh(session, parcels)
    assert [p.category_id for p in parcels] == [
        test_categories[0].id, test_categories[0].id, test_categories[2].id, test_categories[0].id,
    ]


@pytest.mark.asyncio
async def test_confirming_the_representative_leaves_the_parcels_as_they_are(
    session: AsyncSession, test_user, test_workspace, test_account, test_categories, use_provider
):
    parcels = await _digest_setup(
        session, test_user, test_workspace, test_account, test_categories, use_provider
    )
    rep = [r for r in await _ledger(session) if r.notified_at is None][0]

    outcome = await digest_service.approve(session, test_workspace.id, rep.id, test_user.id)

    assert outcome.kind == "done"
    assert {p.category_id for p in await _fresh(session, parcels)} == {test_categories[1].id}
