"""tick() writes the delivery log without endangering delivery bookkeeping."""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.db.session as session_mod
import app.scheduler.loop as loop_mod
from app.db.models import (
    Base,
    Concert,
    ConcertDay,
    DeliveryLog,
    ReminderQueue,
    ReminderRule,
    Round,
    User,
)
from app.domain.types import Anchor, DeliveryOutcome, RoundKind


class FakeUser:
    def __init__(self):
        self.sent = []

    async def send(self, *a, **kw):
        self.sent.append((a, kw))


class FakeBot:
    def __init__(self):
        self.user_obj = FakeUser()

    def get_user(self, _uid):
        return self.user_obj


@pytest_asyncio.fixture()
async def maker(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    m = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(session_mod, "SessionMaker", m)
    monkeypatch.setattr(loop_mod, "SessionMaker", m)
    yield m
    await engine.dispose()


async def _due_reminder(session):
    """One reminder already due: a concert with a leg, a round with a close
    time, a rule, and a queue row whose fire time has passed."""
    past = datetime.now(UTC) - timedelta(minutes=5)
    session.add(User(discord_id=1, username="reiji", timezone="America/Moncton"))
    concert = Concert(event_id="c", title="スノーミク2027", title_en="Snow Miku 2027")
    session.add(concert)
    await session.flush()
    day = ConcertDay(concert_id=concert.id, label="Day 1", starts_at_utc=past + timedelta(days=30))
    # `kind` has no column default, so it must be supplied: the plan's snippet
    # omitted it and the insert failed on rounds.kind's NOT NULL.
    round_ = Round(
        concert_id=concert.id,
        kind=RoundKind.LOTTERY_ROUND,
        label="一次先行",
        closes_at_utc=past + timedelta(days=7),
    )
    session.add_all([day, round_])
    await session.flush()
    rule = ReminderRule(user_id=1, round_id=round_.id, anchor=Anchor.CLOSES, offset_days=-1)
    session.add(rule)
    await session.flush()
    session.add(
        ReminderQueue(
            rule_id=rule.id, round_id=round_.id, anchor=Anchor.CLOSES, fire_at_utc=past
        )
    )
    await session.commit()


@pytest.mark.asyncio
async def test_tick_logs_the_delivery(maker):
    async with maker() as s:
        await _due_reminder(s)

    delivered = await loop_mod.tick(FakeBot())
    assert delivered == 1

    async with maker() as s:
        row = (await s.execute(select(DeliveryLog))).scalar_one()
        assert row.outcome is DeliveryOutcome.SUCCESS
        assert row.concert_title == "Snow Miku 2027"
        assert row.anchor is Anchor.CLOSES


@pytest.mark.asyncio
async def test_an_empty_tick_writes_nothing(maker):
    assert await loop_mod.tick(FakeBot()) == 0
    async with maker() as s:
        assert (await s.execute(select(DeliveryLog))).all() == []


@pytest.mark.asyncio
async def test_a_logging_failure_leaves_the_reminder_marked_sent(maker, monkeypatch):
    """The reason this runs in its own commit AFTER the delivery commit. The
    DM is already on the wire; if a logging bug could roll back sent_at_utc,
    the next tick would send it again. Duplicate reminders must never be
    reachable from an observability feature."""
    async with maker() as s:
        await _due_reminder(s)

    async def boom(*a, **kw):
        raise RuntimeError("log write failed")

    monkeypatch.setattr(loop_mod, "record_deliveries", boom)

    assert await loop_mod.tick(FakeBot()) == 1  # tick survives

    async with maker() as s:
        queued = (await s.execute(select(ReminderQueue))).scalar_one()
        assert queued.sent_at_utc is not None  # bookkeeping survived
        assert (await s.execute(select(DeliveryLog))).all() == []
