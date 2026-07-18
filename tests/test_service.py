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
from app.db.models import Base, Concert, ConcertDay, ReminderQueue, ReminderRule, Round, User
from app.db.service import (
    concert_audit_log,
    due_reminders,
    ensure_user,
    list_editors,
    mark_sent,
    record_concert_edit,
    set_editor,
    snapshot_concert,
    sync_concert,
    sync_rule,
    user_calendar_events,
)
from app.domain.types import Anchor, ConcertKind, RoundKind

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
