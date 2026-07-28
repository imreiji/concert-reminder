"""Per-user board and deadline queries.

"Tracked" is derived from tag subscriptions until ConcertSubscription exists
(see service.tracked_concert_ids), so every fixture here subscribes the user
to a tag and attaches that tag to the concerts it wants on the board.
"""

from datetime import UTC, datetime, timedelta

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
    board_cards,
    discover_peek,
    discoverable_open_round_count,
    ensure_user,
    my_upcoming_deadlines,
    record_round_outcome,
    tracked_concert_ids,
)
from app.domain.board import OPEN_COLUMN_LIMIT, Column, column_for, visible_rungs
from app.domain.types import Anchor, LotteryOutcome, RoundKind, TagKind

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


async def make_tag(s, name: str, *, subscribed: bool) -> Tag:
    tag = Tag(name=name, kind=TagKind.ARTIST)
    s.add(tag)
    await s.flush()
    if subscribed:
        s.add(TagSubscription(user_id=USER, tag_id=tag.id))
        await s.flush()
    return tag


async def make_concert(s, event_id: str, tag: Tag | None = None, title: str = "") -> Concert:
    concert = Concert(title=title or event_id, event_id=event_id, created_by=USER)
    s.add(concert)
    await s.flush()
    if tag is not None:
        s.add(ConcertTag(concert_id=concert.id, tag_id=tag.id))
        await s.flush()
    return concert


async def add_round(
    s,
    concert: Concert,
    label: str,
    *,
    opens=None,
    closes=None,
    payment=None,
    applies_to=None,
    kind=RoundKind.LOTTERY_ROUND,
) -> Round:
    r = Round(
        concert_id=concert.id,
        kind=kind,
        label=label,
        opens_at_utc=opens,
        closes_at_utc=closes,
        payment_deadline_at_utc=payment,
        applies_to=applies_to,
    )
    s.add(r)
    await s.flush()
    return r


async def open_round(s, concert: Concert, label: str = "Round 1", closes=None) -> Round:
    """A round that is open at NOW: already opened, not yet closed."""
    return await add_round(s, concert, label, opens=dt(5, 20), closes=closes or dt(6, 10))


# ── tracked_concert_ids ──────────────────────────────────────────────────


async def test_tracked_ids_follow_tag_subscriptions(session):
    await ensure_user(session, USER, "reiji")
    followed = await make_tag(session, "Aqours", subscribed=True)
    ignored = await make_tag(session, "Muse", subscribed=False)
    mine = await make_concert(session, "aqours-live", followed)
    await make_concert(session, "muse-live", ignored)
    await make_concert(session, "untagged-live", None)

    assert await tracked_concert_ids(session, USER) == {mine.id}


# ── board_cards ──────────────────────────────────────────────────────────


async def test_untracked_concert_is_absent_from_the_board(session):
    await ensure_user(session, USER, "reiji")
    followed = await make_tag(session, "Aqours", subscribed=True)
    ignored = await make_tag(session, "Muse", subscribed=False)
    mine = await make_concert(session, "aqours-live", followed)
    theirs = await make_concert(session, "muse-live", ignored)
    await open_round(session, mine)
    await open_round(session, theirs)

    columns, _ = await board_cards(session, USER, now=NOW)

    placed = {card.concert.id for cards in columns.values() for card in cards}
    assert placed == {mine.id}


async def test_board_places_by_precedence(session):
    """PAID > WON > APPLIED > open. Each concert appears in exactly one column,
    chosen by its most advanced outcome -- not by its most recent one."""
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", subscribed=True)

    # Open only: no outcome anywhere.
    c_open = await make_concert(session, "open-one", tag)
    await open_round(session, c_open)

    # APPLIED on a closed round, plus a still-open later round.
    c_applied = await make_concert(session, "applied-one", tag)
    r_applied = await add_round(session, c_applied, "R1", opens=dt(5, 1), closes=dt(5, 20))
    await open_round(session, c_applied, "R2")
    await record_round_outcome(session, USER, r_applied.id, LotteryOutcome.APPLIED, now=NOW)

    # APPLIED on R1, WON on R2, and R3 still open: WON outranks both.
    c_won = await make_concert(session, "won-one", tag)
    r1 = await add_round(session, c_won, "R1", opens=dt(4, 1), closes=dt(4, 20))
    r2 = await add_round(session, c_won, "R2", opens=dt(5, 1), closes=dt(5, 20))
    await open_round(session, c_won, "R3")
    await record_round_outcome(session, USER, r1.id, LotteryOutcome.APPLIED, now=NOW)
    await record_round_outcome(session, USER, r2.id, LotteryOutcome.WON, now=NOW)

    # PAID (reached through WON, the only legal path) outranks everything.
    c_paid = await make_concert(session, "paid-one", tag)
    r_paid = await add_round(session, c_paid, "R1", opens=dt(4, 1), closes=dt(4, 20))
    await record_round_outcome(session, USER, r_paid.id, LotteryOutcome.WON, now=NOW)
    await record_round_outcome(session, USER, r_paid.id, LotteryOutcome.PAID, now=NOW)

    # NOT_APPLIED everywhere and nothing open: off the board entirely.
    c_absent = await make_concert(session, "absent-one", tag)
    r_absent = await add_round(session, c_absent, "R1", opens=dt(4, 1), closes=dt(4, 20))
    await record_round_outcome(session, USER, r_absent.id, LotteryOutcome.NOT_APPLIED, now=NOW)

    columns, _ = await board_cards(session, USER, now=NOW)
    by_column = {col: {card.concert.event_id for card in cards} for col, cards in columns.items()}

    assert by_column[Column.OPEN] == {"open-one"}
    assert by_column[Column.APPLIED] == {"applied-one"}
    assert by_column[Column.WON] == {"won-one"}
    assert by_column[Column.SECURED] == {"paid-one"}
    # Every concert lands in one column at most, and the NOT_APPLIED one in none.
    placed = [eid for eids in by_column.values() for eid in eids]
    assert sorted(placed) == ["applied-one", "open-one", "paid-one", "won-one"]


async def test_open_column_is_capped(session):
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", subscribed=True)
    for i in range(15):
        c = await make_concert(session, f"open-{i}", tag)
        await open_round(session, c)

    columns, open_total = await board_cards(session, USER, now=NOW)

    assert len(columns[Column.OPEN]) == OPEN_COLUMN_LIMIT == 12
    assert open_total == 15  # pre-cap, so the template can say "+3 more"


async def test_open_column_cap_orders_by_soonest_deadline(session):
    """The cap keeps the 12 SOONEST, not the 12 first created -- so the twelve
    kept must be the ones closing first, in order."""
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", subscribed=True)
    # Created latest-closing first, so insertion order is the reverse of the
    # expected order and cannot accidentally produce a passing assertion.
    for i in reversed(range(15)):
        c = await make_concert(session, f"open-{i:02d}", tag)
        await open_round(session, c, closes=dt(6, 2 + i))

    columns, open_total = await board_cards(session, USER, now=NOW)

    assert open_total == 15
    assert [card.concert.event_id for card in columns[Column.OPEN]] == [
        f"open-{i:02d}" for i in range(12)
    ]


async def test_rungs_mark_lost_rounds_and_the_live_one(session):
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", subscribed=True)
    concert = await make_concert(session, "ladder", tag)
    lost = await add_round(session, concert, "R1", opens=dt(4, 1), closes=dt(4, 20))
    live = await open_round(session, concert, "R2")
    future = await add_round(session, concert, "R3", opens=dt(7, 1), closes=dt(7, 20))
    await record_round_outcome(session, USER, lost.id, LotteryOutcome.LOST, now=NOW)

    columns, _ = await board_cards(session, USER, now=NOW)

    # A lost round is not an end state, so the concert sits in Open now.
    (card,) = columns[Column.OPEN]
    assert [(r.round_id, r.state) for r in card.rungs] == [
        (lost.id, "lost"),
        (live.id, "live"),
        (future.id, "todo"),
    ]
    assert [r.label for r in card.rungs] == ["R1", "R2", "R3"]
    assert card.outcome_by_round == {lost.id: LotteryOutcome.LOST}


async def test_rung_states_follow_recorded_outcomes(session):
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", subscribed=True)
    concert = await make_concert(session, "outcomes", tag)
    applied = await add_round(session, concert, "R1", opens=dt(4, 1), closes=dt(4, 20))
    won = await add_round(session, concert, "R2", opens=dt(5, 1), closes=dt(5, 20))
    await record_round_outcome(session, USER, applied.id, LotteryOutcome.APPLIED, now=NOW)
    await record_round_outcome(session, USER, won.id, LotteryOutcome.WON, now=NOW)

    columns, _ = await board_cards(session, USER, now=NOW)
    (card,) = columns[Column.WON]

    assert [(r.round_id, r.state) for r in card.rungs] == [
        (applied.id, "applied"),
        (won.id, "won"),
    ]


async def test_a_declined_round_gets_its_own_rung_state(session):
    """"Not applying" is not "not yet open".

    Mapping NOT_APPLIED onto "todo" made a declined, already-closed round
    indistinguishable from a round that has not opened -- so the capped ladder
    offered it as the card's "what's next" and hid the round the user could
    actually still enter behind the fold, while the concert page counted the
    same round under its "skipped" fold chip. Two declutter surfaces, two
    stories about one round. `skipped` is the state that makes them agree."""
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", subscribed=True)
    concert = await make_concert(session, "declined-ladder", tag)
    applied = await add_round(session, concert, "R1", opens=dt(4, 1), closes=dt(4, 20))
    # Declined and already closed, and it OPENED BEFORE the live round below --
    # so position alone would hand it the lookahead slot.
    declined = await add_round(session, concert, "R2", opens=dt(5, 1), closes=dt(5, 20))
    live = await open_round(session, concert, "R3")
    await record_round_outcome(session, USER, applied.id, LotteryOutcome.APPLIED, now=NOW)
    await record_round_outcome(session, USER, declined.id, LotteryOutcome.NOT_APPLIED, now=NOW)

    columns, _ = await board_cards(session, USER, now=NOW)
    (card,) = columns[Column.APPLIED]

    assert [(r.round_id, r.state) for r in card.rungs] == [
        (applied.id, "applied"),
        (declined.id, "skipped"),
        (live.id, "live"),
    ]
    # The cap: the APPLIED rung explains the column, and what is next is the
    # OPEN round -- never the one the user has already said no to.
    visible, hidden = visible_rungs(card.rungs)
    assert [r.round_id for _, r in visible] == [applied.id, live.id]
    assert hidden == 1


async def test_board_card_state_rung_agrees_with_the_cards_own_column(session):
    """End-to-end wiring, not ladder arithmetic: the pure tests hand-build
    rungs, so nothing pinned that `board_cards` feeds `visible_rungs` a ladder
    whose surviving state rung actually explains the column `board_cards`
    computed. The mid-campaign shape is where those two can disagree -- a WON
    round followed by an open one places the card in "Won -- pay" while
    position alone would surface the open round."""
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", subscribed=True)
    concert = await make_concert(session, "wired", tag)
    lost = await add_round(session, concert, "R1", opens=dt(3, 1), closes=dt(3, 20))
    won = await add_round(session, concert, "R2", opens=dt(4, 1), closes=dt(4, 20))
    await open_round(session, concert, "R3")
    await record_round_outcome(session, USER, lost.id, LotteryOutcome.LOST, now=NOW)
    await record_round_outcome(session, USER, won.id, LotteryOutcome.WON, now=NOW)

    columns, _ = await board_cards(session, USER, now=NOW)
    (card,) = columns[Column.WON]

    visible, _hidden = visible_rungs(card.rungs)
    state_rung = visible[0][1]
    assert card.column is column_for(
        [(card.outcome_by_round[state_rung.round_id], state_rung.is_upgrade)],
        has_open_round=False,
    )
    assert state_rung.round_id == won.id


async def test_a_rung_carries_the_rounds_upgrade_kind(session):
    """`is_upgrade` is what lets `visible_rungs` prefer a won-but-unpaid
    UPGRADE over a PAID base ticket -- "won" reads the same either way, so the
    flag is the only thing carrying the distinction. Nothing else in the suite
    touches the CONSTRUCTION site, so deleting the kwarg in `board_cards` left
    the whole suite green while the card went back to naming the paid rung."""
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", subscribed=True)
    concert = await make_concert(session, "upgrade-ladder", tag)
    base = await add_round(session, concert, "R1", opens=dt(4, 1), closes=dt(4, 20))
    upgrade = await add_round(
        session, concert, "Seat upgrade", opens=dt(5, 1), closes=dt(5, 20),
        kind=RoundKind.UPGRADE,
    )
    await record_round_outcome(session, USER, base.id, LotteryOutcome.WON, now=NOW)

    columns, _ = await board_cards(session, USER, now=NOW)
    (card,) = columns[Column.WON]

    assert {r.round_id: r.is_upgrade for r in card.rungs} == {
        base.id: False,
        upgrade.id: True,
    }


async def test_board_card_pill_tone_follows_urgency_not_just_column(session):
    """Two OPEN cards with different time left must get different pill tones
    -- before this, the template hardcoded the tone off the column alone
    (won vs everything else), so two OPEN cards always looked identical
    regardless of how much time was actually left."""
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", subscribed=True)

    urgent = await make_concert(session, "urgent-one", tag)
    await open_round(session, urgent, closes=NOW + timedelta(hours=6))

    distant = await make_concert(session, "distant-one", tag)
    await open_round(session, distant, closes=NOW + timedelta(days=20))

    columns, _ = await board_cards(session, USER, now=NOW)
    tones = {card.concert.event_id: card.pill_tone for card in columns[Column.OPEN]}

    assert tones["urgent-one"] == "p-danger"
    assert tones["distant-one"] == "p-quiet"


# ── a fully cancelled concert on the board ───────────────────────────────
#
# Two rules, both concert-level. A dead concert passes has_open_round=False
# into column_for -- with no standing that returns None and the existing
# "column is None -> continue" drops the card, and with standing the outcome
# ranks place it, so it can never land in Open now. And the card's rounds are
# ALL its rounds, not the is_round_cancelled survivors: on a dead concert
# every leg-bound round is implicitly cancelled, so filtering them out would
# throw away the very standing the card exists to record.


async def make_dead_leg(s, concert: Concert, label: str = "Day 1") -> ConcertDay:
    """A cancelled leg. A concert whose every leg is one of these is dead."""
    day = ConcertDay(
        concert_id=concert.id, label=label, starts_at_utc=dt(8, 1), cancelled=True
    )
    s.add(day)
    await s.flush()
    return day


async def test_a_dead_concert_with_no_standing_leaves_the_board(session):
    """No stake in it, no reason to see it: the card goes, exactly as Discover
    already hides the concert. The round here is a GENERAL one (no
    applies_to), which survives is_round_cancelled and is precisely why the
    concert-level rule is needed -- without it this card sits in Open now,
    inviting an application to a show that is not happening."""
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", subscribed=True)

    dead = await make_concert(session, "dead-one", tag)
    await make_dead_leg(session, dead)
    await open_round(session, dead, "General sale")

    alive = await make_concert(session, "alive-one", tag)
    await open_round(session, alive)

    columns, open_total = await board_cards(session, USER, now=NOW)

    assert [c.concert.event_id for c in columns[Column.OPEN]] == ["alive-one"]
    assert open_total == 1
    placed = {c.concert.event_id for cards in columns.values() for c in cards}
    assert placed == {"alive-one"}


async def test_a_dead_concert_you_won_stays_badged(session):
    """A cancelled show you hold a ticket for is news, not noise -- so the card
    stays, marked, and in the column its standing earned."""
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", subscribed=True)

    dead = await make_concert(session, "dead-won", tag)
    await make_dead_leg(session, dead)
    won = await open_round(session, dead, "General sale")
    await record_round_outcome(session, USER, won.id, LotteryOutcome.WON, now=NOW)

    columns, _ = await board_cards(session, USER, now=NOW)

    assert [c.concert.event_id for c in columns[Column.WON]] == ["dead-won"]
    card = columns[Column.WON][0]
    assert card.cancelled is True
    assert card.column is Column.WON
    # The round is open right now; standing is what places the card, and a
    # dead concert must never be offered as somewhere to apply.
    assert columns[Column.OPEN] == []


async def test_a_dead_concert_you_applied_to_stays_in_applied(session):
    """The same rule one rung lower: an application already lodged is standing
    too, so the card survives -- in Applied, not in Open now."""
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", subscribed=True)

    dead = await make_concert(session, "dead-applied", tag)
    await make_dead_leg(session, dead)
    applied = await open_round(session, dead, "General sale")
    await record_round_outcome(session, USER, applied.id, LotteryOutcome.APPLIED, now=NOW)

    columns, open_total = await board_cards(session, USER, now=NOW)

    assert [c.concert.event_id for c in columns[Column.APPLIED]] == ["dead-applied"]
    assert columns[Column.APPLIED][0].cancelled is True
    assert columns[Column.OPEN] == []
    assert open_total == 0


async def test_a_concert_with_one_live_leg_is_untouched(session):
    """The every-leg rule must not over-fire: one surviving leg means the show
    is still happening, so the card keeps its Open now place and no badge."""
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", subscribed=True)

    concert = await make_concert(session, "tour", tag)
    await make_dead_leg(session, concert, "Leg A")
    live_leg = ConcertDay(concert_id=concert.id, label="Leg B", starts_at_utc=dt(8, 2))
    session.add(live_leg)
    await session.flush()
    await open_round(session, concert, "General sale")

    columns, open_total = await board_cards(session, USER, now=NOW)

    assert [c.concert.event_id for c in columns[Column.OPEN]] == ["tour"]
    assert columns[Column.OPEN][0].cancelled is False
    assert open_total == 1


async def test_leg_bound_standing_on_a_dead_concert_keeps_a_badged_card(session):
    """The motivating case, and the COMMON one: a 先行 lottery names the leg it
    is for, so when that leg is cancelled the round is implicitly cancelled
    too. Filtering the card's rounds by is_round_cancelled would drop the
    winning round -- and with it the standing -- leaving the reader who holds
    a ticket with no card at all. On a dead concert the card is built from
    every round, so the win survives and brings its rung with it."""
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", subscribed=True)

    dead = await make_concert(session, "dead-legbound", tag)
    leg = await make_dead_leg(session, dead)
    won = await add_round(
        session, dead, "1次先行", opens=dt(4, 1), closes=dt(4, 20), applies_to=[leg.id]
    )
    await record_round_outcome(session, USER, won.id, LotteryOutcome.WON, now=NOW)

    columns, _ = await board_cards(session, USER, now=NOW)

    assert [c.concert.event_id for c in columns[Column.WON]] == ["dead-legbound"]
    card = columns[Column.WON][0]
    assert card.cancelled is True
    # The rung that EXPLAINS the column must be on the card: a card in "Won --
    # pay" whose ladder does not contain the win names a column nothing on it
    # explains (see domain.board.visible_rungs).
    assert [(r.round_id, r.state) for r in card.rungs] == [(won.id, "won")]
    assert card.outcome_by_round == {won.id: LotteryOutcome.WON}


async def test_leg_bound_standing_survives_an_open_general_round(session):
    """The shape that must not regress: leg-bound standing PLUS a General
    round still open. The General round is the only one is_round_cancelled
    spares, so a live-rounds-only card would be placed by nothing but that
    round -- landing in Open now while the application already lodged went
    unmentioned. The card keeps both rungs and the standing decides."""
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", subscribed=True)

    dead = await make_concert(session, "dead-mixed", tag)
    leg = await make_dead_leg(session, dead)
    applied = await add_round(
        session, dead, "1次先行", opens=dt(4, 1), closes=dt(4, 20), applies_to=[leg.id]
    )
    general = await open_round(session, dead, "General sale")
    await record_round_outcome(session, USER, applied.id, LotteryOutcome.APPLIED, now=NOW)

    columns, open_total = await board_cards(session, USER, now=NOW)

    assert [c.concert.event_id for c in columns[Column.APPLIED]] == ["dead-mixed"]
    assert columns[Column.OPEN] == []
    assert open_total == 0
    card = columns[Column.APPLIED][0]
    assert card.cancelled is True
    # Both rungs, in ladder order. The General round reads "todo", not "live":
    # a dead concert has no open round, whatever its rounds' timestamps say --
    # the same claim the suppressed countdown stops the card making.
    assert [(r.round_id, r.state) for r in card.rungs] == [
        (applied.id, "applied"),
        (general.id, "todo"),
    ]


async def test_a_dead_concert_you_paid_for_stays_in_secured(session):
    """The top rung of the ladder, and the one the reader has most at stake
    in: a paid ticket to a show that is not happening."""
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", subscribed=True)

    dead = await make_concert(session, "dead-paid", tag)
    leg = await make_dead_leg(session, dead)
    paid = await add_round(
        session, dead, "1次先行", opens=dt(4, 1), closes=dt(4, 20), applies_to=[leg.id]
    )
    await record_round_outcome(session, USER, paid.id, LotteryOutcome.WON, now=NOW)
    await record_round_outcome(session, USER, paid.id, LotteryOutcome.PAID, now=NOW)

    columns, _ = await board_cards(session, USER, now=NOW)

    assert [c.concert.event_id for c in columns[Column.SECURED]] == ["dead-paid"]
    card = columns[Column.SECURED][0]
    assert card.cancelled is True
    assert [(r.round_id, r.state) for r in card.rungs] == [(paid.id, "paid")]


async def test_a_dead_card_counts_down_to_nothing(session):
    """A badged card must not also read "closes in 3 days". The deadline is
    dropped at the source rather than hidden in the template, so nothing
    downstream -- the pill, its tone, the card ordering -- can resurrect it."""
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", subscribed=True)

    dead = await make_concert(session, "dead-soon", tag)
    await make_dead_leg(session, dead)
    soon = await add_round(session, dead, "General sale", closes=NOW + timedelta(hours=6))
    await record_round_outcome(session, USER, soon.id, LotteryOutcome.APPLIED, now=NOW)

    columns, _ = await board_cards(session, USER, now=NOW)
    card = columns[Column.APPLIED][0]

    assert card.next_deadline is None
    # No deadline, no urgency: pill_tone must not read the missing moment as
    # imminent.
    assert card.pill_tone == "p-quiet"


# ── my_upcoming_deadlines ────────────────────────────────────────────────


async def test_my_deadlines_exclude_untracked_concerts(session):
    await ensure_user(session, USER, "reiji")
    followed = await make_tag(session, "Aqours", subscribed=True)
    ignored = await make_tag(session, "Muse", subscribed=False)
    mine = await make_concert(session, "aqours-live", followed, title="Aqours Live")
    theirs = await make_concert(session, "muse-live", ignored, title="Muse Live")
    await add_round(session, mine, "R1", opens=dt(6, 5), closes=dt(6, 20))
    await add_round(session, theirs, "R1", opens=dt(6, 2), closes=dt(6, 10))

    rows = await my_upcoming_deadlines(session, USER, now=NOW)

    assert {row.event_id for row in rows} == {"aqours-live"}
    # The untracked concert's earlier deadline would otherwise sort first.
    assert [row.anchor for row in rows] == [Anchor.OPENS, Anchor.CLOSES]


async def test_my_deadlines_exclude_cancelled_legs(session):
    """Cancelled-leg handling is upcoming_deadlines' job (it reuses
    is_round_cancelled); this asserts my_upcoming_deadlines inherits it rather
    than reimplementing it."""
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", subscribed=True)
    concert = await make_concert(session, "tour", tag, title="Tour")
    leg_a = ConcertDay(
        concert_id=concert.id, label="Leg A", starts_at_utc=dt(8, 1), cancelled=True
    )
    leg_b = ConcertDay(concert_id=concert.id, label="Leg B", starts_at_utc=dt(8, 2))
    session.add_all([leg_a, leg_b])
    await session.flush()
    await add_round(session, concert, "Leg A round", closes=dt(6, 5), applies_to=[leg_a.id])
    await add_round(session, concert, "Leg B round", closes=dt(6, 6), applies_to=[leg_b.id])

    rows = await my_upcoming_deadlines(session, USER, now=NOW)

    assert [row.label for row in rows] == ["Leg B round", "Leg B"]
    assert [row.anchor for row in rows] == [Anchor.CLOSES, Anchor.EVENT_START]


async def test_my_deadlines_respect_the_limit(session):
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", subscribed=True)
    concert = await make_concert(session, "many", tag)
    for i in range(5):
        await add_round(session, concert, f"R{i}", closes=dt(6, 2 + i))

    rows = await my_upcoming_deadlines(session, USER, now=NOW, limit=3)

    assert [row.label for row in rows] == ["R0", "R1", "R2"]


# ── discoverable_open_round_count: the teaser's "N with a round still open" ──


async def test_open_round_count_counts_only_concerts_with_a_round_open_now(session):
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", subscribed=True)
    open_now = await make_concert(session, "open-now", tag)
    await open_round(session, open_now)
    not_open_yet = await make_concert(session, "not-yet", tag)
    await add_round(session, not_open_yet, "R1", opens=dt(7, 1), closes=dt(7, 20))
    already_closed = await make_concert(session, "closed", tag)
    await add_round(session, already_closed, "R1", opens=dt(4, 1), closes=dt(4, 20))

    assert await discoverable_open_round_count(session, now=NOW) == 1


# ── discover_peek: Home's 4 sample Discover cards ────────────────────────


async def test_peek_excludes_the_callers_tracked_concerts(session):
    await ensure_user(session, USER, "reiji")
    tag = await make_tag(session, "Aqours", subscribed=True)
    tracked = await make_concert(session, "tracked-one", tag)
    await make_concert(session, "untracked-one", None)

    peek = await discover_peek(session, {tracked.id}, limit=4)

    assert [c.event_id for c in peek] == ["untracked-one"]


async def test_peek_respects_the_limit(session):
    await ensure_user(session, USER, "reiji")
    for i in range(6):
        await make_concert(session, f"peek-{i}", None)

    peek = await discover_peek(session, set(), limit=4)

    assert len(peek) == 4
