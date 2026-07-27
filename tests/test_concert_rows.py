"""`concert_round_rows` -- the per-leg round grouping the concert page renders,
carrying each round's per-user standing and the two capture gates.

Every round renders under EACH live leg it applies to, including the ones that
apply to all of them: a leg is a complete story, and the viewer's standing is a
per-leg fact now (a lottery covering Sat+Sun really can come back "won Sat,
lost Sun"). The second returned list is only the fallback for a concert with no
legs at all.

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
from app.db.service import (
    RoundRow,
    _needs_you,
    _wants_you,
    concert_next_moment,
    concert_round_rows,
    ensure_user,
    record_round_day_result,
    record_round_outcome,
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
    """Two legs and three rounds, one per applies_to shape: leg A only, both
    legs, and neither leg (the all-legs convention)."""
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


def by_leg(legs) -> dict[int, list[str]]:
    return {leg.day.id: labels(leg.rounds) for leg in legs}


def row_for(legs, leg_id: int, label: str):
    """The one RoundRow for (leg, round label) -- rows are per-leg instances
    now, so which leg you ask about is part of the question."""
    return next(
        row for leg in legs if leg.day.id == leg_id
        for row in leg.rounds if row.round_.label == label
    )


# ── grouping ─────────────────────────────────────────────────────────────


async def test_a_single_leg_round_appears_only_under_that_leg(session):
    concert, leg_a, leg_b, *_ = await seed(session)
    legs, fallback = await concert_round_rows(session, 42, concert, now=NOW)

    grouped = by_leg(legs)
    assert "A-only" in grouped[leg_a.id]
    assert "A-only" not in grouped[leg_b.id]
    assert fallback == []


async def test_concert_rows_all_legs_round_appears_under_each_live_leg(session):
    """A round covering every leg is a real fact about each of them: the page
    reads leg by leg, so it renders under both rather than in a separate
    section the owner had to cross-reference."""
    concert, leg_a, leg_b, *_ = await seed(session)
    legs, fallback = await concert_round_rows(session, 42, concert, now=NOW)

    grouped = by_leg(legs)
    assert "Both-legs" in grouped[leg_a.id]
    assert "Both-legs" in grouped[leg_b.id]
    # Empty applies_to (the all-legs convention) expands the same way.
    assert "General" in grouped[leg_a.id]
    assert "General" in grouped[leg_b.id]
    assert fallback == []


async def test_concert_rows_fallback_group_only_when_no_days(session):
    """The second list survives for exactly one case: a concert with no legs
    at all, where there is no group to put a round under."""
    await ensure_user(session, 42, "reiji")
    concert = Concert(title="Dateless", event_id="dateless", created_by=42)
    session.add(concert)
    await session.flush()
    session.add(Round(
        concert_id=concert.id, kind=RoundKind.GENERAL_SALE, label="Sale",
        opens_at_utc=dt(5, 1), closes_at_utc=dt(6, 27),
    ))
    await session.commit()

    legs, fallback = await concert_round_rows(session, 42, concert, now=NOW)
    assert legs == []
    assert labels(fallback) == ["Sale"]


async def test_a_round_covering_some_but_not_all_legs_appears_under_each(session):
    """Three legs, one round on two of them."""
    concert, leg_a, leg_b, *_ = await seed(session)
    leg_c = ConcertDay(concert_id=concert.id, label="Leg C", starts_at_utc=dt(8, 3, 9))
    session.add(leg_c)
    await session.flush()
    session.add(Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="A-and-B",
        closes_at_utc=dt(6, 28), applies_to=[leg_a.id, leg_b.id],
    ))
    await session.commit()

    legs, fallback = await concert_round_rows(session, 42, concert, now=NOW)
    grouped = by_leg(legs)
    assert "A-and-B" in grouped[leg_a.id]
    assert "A-and-B" in grouped[leg_b.id]
    assert "A-and-B" not in grouped[leg_c.id]
    assert fallback == []


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

    legs, _fallback = await concert_round_rows(session, 42, concert, now=NOW)
    cancelled = [leg for leg in legs if leg.day.id == leg_b.id]
    assert len(cancelled) == 1
    assert cancelled[0].day.cancelled is True
    assert "B-only" in labels(cancelled[0].rounds)


async def test_an_all_legs_round_skips_a_cancelled_leg(session):
    """"Every live leg" is what an empty applies_to means -- a cancelled leg
    is not one of them, so the round does not appear under it. A round that
    NAMES the cancelled leg still does (the test above)."""
    concert, leg_a, leg_b, *_ = await seed(session, cancel_leg_b=True)
    legs, fallback = await concert_round_rows(session, 42, concert, now=NOW)

    grouped = by_leg(legs)
    assert "General" in grouped[leg_a.id]
    assert "General" not in grouped[leg_b.id]
    assert fallback == []


# ── gates ────────────────────────────────────────────────────────────────


async def test_can_capture_is_false_for_a_round_that_has_not_opened(session):
    """Recording APPLIED against a round nobody could have entered is both
    false and irreversible -- record_round_outcome refuses to overwrite a
    starting state."""
    concert, leg_a, *_ = await seed(session)
    session.add(Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="Not open yet",
        opens_at_utc=NOW + timedelta(days=3), closes_at_utc=NOW + timedelta(days=10),
    ))
    await session.commit()

    legs, _fallback = await concert_round_rows(session, 42, concert, now=NOW)
    row = row_for(legs, leg_a.id, "Not open yet")
    assert row.can_capture is False
    assert row.can_report_result is False

    assert row_for(legs, leg_a.id, "General").can_capture is True


async def test_can_report_result_only_once_the_result_is_due(session):
    """APPLIED alone is not enough: "I won"/"I lost" are guesses until the
    announced results time (or, failing that, the close) has passed."""
    concert, leg_a, *_ = await seed(session)
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

    legs, _fallback = await concert_round_rows(session, 42, concert, now=NOW)
    assert row_for(legs, leg_a.id, "Awaiting results").outcome is LotteryOutcome.APPLIED
    assert row_for(legs, leg_a.id, "Awaiting results").can_report_result is False
    assert row_for(legs, leg_a.id, "Results due").can_report_result is True
    # Not applied at all: nothing to report either way.
    assert row_for(legs, leg_a.id, "General").can_report_result is False


async def test_concert_rows_covered_round_renders_quiet(session):
    """A round every one of whose legs the viewer already secured elsewhere
    still renders -- the page shows the whole campaign -- but it offers
    nothing to press: there is no honest answer left to give."""
    concert, leg_a, leg_b, r_a, r_both, _r_none = await seed(session)
    await record_round_outcome(session, 42, r_both.id, LotteryOutcome.WON)
    await session.commit()

    legs, _fallback = await concert_round_rows(session, 42, concert, now=NOW)
    covered = row_for(legs, leg_a.id, "A-only")
    assert covered.covered is True
    assert covered.can_capture is False
    assert covered.can_report_result is False
    # The round that DID the securing is never covered by its own outcome.
    assert row_for(legs, leg_a.id, "Both-legs").covered is False


async def test_two_won_rounds_over_the_same_legs_both_stay_payable(session):
    """Each round's legs are secured "elsewhere" by the other, so a naive
    covered fold would silence BOTH -- and the user owes payment on two
    tickets with no surface offering Paid. A round you won is never
    covered."""
    concert, leg_a, _leg_b, r_a, r_both, _r_none = await seed(session)
    await record_round_outcome(session, 42, r_a.id, LotteryOutcome.WON)
    await record_round_outcome(session, 42, r_both.id, LotteryOutcome.WON)
    await session.commit()

    legs, _fallback = await concert_round_rows(session, 42, concert, now=NOW)
    for label in ("A-only", "Both-legs"):
        row = row_for(legs, leg_a.id, label)
        assert row.covered is False
        assert row.outcome is LotteryOutcome.WON  # the Paid path stays reachable


async def test_a_covered_round_never_leads_next_for_you(session):
    """It closes soonest, so it would win the urgency pick outright -- but it
    offers nothing to press, and a panel you cannot act on is worse than the
    round it displaced."""
    concert, leg_a, _leg_b, r_a, r_both, _r_none = await seed(session)
    await record_round_outcome(session, 42, r_both.id, LotteryOutcome.WON)
    await session.commit()

    legs, _fallback = await concert_round_rows(session, 42, concert, now=NOW)
    rows = [row for leg in legs for row in leg.rounds]
    assert concert_next_moment(rows, now=NOW).round_.label != "A-only"


async def test_a_covered_round_is_quiet_for_that_viewer_only(session):
    concert, leg_a, _leg_b, _r_a, r_both, _r_none = await seed(session)
    await record_round_outcome(session, 42, r_both.id, LotteryOutcome.WON)
    await session.commit()

    legs, _fallback = await concert_round_rows(session, 99, concert, now=NOW)
    assert row_for(legs, leg_a.id, "A-only").covered is False


# ── per-leg standing ─────────────────────────────────────────────────────


async def test_concert_rows_leg_result_reflects_partial_win(session):
    """One round, two legs, one ticket: the leg you won and the leg you lost
    must not read the same."""
    concert, leg_a, leg_b, _r_a, r_both, _r_none = await seed(session)
    await record_round_outcome(session, 42, r_both.id, LotteryOutcome.APPLIED)
    await record_round_day_result(session, 42, r_both.id, leg_a.id, LegResult.WON, NOW)
    await record_round_day_result(session, 42, r_both.id, leg_b.id, LegResult.LOST, NOW)
    await session.commit()

    legs, _fallback = await concert_round_rows(session, 42, concert, now=NOW)
    assert row_for(legs, leg_a.id, "Both-legs").leg_result is LegResult.WON
    assert row_for(legs, leg_b.id, "Both-legs").leg_result is LegResult.LOST
    # A round with no standing at all resolves to nothing on either leg.
    assert row_for(legs, leg_a.id, "General").leg_result is None


async def test_leg_result_falls_back_to_the_no_rows_means_all_convention(session):
    """A whole-round WON with no day rows means every covered leg was won --
    the convention made visible, so a page never renders a won round blank."""
    concert, leg_a, leg_b, _r_a, r_both, _r_none = await seed(session)
    await record_round_outcome(session, 42, r_both.id, LotteryOutcome.WON)
    await session.commit()

    legs, _fallback = await concert_round_rows(session, 42, concert, now=NOW)
    assert row_for(legs, leg_a.id, "Both-legs").leg_result is LegResult.WON
    assert row_for(legs, leg_b.id, "Both-legs").leg_result is LegResult.WON


async def test_leg_result_is_lost_on_every_leg_of_a_lost_round(session):
    concert, leg_a, leg_b, _r_a, r_both, _r_none = await seed(session)
    await record_round_outcome(session, 42, r_both.id, LotteryOutcome.LOST)
    await session.commit()

    legs, _fallback = await concert_round_rows(session, 42, concert, now=NOW)
    assert row_for(legs, leg_a.id, "Both-legs").leg_result is LegResult.LOST
    assert row_for(legs, leg_b.id, "Both-legs").leg_result is LegResult.LOST


# ── per-day result capture ───────────────────────────────────────────────


async def test_capture_days_lists_every_unresolved_leg_once_results_are_out(session):
    concert, leg_a, leg_b, _r_a, r_both, _r_none = await seed(session)
    r_both.results_at_utc = dt(5, 28)
    await session.flush()
    await record_round_outcome(session, 42, r_both.id, LotteryOutcome.APPLIED)
    await session.commit()

    legs, _fallback = await concert_round_rows(session, 42, concert, now=NOW)
    row = row_for(legs, leg_a.id, "Both-legs")
    assert row.capture_days == ((leg_a.id, "Leg A"), (leg_b.id, "Leg B"))
    assert row.any_day_won is False
    # Every leg's copy of the round carries the same work list.
    assert row_for(legs, leg_b.id, "Both-legs").capture_days == row.capture_days


async def test_capture_days_narrows_to_the_legs_still_unresolved(session):
    concert, leg_a, leg_b, _r_a, r_both, _r_none = await seed(session)
    r_both.results_at_utc = dt(5, 28)
    await session.flush()
    await record_round_outcome(session, 42, r_both.id, LotteryOutcome.APPLIED)
    await record_round_day_result(session, 42, r_both.id, leg_a.id, LegResult.WON, NOW)
    await session.commit()

    legs, _fallback = await concert_round_rows(session, 42, concert, now=NOW)
    row = row_for(legs, leg_a.id, "Both-legs")
    assert row.capture_days == ((leg_b.id, "Leg B"),)
    assert row.any_day_won is True
    # And the leg still waiting shows no standing -- the round-level WON must
    # not be read as a ticket for a leg nobody has heard about yet.
    assert row.leg_result is LegResult.WON
    assert row_for(legs, leg_b.id, "Both-legs").leg_result is None


async def test_no_capture_days_before_the_result_is_knowable(session):
    concert, leg_a, _leg_b, _r_a, r_both, _r_none = await seed(session)
    r_both.results_at_utc = NOW + timedelta(days=5)
    await session.flush()
    await record_round_outcome(session, 42, r_both.id, LotteryOutcome.APPLIED)
    await session.commit()

    legs, _fallback = await concert_round_rows(session, 42, concert, now=NOW)
    assert row_for(legs, leg_a.id, "Both-legs").capture_days == ()


async def test_no_capture_days_for_a_single_leg_round(session):
    """One leg is not a per-day question -- the flat "I won"/"I lost" pair
    already says everything there is to say."""
    concert, leg_a, _leg_b, r_a, _r_both, _r_none = await seed(session)
    r_a.results_at_utc = dt(5, 28)
    await session.flush()
    await record_round_outcome(session, 42, r_a.id, LotteryOutcome.APPLIED)
    await session.commit()

    legs, _fallback = await concert_round_rows(session, 42, concert, now=NOW)
    assert row_for(legs, leg_a.id, "A-only").capture_days == ()


async def test_has_day_results_is_false_until_a_leg_is_answered(session):
    """The switch the whole-round "Won (all)" shortcut hides behind: once ANY
    day row exists the round is being resolved leg by leg, and a whole-round
    WON write would secure nothing (see the template gate)."""
    concert, _leg_a, _leg_b, _r_a, r_both, _r_none = await seed(session)
    r_both.results_at_utc = dt(5, 28)
    await session.flush()
    await record_round_outcome(session, 42, r_both.id, LotteryOutcome.APPLIED)
    await session.commit()

    legs, _fallback = await concert_round_rows(session, 42, concert, now=NOW)
    assert row_for(legs, _leg_a.id, "Both-legs").has_day_results is False


async def test_has_day_results_is_true_once_any_leg_is_answered(session):
    """A LOST leg counts as much as a won one -- it is the EXISTENCE of a row
    that turns the no-rows-means-all fallback off, not what the row says."""
    concert, leg_a, _leg_b, _r_a, r_both, _r_none = await seed(session)
    r_both.results_at_utc = dt(5, 28)
    await session.flush()
    await record_round_outcome(session, 42, r_both.id, LotteryOutcome.APPLIED)
    await record_round_day_result(session, 42, r_both.id, leg_a.id, LegResult.LOST, NOW)
    await session.commit()

    legs, _fallback = await concert_round_rows(session, 42, concert, now=NOW)
    row = row_for(legs, leg_a.id, "Both-legs")
    assert row.has_day_results is True
    assert row.any_day_won is False  # and so "Won (all)" has no honest meaning
    # Another round on the same concert has rows of its own to answer for.
    assert row_for(legs, leg_a.id, "A-only").has_day_results is False


async def test_no_capture_days_once_the_round_is_won_outright(session):
    """WON with no day rows is the no-rows-means-all whole-round win: there is
    nothing left unresolved, so the row moves on to the payment question
    instead of asking about days again."""
    concert, leg_a, _leg_b, _r_a, r_both, _r_none = await seed(session)
    r_both.results_at_utc = dt(5, 28)
    await session.flush()
    await record_round_outcome(session, 42, r_both.id, LotteryOutcome.APPLIED)
    await record_round_outcome(session, 42, r_both.id, LotteryOutcome.WON)
    await session.commit()

    legs, _fallback = await concert_round_rows(session, 42, concert, now=NOW)
    assert row_for(legs, leg_a.id, "Both-legs").capture_days == ()


# ── outcomes ─────────────────────────────────────────────────────────────


async def test_two_users_see_independent_standings_on_the_same_concert(session):
    concert, leg_a, *_ = await seed(session)
    legs, _fallback = await concert_round_rows(session, 42, concert, now=NOW)
    general = row_for(legs, leg_a.id, "General")
    await record_round_outcome(session, 42, general.round_.id, LotteryOutcome.APPLIED)
    await session.commit()

    mine, _f = await concert_round_rows(session, 42, concert, now=NOW)
    theirs, _f2 = await concert_round_rows(session, 99, concert, now=NOW)
    assert row_for(mine, leg_a.id, "General").outcome is LotteryOutcome.APPLIED
    assert row_for(theirs, leg_a.id, "General").outcome is None


async def test_an_anonymous_caller_gets_rows_with_no_outcome(session):
    """The concert page is reachable without a standing to show; user_id=None
    must yield rows, not an exception."""
    concert, leg_a, *_ = await seed(session)
    legs, _fallback = await concert_round_rows(session, None, concert, now=NOW)

    assert len(legs) == 2
    assert all(row.outcome is None for leg in legs for row in leg.rounds)
    assert all(row.covered is False for leg in legs for row in leg.rounds)
    assert all(row.leg_result is None for leg in legs for row in leg.rounds)
    # A gate that depends only on round timing still resolves.
    assert row_for(legs, leg_a.id, "General").can_capture is True


# ── the prominent anchor ─────────────────────────────────────────────────


async def test_the_primary_anchor_is_the_next_moment_still_ahead(session):
    concert, leg_a, *_ = await seed(session)
    session.add(Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="Full ladder",
        opens_at_utc=dt(5, 1), closes_at_utc=dt(6, 10),
        results_at_utc=dt(6, 20), payment_deadline_at_utc=dt(6, 30),
    ))
    await session.commit()

    legs, _fallback = await concert_round_rows(session, 42, concert, now=NOW)
    row = row_for(legs, leg_a.id, "Full ladder")
    # Opens is behind us; the close is the next thing that happens.
    assert row.primary_anchor is Anchor.CLOSES
    assert row.primary_at_utc == dt(6, 10)


async def test_a_wholly_past_round_falls_back_to_its_last_moment(session):
    """Nothing ahead to lead with, so the row still says something rather than
    rendering a blank date column."""
    concert, leg_a, *_ = await seed(session)
    session.add(Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="All over",
        opens_at_utc=dt(4, 1), closes_at_utc=dt(4, 20), results_at_utc=dt(5, 2),
    ))
    await session.commit()

    legs, _fallback = await concert_round_rows(session, 42, concert, now=NOW)
    row = row_for(legs, leg_a.id, "All over")
    assert row.primary_anchor is Anchor.RESULTS
    assert row.primary_at_utc == dt(5, 2)


# ── one "wants me?" rule, two row shapes ─────────────────────────────────


async def test_the_lead_rule_matches_the_concert_pages_needs_you():
    """Both surfaces answer 'does this want me?' identically for the same
    inputs -- one rule, two row shapes. No DB: the predicate is pure, and the
    point is the agreement, not the plumbing that reaches it."""
    now = NOW
    for outcome, can_capture, closes in [
        (None, True, now + timedelta(days=1)),
        (None, True, now - timedelta(days=1)),
        (None, False, None),
        (LotteryOutcome.APPLIED, True, None),
        (LotteryOutcome.WON, False, None),
        (LotteryOutcome.LOST, True, now + timedelta(days=1)),
        (LotteryOutcome.PAID, True, None),
        (LotteryOutcome.NOT_APPLIED, True, None),
    ]:
        round_ = Round(id=1, concert_id=1, kind=RoundKind.LOTTERY_ROUND,
                       label="x", closes_at_utc=closes)
        row = RoundRow(round_=round_, outcome=outcome, can_capture=can_capture,
                       can_report_result=False)
        assert _needs_you(row, now) == _wants_you(outcome, can_capture, closes, now)


async def test_covered_still_vetoes_a_round_you_applied_to(session):
    """`covered` is the concert page's OWN veto, on top of the shared rule:
    the shared rule says APPLIED wants you, and every leg being secured
    elsewhere says it does not. Generalising the predicate must not lose it --
    Home never meets this case, because `my_deadline_rows` drops covered rows
    before a block ever sees them."""
    concert, leg_a, _leg_b, r_a, r_both, _r_none = await seed(session)
    await record_round_outcome(session, 42, r_a.id, LotteryOutcome.APPLIED)
    await record_round_outcome(session, 42, r_both.id, LotteryOutcome.WON)
    await session.commit()

    legs, _fallback = await concert_round_rows(session, 42, concert, now=NOW)
    covered = row_for(legs, leg_a.id, "A-only")
    assert covered.covered is True
    assert covered.outcome is LotteryOutcome.APPLIED
    assert _wants_you(covered.outcome, covered.can_capture,
                      covered.round_.closes_at_utc, NOW) is True
    assert _needs_you(covered, NOW) is False

    rows = [row for leg in legs for row in leg.rounds]
    assert concert_next_moment(rows, now=NOW).round_.label != "A-only"
