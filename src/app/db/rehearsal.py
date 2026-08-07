"""The local rehearsal harness's data layer (`/admin/rehearsal`).

Third module of that name, and consistent with the others rather than a
collision: `domain/rehearsal.py` is the pure expectations, this is the DB half,
`web/routes/rehearsal.py` is the routes -- the same domain/db/web split the
rest of the app uses. Flag-gated; the router is not even registered in
production.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.core import (
    _covered_day_ids,
    _now,
    get_default_preset,
    handle_newly_tagged,
    notify_newly_cancelled_legs,
    set_concert_subscription,
    sync_concert,
)
from app.db.models import (
    Concert,
    ConcertDay,
    ReminderQueue,
    ReminderRule,
    Round,
    RoundOutcome,
    RoundQualifier,
    Tag,
    TagSubscription,
)
from app.db.tags import assign_tag_slug, attach_tag, find_tags_by_name_and_kind
from app.domain.rehearsal import expected_buttons
from app.domain.types import (
    Anchor,
    RoundKind,
    SubscriptionState,
    TagKind,
)
from app.i18n import loc_field

# ── Rehearsal harness (local only) ───────────────────────────────────────

# The rehearsal concert is identified by a constant event_id rather than a
# column. That is deliberate: it means the pull-forward action can only ever
# reach THIS concert's queue rows, because there is no id for a caller to
# pass. Not added to RESERVED_EVENT_IDS -- that set exists to stop collisions
# with the /concerts/new and /concerts/import routes, and there is no
# /concerts/rehearsal route.
REHEARSAL_EVENT_ID = "rehearsal"
# The ARTIST tag the seed attaches so `handle_newly_tagged` actually fires.
# Survives teardown deliberately: it is shared taxonomy, and reusing it means a
# reseed re-fires the notice (the operator has no rules on the NEW concert yet).
REHEARSAL_TAG_NAME = "リハーサル・アーティスト"


async def get_rehearsal_concert(session: AsyncSession) -> Concert | None:
    res = await session.execute(
        select(Concert).where(Concert.event_id == REHEARSAL_EVENT_ID)
    )
    return res.scalar_one_or_none()


async def teardown_rehearsal(session: AsyncSession) -> bool:
    """Delete the rehearsal concert. Returns whether one existed.

    Deletes the Concert row only and lets the existing cascades take days,
    rounds, queue rows, outcomes and audits. It never touches users, presets
    or subscriptions -- those are the operator's real local state.
    """
    concert = await get_rehearsal_concert(session)
    if concert is None:
        return False
    await session.delete(concert)
    await session.flush()
    return True


async def seed_rehearsal(
    session: AsyncSession, user_id: int, now: datetime | None = None
) -> Concert:
    """Build the canonical scenario, replacing any previous one.

    Two legs, three rounds, and one reminder rule per anchor. Anchors are set
    at realistic future distances and the rules are real, so `sync_concert`
    and the pure planner compute genuine fire times -- the harness later pulls
    those rows forward rather than fabricating them.
    """
    now = now or _now()
    await teardown_rehearsal(session)

    concert = Concert(
        event_id=REHEARSAL_EVENT_ID,
        title="リハーサル公演",
        title_en="Rehearsal Concert",
        title_zh="彩排演出",
        created_by=user_id,
    )
    session.add(concert)
    await session.flush()

    day1 = ConcertDay(
        concert_id=concert.id,
        label="Day 1",
        label_en="Day 1",
        label_zh="第一天",
        starts_at_utc=now + timedelta(days=30),
    )
    day2 = ConcertDay(
        concert_id=concert.id,
        label="Day 2",
        label_en="Day 2",
        label_zh="第二天",
        starts_at_utc=now + timedelta(days=31),
    )
    session.add_all([day1, day2])
    await session.flush()

    # R1 carries all four anchors and both legs: the whole ladder from one
    # round, and a WON on it exercises the per-day RoundOutcomeDay
    # materialization that a single-leg round never reaches.
    lottery = Round(
        concert_id=concert.id,
        kind=RoundKind.LOTTERY_ROUND,
        label="一次先行",
        label_en="1st lottery",
        label_zh="第一轮抽选",
        applies_to=[day1.id, day2.id],
        opens_at_utc=now + timedelta(days=1),
        closes_at_utc=now + timedelta(days=7),
        results_at_utc=now + timedelta(days=10),
        payment_deadline_at_utc=now + timedelta(days=14),
    )
    # R2 exists to prove SUPPRESSION: once R1 is won on Day 1, the
    # secured-elsewhere pass should silently delete this round's reminders.
    # A round that stops arriving is the hardest thing to notice by hand.
    fcfs = Round(
        concert_id=concert.id,
        kind=RoundKind.FCFS_SALE,
        label="一般発売",
        label_en="General sale",
        label_zh="一般发售",
        applies_to=[day1.id],
        opens_at_utc=now + timedelta(days=3),
        closes_at_utc=now + timedelta(days=8),
    )
    # R3 is invisible until the viewer holds a ticket -- the eligibility gate,
    # proven end to end rather than by unit test.
    upgrade = Round(
        concert_id=concert.id,
        kind=RoundKind.UPGRADE,
        label="アップグレード先行",
        label_en="Upgrade lottery",
        label_zh="升级抽选",
        applies_to=[day1.id, day2.id],
        opens_at_utc=now + timedelta(days=11),
        closes_at_utc=now + timedelta(days=13),
    )
    session.add_all([lottery, fcfs, upgrade])
    await session.flush()

    session.add(
        RoundQualifier(upgrade_round_id=upgrade.id, qualifying_round_id=lottery.id)
    )

    # A followed tag, attached HERE -- before any rule exists on this concert.
    # handle_newly_tagged skips a user who already has rules on the concert, so
    # seeding the rules first would make Start queue nothing at all and step 1
    # of the walk would demonstrate the new-event DM by not sending it.
    #
    # This is the only way the pipeline half reaches `handle_newly_tagged`, and
    # that is the fan-out worth rehearsing: it is the path that DMs every
    # follower of a tag, and the likeliest way this app ever messages the wrong
    # people. The shape catalogue can render the embed, but only this exercises
    # the delivery.
    # First match, not "the" match: names are not unique, so this asks for A tag
    # called this rather than THE one. Re-seeding must not mint a second.
    existing = await find_tags_by_name_and_kind(session, REHEARSAL_TAG_NAME, TagKind.ARTIST)
    tag = existing[0] if existing else None
    if tag is None:
        tag = Tag(
            name=REHEARSAL_TAG_NAME,
            name_en="Rehearsal Artist",
            name_zh="彩排歌手",
            kind=TagKind.ARTIST,
            created_by=user_id,
        )
        session.add(tag)
        await assign_tag_slug(session, tag)
        await session.flush()

    following = (
        await session.execute(
            select(TagSubscription).where(
                TagSubscription.user_id == user_id, TagSubscription.tag_id == tag.id
            )
        )
    ).scalar_one_or_none()
    if following is None:
        default = await get_default_preset(session, user_id)
        session.add(
            TagSubscription(
                user_id=user_id,
                tag_id=tag.id,
                notify=True,
                preset_id=default.id if default else None,
            )
        )
        await session.flush()

    newly = await attach_tag(session, concert.id, tag, expand=False)
    await handle_newly_tagged(session, concert, newly)

    # Track it explicitly TOO. The tag above already makes it tracked, but an
    # explicit subscription keeps the harness working if the operator later
    # unfollows the tag by hand mid-walk.
    await set_concert_subscription(session, user_id, concert.id, SubscriptionState.SUBSCRIBED)

    # One rule per anchor, at zero offset. The offset is irrelevant to what
    # this proves -- pull-forward moves the fire time regardless -- and zero
    # keeps the seeded plan legible in the harness's own state table.
    for anchor in (Anchor.OPENS, Anchor.CLOSES, Anchor.RESULTS, Anchor.PAYMENT):
        session.add(
            ReminderRule(
                user_id=user_id, concert_id=concert.id, anchor=anchor,
                offset_days=0, offset_hours=0,
            )
        )
    session.add(
        ReminderRule(
            user_id=user_id, concert_id=concert.id, anchor=Anchor.EVENT_START,
            offset_days=0, offset_hours=0,
        )
    )
    await session.flush()

    await sync_concert(session, concert.id)
    return concert


async def rehearsal_queue_rows(session: AsyncSession) -> list[ReminderQueue]:
    """Every queue row belonging to the rehearsal concert, soonest first.

    Scoped by joining through the concert's rounds and days rather than by an
    id the caller supplies -- see REHEARSAL_EVENT_ID's note.
    """
    concert = await get_rehearsal_concert(session)
    if concert is None:
        return []
    round_ids = set(
        (await session.execute(
            select(Round.id).where(Round.concert_id == concert.id)
        )).scalars()
    )
    day_ids = set(
        (await session.execute(
            select(ConcertDay.id).where(ConcertDay.concert_id == concert.id)
        )).scalars()
    )
    if not round_ids and not day_ids:
        return []
    res = await session.execute(
        select(ReminderQueue)
        .where(
            or_(
                ReminderQueue.round_id.in_(round_ids) if round_ids else false(),
                ReminderQueue.day_id.in_(day_ids) if day_ids else false(),
            )
        )
        .order_by(ReminderQueue.fire_at_utc)
    )
    return list(res.scalars())


async def pull_rehearsal_forward(
    session: AsyncSession, now: datetime | None = None
) -> ReminderQueue | None:
    """Rewrite the soonest UNSENT rehearsal queue row's fire time into the
    past, so the next real tick delivers it. Returns the row, or None.

    This is the only thing the harness fakes, and it fakes the wait, not the
    work: sync_rule and the pure planner already computed this row and its
    anchor. Everything downstream -- suppression, gating, the send path, the
    buttons -- runs exactly as in production.
    """
    now = now or _now()
    for row in await rehearsal_queue_rows(session):
        if row.sent_at_utc is None:
            row.fire_at_utc = now - timedelta(seconds=1)
            await session.flush()
            return row
    return None


async def cancel_rehearsal_show(
    session: AsyncSession, now: datetime | None = None
) -> int:
    """Cancel the rehearsal concert's remaining live legs and queue the notices.

    Order is load-bearing: notify_newly_cancelled_legs must run BEFORE
    sync_concert, which deletes the very queue rows it inspects to decide who
    is losing a reminder. Get it backwards and the notice is silently never
    queued -- see that function's own docstring.

    It cancels EVERY live leg rather than only the last one, and that is not
    over-reach: it is the only way this harness can show a real leg_cancelled
    DM. That notice is CONCERT-scoped by design -- notify_newly_cancelled_legs
    stays deliberately silent for anyone who still holds a live reminder
    anywhere on the concert. Cancelling Day 2 alone therefore queues nothing,
    because Day 1's EVENT_START and R1's four anchors are all still standing
    (measured: it returns 0). Emitting the notice for a half-cancelled concert
    would mean faking the one thing this button exists to demonstrate, so the
    button calls the whole show off instead. The plan's per-leg version is the
    deviation recorded in the spec.
    """
    concert = await get_rehearsal_concert(session)
    if concert is None:
        return 0
    res = await session.execute(
        select(ConcertDay)
        .where(ConcertDay.concert_id == concert.id, ConcertDay.cancelled.is_(False))
        .order_by(ConcertDay.starts_at_utc.desc())
    )
    legs = list(res.scalars())
    if not legs:
        return 0
    for leg in legs:
        leg.cancelled = True
    await session.flush()
    queued = await notify_newly_cancelled_legs(
        session, concert.id, {leg.id for leg in legs}, now
    )
    await sync_concert(session, concert.id)
    return queued


@dataclass(frozen=True)
class RehearsalRow:
    """One rehearsal queue row, as the harness page shows it."""

    queue_id: int
    anchor: Anchor
    # The round's label, or the leg's on an EVENT_START row. English: this page
    # renders on a developer machine only.
    subject: str
    fire_at_utc: datetime
    sent: bool
    # The soonest UNSENT row -- the one "Next" will pull forward.
    is_next: bool
    # What a correct DM for that row should carry, per domain/rehearsal.py.
    # Empty on every other row: an expectation is only meaningful for the row
    # about to fire, since the ones below it will be composed under an outcome
    # the walk has not reached yet.
    expected: tuple[str, ...] = ()


async def rehearsal_rows(session: AsyncSession, user_id: int) -> list[RehearsalRow]:
    """The harness's state table: every rehearsal queue row, soonest first,
    with the button expectation attached to the next one to fire.

    A read-side decoration of `rehearsal_queue_rows` -- it writes nothing, and
    the expectation is computed, never stored.
    """
    rows = await rehearsal_queue_rows(session)
    concert = await get_rehearsal_concert(session)
    if not rows or concert is None:
        return []

    rounds = {
        r.id: r for r in (await session.execute(
            select(Round).where(Round.concert_id == concert.id)
        )).scalars()
    }
    days = list((await session.execute(
        select(ConcertDay)
        .where(ConcertDay.concert_id == concert.id)
        .order_by(ConcertDay.starts_at_utc, ConcertDay.id)
    )).scalars())
    day_labels = {d.id: loc_field(d, "label", "en") for d in days}
    live_day_ids = {d.id for d in days if not d.cancelled}
    outcomes = {
        o.round_id: o.outcome for o in (await session.execute(
            select(RoundOutcome).where(
                RoundOutcome.user_id == user_id,
                RoundOutcome.round_id.in_(rounds),
            )
        )).scalars()
    } if rounds else {}

    next_id = next((r.id for r in rows if r.sent_at_utc is None), None)
    out: list[RehearsalRow] = []
    for row in rows:
        round_ = rounds.get(row.round_id) if row.round_id is not None else None
        expected: tuple[str, ...] = ()
        if row.id == next_id:
            # Cancelled legs are excluded before the count, exactly as
            # due_reminders does it: a two-leg round with one leg cancelled is
            # a one-leg question.
            legs = len(_covered_day_ids(round_, live_day_ids)) if round_ else 0
            expected = expected_buttons(
                row.anchor, outcomes.get(row.round_id) if round_ else None, legs
            )
        out.append(RehearsalRow(
            queue_id=row.id,
            anchor=row.anchor,
            subject=(
                loc_field(round_, "label", "en") if round_
                else day_labels.get(row.day_id, "—")
            ),
            fire_at_utc=row.fire_at_utc,
            sent=row.sent_at_utc is not None,
            is_next=row.id == next_id,
            expected=expected,
        ))
    return out
