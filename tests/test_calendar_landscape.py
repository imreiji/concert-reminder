"""user_calendar_events as the standing-aware landscape (spec 2026-08-04).

Shows + live deadlines over TRACKED concerts, selected by the user's standing
-- reminder rules play no part. Every exclusion is a shared helper the other
read surfaces already use; these tests pin the derivation, not the helpers.
"""

from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Concert, ConcertDay, Round, RoundQualifier
from app.db.service import (
    ensure_user,
    record_round_outcome,
    set_concert_subscription,
    set_leg_opt_out,
    user_calendar_events,
)
from app.domain.types import Anchor, LotteryOutcome, RoundKind, SubscriptionState

USER = 42
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


async def make_tracked_concert(s, event_id: str = "c") -> Concert:
    await ensure_user(s, USER, "reiji")
    concert = Concert(title=event_id, event_id=event_id, created_by=USER)
    s.add(concert)
    await s.flush()
    await set_concert_subscription(s, USER, concert.id, SubscriptionState.SUBSCRIBED)
    return concert


async def make_day(s, concert: Concert, label: str, starts=None, cancelled=False) -> ConcertDay:
    day = ConcertDay(
        concert_id=concert.id, label=label,
        starts_at_utc=starts or dt(8, 1, 9), cancelled=cancelled,
    )
    s.add(day)
    await s.flush()
    return day


async def make_round(s, concert: Concert, label: str = "R1", applies_to=None, *,
                     kind=RoundKind.LOTTERY_ROUND, opens=None, closes=None,
                     results=None, payment=None) -> Round:
    round_ = Round(
        concert_id=concert.id, kind=kind, label=label, applies_to=applies_to,
        opens_at_utc=opens, closes_at_utc=closes,
        results_at_utc=results, payment_deadline_at_utc=payment,
    )
    s.add(round_)
    await s.flush()
    return round_


def moments(events, label):
    return {(e.anchor, e.at_utc) for e in events if e.label == label}


async def test_untracked_concert_contributes_nothing(session):
    await ensure_user(session, USER, "reiji")
    concert = Concert(title="x", event_id="x", created_by=USER)
    session.add(concert)
    await session.flush()
    await make_day(session, concert, "Leg A")
    await make_round(session, concert, closes=dt(6, 25))

    assert await user_calendar_events(session, USER, NOW) == []


async def test_show_dates_for_live_legs_only(session):
    concert = await make_tracked_concert(session)
    await make_day(session, concert, "Leg A")
    await make_day(session, concert, "Cancelled", cancelled=True)
    await make_day(session, concert, "Past", starts=dt(5, 1, 9))
    b = await make_day(session, concert, "Opted out")
    await set_leg_opt_out(session, USER, b.id, True, now=NOW)

    events = await user_calendar_events(session, USER, NOW)
    assert {e.label for e in events} == {"Leg A"}
    assert events[0].anchor is Anchor.EVENT_START
    assert events[0].at_utc == dt(8, 1, 9)


async def test_no_outcome_round_contributes_opens_and_closes(session):
    concert = await make_tracked_concert(session)
    await make_round(session, concert, opens=dt(6, 10), closes=dt(6, 25),
                     results=dt(6, 28), payment=dt(6, 30))

    events = await user_calendar_events(session, USER, NOW)
    assert moments(events, "R1") == {(Anchor.OPENS, dt(6, 10)), (Anchor.CLOSES, dt(6, 25))}


async def test_applied_round_contributes_its_result_moment(session):
    concert = await make_tracked_concert(session)
    r = await make_round(session, concert, opens=dt(5, 10), closes=dt(5, 25),
                         results=dt(6, 28), payment=dt(7, 30))
    await record_round_outcome(session, USER, r.id, LotteryOutcome.APPLIED, now=NOW)

    events = await user_calendar_events(session, USER, NOW)
    assert moments(events, "R1") == {(Anchor.RESULTS, dt(6, 28))}


async def test_applied_round_without_results_time_falls_back_to_the_close(session):
    """_result_moment's rule: results become knowable at the close."""
    concert = await make_tracked_concert(session)
    r = await make_round(session, concert, closes=dt(6, 25))
    await record_round_outcome(session, USER, r.id, LotteryOutcome.APPLIED, now=NOW)

    events = await user_calendar_events(session, USER, NOW)
    assert moments(events, "R1") == {(Anchor.RESULTS, dt(6, 25))}


async def test_won_round_contributes_payment_only(session):
    concert = await make_tracked_concert(session)
    r = await make_round(session, concert, closes=dt(5, 25), results=dt(5, 28),
                         payment=dt(6, 30))
    await record_round_outcome(session, USER, r.id, LotteryOutcome.WON, now=NOW)

    events = await user_calendar_events(session, USER, NOW)
    assert moments(events, "R1") == {(Anchor.PAYMENT, dt(6, 30))}


async def test_settled_rounds_contribute_nothing(session):
    concert = await make_tracked_concert(session)
    for label, outcome in (
        ("Lost", LotteryOutcome.LOST),
        ("Skipped", LotteryOutcome.NOT_APPLIED),
    ):
        r = await make_round(session, concert, label, opens=dt(6, 10),
                            closes=dt(6, 25), payment=dt(6, 30))
        await record_round_outcome(session, USER, r.id, outcome, now=NOW)
    won = await make_round(session, concert, "Paid", closes=dt(5, 25), payment=dt(6, 30))
    await record_round_outcome(session, USER, won.id, LotteryOutcome.WON, now=NOW)
    await record_round_outcome(session, USER, won.id, LotteryOutcome.PAID, now=NOW)

    assert await user_calendar_events(session, USER, NOW) == []


async def test_fully_opted_out_round_contributes_nothing_partial_survives(session):
    concert = await make_tracked_concert(session)
    a = await make_day(session, concert, "Leg A", starts=dt(5, 1))  # past: no show event
    b = await make_day(session, concert, "Leg B", starts=dt(5, 2))
    await make_round(session, concert, "Solo", applies_to=[a.id], closes=dt(6, 25))
    await make_round(session, concert, "Both", applies_to=[a.id, b.id], closes=dt(6, 26))
    await set_leg_opt_out(session, USER, a.id, True, now=NOW)

    events = await user_calendar_events(session, USER, NOW)
    assert {e.label for e in events} == {"Both"}


async def test_lost_round_hands_off_to_its_successor(session):
    """A LOST round contributes nothing, and the next round -- an ordinary
    no-outcome round -- contributes its own moments: the ladder stays
    visible through the round that is actually next."""
    concert = await make_tracked_concert(session)
    r1 = await make_round(session, concert, "R1", opens=dt(5, 1), closes=dt(5, 20))
    await make_round(session, concert, "R2", opens=dt(6, 10), closes=dt(6, 25))
    await record_round_outcome(session, USER, r1.id, LotteryOutcome.LOST, now=NOW)

    events = await user_calendar_events(session, USER, NOW)
    assert moments(events, "R1") == set()
    assert moments(events, "R2") == {(Anchor.OPENS, dt(6, 10)), (Anchor.CLOSES, dt(6, 25))}


async def test_covered_round_contributes_nothing(session):
    """Leg secured through round A: round B selling the same leg is covered."""
    concert = await make_tracked_concert(session)
    leg = await make_day(session, concert, "Leg A", starts=dt(8, 1))
    a = await make_round(session, concert, "A", applies_to=[leg.id], closes=dt(5, 25))
    await make_round(session, concert, "B", applies_to=[leg.id],
                     opens=dt(6, 10), closes=dt(6, 25))
    await record_round_outcome(session, USER, a.id, LotteryOutcome.WON, now=NOW)
    await record_round_outcome(session, USER, a.id, LotteryOutcome.PAID, now=NOW)

    events = await user_calendar_events(session, USER, NOW)
    assert "B" not in {e.label for e in events}
    assert "Leg A" in {e.label for e in events}  # the show itself stays


async def test_dead_concert_contributes_nothing(session):
    concert = await make_tracked_concert(session)
    await make_day(session, concert, "Leg A", cancelled=True)
    await make_round(session, concert, closes=dt(6, 25))  # General: no leg named

    assert await user_calendar_events(session, USER, NOW) == []


async def test_upgrade_round_only_when_eligible(session):
    concert = await make_tracked_concert(session)
    base = await make_round(session, concert, "Base", closes=dt(5, 25))
    up = await make_round(session, concert, "Upgrade", kind=RoundKind.UPGRADE,
                          opens=dt(6, 10), closes=dt(6, 25))
    session.add(RoundQualifier(upgrade_round_id=up.id, qualifying_round_id=base.id))
    await session.flush()

    events = await user_calendar_events(session, USER, NOW)
    assert "Upgrade" not in {e.label for e in events}

    await record_round_outcome(session, USER, base.id, LotteryOutcome.WON, now=NOW)
    events = await user_calendar_events(session, USER, NOW)
    assert moments(events, "Upgrade") == {(Anchor.OPENS, dt(6, 10)), (Anchor.CLOSES, dt(6, 25))}


async def test_events_are_future_only_and_sorted(session):
    concert = await make_tracked_concert(session)
    await make_day(session, concert, "Show", starts=dt(8, 1, 9))
    await make_round(session, concert, "Late", opens=dt(7, 10), closes=dt(7, 25))
    await make_round(session, concert, "Early", opens=dt(5, 10), closes=dt(6, 5))

    events = await user_calendar_events(session, USER, NOW)
    assert [e.at_utc for e in events] == sorted(e.at_utc for e in events)
    assert all(e.at_utc > NOW for e in events)
    assert moments(events, "Early") == {(Anchor.CLOSES, dt(6, 5))}  # past opens dropped
