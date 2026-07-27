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
    RoundOutcomeDay,
)
from app.db.service import (
    covered_round_ids,
    ensure_user,
    my_deadline_rows,
    record_remaining_days_lost,
    record_round_day_result,
    record_round_outcome,
    secured_day_ids_by_round,
    set_leg_opt_out,
    sync_concert,
    sync_rule,
    unresolved_day_ids,
)
from app.domain.types import Anchor, LegResult, LotteryOutcome, RoundKind

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


async def test_record_round_outcome_allows_not_applied_when_fresh(session):
    from app.db.service import record_round_outcome

    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    await record_round_outcome(session, 42, round_a_only.id, LotteryOutcome.NOT_APPLIED, NOW)
    (row,) = (await session.execute(
        select(RoundOutcome).where(RoundOutcome.round_id == round_a_only.id)
    )).scalars()
    assert row.outcome == LotteryOutcome.NOT_APPLIED


async def test_record_round_outcome_ignores_repeated_not_applied(session):
    """Like APPLIED, NOT_APPLIED only ever applies as a first outcome --
    once WON is recorded, a stray repeated "didn't apply" click must not
    revert it."""
    from app.db.service import record_round_outcome

    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    await record_round_outcome(session, 42, round_a_only.id, LotteryOutcome.WON, NOW)
    await record_round_outcome(session, 42, round_a_only.id, LotteryOutcome.NOT_APPLIED, NOW)
    (row,) = (await session.execute(
        select(RoundOutcome).where(RoundOutcome.round_id == round_a_only.id)
    )).scalars()
    assert row.outcome == LotteryOutcome.WON


async def test_record_round_outcome_allows_applied_when_fresh(session):
    from app.db.service import record_round_outcome

    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    await record_round_outcome(session, 42, round_a_only.id, LotteryOutcome.APPLIED, NOW)
    (row,) = (await session.execute(
        select(RoundOutcome).where(RoundOutcome.round_id == round_a_only.id)
    )).scalars()
    assert row.outcome == LotteryOutcome.APPLIED


async def test_record_round_outcome_overwrites_lost_with_won(session):
    """WON/LOST can be re-set regardless of the current state -- confirms
    the row's outcome actually flips between two non-PAID terminal
    states, not just from-nothing or into-PAID."""
    from app.db.service import record_round_outcome

    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    await record_round_outcome(session, 42, round_a_only.id, LotteryOutcome.LOST, NOW)
    await record_round_outcome(session, 42, round_a_only.id, LotteryOutcome.WON, NOW)
    (row,) = (await session.execute(
        select(RoundOutcome).where(RoundOutcome.round_id == round_a_only.id)
    )).scalars()
    assert row.outcome == LotteryOutcome.WON


async def test_record_round_outcome_rejects_paid_when_existing_state_is_not_won(session):
    """PAID is only reachable from WON -- confirm this also blocks PAID
    when a DIFFERENT terminal state (not just "no row") already exists."""
    from app.db.service import record_round_outcome

    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    await record_round_outcome(session, 42, round_a_only.id, LotteryOutcome.LOST, NOW)
    await record_round_outcome(session, 42, round_a_only.id, LotteryOutcome.PAID, NOW)
    (row,) = (await session.execute(
        select(RoundOutcome).where(RoundOutcome.round_id == round_a_only.id)
    )).scalars()
    assert row.outcome == LotteryOutcome.LOST  # unchanged -- PAID rejected


# ── Auto-arm the next round ────────────────────────────────────────────────


async def test_losing_arms_the_next_round_for_the_same_leg(session):
    from app.db.service import record_round_outcome

    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    round_a_only.opens_at_utc = dt(6, 10)
    next_round = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="A-only round 2",
        opens_at_utc=dt(7, 1), closes_at_utc=dt(7, 15), applies_to=[leg_a.id],
    )
    session.add(next_round)
    await session.flush()

    await record_round_outcome(session, 42, round_a_only.id, LotteryOutcome.LOST, NOW)

    (rule,) = (await session.execute(
        select(ReminderRule).where(
            ReminderRule.round_id == next_round.id, ReminderRule.anchor == Anchor.OPENS,
        )
    )).scalars()
    assert rule.offset_days == 0 and rule.offset_hours == 0
    assert len(await queue_rows_for(session, rule.id)) == 1


async def test_auto_arm_uses_default_preset_opens_offset(session):
    from app.db.models import PresetItem, ReminderPreset
    from app.db.service import record_round_outcome

    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    round_a_only.opens_at_utc = dt(6, 10)
    next_round = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="A-only round 2",
        opens_at_utc=dt(7, 1), closes_at_utc=dt(7, 15), applies_to=[leg_a.id],
    )
    session.add(next_round)
    preset = ReminderPreset(user_id=42, name="standard", is_default=True)
    session.add(preset)
    await session.flush()
    session.add(
        PresetItem(preset_id=preset.id, anchor=Anchor.OPENS, offset_days=-2, offset_hours=0)
    )
    await session.flush()

    await record_round_outcome(session, 42, round_a_only.id, LotteryOutcome.LOST, NOW)

    (rule,) = (await session.execute(
        select(ReminderRule).where(
            ReminderRule.round_id == next_round.id, ReminderRule.anchor == Anchor.OPENS,
        )
    )).scalars()
    assert rule.offset_days == -2


async def test_auto_arm_does_not_duplicate_existing_rule(session):
    from app.db.service import record_round_outcome

    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    round_a_only.opens_at_utc = dt(6, 10)
    next_round = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="A-only round 2",
        opens_at_utc=dt(7, 1), closes_at_utc=dt(7, 15), applies_to=[leg_a.id],
    )
    session.add(next_round)
    await session.flush()
    existing_rule = ReminderRule(
        user_id=42, round_id=next_round.id, anchor=Anchor.OPENS, offset_days=-5,
    )
    session.add(existing_rule)
    await session.flush()

    await record_round_outcome(session, 42, round_a_only.id, LotteryOutcome.LOST, NOW)

    rules = list((await session.execute(
        select(ReminderRule).where(
            ReminderRule.round_id == next_round.id, ReminderRule.anchor == Anchor.OPENS,
        )
    )).scalars())
    assert len(rules) == 1
    assert rules[0].offset_days == -5  # untouched, not duplicated/overwritten


async def test_auto_arm_catches_up_when_next_round_added_later(session):
    from app.db.service import record_round_outcome

    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    round_a_only.opens_at_utc = dt(6, 10)
    await session.flush()

    await record_round_outcome(session, 42, round_a_only.id, LotteryOutcome.LOST, NOW)
    # nothing to arm yet -- no next round exists

    next_round = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="A-only round 2",
        opens_at_utc=dt(7, 1), closes_at_utc=dt(7, 15), applies_to=[leg_a.id],
    )
    session.add(next_round)
    await session.flush()

    await sync_concert(session, concert.id, NOW)

    (rule,) = (await session.execute(
        select(ReminderRule).where(
            ReminderRule.round_id == next_round.id, ReminderRule.anchor == Anchor.OPENS,
        )
    )).scalars()
    assert len(await queue_rows_for(session, rule.id)) == 1


# ── Shared secured-days / covered-rounds derivation ────────────────────────


async def seed_ab(s) -> tuple[Concert, ConcertDay, ConcertDay, Round, Round]:
    """Minimal world for the derivation helpers: two legs, round A covering
    both legs, round B covering leg B only -- and nothing else, so
    `covered_round_ids` returns exactly what these tests reason about
    (seed_two_legs' extra rounds would each answer the question too)."""
    await ensure_user(s, 42, "reiji")
    concert = Concert(title="AB Tour", event_id="ab-tour", created_by=42)
    s.add(concert)
    await s.flush()
    leg_a = ConcertDay(concert_id=concert.id, label="Leg A", starts_at_utc=dt(8, 1, 9))
    leg_b = ConcertDay(concert_id=concert.id, label="Leg B", starts_at_utc=dt(8, 2, 9))
    s.add_all([leg_a, leg_b])
    await s.flush()
    round_a = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="Both legs",
        closes_at_utc=dt(6, 25), applies_to=[leg_a.id, leg_b.id],
    )
    round_b = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="B only",
        closes_at_utc=dt(6, 26), applies_to=[leg_b.id],
    )
    s.add_all([round_a, round_b])
    await s.flush()
    return concert, leg_a, leg_b, round_a, round_b


async def test_secured_days_no_rows_means_all(session):
    concert, leg_a, leg_b, round_a, round_b = await seed_ab(session)
    await record_round_outcome(session, 42, round_a.id, LotteryOutcome.WON, NOW)

    secured = await secured_day_ids_by_round(session, 42, concert.id)

    assert secured[round_a.id] == {leg_a.id, leg_b.id}
    assert round_b.id not in secured  # no outcome recorded -- secures nothing


async def test_secured_days_partial_win_is_exact(session):
    concert, leg_a, leg_b, round_a, round_b = await seed_ab(session)
    await record_round_outcome(session, 42, round_a.id, LotteryOutcome.WON, NOW)
    session.add(RoundOutcomeDay(
        user_id=42, round_id=round_a.id, day_id=leg_a.id, result=LegResult.WON,
    ))
    await session.flush()

    secured = await secured_day_ids_by_round(session, 42, concert.id)

    assert secured[round_a.id] == {leg_a.id}


async def test_secured_days_all_legs_lost_secures_nothing(session):
    """Day rows exist, none of them WON -- the round secured no day at all.
    The no-rows-means-all fallback must not fire once rows exist."""
    concert, leg_a, leg_b, round_a, round_b = await seed_ab(session)
    await record_round_outcome(session, 42, round_a.id, LotteryOutcome.WON, NOW)
    session.add_all([
        RoundOutcomeDay(user_id=42, round_id=round_a.id, day_id=leg_a.id, result=LegResult.LOST),
        RoundOutcomeDay(user_id=42, round_id=round_a.id, day_id=leg_b.id, result=LegResult.LOST),
    ])
    await session.flush()

    secured = await secured_day_ids_by_round(session, 42, concert.id)

    assert secured[round_a.id] == set()


async def test_secured_days_ignores_another_users_rows(session):
    concert, leg_a, leg_b, round_a, round_b = await seed_ab(session)
    await ensure_user(session, 99, "someone-else")
    await record_round_outcome(session, 42, round_a.id, LotteryOutcome.WON, NOW)
    session.add(RoundOutcomeDay(
        user_id=99, round_id=round_a.id, day_id=leg_a.id, result=LegResult.WON,
    ))
    await session.flush()

    secured = await secured_day_ids_by_round(session, 42, concert.id)

    assert secured[round_a.id] == {leg_a.id, leg_b.id}  # user 99's partial row is not ours


async def test_covered_round_ids_partial_win_does_not_cover_other_day(session):
    """Round A covers both legs but only won leg A; round B covers leg B,
    which was never actually secured -- B must stay uncovered."""
    concert, leg_a, leg_b, round_a, round_b = await seed_ab(session)
    await record_round_outcome(session, 42, round_a.id, LotteryOutcome.WON, NOW)
    session.add(RoundOutcomeDay(
        user_id=42, round_id=round_a.id, day_id=leg_a.id, result=LegResult.WON,
    ))
    await session.flush()

    assert await covered_round_ids(session, 42, concert.id) == set()


async def test_covered_round_ids_full_win_covers_single_day_round(session):
    concert, leg_a, leg_b, round_a, round_b = await seed_ab(session)
    await record_round_outcome(session, 42, round_a.id, LotteryOutcome.WON, NOW)

    covered = await covered_round_ids(session, 42, concert.id)

    assert round_b.id in covered
    assert round_a.id not in covered  # a round never covers itself


async def test_a_won_round_is_never_covered_by_another_win(session):
    """Two rounds over the same legs, both won: each one's legs are secured
    "elsewhere" by the other, so a naive fold covers BOTH and the user is left
    owing payment on two tickets with nowhere to say so. "Covered" answers the
    apply/results question; on a round you won, the open question is
    payment."""
    concert, leg_a, leg_b, round_a, round_b = await seed_ab(session)
    await record_round_outcome(session, 42, round_a.id, LotteryOutcome.WON, NOW)
    await record_round_outcome(session, 42, round_b.id, LotteryOutcome.WON, NOW)

    covered = await covered_round_ids(session, 42, concert.id)

    assert round_a.id not in covered
    assert round_b.id not in covered


async def test_payment_reminder_survives_when_another_round_secured_the_same_legs(session):
    """The planner half of the same rule: a WON round's payment deadline is
    money owed, and no amount of securing the same seat elsewhere pays it."""
    concert, leg_a, leg_b, round_a, round_b = await seed_ab(session)
    round_b.payment_deadline_at_utc = dt(6, 30)
    await session.flush()
    await record_round_outcome(session, 42, round_a.id, LotteryOutcome.WON, NOW)
    await record_round_outcome(session, 42, round_b.id, LotteryOutcome.WON, NOW)

    rule = ReminderRule(user_id=42, round_id=round_b.id, anchor=Anchor.PAYMENT, offset_days=0)
    session.add(rule)
    await session.flush()
    await sync_rule(session, rule, NOW)

    assert len(await queue_rows_for(session, rule.id)) == 1


async def test_covered_round_ids_excludes_upgrade_rounds(session):
    """An upgrade round is entered BECAUSE you hold a ticket -- holding one
    must never mark it "stop asking" (invariant 2's upgrade exemption)."""
    concert, leg_a, leg_b, round_a, round_b = await seed_ab(session)
    upgrade = Round(
        concert_id=concert.id, kind=RoundKind.UPGRADE, label="Upgrade",
        closes_at_utc=dt(6, 28), applies_to=[leg_a.id, leg_b.id],
    )
    session.add(upgrade)
    await session.flush()
    await record_round_outcome(session, 42, round_a.id, LotteryOutcome.WON, NOW)

    assert upgrade.id not in await covered_round_ids(session, 42, concert.id)


# ── Per-day results: record_round_day_result / record_remaining_days_lost ──


async def outcome_of(s, round_id) -> LotteryOutcome | None:
    row = (await s.execute(
        select(RoundOutcome).where(RoundOutcome.round_id == round_id)
    )).scalar_one_or_none()
    return row.outcome if row is not None else None


async def day_results(s, round_id) -> dict[int, LegResult]:
    return {
        row.day_id: row.result for row in (await s.execute(
            select(RoundOutcomeDay).where(RoundOutcomeDay.round_id == round_id)
        )).scalars()
    }


async def opens_rules_for(s, round_id) -> list[ReminderRule]:
    return list((await s.execute(
        select(ReminderRule).where(
            ReminderRule.round_id == round_id, ReminderRule.anchor == Anchor.OPENS,
        )
    )).scalars())


async def test_won_day_flips_round_to_won(session):
    concert, leg_a, leg_b, round_a, round_b = await seed_ab(session)
    await record_round_outcome(session, 42, round_a.id, LotteryOutcome.APPLIED, NOW)

    await record_round_day_result(session, 42, round_a.id, leg_a.id, LegResult.WON, NOW)

    assert await outcome_of(session, round_a.id) is LotteryOutcome.WON
    assert await day_results(session, round_a.id) == {leg_a.id: LegResult.WON}
    # And the shared derivation now claims exactly the won leg, not both.
    secured = await secured_day_ids_by_round(session, 42, concert.id)
    assert secured[round_a.id] == {leg_a.id}


async def test_won_day_upserts_in_place(session):
    """A second click on the same leg updates the row rather than adding a
    duplicate -- the unique index would otherwise be an IntegrityError."""
    concert, leg_a, leg_b, round_a, round_b = await seed_ab(session)
    await record_round_day_result(session, 42, round_a.id, leg_a.id, LegResult.LOST, NOW)
    await record_round_day_result(session, 42, round_a.id, leg_a.id, LegResult.WON, NOW)

    assert await day_results(session, round_a.id) == {leg_a.id: LegResult.WON}


async def test_lost_day_partial_keeps_round_won_and_arms_next_round_for_that_day(session):
    """Won leg A, lost leg B: the round's own outcome stays WON (a win is a
    win), and the loss arms the next round covering leg B specifically."""
    concert, leg_a, leg_b, round_a, round_b = await seed_ab(session)
    round_b.opens_at_utc = dt(7, 1)
    round_b.closes_at_utc = dt(7, 15)
    await session.flush()

    await record_round_day_result(session, 42, round_a.id, leg_a.id, LegResult.WON, NOW)
    await record_round_day_result(session, 42, round_a.id, leg_b.id, LegResult.LOST, NOW)

    assert await outcome_of(session, round_a.id) is LotteryOutcome.WON
    assert await day_results(session, round_a.id) == {
        leg_a.id: LegResult.WON, leg_b.id: LegResult.LOST,
    }
    (rule,) = await opens_rules_for(session, round_b.id)
    assert len(await queue_rows_for(session, rule.id)) == 1


async def test_lost_day_partial_does_not_arm_a_round_for_another_leg(session):
    """The per-day narrowing is the point: losing leg B must not arm a later
    round that only covers leg A."""
    concert, leg_a, leg_b, round_a, round_b = await seed_ab(session)
    a_later = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="A only, later",
        opens_at_utc=dt(7, 1), closes_at_utc=dt(7, 15), applies_to=[leg_a.id],
    )
    session.add(a_later)
    await session.flush()

    await record_round_day_result(session, 42, round_a.id, leg_a.id, LegResult.WON, NOW)
    await record_round_day_result(session, 42, round_a.id, leg_b.id, LegResult.LOST, NOW)

    assert await opens_rules_for(session, a_later.id) == []


async def test_all_days_lost_flips_round_to_lost(session):
    concert, leg_a, leg_b, round_a, round_b = await seed_ab(session)
    await record_round_outcome(session, 42, round_a.id, LotteryOutcome.APPLIED, NOW)

    await record_round_day_result(session, 42, round_a.id, leg_a.id, LegResult.LOST, NOW)
    # One leg still unresolved -- the round is not settled yet.
    assert await outcome_of(session, round_a.id) is LotteryOutcome.APPLIED

    await record_round_day_result(session, 42, round_a.id, leg_b.id, LegResult.LOST, NOW)

    assert await outcome_of(session, round_a.id) is LotteryOutcome.LOST


async def test_forged_day_id_is_noop(session):
    """A day the round does not cover (or a round that does not exist) writes
    nothing at all -- same recompute-server-side rule as /setup."""
    concert, leg_a, leg_b, round_a, round_b = await seed_ab(session)

    # round_b covers leg B only; leg A is not one of its legs.
    await record_round_day_result(session, 42, round_b.id, leg_a.id, LegResult.WON, NOW)
    await record_round_day_result(session, 42, round_b.id, 999_999, LegResult.WON, NOW)
    await record_round_day_result(session, 42, 999_999, leg_b.id, LegResult.WON, NOW)

    assert await day_results(session, round_b.id) == {}
    assert await outcome_of(session, round_b.id) is None


async def test_not_going_day_counts_as_resolved_for_lost_terminal(session):
    """A leg you opted out of is not a leg anyone is waiting on: with leg B
    opted out, losing leg A settles the whole round as LOST."""
    concert, leg_a, leg_b, round_a, round_b = await seed_ab(session)
    await set_leg_opt_out(session, 42, leg_b.id, True)

    await record_round_day_result(session, 42, round_a.id, leg_a.id, LegResult.LOST, NOW)

    assert await outcome_of(session, round_a.id) is LotteryOutcome.LOST
    assert await day_results(session, round_a.id) == {leg_a.id: LegResult.LOST}


async def test_remaining_days_lost_writes_rows_and_settles(session):
    concert, leg_a, leg_b, round_a, round_b = await seed_ab(session)
    await record_round_outcome(session, 42, round_a.id, LotteryOutcome.APPLIED, NOW)

    await record_remaining_days_lost(session, 42, round_a.id, NOW)

    assert await day_results(session, round_a.id) == {
        leg_a.id: LegResult.LOST, leg_b.id: LegResult.LOST,
    }
    assert await outcome_of(session, round_a.id) is LotteryOutcome.LOST


async def test_remaining_days_lost_keeps_a_partial_win_won(session):
    """"Lost the rest" after a win leaves the round WON and secures exactly
    the won leg."""
    concert, leg_a, leg_b, round_a, round_b = await seed_ab(session)
    round_b.opens_at_utc = dt(7, 1)
    round_b.closes_at_utc = dt(7, 15)
    await session.flush()
    await record_round_day_result(session, 42, round_a.id, leg_a.id, LegResult.WON, NOW)

    await record_remaining_days_lost(session, 42, round_a.id, NOW)

    assert await outcome_of(session, round_a.id) is LotteryOutcome.WON
    assert await day_results(session, round_a.id) == {
        leg_a.id: LegResult.WON, leg_b.id: LegResult.LOST,
    }
    secured = await secured_day_ids_by_round(session, 42, concert.id)
    assert secured[round_a.id] == {leg_a.id}
    # ...and the newly-lost leg still gets its next round armed.
    assert len(await opens_rules_for(session, round_b.id)) == 1


async def test_lost_day_on_a_won_all_round_keeps_other_days_secured(session):
    """A round won with NO day rows means "won every leg" (Task 2's
    no-rows-means-all). Recording a loss on one leg must materialize that
    implicit state first -- otherwise the round's secured set collapses to
    "exactly the WON rows" = nothing, silently losing the other ticket."""
    concert, leg_a, leg_b, round_a, round_b = await seed_ab(session)
    await record_round_outcome(session, 42, round_a.id, LotteryOutcome.WON, NOW)

    await record_round_day_result(session, 42, round_a.id, leg_b.id, LegResult.LOST, NOW)

    assert await outcome_of(session, round_a.id) is LotteryOutcome.WON
    assert await day_results(session, round_a.id) == {
        leg_a.id: LegResult.WON, leg_b.id: LegResult.LOST,
    }
    secured = await secured_day_ids_by_round(session, 42, concert.id)
    assert secured[round_a.id] == {leg_a.id}  # leg A's ticket survives


async def test_remaining_days_lost_on_a_won_all_round_is_a_noop(session):
    """Same materialization from the other entry point: nothing is
    unresolved on a won-all round, so "lost the rest" loses nothing."""
    concert, leg_a, leg_b, round_a, round_b = await seed_ab(session)
    await record_round_outcome(session, 42, round_a.id, LotteryOutcome.WON, NOW)

    await record_remaining_days_lost(session, 42, round_a.id, NOW)

    assert await outcome_of(session, round_a.id) is LotteryOutcome.WON
    assert await day_results(session, round_a.id) == {
        leg_a.id: LegResult.WON, leg_b.id: LegResult.WON,
    }
    secured = await secured_day_ids_by_round(session, 42, concert.id)
    assert secured[round_a.id] == {leg_a.id, leg_b.id}


async def test_won_day_does_not_revert_a_paid_round(session):
    """PAID is downstream of WON: a per-day win on an already-secured round
    must not route through record_round_outcome, which would overwrite PAID
    back to WON and re-arm the payment reminder."""
    concert, leg_a, leg_b, round_a, round_b = await seed_ab(session)
    await record_round_outcome(session, 42, round_a.id, LotteryOutcome.WON, NOW)
    await record_round_outcome(session, 42, round_a.id, LotteryOutcome.PAID, NOW)

    await record_round_day_result(session, 42, round_a.id, leg_a.id, LegResult.WON, NOW)

    assert await outcome_of(session, round_a.id) is LotteryOutcome.PAID
    assert await day_results(session, round_a.id) == {
        leg_a.id: LegResult.WON, leg_b.id: LegResult.WON,
    }


async def test_won_day_on_an_already_won_round_still_resyncs_rules(session):
    """The skip is only of the outcome WRITE -- the secured set still
    changed, so the planner must be re-run: winning leg B here is what makes
    round B (leg B only) redundant, and its queued reminder must go."""
    concert, leg_a, leg_b, round_a, round_b = await seed_ab(session)
    await record_round_day_result(session, 42, round_a.id, leg_a.id, LegResult.WON, NOW)
    rule = ReminderRule(user_id=42, round_id=round_b.id, anchor=Anchor.CLOSES, offset_days=-1)
    session.add(rule)
    await session.flush()
    await sync_rule(session, rule, NOW)
    assert len(await queue_rows_for(session, rule.id)) == 1  # leg B not secured yet

    await record_round_day_result(session, 42, round_a.id, leg_b.id, LegResult.WON, NOW)

    assert await outcome_of(session, round_a.id) is LotteryOutcome.WON
    assert await queue_rows_for(session, rule.id) == []


async def test_unresolved_day_ids_excludes_cancelled_resolved_and_opted_out(session):
    await ensure_user(session, 42, "reiji")
    concert = Concert(title="Four-Leg Tour", event_id="four-leg-tour", created_by=42)
    session.add(concert)
    await session.flush()
    cancelled = ConcertDay(
        concert_id=concert.id, label="Cancelled", starts_at_utc=dt(8, 1, 9), cancelled=True,
    )
    resolved = ConcertDay(concert_id=concert.id, label="Resolved", starts_at_utc=dt(8, 2, 9))
    opted_out = ConcertDay(concert_id=concert.id, label="Not going", starts_at_utc=dt(8, 3, 9))
    # Created last but starting FIRST of the two open legs, so the returned
    # order proves it sorts by starts_at_utc, not by id.
    open_early = ConcertDay(concert_id=concert.id, label="Early", starts_at_utc=dt(8, 4, 9))
    open_late = ConcertDay(concert_id=concert.id, label="Late", starts_at_utc=dt(8, 5, 9))
    session.add_all([cancelled, resolved, opted_out, open_late, open_early])
    await session.flush()
    round_all = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="All legs",
        closes_at_utc=dt(6, 25),
    )
    session.add(round_all)
    await session.flush()
    session.add(RoundOutcomeDay(
        user_id=42, round_id=round_all.id, day_id=resolved.id, result=LegResult.LOST,
    ))
    await set_leg_opt_out(session, 42, opted_out.id, True)
    await session.flush()

    assert await unresolved_day_ids(session, 42, round_all) == [open_early.id, open_late.id]


# ── read-side covered gates ──────────────────────────────────────────────


async def round_row_labels(s, concert: Concert) -> set[str]:
    """The round labels Home's "Coming up" would show for user 42 (the
    EVENT_START rows a leg produces carry no round and are not the subject
    here)."""
    rows = await my_deadline_rows(s, 42, now=NOW, limit=50, concert_ids={concert.id})
    return {r.deadline.label for r in rows if r.deadline.round_id is not None}


async def test_coming_up_drops_secured_elsewhere_round(session):
    """Home's "Coming up" and the DM planner must agree on what is still
    worth asking about: once the two-leg round is won, the leg-A round and
    the General round cover nothing the user is still waiting on, so their
    rows go -- exactly as _apply_outcome_suppression already drops them from
    the queue."""
    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    await session.flush()

    before = await round_row_labels(session, concert)
    assert before == {"A-only", "Both-legs", "General"}

    await record_round_outcome(session, 42, round_both.id, LotteryOutcome.WON, NOW)

    assert await round_row_labels(session, concert) == {"Both-legs"}


async def test_coming_up_keeps_a_round_a_partial_win_did_not_secure(session):
    """Won Saturday, still waiting on Sunday: the round that sells Sunday is
    not covered, and dropping it would hide a live chance."""
    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    b_only = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="B-only",
        closes_at_utc=dt(6, 28), applies_to=[leg_b.id],
    )
    session.add(b_only)
    await session.flush()
    await record_round_outcome(session, 42, round_both.id, LotteryOutcome.APPLIED, NOW)
    await record_round_day_result(session, 42, round_both.id, leg_a.id, LegResult.WON, NOW)
    await record_round_day_result(session, 42, round_both.id, leg_b.id, LegResult.LOST, NOW)

    labels = await round_row_labels(session, concert)
    assert "B-only" in labels      # leg B was never secured
    assert "A-only" not in labels  # leg A was


async def test_coming_up_keeps_both_payment_rows_when_two_wins_overlap(session):
    """The read side of the same rule: two won rounds over the same legs each
    still owe a payment, so both keep their row on Home."""
    concert, leg_a, leg_b, round_a, round_b = await seed_ab(session)
    round_a.payment_deadline_at_utc = dt(6, 30)
    round_b.payment_deadline_at_utc = dt(7, 1)
    await session.flush()
    await record_round_outcome(session, 42, round_a.id, LotteryOutcome.WON, NOW)
    await record_round_outcome(session, 42, round_b.id, LotteryOutcome.WON, NOW)

    assert await round_row_labels(session, concert) == {"Both legs", "B only"}
