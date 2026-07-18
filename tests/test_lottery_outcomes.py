"""Per-round lottery outcome tracking: RoundOutcome suppresses reminders
that no longer apply and (on a loss) auto-arms the next round for the
same leg.
"""

from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import (
    Base,
    Concert,
    ConcertDay,
    ReminderQueue,
    ReminderRule,
    Round,
    RoundOutcome,
)
from app.db.service import ensure_user, sync_rule
from app.domain.types import Anchor, LotteryOutcome, RoundKind

NOW = datetime(2026, 6, 1, tzinfo=UTC)


def dt(month: int, day: int, hour: int = 12) -> datetime:
    return datetime(2026, month, day, hour, tzinfo=UTC)


@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def seed_two_legs(s) -> tuple[Concert, ConcertDay, ConcertDay, Round, Round, Round]:
    """Two legs, three rounds covering all three applies_to shapes: tied
    only to leg A, tied to both legs, and General (no day association)."""
    await ensure_user(s, 42, "reiji")
    concert = Concert(title="Two-Leg Tour", event_id="two-leg-tour", created_by=42)
    s.add(concert)
    await s.flush()
    leg_a = ConcertDay(concert_id=concert.id, label="Leg A", starts_at_utc=dt(8, 1, 9))
    leg_b = ConcertDay(concert_id=concert.id, label="Leg B", starts_at_utc=dt(8, 2, 9))
    s.add_all([leg_a, leg_b])
    await s.flush()
    round_a_only = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="A-only",
        closes_at_utc=dt(6, 25), applies_to=[leg_a.id],
    )
    round_both = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="Both-legs",
        closes_at_utc=dt(6, 26), applies_to=[leg_a.id, leg_b.id],
    )
    round_general = Round(
        concert_id=concert.id, kind=RoundKind.GENERAL_SALE, label="General",
        closes_at_utc=dt(6, 27),
    )
    s.add_all([round_a_only, round_both, round_general])
    await s.flush()
    return concert, leg_a, leg_b, round_a_only, round_both, round_general


async def queue_rows_for(s, rule_id) -> list[ReminderQueue]:
    return list((await s.execute(
        select(ReminderQueue).where(ReminderQueue.rule_id == rule_id)
    )).scalars())


# ── sync_rule suppression ─────────────────────────────────────────────────


async def test_lost_suppresses_this_rounds_payment_reminder(session):
    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    round_a_only.payment_deadline_at_utc = dt(6, 28)
    await session.flush()
    session.add(RoundOutcome(user_id=42, round_id=round_a_only.id, outcome=LotteryOutcome.LOST))
    await session.flush()

    rule = ReminderRule(user_id=42, round_id=round_a_only.id, anchor=Anchor.PAYMENT, offset_days=0)
    session.add(rule)
    await session.flush()
    await sync_rule(session, rule, NOW)

    assert await queue_rows_for(session, rule.id) == []


async def test_not_applied_suppresses_results_and_payment(session):
    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    round_a_only.results_at_utc = dt(6, 27)
    round_a_only.payment_deadline_at_utc = dt(6, 28)
    await session.flush()
    session.add(RoundOutcome(
        user_id=42, round_id=round_a_only.id, outcome=LotteryOutcome.NOT_APPLIED,
    ))
    await session.flush()

    results_rule = ReminderRule(
        user_id=42, round_id=round_a_only.id, anchor=Anchor.RESULTS, offset_days=0
    )
    payment_rule = ReminderRule(
        user_id=42, round_id=round_a_only.id, anchor=Anchor.PAYMENT, offset_days=0
    )
    session.add_all([results_rule, payment_rule])
    await session.flush()
    await sync_rule(session, results_rule, NOW)
    await sync_rule(session, payment_rule, NOW)

    assert await queue_rows_for(session, results_rule.id) == []
    assert await queue_rows_for(session, payment_rule.id) == []


async def test_won_does_not_suppress_its_own_payment_reminder(session):
    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    round_a_only.payment_deadline_at_utc = dt(6, 28)
    await session.flush()
    session.add(RoundOutcome(user_id=42, round_id=round_a_only.id, outcome=LotteryOutcome.WON))
    await session.flush()

    rule = ReminderRule(user_id=42, round_id=round_a_only.id, anchor=Anchor.PAYMENT, offset_days=0)
    session.add(rule)
    await session.flush()
    await sync_rule(session, rule, NOW)

    assert len(await queue_rows_for(session, rule.id)) == 1


async def test_paid_suppresses_payment_reminder(session):
    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    round_a_only.payment_deadline_at_utc = dt(6, 28)
    await session.flush()
    session.add(RoundOutcome(user_id=42, round_id=round_a_only.id, outcome=LotteryOutcome.PAID))
    await session.flush()

    rule = ReminderRule(user_id=42, round_id=round_a_only.id, anchor=Anchor.PAYMENT, offset_days=0)
    session.add(rule)
    await session.flush()
    await sync_rule(session, rule, NOW)

    assert await queue_rows_for(session, rule.id) == []


async def test_winning_one_leg_does_not_suppress_a_round_covering_both_legs(session):
    """Cross-round rule: a round with applies_to=[leg_a, leg_b] must stay
    active until BOTH legs are secured -- winning only leg_a leaves it
    fully planned."""
    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    session.add(RoundOutcome(user_id=42, round_id=round_a_only.id, outcome=LotteryOutcome.WON))
    await session.flush()

    rule = ReminderRule(user_id=42, round_id=round_both.id, anchor=Anchor.CLOSES, offset_days=-1)
    session.add(rule)
    await session.flush()
    await sync_rule(session, rule, NOW)

    assert len(await queue_rows_for(session, rule.id)) == 1


async def test_winning_both_legs_suppresses_the_shared_round(session):
    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    round_leg_b_only = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="B-only",
        closes_at_utc=dt(6, 24), applies_to=[leg_b.id],
    )
    session.add(round_leg_b_only)
    await session.flush()
    session.add_all([
        RoundOutcome(user_id=42, round_id=round_a_only.id, outcome=LotteryOutcome.WON),
        RoundOutcome(user_id=42, round_id=round_leg_b_only.id, outcome=LotteryOutcome.WON),
    ])
    await session.flush()

    rule = ReminderRule(user_id=42, round_id=round_both.id, anchor=Anchor.CLOSES, offset_days=-1)
    session.add(rule)
    await session.flush()
    await sync_rule(session, rule, NOW)

    assert await queue_rows_for(session, rule.id) == []


async def test_losing_one_leg_leaves_its_own_rounds_reminding_as_normal(session):
    """Winning leg_a while losing leg_b must NOT suppress leg_b's own
    general-sale round -- the user still needs that leg."""
    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    round_leg_b_only = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="B-only",
        closes_at_utc=dt(6, 24), applies_to=[leg_b.id],
    )
    session.add(round_leg_b_only)
    await session.flush()
    session.add_all([
        RoundOutcome(user_id=42, round_id=round_a_only.id, outcome=LotteryOutcome.WON),
        RoundOutcome(user_id=42, round_id=round_leg_b_only.id, outcome=LotteryOutcome.LOST),
    ])
    await session.flush()

    # round_general has no applies_to -- covers every leg, including the
    # still-unsecured leg_b -- must stay active.
    rule = ReminderRule(user_id=42, round_id=round_general.id, anchor=Anchor.CLOSES, offset_days=-1)
    session.add(rule)
    await session.flush()
    await sync_rule(session, rule, NOW)

    assert len(await queue_rows_for(session, rule.id)) == 1


# ── record_round_outcome ───────────────────────────────────────────────────


async def test_record_round_outcome_upserts_and_resyncs(session):
    from app.db.service import record_round_outcome

    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    round_a_only.payment_deadline_at_utc = dt(6, 28)
    await session.flush()
    rule = ReminderRule(user_id=42, round_id=round_a_only.id, anchor=Anchor.PAYMENT, offset_days=0)
    session.add(rule)
    await session.flush()
    await sync_rule(session, rule, NOW)
    assert len(await queue_rows_for(session, rule.id)) == 1

    await record_round_outcome(session, 42, round_a_only.id, LotteryOutcome.LOST, NOW)

    assert await queue_rows_for(session, rule.id) == []
    (row,) = (await session.execute(
        select(RoundOutcome).where(RoundOutcome.round_id == round_a_only.id)
    )).scalars()
    assert row.outcome == LotteryOutcome.LOST


async def test_record_round_outcome_permissively_allows_lost_without_prior_applied(session):
    from app.db.service import record_round_outcome

    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    await record_round_outcome(session, 42, round_a_only.id, LotteryOutcome.LOST, NOW)
    (row,) = (await session.execute(
        select(RoundOutcome).where(RoundOutcome.round_id == round_a_only.id)
    )).scalars()
    assert row.outcome == LotteryOutcome.LOST


async def test_record_round_outcome_rejects_paid_without_prior_won(session):
    from app.db.service import record_round_outcome

    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    await record_round_outcome(session, 42, round_a_only.id, LotteryOutcome.PAID, NOW)
    assert (await session.execute(
        select(RoundOutcome).where(RoundOutcome.round_id == round_a_only.id)
    )).scalar_one_or_none() is None


async def test_record_round_outcome_allows_paid_after_won(session):
    from app.db.service import record_round_outcome

    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    await record_round_outcome(session, 42, round_a_only.id, LotteryOutcome.WON, NOW)
    await record_round_outcome(session, 42, round_a_only.id, LotteryOutcome.PAID, NOW)
    (row,) = (await session.execute(
        select(RoundOutcome).where(RoundOutcome.round_id == round_a_only.id)
    )).scalars()
    assert row.outcome == LotteryOutcome.PAID


async def test_record_round_outcome_ignores_repeated_applied(session):
    """A starting state (NOT_APPLIED/APPLIED) only ever applies once --
    once WON is recorded, a stray repeated "I applied" click must not
    revert it."""
    from app.db.service import record_round_outcome

    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    await record_round_outcome(session, 42, round_a_only.id, LotteryOutcome.WON, NOW)
    await record_round_outcome(session, 42, round_a_only.id, LotteryOutcome.APPLIED, NOW)
    (row,) = (await session.execute(
        select(RoundOutcome).where(RoundOutcome.round_id == round_a_only.id)
    )).scalars()
    assert row.outcome == LotteryOutcome.WON
