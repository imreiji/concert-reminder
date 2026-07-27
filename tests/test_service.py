"""Service-layer tests: queue sync semantics against a real async SQLite.

The scenarios mirror what actually happens when concert staff shift dates:
create -> plan; edit -> reschedule; postpone-after-sent -> re-arm; delete -> clean up.
"""

from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import (
    Base,
    Concert,
    ConcertDay,
    ConcertTag,
    Notification,
    ReminderQueue,
    ReminderRule,
    Round,
    RoundOutcome,
    Tag,
    User,
)
from app.db.service import (
    active_concerts_missing_member,
    concert_audit_log,
    due_reminders,
    ensure_user,
    leg_cancelled_context,
    list_editors,
    mark_sent,
    my_deadline_blocks,
    notify_newly_cancelled_legs,
    record_concert_edit,
    record_dm_outcome,
    reinstate_user_rules,
    set_editor,
    snapshot_concert,
    sync_concert,
    sync_rule,
    upcoming_deadlines,
    upcoming_rounds,
    user_calendar_events,
)
from app.domain.types import (
    Anchor,
    ConcertKind,
    LotteryOutcome,
    RoundKind,
    TagKind,
)

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


async def seed(s) -> tuple[Concert, Round, ReminderRule]:
    await ensure_user(s, 42, "reiji")
    concert = Concert(title="Hasunosora 5th", event_id="hasunosora-5th", created_by=42)
    s.add(concert)
    await s.flush()
    round_ = Round(
        concert_id=concert.id,
        kind=RoundKind.LOTTERY_ROUND,
        label="最速先行",
        opens_at_utc=dt(6, 10),
        closes_at_utc=dt(6, 25),
    )
    day = ConcertDay(concert_id=concert.id, label="Day 1", starts_at_utc=dt(8, 1, 9))
    s.add_all([round_, day])
    await s.flush()
    rule = ReminderRule(user_id=42, concert_id=concert.id, anchor=Anchor.CLOSES, offset_days=-3)
    s.add(rule)
    await s.flush()
    return concert, round_, rule


async def queue_rows(s) -> list[ReminderQueue]:
    return list((await s.execute(select(ReminderQueue))).scalars())


async def seed_two_legs(s) -> tuple[Concert, ConcertDay, ConcertDay, Round, Round, Round]:
    """Two legs (one will be cancelled by the test), three rounds covering
    all three applies_to shapes: tied only to leg A, tied to both legs, and
    General (no day association)."""
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


async def test_sync_creates_queue_rows(session):
    _, round_, rule = await seed(session)
    await sync_rule(session, rule, NOW)
    rows = await queue_rows(session)
    assert len(rows) == 1
    assert rows[0].fire_at_utc == dt(6, 22)  # 3 days before June 25 close
    assert rows[0].round_id == round_.id


async def test_resync_is_idempotent(session):
    _, _, rule = await seed(session)
    await sync_rule(session, rule, NOW)
    await sync_rule(session, rule, NOW)
    await sync_rule(session, rule, NOW)
    assert len(await queue_rows(session)) == 1


async def test_editing_round_reschedules(session):
    _, round_, rule = await seed(session)
    await sync_rule(session, rule, NOW)
    round_.closes_at_utc = dt(6, 28)  # staff extended the lottery
    await sync_concert(session, round_.concert_id, NOW)
    (row,) = await queue_rows(session)
    assert row.fire_at_utc == dt(6, 25)  # rescheduled: 3 days before the NEW close


async def test_postponed_deadline_rearms_sent_reminder(session):
    """The 'deadline moved after we already reminded' case — must re-fire."""
    _, round_, rule = await seed(session)
    await sync_rule(session, rule, NOW)
    (row,) = await queue_rows(session)
    await mark_sent(session, row.id, dt(6, 22, 13))
    round_.closes_at_utc = dt(7, 5)  # postponed well after the sent reminder
    await sync_concert(session, round_.concert_id, dt(6, 23))
    (row,) = await queue_rows(session)
    assert row.sent_at_utc is None  # re-armed
    assert row.fire_at_utc == dt(7, 2)


async def test_sent_rows_left_alone_when_nothing_changed(session):
    _, _, rule = await seed(session)
    await sync_rule(session, rule, NOW)
    (row,) = await queue_rows(session)
    sent_at = dt(6, 22, 13)
    await mark_sent(session, row.id, sent_at)
    await sync_rule(session, rule, dt(6, 23))
    (row,) = await queue_rows(session)
    assert row.sent_at_utc == sent_at  # not re-armed, not duplicated


async def test_removing_round_cleans_unsent_rows(session):
    _, round_, rule = await seed(session)
    await sync_rule(session, rule, NOW)
    await session.delete(round_)
    await session.flush()
    await sync_rule(session, rule, NOW)
    assert await queue_rows(session) == []


async def test_due_and_mark_sent_roundtrip(session):
    _, _, rule = await seed(session)
    await sync_rule(session, rule, NOW)

    assert await due_reminders(session, dt(6, 21)) == []  # not due yet
    due = await due_reminders(session, dt(6, 22, 13))
    assert len(due) == 1
    item = due[0]
    assert item.discord_id == 42
    assert item.concert_title == "Hasunosora 5th"
    assert item.round_label == "最速先行"
    assert item.anchor_time_utc == dt(6, 25)

    await mark_sent(session, item.queue_id, dt(6, 22, 13))
    assert await due_reminders(session, dt(6, 22, 14)) == []  # drained


async def test_due_reminders_batches_queries_regardless_of_row_count(session):
    """Regression guard for the N+1 fix: due_reminders must do a fixed
    number of round trips (queue + one batch select per entity type)
    rather than one round trip per due row."""
    await ensure_user(session, 42, "reiji")
    n = 5
    for i in range(n):
        concert = Concert(title=f"Concert {i}", event_id=f"concert-{i}", created_by=42)
        session.add(concert)
        await session.flush()
        round_ = Round(
            concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label=f"R{i}",
            closes_at_utc=dt(6, 25),
        )
        session.add(round_)
        await session.flush()
        rule = ReminderRule(
            user_id=42, round_id=round_.id, anchor=Anchor.CLOSES, offset_days=0
        )
        session.add(rule)
        await session.flush()
        await sync_rule(session, rule, NOW)
    await session.commit()

    queries: list[str] = []

    def _count(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    event.listen(session.bind.sync_engine, "before_cursor_execute", _count)
    try:
        due = await due_reminders(session, dt(6, 26))
    finally:
        event.remove(session.bind.sync_engine, "before_cursor_execute", _count)

    assert len(due) == n
    # queue + rules + users + rounds + concerts (days skipped: none used here)
    # -- fixed regardless of n, not one query per due row.
    assert len(queries) <= 6


async def test_event_start_rule_targets_days(session):
    concert, _, _ = await seed(session)
    rule = ReminderRule(
        user_id=42, concert_id=concert.id, anchor=Anchor.EVENT_START, offset_days=-7
    )
    session.add(rule)
    await session.flush()
    await sync_rule(session, rule, NOW)
    rows = [r for r in await queue_rows(session) if r.rule_id == rule.id]
    assert len(rows) == 1
    assert rows[0].day_id is not None
    assert rows[0].fire_at_utc == dt(7, 25, 9)


async def test_user_calendar_events_covers_rounds_and_days(session):
    """The personal calendar feed shows each covered round/day's own real
    deadline -- not the reminder rule's lead-time-adjusted fire_at."""
    concert, round_, rule = await seed(session)  # concert-wide CLOSES rule -> round only
    await sync_rule(session, rule, NOW)

    day_rule = ReminderRule(
        user_id=42, concert_id=concert.id, anchor=Anchor.EVENT_START, offset_days=-7
    )
    session.add(day_rule)
    await session.flush()
    await sync_rule(session, day_rule, NOW)

    events = await user_calendar_events(session, 42, NOW)
    by_label = {e.label: e for e in events}
    assert by_label["最速先行"].at_utc == dt(6, 25)  # the round's own close, not a lead time
    assert by_label["最速先行"].concert_title == "Hasunosora 5th"
    assert by_label["Day 1"].at_utc == dt(8, 1, 9)  # the day's own start, not the -7d fire time


async def test_user_calendar_events_excludes_past_deadlines(session):
    _, _, rule = await seed(session)
    await sync_rule(session, rule, NOW)
    assert await user_calendar_events(session, 42, dt(7, 1)) == []  # round already closed by then


# ── A round with all 4 timestamps: the actual point of this refactor ────


async def test_round_with_all_four_timestamps_syncs_each_anchor_independently(session):
    """One round entry, 4 reminder rules (one per anchor) -- each gets its
    own queue row and re-arms independently of the others."""
    await ensure_user(session, 42, "reiji")
    concert = Concert(title="Full Round", event_id="full-round", created_by=42)
    session.add(concert)
    await session.flush()
    round_ = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="Bundled round",
        opens_at_utc=dt(6, 5), closes_at_utc=dt(6, 10),
        results_at_utc=dt(6, 15), payment_deadline_at_utc=dt(6, 22),
    )
    session.add(round_)
    await session.flush()

    rules = {
        anchor: ReminderRule(
            user_id=42, round_id=round_.id, anchor=anchor, offset_days=-1
        )
        for anchor in (Anchor.OPENS, Anchor.CLOSES, Anchor.RESULTS, Anchor.PAYMENT)
    }
    session.add_all(rules.values())
    await session.flush()
    for rule in rules.values():
        await sync_rule(session, rule, NOW)

    rows = {r.rule_id: r for r in await queue_rows(session)}
    assert rows[rules[Anchor.OPENS].id].fire_at_utc == dt(6, 4)
    assert rows[rules[Anchor.CLOSES].id].fire_at_utc == dt(6, 9)
    assert rows[rules[Anchor.RESULTS].id].fire_at_utc == dt(6, 14)
    assert rows[rules[Anchor.PAYMENT].id].fire_at_utc == dt(6, 21)

    # Mark the RESULTS reminder sent, then postpone only the results
    # timestamp and re-sync only that rule -- it re-arms without touching
    # the other 3 anchors' queue rows at all.
    results_row = rows[rules[Anchor.RESULTS].id]
    await mark_sent(session, results_row.id, dt(6, 14, 1))
    round_.results_at_utc = dt(6, 18)
    await sync_rule(session, rules[Anchor.RESULTS], dt(6, 14, 2))

    rows = {r.rule_id: r for r in await queue_rows(session)}
    assert rows[rules[Anchor.RESULTS].id].sent_at_utc is None  # re-armed
    assert rows[rules[Anchor.RESULTS].id].fire_at_utc == dt(6, 17)
    assert rows[rules[Anchor.OPENS].id].fire_at_utc == dt(6, 4)  # untouched
    assert rows[rules[Anchor.CLOSES].id].fire_at_utc == dt(6, 9)  # untouched
    assert rows[rules[Anchor.PAYMENT].id].fire_at_utc == dt(6, 21)  # untouched


# ── Cancelled-leg filtering ──────────────────────────────────────────────


async def test_sync_skips_cancelled_leg_and_its_solely_tied_round(session):
    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    rule = ReminderRule(user_id=42, concert_id=concert.id, anchor=Anchor.CLOSES, offset_days=0)
    session.add(rule)
    await session.flush()
    await sync_rule(session, rule, NOW)
    before = {(r.round_id, r.day_id) for r in await queue_rows(session)}
    assert (round_a_only.id, None) in before
    assert (round_both.id, None) in before
    assert (round_general.id, None) in before

    leg_a.cancelled = True
    await session.flush()
    await sync_rule(session, rule, NOW)
    after = {(r.round_id, r.day_id) for r in await queue_rows(session)}
    # A-only is fully cancelled (its one leg is cancelled) -> gone.
    assert (round_a_only.id, None) not in after
    # Both-legs still has leg B live -> untouched.
    assert (round_both.id, None) in after
    # General has no day association -> never affected.
    assert (round_general.id, None) in after


async def test_sync_event_start_rule_skips_cancelled_day(session):
    concert, leg_a, leg_b, _, _, _ = await seed_two_legs(session)
    rule = ReminderRule(
        user_id=42, concert_id=concert.id, anchor=Anchor.EVENT_START, offset_days=-1
    )
    session.add(rule)
    await session.flush()
    await sync_rule(session, rule, NOW)
    before = {r.day_id for r in await queue_rows(session)}
    assert {leg_a.id, leg_b.id} <= before

    leg_a.cancelled = True
    await session.flush()
    await sync_rule(session, rule, NOW)
    after = {r.day_id for r in await queue_rows(session)}
    assert leg_a.id not in after
    assert leg_b.id in after


async def test_sync_round_specific_rule_on_cancelled_round_clears_it(session):
    concert, leg_a, leg_b, round_a_only, _, _ = await seed_two_legs(session)
    rule = ReminderRule(user_id=42, round_id=round_a_only.id, anchor=Anchor.CLOSES, offset_days=0)
    session.add(rule)
    await session.flush()
    await sync_rule(session, rule, NOW)
    assert len(await queue_rows(session)) == 1

    leg_a.cancelled = True
    await session.flush()
    await sync_rule(session, rule, NOW)
    assert await queue_rows(session) == []


# ── Notification on cancel + reinstate ───────────────────────────────────


async def test_notify_newly_cancelled_legs_notifies_only_users_left_with_nothing(session):
    concert, leg_a, leg_b, round_a_only, round_both, _ = await seed_two_legs(session)
    # user 42: a rule only on the A-only round -- will lose everything.
    rule_a_only = ReminderRule(
        user_id=42, round_id=round_a_only.id, anchor=Anchor.CLOSES, offset_days=0
    )
    # user 43: a concert-wide rule -- still has round_both + round_general on leg B.
    await ensure_user(session, 43, "other-fan")
    rule_concert_wide = ReminderRule(
        user_id=43, concert_id=concert.id, anchor=Anchor.CLOSES, offset_days=0
    )
    session.add_all([rule_a_only, rule_concert_wide])
    await session.flush()
    await sync_rule(session, rule_a_only, NOW)
    await sync_rule(session, rule_concert_wide, NOW)

    leg_a.cancelled = True
    await session.flush()
    n = await notify_newly_cancelled_legs(session, concert.id, {leg_a.id}, NOW)
    await session.commit()

    assert n == 1
    notes = list((await session.execute(select(Notification))).scalars())
    assert len(notes) == 1
    assert notes[0].user_id == 42
    assert notes[0].kind == "leg_cancelled"
    assert notes[0].concert_id == concert.id


async def test_notify_newly_cancelled_legs_noop_when_no_new_cancellations(session):
    concert, leg_a, _, _, _, _ = await seed_two_legs(session)
    n = await notify_newly_cancelled_legs(session, concert.id, set(), NOW)
    assert n == 0
    assert list((await session.execute(select(Notification))).scalars()) == []


async def test_reinstate_user_rules_resyncs_when_uncancelled(session):
    concert, leg_a, leg_b, round_a_only, _, _ = await seed_two_legs(session)
    rule = ReminderRule(user_id=42, round_id=round_a_only.id, anchor=Anchor.CLOSES, offset_days=0)
    session.add(rule)
    await session.flush()
    await sync_rule(session, rule, NOW)
    leg_a.cancelled = True
    await session.flush()
    await sync_rule(session, rule, NOW)
    assert await queue_rows(session) == []  # cleared

    leg_a.cancelled = False  # editor un-cancels it
    await session.flush()
    n = await reinstate_user_rules(session, 42, concert.id, NOW)
    assert n == 1  # one rule re-synced
    assert len(await queue_rows(session)) == 1  # re-armed


async def test_reinstate_user_rules_is_a_noop_while_still_cancelled(session):
    concert, leg_a, leg_b, round_a_only, _, _ = await seed_two_legs(session)
    rule = ReminderRule(user_id=42, round_id=round_a_only.id, anchor=Anchor.CLOSES, offset_days=0)
    session.add(rule)
    await session.flush()
    leg_a.cancelled = True
    await session.flush()

    n = await reinstate_user_rules(session, 42, concert.id, NOW)
    assert n == 1  # the rule was re-synced...
    assert await queue_rows(session) == []  # ...but nothing gets planned, still cancelled


async def test_leg_cancelled_context_reads_concert_title(session):
    concert, _, _, _, _, _ = await seed_two_legs(session)
    ctx = await leg_cancelled_context(session, concert.id)
    assert ctx.title == "Two-Leg Tour"
    assert ctx.event_id == "two-leg-tour"
    assert ctx.concert_id == concert.id


async def test_leg_cancelled_context_none_for_missing_concert(session):
    assert await leg_cancelled_context(session, 999) is None


async def test_leg_cancelled_context_carries_recipient_language(session):
    """The leg-cancel DM localizes: the context carries the recipient's
    language so _send_notification sets the locale before composing the embed.
    Passing no user_id keeps the English default (the plain fallback path)."""
    concert, *_ = await seed_two_legs(session)
    user = await session.get(User, 42)
    user.language = "ja"
    await session.flush()
    ctx = await leg_cancelled_context(session, concert.id, 42)
    assert ctx.user_language == "ja"
    ctx_default = await leg_cancelled_context(session, concert.id)
    assert ctx_default.user_language == "en"


# ── Concert edit history ─────────────────────────────────────────────────


async def test_record_concert_edit_diffs_only_changed_fields(session):
    concert, _, _ = await seed(session)
    before = snapshot_concert(concert)
    concert.title = "Hasunosora 5th (renamed)"
    concert.organizer = "New Organizer"
    audit = await record_concert_edit(session, concert, edited_by=42, before=before)
    assert audit is not None
    assert {c["field"] for c in audit.changes} == {"title", "organizer"}
    title_change = next(c for c in audit.changes if c["field"] == "title")
    assert title_change == {
        "field": "title", "before": "Hasunosora 5th", "after": "Hasunosora 5th (renamed)"
    }


async def test_record_concert_edit_returns_none_when_nothing_changed(session):
    concert, _, _ = await seed(session)
    before = snapshot_concert(concert)
    # re-set every field to its own current value -- a no-op resubmit
    concert.title = concert.title
    audit = await record_concert_edit(session, concert, edited_by=42, before=before)
    assert audit is None
    assert await concert_audit_log(session, concert.id) == []


async def test_record_concert_edit_serializes_enum_fields(session):
    concert, _, _ = await seed(session)
    before = snapshot_concert(concert)
    assert before["kind"] is None
    concert.kind = ConcertKind.TOUR
    audit = await record_concert_edit(session, concert, edited_by=42, before=before)
    kind_change = next(c for c in audit.changes if c["field"] == "kind")
    assert kind_change == {"field": "kind", "before": None, "after": "tour"}


async def test_concert_audit_log_orders_newest_first_and_loads_editor(session):
    concert, _, _ = await seed(session)
    before1 = snapshot_concert(concert)
    concert.title = "First edit"
    await record_concert_edit(session, concert, edited_by=42, before=before1)

    before2 = snapshot_concert(concert)
    concert.title = "Second edit"
    await record_concert_edit(session, concert, edited_by=42, before=before2)
    await session.commit()

    log = await concert_audit_log(session, concert.id)
    assert len(log) == 2
    assert log[0].changes[0]["after"] == "Second edit"  # newest first
    assert log[0].editor.username == "reiji"


async def test_deleting_concert_cascades_its_audit_log(session):
    concert, _, _ = await seed(session)
    before = snapshot_concert(concert)
    concert.title = "Renamed"
    await record_concert_edit(session, concert, edited_by=42, before=before)
    await session.commit()

    await session.delete(concert)
    await session.commit()
    assert await concert_audit_log(session, concert.id) == []


# ── Retroactive artist-to-active-events (Tags page) ──────────────────────


async def seed_group_and_concerts(s) -> tuple[Tag, Tag, Tag]:
    """A group with one already-attached artist, and three concerts all
    tagged with the group: one with a live future leg (active, missing the
    new member), one with only a cancelled leg (not active), one already
    carrying the new member (not eligible -- already covered)."""
    await ensure_user(s, 42, "reiji")
    group = Tag(name="Liella", kind=TagKind.GROUP, created_by=42)
    existing_member = Tag(name="Kaho", kind=TagKind.ARTIST, created_by=42)
    new_member = Tag(name="Sumire", kind=TagKind.ARTIST, created_by=42)
    s.add_all([group, existing_member, new_member])
    await s.flush()

    active = Concert(title="Active Show", event_id="active-show", created_by=42)
    cancelled_only = Concert(title="Cancelled Show", event_id="cancelled-show", created_by=42)
    already_covered = Concert(title="Covered Show", event_id="covered-show", created_by=42)
    s.add_all([active, cancelled_only, already_covered])
    await s.flush()

    s.add_all([
        ConcertDay(concert_id=active.id, label="Day 1", starts_at_utc=dt(9, 1, 18)),
        ConcertDay(
            concert_id=cancelled_only.id, label="Day 1", starts_at_utc=dt(9, 1, 18), cancelled=True
        ),
        ConcertDay(concert_id=already_covered.id, label="Day 1", starts_at_utc=dt(9, 1, 18)),
    ])
    await s.flush()

    s.add_all([
        ConcertTag(concert_id=active.id, tag_id=group.id),
        ConcertTag(concert_id=cancelled_only.id, tag_id=group.id),
        ConcertTag(concert_id=already_covered.id, tag_id=group.id),
        ConcertTag(concert_id=already_covered.id, tag_id=new_member.id),
    ])
    await s.flush()
    return group, existing_member, new_member


async def test_active_concerts_missing_member_excludes_cancelled_and_covered(session):
    group, _, new_member = await seed_group_and_concerts(session)
    result = await active_concerts_missing_member(session, group.id, new_member.id, NOW)
    assert [c.title for c in result] == ["Active Show"]


async def test_active_concerts_missing_member_excludes_past_dated(session):
    """Same seed as above, but queried with a `now` after the "Active
    Show"'s only leg (2026-09-01) -- it should no longer count as active,
    leaving nothing eligible at all (the other two concerts are already
    excluded for their own reasons regardless of `now`)."""
    group, _, new_member = await seed_group_and_concerts(session)
    result = await active_concerts_missing_member(session, group.id, new_member.id, dt(10, 1))
    assert result == []


async def test_active_concerts_missing_member_empty_for_ungrouped_concert(session):
    group, _, new_member = await seed_group_and_concerts(session)
    unrelated = Concert(title="No Group", event_id="no-group", created_by=42)
    session.add(unrelated)
    await session.flush()
    session.add(ConcertDay(concert_id=unrelated.id, label="Day 1", starts_at_utc=dt(9, 1, 18)))
    await session.flush()
    result = await active_concerts_missing_member(session, group.id, new_member.id, NOW)
    assert "No Group" not in [c.title for c in result]


# ── Global upcoming-deadlines list (index page) ──────────────────────────


async def seed_deadline_scenarios(s) -> tuple[Concert, Round, ConcertDay]:
    """One concert with a round carrying two set timestamps (to confirm it
    produces two independent rows) plus a live future day; one concert
    that's entirely cancelled; one concert with only a past-dated round."""
    await ensure_user(s, 42, "reiji")

    concert = Concert(title="Two-Timestamp Show", event_id="two-ts", created_by=42)
    s.add(concert)
    await s.flush()
    round_both = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="Bundled",
        opens_at_utc=dt(6, 5), closes_at_utc=dt(6, 10),
    )
    day = ConcertDay(concert_id=concert.id, label="Day 1", starts_at_utc=dt(8, 1, 9))
    s.add_all([round_both, day])

    cancelled_concert = Concert(title="Cancelled Show", event_id="cancelled-show", created_by=42)
    s.add(cancelled_concert)
    await s.flush()
    cancelled_day = ConcertDay(
        concert_id=cancelled_concert.id, label="Day 1", starts_at_utc=dt(8, 5, 9), cancelled=True,
    )
    s.add(cancelled_day)
    await s.flush()
    cancelled_round = Round(
        concert_id=cancelled_concert.id, kind=RoundKind.LOTTERY_ROUND, label="Cancelled Round",
        closes_at_utc=dt(6, 15), applies_to=[cancelled_day.id],
    )
    s.add(cancelled_round)

    past_concert = Concert(title="Past Show", event_id="past-show", created_by=42)
    s.add(past_concert)
    await s.flush()
    past_round = Round(
        concert_id=past_concert.id, kind=RoundKind.LOTTERY_ROUND, label="Past Round",
        closes_at_utc=dt(1, 1),
    )
    s.add(past_round)
    await s.flush()
    return concert, round_both, day


async def test_upcoming_deadlines_one_row_per_set_timestamp(session):
    concert, round_both, day = await seed_deadline_scenarios(session)
    result = await upcoming_deadlines(session, NOW, limit=10)
    pairs = [(e.label, e.anchor) for e in result if e.concert_title == "Two-Timestamp Show"]
    assert (round_both.label, Anchor.OPENS) in pairs
    assert (round_both.label, Anchor.CLOSES) in pairs
    assert (day.label, Anchor.EVENT_START) in pairs


async def test_upcoming_deadlines_excludes_cancelled_and_past(session):
    await seed_deadline_scenarios(session)
    result = await upcoming_deadlines(session, NOW, limit=10)
    titles = {e.concert_title for e in result}
    assert "Cancelled Show" not in titles
    assert "Past Show" not in titles


async def test_upcoming_deadlines_sorted_chronologically_and_truncated(session):
    await seed_deadline_scenarios(session)
    result = await upcoming_deadlines(session, NOW, limit=2)
    assert len(result) == 2
    assert result[0].at_utc <= result[1].at_utc


async def test_upcoming_rounds_excludes_implicitly_cancelled_round(session):
    concert, leg_a, leg_b, round_a_only, round_both, round_general = await seed_two_legs(session)
    leg_a.cancelled = True
    await session.flush()
    result = await upcoming_rounds(session, NOW, horizon_days=60)
    round_ids = {r.id for _, r in result}
    assert round_a_only.id not in round_ids  # fully cancelled (its only leg is now cancelled)
    assert round_both.id in round_ids  # leg B still live
    assert round_general.id in round_ids  # never tied to a leg, unaffected


# ── set_editor / list_editors ───────────────────────────────────────────


async def test_set_editor_creates_stub_user_when_unknown(session):
    user = await set_editor(session, 99, True)
    assert user.username == "99"  # placeholder, corrected on next login
    assert user.is_editor is True


async def test_set_editor_uses_provided_username(session):
    user = await set_editor(session, 99, True, username="reiji")
    assert user.username == "reiji"


async def test_set_editor_toggles_existing_user(session):
    await ensure_user(session, 42, "reiji")
    await set_editor(session, 42, True)
    await session.flush()
    (row,) = (await session.execute(select(User).where(User.discord_id == 42))).scalars()
    assert row.is_editor is True

    await set_editor(session, 42, False)
    await session.flush()
    (row,) = (await session.execute(select(User).where(User.discord_id == 42))).scalars()
    assert row.is_editor is False


async def test_list_editors_combines_db_and_env(session, monkeypatch):
    monkeypatch.setattr(settings, "editor_whitelist", "777")  # never logged in
    await ensure_user(session, 42, "reiji")
    await set_editor(session, 42, True)
    await session.flush()

    editors = await list_editors(session)
    by_id = {e["id"]: e for e in editors}
    assert by_id[42] == {"id": 42, "username": "reiji", "env": False}
    assert by_id[777] == {"id": 777, "username": None, "env": True}


async def test_list_editors_marks_env_lock_on_db_editor(session, monkeypatch):
    monkeypatch.setattr(settings, "editor_whitelist", "42")
    await ensure_user(session, 42, "reiji")
    await set_editor(session, 42, True)
    await session.flush()

    editors = await list_editors(session)
    assert editors == [{"id": 42, "username": "reiji", "env": True}]


async def test_record_dm_outcome_sets_and_clears_flag(session):
    await ensure_user(session, 42, "reiji")

    await record_dm_outcome(session, 42, blocked=True)
    user = await session.get(User, 42)
    assert user.dm_blocked_since is not None

    await record_dm_outcome(session, 42, blocked=False)
    assert user.dm_blocked_since is None


async def test_due_reminder_carries_round_id_and_outcome(session):
    from app.db.models import RoundOutcome
    from app.domain.types import LotteryOutcome

    concert, round_, rule = await seed(session)
    await sync_rule(session, rule, NOW)
    session.add(RoundOutcome(user_id=42, round_id=round_.id, outcome=LotteryOutcome.APPLIED))
    await session.flush()

    (due,) = await due_reminders(session, dt(6, 22))
    assert due.round_id == round_.id
    assert due.outcome == LotteryOutcome.APPLIED


# ── DueReminder.covered_days ────────────────────────────────────────────


async def seed_results_round(
    s, leg_labels: list[str], cancelled: set[str] | None = None,
    *, language: str = "en", label_en: dict[str, str] | None = None,
) -> tuple[Round, list[ConcertDay]]:
    """A concert with one RESULTS-anchored round covering every leg, plus a
    queued RESULTS reminder for user 42."""
    cancelled = cancelled or set()
    user = await ensure_user(s, 42, "reiji")
    user.language = language
    concert = Concert(title="Covered Tour", event_id="covered-tour", created_by=42)
    s.add(concert)
    await s.flush()
    days = [
        ConcertDay(
            concert_id=concert.id, label=lbl,
            label_en=(label_en or {}).get(lbl),
            starts_at_utc=dt(8, i + 1, 9), cancelled=lbl in cancelled,
        )
        for i, lbl in enumerate(leg_labels)
    ]
    s.add_all(days)
    await s.flush()
    round_ = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="先行",
        closes_at_utc=dt(6, 20), results_at_utc=dt(6, 25),
        applies_to=[d.id for d in days],
    )
    s.add(round_)
    await s.flush()
    return round_, days


async def queue_anchor(s, round_: Round, anchor: Anchor) -> None:
    rule = ReminderRule(user_id=42, round_id=round_.id, anchor=anchor, offset_days=0)
    s.add(rule)
    await s.flush()
    await sync_rule(s, rule, NOW)


async def test_due_reminder_results_row_carries_covered_days(session):
    round_, days = await seed_results_round(session, ["Leg A", "Leg B"])
    await queue_anchor(session, round_, Anchor.RESULTS)

    (due,) = await due_reminders(session, dt(6, 26))
    assert due.anchor is Anchor.RESULTS
    assert due.covered_days == ((days[0].id, "Leg A"), (days[1].id, "Leg B"))


async def test_due_reminder_non_results_row_has_no_covered_days(session):
    """Same multi-leg round, a CLOSES reminder: per-day result capture is a
    RESULTS-only question, so the field stays empty."""
    round_, _ = await seed_results_round(session, ["Leg A", "Leg B"])
    await queue_anchor(session, round_, Anchor.CLOSES)

    (due,) = await due_reminders(session, dt(6, 26))
    assert due.anchor is Anchor.CLOSES
    assert due.covered_days == ()


async def test_due_reminder_closes_row_stays_empty_beside_a_results_row(session):
    """The real test of the anchor guard: ONE round carrying BOTH a RESULTS
    and a CLOSES queue row in the SAME batch. The legs are loaded (the RESULTS
    row needs them), so only the per-row anchor check keeps them off the
    CLOSES row -- a CLOSES-only batch never loads legs at all and would pass
    with the guard deleted."""
    round_, days = await seed_results_round(session, ["Leg A", "Leg B"])
    await queue_anchor(session, round_, Anchor.RESULTS)
    await queue_anchor(session, round_, Anchor.CLOSES)

    due = await due_reminders(session, dt(6, 26))
    by_anchor = {d.anchor: d for d in due}
    assert set(by_anchor) == {Anchor.RESULTS, Anchor.CLOSES}
    assert by_anchor[Anchor.RESULTS].covered_days == (
        (days[0].id, "Leg A"), (days[1].id, "Leg B")
    )
    assert by_anchor[Anchor.CLOSES].covered_days == ()


async def test_due_reminder_covered_days_honours_all_legs_convention(session):
    """An empty applies_to means every leg (the all-legs convention), so the
    pairs come out the same as an explicit list would give."""
    round_, days = await seed_results_round(session, ["Leg A", "Leg B"])
    round_.applies_to = None
    await session.flush()
    await queue_anchor(session, round_, Anchor.RESULTS)

    (due,) = await due_reminders(session, dt(6, 26))
    assert due.covered_days == ((days[0].id, "Leg A"), (days[1].id, "Leg B"))


async def test_due_reminder_single_leg_round_has_no_covered_days(session):
    round_, _ = await seed_results_round(session, ["Leg A"])
    await queue_anchor(session, round_, Anchor.RESULTS)

    (due,) = await due_reminders(session, dt(6, 26))
    assert due.covered_days == ()


async def test_due_reminder_covered_day_labels_use_recipient_language(session):
    """Scheduler path: labels resolve from the RECIPIENT's stored language,
    never the ambient ContextVar locale (one pass composes for many users)."""
    from app.i18n import set_locale

    round_, days = await seed_results_round(
        session, ["1日目", "2日目"], language="ja",
        label_en={"1日目": "Day 1", "2日目": "Day 2"},
    )
    await queue_anchor(session, round_, Anchor.RESULTS)

    set_locale("en")
    try:
        (due,) = await due_reminders(session, dt(6, 26))
    finally:
        set_locale("en")
    assert due.covered_days == ((days[0].id, "1日目"), (days[1].id, "2日目"))


async def test_due_reminder_covered_days_excludes_cancelled_leg(session):
    round_, days = await seed_results_round(
        session, ["Leg A", "Leg B", "Leg C"], cancelled={"Leg B"}
    )
    await queue_anchor(session, round_, Anchor.RESULTS)

    (due,) = await due_reminders(session, dt(6, 26))
    assert due.covered_days == ((days[0].id, "Leg A"), (days[2].id, "Leg C"))


async def test_due_reminder_cancelled_leg_drops_round_below_two_live(session):
    """A cancelled leg is not just filtered out of the pairs -- it does not
    count toward the >=2-live-legs threshold either."""
    round_, _ = await seed_results_round(
        session, ["Leg A", "Leg B"], cancelled={"Leg B"}
    )
    await queue_anchor(session, round_, Anchor.RESULTS)

    (due,) = await due_reminders(session, dt(6, 26))
    assert due.covered_days == ()


# ── LABEL_BY_ROUND_KIND ─────────────────────────────────────────────────


def test_label_by_round_kind_covers_every_kind():
    from app.db.service import LABEL_BY_ROUND_KIND

    assert set(LABEL_BY_ROUND_KIND) == set(RoundKind)


def test_label_by_round_kind_exact_text():
    from app.db.service import LABEL_BY_ROUND_KIND

    assert LABEL_BY_ROUND_KIND[RoundKind.LOTTERY_ROUND] == "Lottery round"
    assert LABEL_BY_ROUND_KIND[RoundKind.ELIGIBILITY_ITEM_SALE] == "Eligibility item sale"
    assert LABEL_BY_ROUND_KIND[RoundKind.STREAM_TICKET_SALE] == "Stream ticket sale"
    assert LABEL_BY_ROUND_KIND[RoundKind.GENERAL_SALE] == "General sale"
    assert LABEL_BY_ROUND_KIND[RoundKind.RESULT_ANNOUNCEMENT] == "Result announcement"
    assert LABEL_BY_ROUND_KIND[RoundKind.PAYMENT_DEADLINE] == "Payment deadline"
    assert LABEL_BY_ROUND_KIND[RoundKind.OTHER] == "Other"
    assert LABEL_BY_ROUND_KIND[RoundKind.FCFS_SALE] == "First come, first served"
    assert LABEL_BY_ROUND_KIND[RoundKind.TOUR_PACKAGE] == "Overseas tour package"
    assert LABEL_BY_ROUND_KIND[RoundKind.UPGRADE] == "Upgrade round"


async def test_my_deadline_blocks_query_count_is_pinned(session):
    """Measurement debt, paid: Home's "Coming up" has never had a number on it.

    `my_deadline_blocks` fetches `limit * ANCHOR_FAN_OUT` (6x) rows so that
    grouping by concert does not under-fill, and `my_deadline_rows` still runs
    two loops that scale with what comes back: `covered_round_ids` once per
    concert the viewer holds a ticket on, and `_day_capture_context` once per
    round they are partway through. Everything else there is batched.

    So this pins the number rather than a design. Same technique as
    `test_due_reminders_batches_queries_regardless_of_row_count` above: count
    statements over one call, with a realistic-worst-ish page (12 concerts,
    two legs and two rounds each, four of them secured so the per-concert loop
    actually runs). The bound is deliberately loose enough not to fail on an
    incidental extra batch query and tight enough that a return to per-ROW
    work would blow it.
    """
    await ensure_user(session, 42, "reiji")
    concert_ids: set[int] = set()
    for i in range(12):
        concert = Concert(title=f"Concert {i}", event_id=f"c-{i:02d}", created_by=42)
        session.add(concert)
        await session.flush()
        concert_ids.add(concert.id)
        session.add_all([
            ConcertDay(concert_id=concert.id, label="Day 1", starts_at_utc=dt(8, 1, 9)),
            ConcertDay(concert_id=concert.id, label="Day 2", starts_at_utc=dt(8, 2, 9)),
        ])
        # A settled round: results are out, payment is not due yet -- so it
        # still emits a future anchor row AND it is the shape that drives both
        # per-item loops (secured concert -> covered_round_ids, WON with two
        # live legs past its result moment -> _day_capture_context).
        settled = Round(
            concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label=f"FC {i}",
            opens_at_utc=dt(5, 1), closes_at_utc=dt(5, 20),
            results_at_utc=dt(5, 25), payment_deadline_at_utc=dt(6, 20),
        )
        # An open round: three future anchors each, which is where the 6x
        # fan-out comes from in the first place.
        open_ = Round(
            concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label=f"General {i}",
            opens_at_utc=dt(6, 5), closes_at_utc=dt(6, 25), results_at_utc=dt(7, 1),
        )
        session.add_all([settled, open_])
        await session.flush()
        if i < 4:
            session.add(RoundOutcome(
                user_id=42, round_id=settled.id, outcome=LotteryOutcome.WON
            ))
        if i < 8:
            session.add(RoundOutcome(
                user_id=42, round_id=open_.id, outcome=LotteryOutcome.APPLIED
            ))
    await session.commit()

    queries: list[str] = []

    def _count(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    event.listen(session.bind.sync_engine, "before_cursor_execute", _count)
    try:
        blocks = await my_deadline_blocks(
            session, 42, now=NOW, concert_ids=concert_ids
        )
    finally:
        event.remove(session.bind.sync_engine, "before_cursor_execute", _count)

    assert blocks  # the page is not empty, or the count would measure nothing
    # MEASURED: 42 statements for this page (2026-07-27). Not a target -- a
    # record. Roughly 12 of those are the batched reads the row decorator does
    # once each; the other ~30 are the two per-item loops, ~6 statements per
    # secured concert (covered_round_ids re-derives its rounds, legs, opt-outs
    # and day results per concert) plus one per round partway through. Four
    # secured concerts is not an unusual page. Left pinned rather than
    # redesigned: the shape is a known cost, and a bound is what makes the
    # next change to it visible.
    assert len(queries) <= 45, f"my_deadline_blocks ran {len(queries)} queries"
