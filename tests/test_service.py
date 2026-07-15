"""Service-layer tests: queue sync semantics against a real async SQLite.

The scenarios mirror what actually happens when concert staff shift dates:
create -> plan; edit -> reschedule; postpone-after-sent -> re-arm; delete -> clean up.
"""

from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Concert, ConcertDay, ReminderQueue, ReminderRule, Window
from app.db.service import due_reminders, ensure_user, mark_sent, sync_concert, sync_rule
from app.domain.types import Anchor, WindowKind

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


async def seed(s) -> tuple[Concert, Window, ReminderRule]:
    await ensure_user(s, 42, "reiji")
    concert = Concert(title="Hasunosora 5th", created_by=42)
    s.add(concert)
    await s.flush()
    window = Window(
        concert_id=concert.id,
        kind=WindowKind.LOTTERY_ROUND,
        label="最速先行",
        opens_at_utc=dt(6, 10),
        closes_at_utc=dt(6, 25),
    )
    day = ConcertDay(concert_id=concert.id, label="Day 1", starts_at_utc=dt(8, 1, 9))
    s.add_all([window, day])
    await s.flush()
    rule = ReminderRule(user_id=42, concert_id=concert.id, anchor=Anchor.CLOSES, offset_days=-3)
    s.add(rule)
    await s.flush()
    return concert, window, rule


async def queue_rows(s) -> list[ReminderQueue]:
    return list((await s.execute(select(ReminderQueue))).scalars())


async def test_sync_creates_queue_rows(session):
    _, window, rule = await seed(session)
    await sync_rule(session, rule, NOW)
    rows = await queue_rows(session)
    assert len(rows) == 1
    assert rows[0].fire_at_utc == dt(6, 22)  # 3 days before June 25 close
    assert rows[0].window_id == window.id


async def test_resync_is_idempotent(session):
    _, _, rule = await seed(session)
    await sync_rule(session, rule, NOW)
    await sync_rule(session, rule, NOW)
    await sync_rule(session, rule, NOW)
    assert len(await queue_rows(session)) == 1


async def test_editing_window_reschedules(session):
    _, window, rule = await seed(session)
    await sync_rule(session, rule, NOW)
    window.closes_at_utc = dt(6, 28)  # staff extended the lottery
    await sync_concert(session, window.concert_id, NOW)
    (row,) = await queue_rows(session)
    assert row.fire_at_utc == dt(6, 25)  # rescheduled: 3 days before the NEW close


async def test_postponed_deadline_rearms_sent_reminder(session):
    """The 'deadline moved after we already reminded' case — must re-fire."""
    _, window, rule = await seed(session)
    await sync_rule(session, rule, NOW)
    (row,) = await queue_rows(session)
    await mark_sent(session, row.id, dt(6, 22, 13))
    window.closes_at_utc = dt(7, 5)  # postponed well after the sent reminder
    await sync_concert(session, window.concert_id, dt(6, 23))
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


async def test_removing_window_cleans_unsent_rows(session):
    _, window, rule = await seed(session)
    await sync_rule(session, rule, NOW)
    await session.delete(window)
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
    assert item.window_label == "最速先行"
    assert item.anchor_time_utc == dt(6, 25)

    await mark_sent(session, item.queue_id, dt(6, 22, 13))
    assert await due_reminders(session, dt(6, 22, 14)) == []  # drained


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
