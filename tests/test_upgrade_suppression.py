"""Upgrade-round eligibility as a read-side suppression pass.

An UPGRADE round is a nested second campaign only holders of a qualifying
round's ticket may enter. The trap this file guards against: the existing
cross-round "secured elsewhere" pass drops a round when every leg it covers
is already secured (WON/PAID) elsewhere on the concert -- but an upgrade
round covers the SAME legs as its base rounds, so that pass would silence
the upgrade for exactly the users who secured a ticket and are therefore
eligible for it. The feature would be invisible to its own audience.

So _apply_outcome_suppression exempts upgrade rounds from the cross-round
pass (a held ticket is the PREREQUISITE for entering, not a reason to hide
the round) and instead drops an upgrade round for a user who is NOT
is_upgrade_eligible. The same-round anchor pass (RESULTS/PAYMENT vs the
round's own outcome) still applies to upgrade rounds unchanged.
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
    RoundQualifier,
)
from app.db.service import ensure_user, record_round_outcome, set_leg_opt_out, sync_rule
from app.domain.types import Anchor, LotteryOutcome, RoundKind

USER = 42
OTHER = 99
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


async def make_concert(s) -> Concert:
    await ensure_user(s, USER, "reiji")
    await ensure_user(s, OTHER, "other")
    concert = Concert(title="Upgrade tour", event_id="upgrade-tour", created_by=USER)
    s.add(concert)
    await s.flush()
    return concert


async def make_day(s, concert: Concert, label: str) -> ConcertDay:
    day = ConcertDay(concert_id=concert.id, label=label, starts_at_utc=dt(8, 1, 9))
    s.add(day)
    await s.flush()
    return day


async def make_round(s, concert, kind, applies_to=None, closes=None, payment=None) -> Round:
    round_ = Round(
        concert_id=concert.id, kind=kind, label=kind.value,
        closes_at_utc=closes, payment_deadline_at_utc=payment, applies_to=applies_to,
    )
    s.add(round_)
    await s.flush()
    return round_


async def add_qualifier(s, upgrade: Round, qualifying: Round) -> None:
    s.add(RoundQualifier(upgrade_round_id=upgrade.id, qualifying_round_id=qualifying.id))
    await s.flush()


async def set_outcome(s, user: int, round_: Round, outcome: LotteryOutcome) -> None:
    s.add(RoundOutcome(user_id=user, round_id=round_.id, outcome=outcome))
    await s.flush()


async def make_rule(s, user, *, round_=None, concert=None, anchor=Anchor.CLOSES) -> ReminderRule:
    rule = ReminderRule(
        user_id=user,
        round_id=round_.id if round_ is not None else None,
        concert_id=concert.id if concert is not None else None,
        anchor=anchor,
        offset_days=0,
    )
    s.add(rule)
    await s.flush()
    return rule


async def queue_rows(s, rule: ReminderRule) -> list[ReminderQueue]:
    return list((await s.execute(
        select(ReminderQueue).where(ReminderQueue.rule_id == rule.id)
    )).scalars())


async def round_ids_in_queue(s, rule: ReminderRule) -> set[int]:
    return {row.round_id for row in await queue_rows(s, rule)}


# ── The core fix: eligible users keep their upgrade reminders ───────────────


async def test_upgrade_not_suppressed_for_user_who_secured_qualifying_round(session):
    """THE regression the unmodified cross-round pass would cause: securing
    the base round (WON->PAID) secures the leg, which today suppresses every
    OTHER round covering it -- including the upgrade the win just made
    enterable. The exemption keeps the upgrade's own reminder alive."""
    concert = await make_concert(session)
    leg = await make_day(session, concert, "Leg A")
    r1 = await make_round(session, concert, RoundKind.LOTTERY_ROUND, [leg.id], closes=dt(6, 20))
    up = await make_round(session, concert, RoundKind.UPGRADE, [leg.id], closes=dt(6, 25))
    await add_qualifier(session, up, r1)
    await set_outcome(session, USER, r1, LotteryOutcome.PAID)  # leg fully secured

    rule = await make_rule(session, USER, concert=concert, anchor=Anchor.CLOSES)
    await sync_rule(session, rule, NOW)

    # Without the exemption, up's close row would be deleted (leg secured by
    # r1). With it, the eligible user keeps it.
    assert up.id in await round_ids_in_queue(session, rule)


async def test_upgrade_suppressed_for_user_who_secured_nothing(session):
    """An upgrade round is dropped for a user who has not secured a
    qualifying ticket -- only applied, or nothing at all."""
    concert = await make_concert(session)
    leg = await make_day(session, concert, "Leg A")
    r1 = await make_round(session, concert, RoundKind.LOTTERY_ROUND, [leg.id], closes=dt(6, 20))
    up = await make_round(session, concert, RoundKind.UPGRADE, [leg.id], closes=dt(6, 25))
    await add_qualifier(session, up, r1)
    await set_outcome(session, USER, r1, LotteryOutcome.APPLIED)  # applied, not secured

    rule = await make_rule(session, USER, round_=up, anchor=Anchor.CLOSES)
    await sync_rule(session, rule, NOW)

    assert await queue_rows(session, rule) == []


async def test_recording_the_qualifying_win_makes_the_upgrade_appear(session):
    """record_round_outcome re-syncs the concert's rules: once the base win
    is recorded the upgrade becomes eligible and its reminder reinstates."""
    concert = await make_concert(session)
    leg = await make_day(session, concert, "Leg A")
    r1 = await make_round(session, concert, RoundKind.LOTTERY_ROUND, [leg.id], closes=dt(6, 20))
    up = await make_round(session, concert, RoundKind.UPGRADE, [leg.id], closes=dt(6, 25))
    await add_qualifier(session, up, r1)

    rule = await make_rule(session, USER, round_=up, anchor=Anchor.CLOSES)
    await sync_rule(session, rule, NOW)
    assert await queue_rows(session, rule) == []  # not eligible yet

    await record_round_outcome(session, USER, r1.id, LotteryOutcome.WON, NOW)

    assert len(await queue_rows(session, rule)) == 1


# ── Empty qualifier set: any secured base ticket, but not the upgrade's own ──


async def test_empty_qualifiers_eligible_when_any_base_round_secured(session):
    concert = await make_concert(session)
    leg = await make_day(session, concert, "Leg A")
    r1 = await make_round(session, concert, RoundKind.LOTTERY_ROUND, [leg.id], closes=dt(6, 20))
    up = await make_round(session, concert, RoundKind.UPGRADE, [leg.id], closes=dt(6, 25))
    # no RoundQualifier rows -> "any secured ticket qualifies"
    await set_outcome(session, USER, r1, LotteryOutcome.WON)

    rule = await make_rule(session, USER, round_=up, anchor=Anchor.CLOSES)
    await sync_rule(session, rule, NOW)

    assert len(await queue_rows(session, rule)) == 1


async def test_empty_qualifiers_not_eligible_when_nothing_secured(session):
    concert = await make_concert(session)
    leg = await make_day(session, concert, "Leg A")
    await make_round(session, concert, RoundKind.LOTTERY_ROUND, [leg.id], closes=dt(6, 20))
    up = await make_round(session, concert, RoundKind.UPGRADE, [leg.id], closes=dt(6, 25))

    rule = await make_rule(session, USER, round_=up, anchor=Anchor.CLOSES)
    await sync_rule(session, rule, NOW)

    assert await queue_rows(session, rule) == []


async def test_empty_qualifiers_own_win_does_not_make_eligible(session):
    """You cannot qualify for an upgrade by winning the upgrade itself --
    only base-round tickets qualify. The caller excludes the upgrade round's
    own outcome from the secured set, so an empty-qualifier upgrade whose
    ONLY outcome is its own WON is still suppressed."""
    concert = await make_concert(session)
    leg = await make_day(session, concert, "Leg A")
    await make_round(session, concert, RoundKind.LOTTERY_ROUND, [leg.id], closes=dt(6, 20))
    up = await make_round(session, concert, RoundKind.UPGRADE, [leg.id], closes=dt(6, 25))
    await set_outcome(session, USER, up, LotteryOutcome.WON)  # only the upgrade's own win

    rule = await make_rule(session, USER, round_=up, anchor=Anchor.CLOSES)
    await sync_rule(session, rule, NOW)

    assert await queue_rows(session, rule) == []


# ── Non-empty qualifiers: only a round IN the set counts ────────────────────


async def test_non_empty_qualifiers_eligible_only_from_a_qualifier(session):
    """Two base rounds; the upgrade lists only r1 as a qualifier. Securing
    r2 (outside the set) does not qualify; securing r1 does."""
    concert = await make_concert(session)
    leg = await make_day(session, concert, "Leg A")
    r1 = await make_round(session, concert, RoundKind.LOTTERY_ROUND, [leg.id], closes=dt(6, 18))
    r2 = await make_round(session, concert, RoundKind.LOTTERY_ROUND, [leg.id], closes=dt(6, 20))
    up = await make_round(session, concert, RoundKind.UPGRADE, [leg.id], closes=dt(6, 25))
    await add_qualifier(session, up, r1)

    # USER secured only r2 (not a qualifier) -> ineligible.
    await set_outcome(session, USER, r2, LotteryOutcome.WON)
    # OTHER secured r1 (the qualifier) -> eligible.
    await set_outcome(session, OTHER, r1, LotteryOutcome.WON)

    mine = await make_rule(session, USER, round_=up, anchor=Anchor.CLOSES)
    theirs = await make_rule(session, OTHER, round_=up, anchor=Anchor.CLOSES)
    await sync_rule(session, mine, NOW)
    await sync_rule(session, theirs, NOW)

    assert await queue_rows(session, mine) == []
    assert len(await queue_rows(session, theirs)) == 1


# ── Two sequential payments on one concert survive the queue dedup ──────────


async def test_two_payment_reminders_on_one_concert_survive_dedup(session):
    """A user can owe two payments on one concert: the base round and the
    upgrade round. The queue dedup key includes coalesce(round_id, 0), so the
    two payments are independent rows -- they must not collapse into one.

    r1 covers BOTH legs (WON, payment owed); up covers only leg A (WON,
    payment owed) and qualifies on r1. r1 survives up's cross-round pass
    because up secures only leg A, not r1's {A, B}; up survives as an eligible
    upgrade. One concert-wide PAYMENT rule -> exactly two rows, one each."""
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    b = await make_day(session, concert, "Leg B")
    r1 = await make_round(
        session, concert, RoundKind.LOTTERY_ROUND, [a.id, b.id], payment=dt(6, 28)
    )
    up = await make_round(session, concert, RoundKind.UPGRADE, [a.id], payment=dt(7, 5))
    await add_qualifier(session, up, r1)
    await set_outcome(session, USER, r1, LotteryOutcome.WON)  # base payment owed
    await set_outcome(session, USER, up, LotteryOutcome.WON)  # upgrade payment owed

    rule = await make_rule(session, USER, concert=concert, anchor=Anchor.PAYMENT)
    await sync_rule(session, rule, NOW)

    assert await round_ids_in_queue(session, rule) == {r1.id, up.id}


async def test_upgrade_payment_pass_still_applies_when_paid(session):
    """The same-round PAYMENT anchor pass is unchanged for upgrade rounds: an
    upgrade the user has already PAID drops its own payment reminder."""
    concert = await make_concert(session)
    leg = await make_day(session, concert, "Leg A")
    r1 = await make_round(session, concert, RoundKind.LOTTERY_ROUND, [leg.id], payment=dt(6, 28))
    up = await make_round(session, concert, RoundKind.UPGRADE, [leg.id], payment=dt(7, 5))
    await add_qualifier(session, up, r1)
    await set_outcome(session, USER, r1, LotteryOutcome.PAID)
    await set_outcome(session, USER, up, LotteryOutcome.PAID)

    rule = await make_rule(session, USER, round_=up, anchor=Anchor.PAYMENT)
    await sync_rule(session, rule, NOW)

    assert await queue_rows(session, rule) == []


# ── Regressions: non-upgrade behaviour must be untouched ────────────────────


async def test_ordinary_round_still_suppressed_by_secured_elsewhere(session):
    """The cross-round pass is unchanged for ordinary rounds: an ordinary
    round whose only leg is secured (WON) by another round is suppressed."""
    concert = await make_concert(session)
    leg = await make_day(session, concert, "Leg A")
    r1 = await make_round(session, concert, RoundKind.LOTTERY_ROUND, [leg.id], closes=dt(6, 18))
    r2 = await make_round(session, concert, RoundKind.GENERAL_SALE, [leg.id], closes=dt(6, 25))
    await set_outcome(session, USER, r1, LotteryOutcome.WON)  # secures leg A

    rule = await make_rule(session, USER, round_=r2, anchor=Anchor.CLOSES)
    await sync_rule(session, rule, NOW)

    assert await queue_rows(session, rule) == []


async def test_ordinary_round_still_suppressed_by_leg_opt_out(session):
    """The leg-opt-out pass is unchanged for ordinary rounds."""
    concert = await make_concert(session)
    leg = await make_day(session, concert, "Leg A")
    r1 = await make_round(session, concert, RoundKind.LOTTERY_ROUND, [leg.id], closes=dt(6, 25))
    await set_leg_opt_out(session, USER, leg.id, True)

    rule = await make_rule(session, USER, round_=r1, anchor=Anchor.CLOSES)
    await sync_rule(session, rule, NOW)

    assert await queue_rows(session, rule) == []


async def test_losing_an_upgrade_arms_nothing(session):
    """Losing an upgrade ends that nested side-campaign -- no next round is
    auto-armed for the leg (the user still holds their base ticket)."""
    concert = await make_concert(session)
    leg = await make_day(session, concert, "Leg A")
    r1 = await make_round(session, concert, RoundKind.LOTTERY_ROUND, [leg.id], closes=dt(6, 18))
    up = Round(
        concert_id=concert.id, kind=RoundKind.UPGRADE, label="upgrade",
        opens_at_utc=dt(6, 20), closes_at_utc=dt(6, 25), applies_to=[leg.id],
    )
    session.add(up)
    await session.flush()
    await add_qualifier(session, up, r1)
    later = Round(
        concert_id=concert.id, kind=RoundKind.GENERAL_SALE, label="later sale",
        opens_at_utc=dt(7, 1), closes_at_utc=dt(7, 10), applies_to=[leg.id],
    )
    session.add(later)
    await session.flush()

    await record_round_outcome(session, USER, up.id, LotteryOutcome.LOST, NOW)

    armed = list((await session.execute(
        select(ReminderRule).where(ReminderRule.user_id == USER)
    )).scalars())
    assert armed == []


async def test_losing_a_base_round_never_arms_an_upgrade(session):
    """An upgrade round is never auto-armed as the next round for a lost
    base round -- the loser holds no qualifying ticket to enter it."""
    concert = await make_concert(session)
    leg = await make_day(session, concert, "Leg A")
    r1 = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="base",
        opens_at_utc=dt(6, 1), closes_at_utc=dt(6, 18), applies_to=[leg.id],
    )
    session.add(r1)
    await session.flush()
    up = Round(
        concert_id=concert.id, kind=RoundKind.UPGRADE, label="upgrade",
        opens_at_utc=dt(7, 1), closes_at_utc=dt(7, 10), applies_to=[leg.id],
    )
    session.add(up)
    await session.flush()
    await add_qualifier(session, up, r1)

    await record_round_outcome(session, USER, r1.id, LotteryOutcome.LOST, NOW)

    armed = list((await session.execute(
        select(ReminderRule).where(ReminderRule.round_id == up.id)
    )).scalars())
    assert armed == []


async def test_upgrade_won_still_suppresses_ordinary_rounds(session):
    """secured_by is unchanged: an upgrade the user WON still secures its
    legs for OTHER (non-upgrade) rounds, suppressing a later general sale on
    the same leg."""
    concert = await make_concert(session)
    leg = await make_day(session, concert, "Leg A")
    r1 = await make_round(session, concert, RoundKind.LOTTERY_ROUND, [leg.id], closes=dt(6, 18))
    up = await make_round(session, concert, RoundKind.UPGRADE, [leg.id], closes=dt(6, 22))
    later = await make_round(session, concert, RoundKind.GENERAL_SALE, [leg.id], closes=dt(6, 28))
    await add_qualifier(session, up, r1)
    await set_outcome(session, USER, r1, LotteryOutcome.WON)
    await set_outcome(session, USER, up, LotteryOutcome.WON)  # upgrade secured

    rule = await make_rule(session, USER, round_=later, anchor=Anchor.CLOSES)
    await sync_rule(session, rule, NOW)

    assert await queue_rows(session, rule) == []
