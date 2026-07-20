"""First-run capture flow (/setup): the read shapes, the eligibility
predicate, and the two batch writes -- all in db/service.py.

The flow consumes branch 4's ConcertSubscription override API
(`tracked_concert_ids`, `set_concert_subscription`,
`clear_concert_subscription`, `concert_subscription_states`) and the
existing `record_round_outcome`. It adds no schema of its own.
"""

from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import (
    Base,
    Concert,
    ConcertDay,
    ConcertTag,
    Round,
    Tag,
    TagSubscription,
)
from app.db.service import (
    _round_asks_application,
    ensure_user,
    record_round_outcome,
    set_concert_subscription,
    setup_application_rows,
    setup_prune_tiles,
    setup_tallies,
)
from app.domain.types import LotteryOutcome, RoundKind, SubscriptionState, TagKind

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


async def make_tag(s, name: str, kind: TagKind = TagKind.ARTIST, *, user: int | None = None) -> Tag:
    tag = Tag(name=name, kind=kind)
    s.add(tag)
    await s.flush()
    if user is not None:
        s.add(TagSubscription(user_id=user, tag_id=tag.id))
        await s.flush()
    return tag


async def make_concert(s, event_id: str, *tags: Tag) -> Concert:
    concert = Concert(title=event_id, event_id=event_id, created_by=USER)
    s.add(concert)
    await s.flush()
    for tag in tags:
        s.add(ConcertTag(concert_id=concert.id, tag_id=tag.id))
    await s.flush()
    return concert


async def add_round(s, concert: Concert, label: str = "Round 1", **kw) -> Round:
    round_ = Round(
        concert_id=concert.id, kind=kw.pop("kind", RoundKind.LOTTERY_ROUND), label=label, **kw
    )
    s.add(round_)
    await s.flush()
    return round_


async def add_day(s, concert: Concert, starts: datetime, cancelled: bool = False) -> ConcertDay:
    day = ConcertDay(
        concert_id=concert.id, label="Day 1", starts_at_utc=starts, cancelled=cancelled
    )
    s.add(day)
    await s.flush()
    return day


# ── setup_prune_tiles ────────────────────────────────────────────────────


async def test_prune_tiles_cover_tracked_upcoming_concerts(session):
    await ensure_user(session, USER, "reiji")
    group = await make_tag(session, "Aqours", TagKind.GROUP, user=USER)
    franchise = await make_tag(session, "Love Live!", TagKind.FRANCHISE, user=USER)
    c1 = await make_concert(session, "aqours-9th", group)
    await add_round(session, c1, closes_at_utc=dt(6, 20))
    c2 = await make_concert(session, "ll-fest", franchise)
    await add_round(session, c2, closes_at_utc=dt(6, 25))

    tiles = await setup_prune_tiles(session, USER, now=NOW)

    assert {t.concert.id for t in tiles} == {c1.id, c2.id}
    assert all(t.kept for t in tiles)
    because = {name for t in tiles for name in t.because}
    assert "Aqours" in because and "Love Live!" in because


async def test_prune_tiles_exclude_past_concerts(session):
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", user=USER)
    past = await make_concert(session, "aqours-old", tag)
    await add_day(session, past, dt(5, 1))
    await add_round(session, past, closes_at_utc=dt(5, 2))

    assert await setup_prune_tiles(session, USER, now=NOW) == []


async def test_prune_tiles_include_pruned_concert_as_unkept(session):
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", user=USER)
    concert = await make_concert(session, "aqours-9th", tag)
    await add_round(session, concert, closes_at_utc=dt(6, 20))

    await set_concert_subscription(session, USER, concert.id, SubscriptionState.OPTED_OUT)

    tiles = await setup_prune_tiles(session, USER, now=NOW)
    assert len(tiles) == 1
    assert tiles[0].concert.id == concert.id
    assert tiles[0].kept is False


async def test_prune_tiles_ordered_by_next_moment(session):
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", user=USER)
    later = await make_concert(session, "later", tag)
    await add_round(session, later, closes_at_utc=dt(6, 25))
    sooner = await make_concert(session, "sooner", tag)
    await add_round(session, sooner, closes_at_utc=dt(6, 10))

    tiles = await setup_prune_tiles(session, USER, now=NOW)
    assert [t.concert.id for t in tiles] == [sooner.id, later.id]


# ── _round_asks_application / setup_application_rows ─────────────────────


async def test_asks_open_round(session):
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", user=USER)
    concert = await make_concert(session, "aqours-9th", tag)
    await add_round(session, concert, "Lottery R1", opens_at_utc=dt(5, 20), closes_at_utc=dt(6, 20))

    rows = await setup_application_rows(session, USER, now=NOW)
    assert len(rows) == 1
    assert rows[0].status == "open"
    assert rows[0].moment_utc == dt(6, 20)


async def test_asks_closed_round_awaiting_result(session):
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", user=USER)
    concert = await make_concert(session, "aqours-9th", tag)
    await add_round(
        session, concert, "Lottery R1", closes_at_utc=dt(5, 25), results_at_utc=dt(6, 20)
    )

    rows = await setup_application_rows(session, USER, now=NOW)
    assert len(rows) == 1
    assert rows[0].status == "awaiting"
    assert rows[0].moment_utc == dt(6, 20)


async def test_does_not_ask_decided_round(session):
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", user=USER)
    concert = await make_concert(session, "aqours-9th", tag)
    # A future day keeps the concert upcoming, but the round itself is decided.
    await add_day(session, concert, dt(8, 1))
    await add_round(
        session, concert, "Lottery R1", closes_at_utc=dt(5, 10), results_at_utc=dt(5, 20)
    )

    assert await setup_application_rows(session, USER, now=NOW) == []


async def test_does_not_ask_unopened_round(session):
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", user=USER)
    concert = await make_concert(session, "aqours-9th", tag)
    await add_round(session, concert, "Lottery R1", opens_at_utc=dt(6, 20), closes_at_utc=dt(6, 25))

    assert await setup_application_rows(session, USER, now=NOW) == []


async def test_does_not_ask_round_with_outcome(session):
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", user=USER)
    concert = await make_concert(session, "aqours-9th", tag)
    round_ = await add_round(
        session, concert, "Lottery R1", opens_at_utc=dt(5, 20), closes_at_utc=dt(6, 20)
    )
    await record_round_outcome(session, USER, round_.id, LotteryOutcome.NOT_APPLIED, NOW)

    assert await setup_application_rows(session, USER, now=NOW) == []


async def test_does_not_ask_rounds_of_pruned_concert(session):
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", user=USER)
    concert = await make_concert(session, "aqours-9th", tag)
    await add_round(session, concert, "Lottery R1", opens_at_utc=dt(5, 20), closes_at_utc=dt(6, 20))
    await set_concert_subscription(session, USER, concert.id, SubscriptionState.OPTED_OUT)

    assert await setup_application_rows(session, USER, now=NOW) == []


async def test_does_not_ask_cancelled_round(session):
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", user=USER)
    concert = await make_concert(session, "aqours-9th", tag)
    # Give the concert a live future day so it is still upcoming, plus a
    # cancelled day the round applies to.
    await add_day(session, concert, dt(8, 1))
    cancelled = await add_day(session, concert, dt(8, 2), cancelled=True)
    await add_round(
        session, concert, "Lottery R1", opens_at_utc=dt(5, 20), closes_at_utc=dt(6, 20),
        applies_to=[cancelled.id],
    )

    assert await setup_application_rows(session, USER, now=NOW) == []


async def test_asks_predicate_carries_branch5_hook(session):
    """The predicate is directly callable and reused by both screens; a bare
    smoke test that it agrees with setup_application_rows for one open round."""
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", user=USER)
    concert = await make_concert(session, "aqours-9th", tag)
    round_ = await add_round(
        session, concert, "Lottery R1", opens_at_utc=dt(5, 20), closes_at_utc=dt(6, 20)
    )
    assert _round_asks_application(round_, None, NOW) is True
    assert _round_asks_application(round_, LotteryOutcome.APPLIED, NOW) is False


# ── setup_tallies ────────────────────────────────────────────────────────


async def test_tallies(session):
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", user=USER)

    # Concert 1: an APPLIED round, closing in the future (soonest anchor).
    c1 = await make_concert(session, "aqours-a", tag)
    r1 = await add_round(session, c1, "R1", opens_at_utc=dt(5, 20), closes_at_utc=dt(6, 5))
    await record_round_outcome(session, USER, r1.id, LotteryOutcome.APPLIED, NOW)

    # Concert 2: a WON round with a future payment deadline.
    c2 = await make_concert(session, "aqours-b", tag)
    r2 = await add_round(
        session, c2, "R1", closes_at_utc=dt(5, 10), results_at_utc=dt(5, 20),
        payment_deadline_at_utc=dt(6, 15),
    )
    await record_round_outcome(session, USER, r2.id, LotteryOutcome.WON, NOW)

    # Concert 3: tracked-but-pruned -> not counted.
    c3 = await make_concert(session, "aqours-c", tag)
    await add_round(session, c3, "R1", closes_at_utc=dt(6, 30))
    await set_concert_subscription(session, USER, c3.id, SubscriptionState.OPTED_OUT)

    tallies = await setup_tallies(session, USER, now=NOW)
    assert tallies.tracking == 2
    assert tallies.applied == 1
    assert tallies.payment_due == 1
    assert tallies.next_deadline_utc == dt(6, 5)
    assert tallies.payment_concert is not None and tallies.payment_concert.id == c2.id
