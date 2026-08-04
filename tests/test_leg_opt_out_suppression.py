"""Per-leg opt-out suppression: the read-side planner pass.

A LegOptOut row prunes one ConcertDay for one user. Its rounds drop out of
that user's planner candidate list -- but only when EVERY leg a round
covers is opted out, the per-user analogue of the fully-cancelled-leg rule
(is_round_cancelled). A round covering two legs where the user opted out of
only one is still planned; an all-legs round (empty/None applies_to) is
tied to no specific leg, so no leg opt-out can suppress it. This is a
read-side filter folded into _apply_outcome_suppression -- no write path.
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
)
from app.db.service import ensure_user, set_leg_opt_out, sync_rule, user_calendar_events
from app.domain.types import Anchor, RoundKind

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
    concert = Concert(title="two-leg", event_id="two-leg", created_by=USER)
    s.add(concert)
    await s.flush()
    return concert


async def make_day(s, concert: Concert, label: str) -> ConcertDay:
    day = ConcertDay(concert_id=concert.id, label=label, starts_at_utc=dt(8, 1, 9))
    s.add(day)
    await s.flush()
    return day


async def make_round(s, concert: Concert, applies_to) -> Round:
    round_ = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="Round 1",
        closes_at_utc=dt(6, 25), applies_to=applies_to,
    )
    s.add(round_)
    await s.flush()
    return round_


async def make_rule(s, user: int, round_: Round) -> ReminderRule:
    rule = ReminderRule(user_id=user, round_id=round_.id, anchor=Anchor.CLOSES, offset_days=0)
    s.add(rule)
    await s.flush()
    return rule


async def queue_rows(s, rule: ReminderRule) -> list[ReminderQueue]:
    return list((await s.execute(
        select(ReminderQueue).where(ReminderQueue.rule_id == rule.id)
    )).scalars())


async def test_partial_opt_out_does_not_suppress(session):
    """Opt out of only ONE leg of a two-leg round -> the round is still
    planned. Same rule as cancellation: partial opt-out does not kill it."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    b = await make_day(session, concert, "Leg B")
    round_ = await make_round(session, concert, [a.id, b.id])
    rule = await make_rule(session, USER, round_)

    await set_leg_opt_out(session, USER, a.id, True)  # only one of two legs
    await sync_rule(session, rule, NOW)

    assert await queue_rows(session, rule) != []


async def test_opt_out_only_leg_suppresses(session):
    """Opt out of the ONLY leg a round covers -> the round is suppressed."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    round_ = await make_round(session, concert, [a.id])
    rule = await make_rule(session, USER, round_)

    await set_leg_opt_out(session, USER, a.id, True)
    await sync_rule(session, rule, NOW)

    assert await queue_rows(session, rule) == []


async def test_suppression_is_per_user(session):
    """A different user who did not opt out still gets the round planned."""
    await ensure_user(session, USER, "reiji")
    await ensure_user(session, OTHER, "other")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    round_ = await make_round(session, concert, [a.id])
    mine = await make_rule(session, USER, round_)
    theirs = await make_rule(session, OTHER, round_)

    await set_leg_opt_out(session, USER, a.id, True)
    await sync_rule(session, mine, NOW)
    await sync_rule(session, theirs, NOW)

    assert await queue_rows(session, mine) == []
    assert await queue_rows(session, theirs) != []


async def test_all_legs_round_never_suppressed(session):
    """An all-legs round (empty applies_to) is tied to no specific leg, so
    no set of leg opt-outs can cover it -- mirrors is_round_cancelled
    leaving empty applies_to alone."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    round_ = await make_round(session, concert, None)  # "all legs" / General
    rule = await make_rule(session, USER, round_)

    await set_leg_opt_out(session, USER, a.id, True)
    await sync_rule(session, rule, NOW)

    assert await queue_rows(session, rule) != []


async def test_opting_out_every_leg_clears_already_queued_reminders(session):
    """Invariant 8: the WRITE re-syncs, it does not merely change what a later
    sync would decide.

    Suppression is a read-side pass folded into `sync_rule`, so before this the
    queue kept whatever it had been armed with: opt out of every leg through
    the dialog and the round's already-queued reminders still went out. The
    resync lives in `set_leg_opt_out` rather than in its callers, so both write
    surfaces (the day-result route and the leg opt-out route) get it.

    The partial step in the middle is the other half of the contract: the
    every-leg rule must not over-fire and clear a round the reader is still
    going to."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    b = await make_day(session, concert, "Leg B")
    round_ = await make_round(session, concert, [a.id, b.id])
    rule = await make_rule(session, USER, round_)
    await sync_rule(session, rule, NOW)
    assert await queue_rows(session, rule) != []  # armed

    await set_leg_opt_out(session, USER, a.id, True, now=NOW)
    assert await queue_rows(session, rule) != []  # one leg to go: still armed

    await set_leg_opt_out(session, USER, b.id, True, now=NOW)
    assert await queue_rows(session, rule) == []


async def test_opting_back_in_re_arms_the_queue(session):
    """The same resync in the other direction -- an opt-out is reversible, and
    a reader who changes their mind is owed the reminder back without waiting
    for some unrelated edit to re-plan the round."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    round_ = await make_round(session, concert, [a.id])
    rule = await make_rule(session, USER, round_)
    await sync_rule(session, rule, NOW)

    await set_leg_opt_out(session, USER, a.id, True, now=NOW)
    assert await queue_rows(session, rule) == []

    await set_leg_opt_out(session, USER, a.id, False, now=NOW)
    assert await queue_rows(session, rule) != []


async def test_opting_out_a_leg_that_does_not_exist_stays_a_silent_no_op(session):
    """The resync needs the day's concert, and a day id that names nothing has
    none. That stays forgiving rather than raising: ids reach these writers
    from form posts and Discord custom_ids."""
    await ensure_user(session, USER, "reiji")
    await set_leg_opt_out(session, USER, 987654, False, now=NOW)


async def test_all_legs_opted_out_suppresses(session):
    """Two legs, both opted out -> every leg the round covers is pruned, so
    it is suppressed."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    b = await make_day(session, concert, "Leg B")
    round_ = await make_round(session, concert, [a.id, b.id])
    rule = await make_rule(session, USER, round_)

    await set_leg_opt_out(session, USER, a.id, True)
    await set_leg_opt_out(session, USER, b.id, True)
    await sync_rule(session, rule, NOW)

    assert await queue_rows(session, rule) == []


async def make_event_rule(s, user: int, concert: Concert) -> ReminderRule:
    """A concert-wide 'remind me at show start' rule -- what plans DAY rows."""
    rule = ReminderRule(
        user_id=user, concert_id=concert.id, anchor=Anchor.EVENT_START, offset_days=0
    )
    s.add(rule)
    await s.flush()
    return rule


async def test_day_rows_not_planned_for_opted_out_leg(session):
    """An event_start rule plans a show-start row per live leg -- but not for
    a leg this user opted out of. The other leg's row survives (partial
    opt-out never widens)."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    b = await make_day(session, concert, "Leg B")
    rule = await make_event_rule(session, USER, concert)

    await set_leg_opt_out(session, USER, a.id, True, now=NOW)
    await sync_rule(session, rule, NOW)

    day_ids = {row.day_id for row in await queue_rows(session, rule)}
    assert day_ids == {b.id}


async def test_opting_out_clears_already_queued_day_rows(session):
    """Invariant 8's write-owns-the-resync, now covering DAY rows: the queue is
    a materialized outbox, so the set_leg_opt_out write itself must clear the
    show-start row -- before this, its own resync faithfully re-planned it."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    b = await make_day(session, concert, "Leg B")
    rule = await make_event_rule(session, USER, concert)
    await sync_rule(session, rule, NOW)
    assert {row.day_id for row in await queue_rows(session, rule)} == {a.id, b.id}

    await set_leg_opt_out(session, USER, a.id, True, now=NOW)
    assert {row.day_id for row in await queue_rows(session, rule)} == {b.id}

    await set_leg_opt_out(session, USER, a.id, False, now=NOW)
    assert {row.day_id for row in await queue_rows(session, rule)} == {a.id, b.id}


async def test_day_row_suppression_is_per_user(session):
    """Another user who did not opt out keeps their show-start row."""
    await ensure_user(session, USER, "reiji")
    await ensure_user(session, OTHER, "other")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    mine = await make_event_rule(session, USER, concert)
    theirs = await make_event_rule(session, OTHER, concert)

    await set_leg_opt_out(session, USER, a.id, True)
    await sync_rule(session, mine, NOW)
    await sync_rule(session, theirs, NOW)

    assert await queue_rows(session, mine) == []
    assert {row.day_id for row in await queue_rows(session, theirs)} == {a.id}


async def test_calendar_feed_omits_opted_out_leg(session):
    """The owner's original report was 'shows up on feed': the .ics feed reads
    reminder_queue back out (user_calendar_events), so with the day row gone
    the feed carries only the leg the reader is still going to."""
    await ensure_user(session, USER, "reiji")
    concert = await make_concert(session)
    a = await make_day(session, concert, "Leg A")
    await make_day(session, concert, "Leg B")
    rule = await make_event_rule(session, USER, concert)
    await sync_rule(session, rule, NOW)

    await set_leg_opt_out(session, USER, a.id, True, now=NOW)

    labels = {e.label for e in await user_calendar_events(session, USER, now=NOW)}
    assert "Leg A" not in labels
    assert "Leg B" in labels
