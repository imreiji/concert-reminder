"""First-run capture flow (/setup): the read shapes, the eligibility
predicate, and the two batch writes -- all in db/service.py.

The flow consumes branch 4's ConcertSubscription override API
(`tracked_concert_ids`, `set_concert_subscription`,
`clear_concert_subscription`, `concert_subscription_states`) and the
existing `record_round_outcome`. It adds no schema of its own.
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
    ConcertTag,
    ReminderQueue,
    ReminderRule,
    Round,
    RoundOutcome,
    Tag,
    TagSubscription,
)
from app.db.service import (
    _round_asks_application,
    apply_prune_selection,
    concert_subscription_states,
    ensure_user,
    record_round_outcome,
    record_setup_applications,
    set_concert_subscription,
    setup_application_rows,
    setup_prune_tiles,
    setup_tallies,
    sync_rule,
)
from app.domain.types import Anchor, LotteryOutcome, RoundKind, SubscriptionState, TagKind

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


async def test_setup_never_asks_about_a_dead_concerts_round(session):
    """A concert whose every leg is cancelled is off, and `/setup` must not
    offer to record an application against it -- recording APPLIED is
    irreversible (`record_round_outcome` refuses to overwrite a starting
    state), so the question costs the reader something to answer wrongly.

    `is_round_cancelled` does not cover this: a General round names no leg, so
    it rightly survives that per-round predicate and is exactly the round that
    would keep asking. The concert-level fact comes from `all_legs_cancelled`,
    fed into `_tracked_upcoming_concerts` -- the flow's ONE upcoming filter, so
    all three screens agree, rather than screen 2 alone learning the rule."""
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", user=USER)
    concert = await make_concert(session, "aqours-9th", tag)
    await add_day(session, concert, dt(8, 1), cancelled=True)
    await add_day(session, concert, dt(8, 2), cancelled=True)
    # No applies_to: a General round, tied to no leg and therefore live by
    # is_round_cancelled's reckoning, with a future close to keep it "upcoming".
    await add_round(
        session, concert, "General sale", opens_at_utc=dt(5, 20), closes_at_utc=dt(6, 20)
    )

    assert await setup_application_rows(session, USER, now=NOW) == []
    # And the same filter takes it out of screen 1: a tile inviting a prune of
    # something already inert, captioned with a moment that will not happen.
    assert await setup_prune_tiles(session, USER, now=NOW) == []


async def test_setup_skips_covered_round(session):
    """A round every one of whose legs the user already secured through
    another round asks nothing: they are going, and "did you apply?" has no
    useful answer left. Same "stop asking" definition the reminder planner
    uses -- /setup must not contradict it."""
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", user=USER)
    concert = await make_concert(session, "aqours-9th", tag)
    await add_day(session, concert, dt(8, 1))
    won = await add_round(
        session, concert, "Lottery R1", opens_at_utc=dt(5, 1), closes_at_utc=dt(5, 10),
        results_at_utc=dt(5, 20),
    )
    await add_round(
        session, concert, "Lottery R2", opens_at_utc=dt(5, 20), closes_at_utc=dt(6, 20)
    )

    rows = await setup_application_rows(session, USER, now=NOW)
    assert [r.round_.label for r in rows] == ["Lottery R2"]

    await record_round_outcome(session, USER, won.id, LotteryOutcome.WON, NOW)

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


# ── apply_prune_selection ────────────────────────────────────────────────


async def test_prune_selection_writes_optout_for_unchecked(session):
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", user=USER)
    a = await make_concert(session, "aqours-a", tag)
    await add_round(session, a, closes_at_utc=dt(6, 20))
    b = await make_concert(session, "aqours-b", tag)
    await add_round(session, b, closes_at_utc=dt(6, 21))

    pruned, unpruned = await apply_prune_selection(
        session, USER, shown_ids={a.id, b.id}, keep_ids={a.id}, now=NOW
    )
    assert (pruned, unpruned) == (1, 0)

    states = await concert_subscription_states(session, USER)
    assert states.get(b.id) is SubscriptionState.OPTED_OUT
    tiles = {t.concert.id: t.kept for t in await setup_prune_tiles(session, USER, now=NOW)}
    assert tiles[b.id] is False and tiles[a.id] is True


async def test_prune_selection_clears_override_for_rechecked(session):
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", user=USER)
    a = await make_concert(session, "aqours-a", tag)
    await add_round(session, a, closes_at_utc=dt(6, 20))
    await set_concert_subscription(session, USER, a.id, SubscriptionState.OPTED_OUT)

    pruned, unpruned = await apply_prune_selection(
        session, USER, shown_ids={a.id}, keep_ids={a.id}, now=NOW
    )
    assert (pruned, unpruned) == (0, 1)
    assert await concert_subscription_states(session, USER) == {}
    tiles = {t.concert.id: t.kept for t in await setup_prune_tiles(session, USER, now=NOW)}
    assert tiles[a.id] is True


async def test_prune_selection_ignores_untracked_ids(session):
    await ensure_user(session, USER, "reiji")
    await ensure_user(session, OTHER, "other")
    tag = await make_tag(session, "Aqours", user=USER)
    a = await make_concert(session, "aqours-a", tag)
    await add_round(session, a, closes_at_utc=dt(6, 20))
    # A concert USER does not track at all (no matching tag, no override).
    foreign = await make_concert(session, "foreign")
    await add_round(session, foreign, closes_at_utc=dt(6, 20))

    pruned, unpruned = await apply_prune_selection(
        session, USER, shown_ids={a.id, foreign.id, 9999}, keep_ids={a.id}, now=NOW
    )
    assert (pruned, unpruned) == (0, 0)
    assert await concert_subscription_states(session, USER) == {}


async def test_prune_selection_is_idempotent(session):
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", user=USER)
    a = await make_concert(session, "aqours-a", tag)
    await add_round(session, a, closes_at_utc=dt(6, 20))
    b = await make_concert(session, "aqours-b", tag)
    await add_round(session, b, closes_at_utc=dt(6, 21))

    first = await apply_prune_selection(session, USER, {a.id, b.id}, {a.id}, now=NOW)
    second = await apply_prune_selection(session, USER, {a.id, b.id}, {a.id}, now=NOW)
    assert first == (1, 0)
    assert second == (0, 0)


async def test_prune_writes_resync_reminders(session):
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", user=USER)
    b = await make_concert(session, "aqours-b", tag)
    round_ = await add_round(session, b, closes_at_utc=dt(6, 25))
    rule = ReminderRule(user_id=USER, round_id=round_.id, anchor=Anchor.CLOSES, offset_days=0)
    session.add(rule)
    await session.flush()
    await sync_rule(session, rule, NOW)
    assert list((await session.execute(
        select(ReminderQueue).where(ReminderQueue.rule_id == rule.id)
    )).scalars()) != []

    await apply_prune_selection(session, USER, {b.id}, set(), now=NOW)

    assert list((await session.execute(
        select(ReminderQueue).where(ReminderQueue.rule_id == rule.id)
    )).scalars()) == []


# ── record_setup_applications ────────────────────────────────────────────


async def test_setup_applications_records_applied(session):
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", user=USER)
    concert = await make_concert(session, "aqours-9th", tag)
    round_ = await add_round(
        session, concert, "R1", opens_at_utc=dt(5, 20), closes_at_utc=dt(6, 20)
    )

    count = await record_setup_applications(session, USER, {round_.id}, now=NOW)
    assert count == 1

    outcome = (await session.execute(
        select(RoundOutcome).where(
            RoundOutcome.user_id == USER, RoundOutcome.round_id == round_.id
        )
    )).scalar_one()
    assert outcome.outcome is LotteryOutcome.APPLIED
    assert await setup_application_rows(session, USER, now=NOW) == []


async def test_setup_applications_skips_unchecked(session):
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", user=USER)
    concert = await make_concert(session, "aqours-9th", tag)
    await add_round(session, concert, "R1", opens_at_utc=dt(5, 20), closes_at_utc=dt(6, 20))

    count = await record_setup_applications(session, USER, set(), now=NOW)
    assert count == 0
    assert (await session.execute(select(RoundOutcome))).scalars().all() == []


async def test_setup_applications_ignores_forged_decided_round(session):
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", user=USER)
    concert = await make_concert(session, "aqours-9th", tag)
    await add_day(session, concert, dt(8, 1))
    decided = await add_round(
        session, concert, "R1", closes_at_utc=dt(5, 10), results_at_utc=dt(5, 20)
    )

    count = await record_setup_applications(session, USER, {decided.id}, now=NOW)
    assert count == 0
    assert (await session.execute(select(RoundOutcome))).scalars().all() == []


async def test_setup_applications_never_overwrites(session):
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", user=USER)
    concert = await make_concert(session, "aqours-9th", tag)
    round_ = await add_round(
        session, concert, "R1", opens_at_utc=dt(5, 20), closes_at_utc=dt(6, 20)
    )
    await record_round_outcome(session, USER, round_.id, LotteryOutcome.WON, NOW)

    count = await record_setup_applications(session, USER, {round_.id}, now=NOW)
    assert count == 0
    outcome = (await session.execute(
        select(RoundOutcome).where(RoundOutcome.round_id == round_.id)
    )).scalar_one()
    assert outcome.outcome is LotteryOutcome.WON
