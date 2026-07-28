"""tick() writes the delivery log without endangering delivery bookkeeping."""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.db.session as session_mod
import app.scheduler.loop as loop_mod
from app.config import settings
from app.db.models import (
    Base,
    Concert,
    ConcertDay,
    DeliveryLog,
    Notification,
    OpsCheckState,
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


@pytest.mark.asyncio
async def test_tick_queues_a_digest_for_the_admin(maker, monkeypatch):
    monkeypatch.setattr(settings, "discord_token", "x")
    monkeypatch.setattr(settings, "admin_whitelist", "1")
    async with maker() as s:
        await _due_reminder(s)

    await loop_mod.tick(FakeBot())

    async with maker() as s:
        note = (
            await s.execute(select(Notification).where(Notification.kind == "delivery_digest"))
        ).scalar_one()
        assert "1 sent" in note.body
        assert note.concert_id is None


@pytest.mark.asyncio
async def test_the_digests_own_delivery_is_not_logged(maker, monkeypatch):
    """End-to-end feedback-loop guard: tick 1 delivers the reminder and
    queues the digest; tick 2 delivers the digest. After tick 2 there must
    still be exactly one log row, or the bot DMs admins once a minute
    forever."""
    monkeypatch.setattr(settings, "discord_token", "x")
    monkeypatch.setattr(settings, "admin_whitelist", "1")
    async with maker() as s:
        await _due_reminder(s)

    await loop_mod.tick(FakeBot())  # delivers reminder, queues digest
    await loop_mod.tick(FakeBot())  # delivers digest

    async with maker() as s:
        assert len((await s.execute(select(DeliveryLog))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_a_failing_prune_cannot_suppress_health_alerting(maker, monkeypatch):
    """The prune gets its own commit, not a share of the health block's.

    It first shipped inside health's try/commit, which made the comment there
    a lie: because they shared one commit, a prune that raised rolled the
    OpsCheckState writes back with it. The cheapest, least important operation
    in the tick could therefore silently suppress the pass that decides whether
    to page an admin -- and it would do so exactly when the DB is under enough
    stress to make a DELETE fail, which is when you most want the alert.
    """
    async def boom(*_a, **_kw):
        raise RuntimeError("prune exploded")

    monkeypatch.setattr(loop_mod, "prune_delivery_log", boom)
    monkeypatch.setattr(loop_mod, "_tick_count", loop_mod.HEALTH_EVERY_N_TICKS - 1)

    await loop_mod.tick(FakeBot())  # must not raise

    async with maker() as s:
        # evaluate_and_alert persists one row per registered check; if the
        # prune's rollback had reached them there would be none.
        assert (await s.execute(select(OpsCheckState))).scalars().all() != []
