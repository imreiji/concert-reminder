"""`concert_round_rows` -- the per-leg round grouping the concert page renders,
carrying each round's per-user standing and the two capture gates.

The gates themselves are Home's ("Coming up") gates, shared rather than
re-derived; the tests here pin the behaviour so a future edit to one caller
cannot quietly change the other.
"""

from datetime import UTC, datetime, timedelta

import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Concert, ConcertDay, Round
from app.db.service import concert_round_rows, ensure_user, record_round_outcome
from app.domain.types import Anchor, LotteryOutcome, RoundKind

NOW = datetime(2026, 6, 1, tzinfo=UTC)


def dt(month: int, day: int, hour: int = 12) -> datetime:
    return datetime(2026, month, day, hour, tzinfo=UTC)


@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    # Cascades silently do not fire without this (production registers it too).
    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def seed(s, *, cancel_leg_b: bool = False):
    """Two legs and four rounds, one per applies_to shape: leg A only, both
    legs, neither leg, and leg A + leg B where a third leg exists."""
    await ensure_user(s, 42, "reiji")
    await ensure_user(s, 99, "someone-else")
    concert = Concert(title="Two-Leg Tour", event_id="two-leg-tour", created_by=42)
    s.add(concert)
    await s.flush()
    leg_a = ConcertDay(concert_id=concert.id, label="Leg A", starts_at_utc=dt(8, 1, 9))
    leg_b = ConcertDay(
        concert_id=concert.id, label="Leg B", starts_at_utc=dt(8, 2, 9),
        cancelled=cancel_leg_b,
    )
    s.add_all([leg_a, leg_b])
    await s.flush()
    r_a = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="A-only",
        opens_at_utc=dt(5, 1), closes_at_utc=dt(6, 25), applies_to=[leg_a.id],
    )
    r_both = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="Both-legs",
        opens_at_utc=dt(5, 1), closes_at_utc=dt(6, 26), applies_to=[leg_a.id, leg_b.id],
    )
    r_none = Round(
        concert_id=concert.id, kind=RoundKind.GENERAL_SALE, label="General",
        opens_at_utc=dt(5, 1), closes_at_utc=dt(6, 27),
    )
    s.add_all([r_a, r_both, r_none])
    await s.flush()
    await s.commit()
    return concert, leg_a, leg_b, r_a, r_both, r_none


def labels(rows) -> list[str]:
    return [row.round_.label for row in rows]


# ── grouping ─────────────────────────────────────────────────────────────


async def test_a_single_leg_round_appears_only_under_that_leg(session):
    concert, leg_a, leg_b, *_ = await seed(session)
    legs, all_legs = await concert_round_rows(session, 42, concert, now=NOW)

    by_leg = {leg.day.id: labels(leg.rounds) for leg in legs}
    assert "A-only" in by_leg[leg_a.id]
    assert "A-only" not in by_leg[leg_b.id]
    assert "A-only" not in labels(all_legs)


async def test_a_round_covering_every_live_leg_goes_in_the_all_legs_group(session):
    """Covering all of them is the same statement as covering none of them --
    it is not a per-leg fact, so repeating it under each leg is noise."""
    concert, leg_a, leg_b, *_ = await seed(session)
    legs, all_legs = await concert_round_rows(session, 42, concert, now=NOW)

    assert "Both-legs" in labels(all_legs)
    for leg in legs:
        assert "Both-legs" not in labels(leg.rounds)


async def test_a_round_with_no_applies_to_goes_in_the_all_legs_group(session):
    concert, *_ = await seed(session)
    legs, all_legs = await concert_round_rows(session, 42, concert, now=NOW)

    assert "General" in labels(all_legs)
    for leg in legs:
        assert "General" not in labels(leg.rounds)


async def test_a_round_covering_some_but_not_all_legs_appears_under_each(session):
    """Three legs, one round on two of them: it is a real fact about both, so
    it renders twice rather than being demoted to the all-legs group."""
    concert, leg_a, leg_b, *_ = await seed(session)
    leg_c = ConcertDay(concert_id=concert.id, label="Leg C", starts_at_utc=dt(8, 3, 9))
    session.add(leg_c)
    await session.flush()
    session.add(Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="A-and-B",
        closes_at_utc=dt(6, 28), applies_to=[leg_a.id, leg_b.id],
    ))
    await session.commit()

    legs, all_legs = await concert_round_rows(session, 42, concert, now=NOW)
    by_leg = {leg.day.id: labels(leg.rounds) for leg in legs}
    assert "A-and-B" in by_leg[leg_a.id]
    assert "A-and-B" in by_leg[leg_b.id]
    assert "A-and-B" not in by_leg[leg_c.id]
    assert "A-and-B" not in labels(all_legs)


async def test_a_cancelled_leg_still_yields_its_group_with_its_rounds(session):
    """Invariant 2: a cancelled ConcertDay is never deleted, and the concert
    page dims it rather than hiding it -- so the grouping must still return
    it, rounds and all."""
    concert, leg_a, leg_b, *_ = await seed(session, cancel_leg_b=True)
    session.add(Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="B-only",
        closes_at_utc=dt(6, 29), applies_to=[leg_b.id],
    ))
    await session.commit()

    legs, _all_legs = await concert_round_rows(session, 42, concert, now=NOW)
    cancelled = [leg for leg in legs if leg.day.id == leg_b.id]
    assert len(cancelled) == 1
    assert cancelled[0].day.cancelled is True
    assert "B-only" in labels(cancelled[0].rounds)


async def test_every_live_leg_ignores_a_cancelled_one(session):
    """With Leg B cancelled, Leg A is the only live leg -- so an A-only round
    now covers every live leg and belongs in the all-legs group."""
    concert, leg_a, _leg_b, *_ = await seed(session, cancel_leg_b=True)
    legs, all_legs = await concert_round_rows(session, 42, concert, now=NOW)

    assert "A-only" in labels(all_legs)
    by_leg = {leg.day.id: labels(leg.rounds) for leg in legs}
    assert "A-only" not in by_leg[leg_a.id]


# ── gates ────────────────────────────────────────────────────────────────


async def test_can_capture_is_false_for_a_round_that_has_not_opened(session):
    """Recording APPLIED against a round nobody could have entered is both
    false and irreversible -- record_round_outcome refuses to overwrite a
    starting state."""
    concert, *_ = await seed(session)
    session.add(Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="Not open yet",
        opens_at_utc=NOW + timedelta(days=3), closes_at_utc=NOW + timedelta(days=10),
    ))
    await session.commit()

    _legs, all_legs = await concert_round_rows(session, 42, concert, now=NOW)
    row = next(r for r in all_legs if r.round_.label == "Not open yet")
    assert row.can_capture is False
    assert row.can_report_result is False

    open_row = next(r for r in all_legs if r.round_.label == "General")
    assert open_row.can_capture is True


async def test_can_report_result_only_once_the_result_is_due(session):
    """APPLIED alone is not enough: "I won"/"I lost" are guesses until the
    announced results time (or, failing that, the close) has passed."""
    concert, *_ = await seed(session)
    pending = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="Awaiting results",
        opens_at_utc=dt(5, 1), closes_at_utc=dt(5, 20),
        results_at_utc=NOW + timedelta(days=5),
    )
    due = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="Results due",
        opens_at_utc=dt(5, 1), closes_at_utc=dt(5, 20), results_at_utc=dt(5, 25),
    )
    session.add_all([pending, due])
    await session.flush()
    await record_round_outcome(session, 42, pending.id, LotteryOutcome.APPLIED)
    await record_round_outcome(session, 42, due.id, LotteryOutcome.APPLIED)
    await session.commit()

    _legs, all_legs = await concert_round_rows(session, 42, concert, now=NOW)
    rows = {r.round_.label: r for r in all_legs}
    assert rows["Awaiting results"].outcome is LotteryOutcome.APPLIED
    assert rows["Awaiting results"].can_report_result is False
    assert rows["Results due"].can_report_result is True
    # Not applied at all: nothing to report either way.
    assert rows["General"].can_report_result is False


# ── outcomes ─────────────────────────────────────────────────────────────


async def test_two_users_see_independent_standings_on_the_same_concert(session):
    concert, *_ = await seed(session)
    _legs, mine = await concert_round_rows(session, 42, concert, now=NOW)
    general = next(r for r in mine if r.round_.label == "General")
    await record_round_outcome(session, 42, general.round_.id, LotteryOutcome.APPLIED)
    await session.commit()

    _legs, mine = await concert_round_rows(session, 42, concert, now=NOW)
    _legs, theirs = await concert_round_rows(session, 99, concert, now=NOW)
    assert next(r for r in mine if r.round_.label == "General").outcome \
        is LotteryOutcome.APPLIED
    assert next(r for r in theirs if r.round_.label == "General").outcome is None


async def test_an_anonymous_caller_gets_rows_with_no_outcome(session):
    """The concert page is reachable without a standing to show; user_id=None
    must yield rows, not an exception."""
    concert, *_ = await seed(session)
    legs, all_legs = await concert_round_rows(session, None, concert, now=NOW)

    assert len(legs) == 2
    assert labels(all_legs)
    assert all(row.outcome is None for row in all_legs)
    assert all(row.outcome is None for leg in legs for row in leg.rounds)
    # A gate that depends only on round timing still resolves.
    assert next(r for r in all_legs if r.round_.label == "General").can_capture is True


# ── the prominent anchor ─────────────────────────────────────────────────


async def test_the_primary_anchor_is_the_next_moment_still_ahead(session):
    concert, *_ = await seed(session)
    session.add(Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="Full ladder",
        opens_at_utc=dt(5, 1), closes_at_utc=dt(6, 10),
        results_at_utc=dt(6, 20), payment_deadline_at_utc=dt(6, 30),
    ))
    await session.commit()

    _legs, all_legs = await concert_round_rows(session, 42, concert, now=NOW)
    row = next(r for r in all_legs if r.round_.label == "Full ladder")
    # Opens is behind us; the close is the next thing that happens.
    assert row.primary_anchor is Anchor.CLOSES
    assert row.primary_at_utc == dt(6, 10)


async def test_a_wholly_past_round_falls_back_to_its_last_moment(session):
    """Nothing ahead to lead with, so the row still says something rather than
    rendering a blank date column."""
    concert, *_ = await seed(session)
    session.add(Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="All over",
        opens_at_utc=dt(4, 1), closes_at_utc=dt(4, 20), results_at_utc=dt(5, 2),
    ))
    await session.commit()

    _legs, all_legs = await concert_round_rows(session, 42, concert, now=NOW)
    row = next(r for r in all_legs if r.round_.label == "All over")
    assert row.primary_anchor is Anchor.RESULTS
    assert row.primary_at_utc == dt(5, 2)
