"""The scheduler hook for round watch: runs every tick (no cadence clock, see
run_quiet_ladder_pass's docstring) and cannot hurt reminder delivery -- the
same shape test_discovery_tick.py and test_triage_tick.py pin for their own
blocks, with a quiet Concert standing in for a DiscoveryState/TriageRun row."""

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.db.session as session_mod
import app.scheduler.loop as loop_mod
from app.config import settings
from app.db.models import Base, Concert, Notification, User

ADMIN_ID = 42


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
    # Tick 1 of 5: keep the health/prune cadence out of these assertions.
    monkeypatch.setattr(loop_mod, "_tick_count", 0)
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    yield m
    await engine.dispose()


async def _add_quiet_concert(maker, event_id="quiet-one"):
    async with maker() as s:
        s.add(User(discord_id=99, username="editor"))
        await s.flush()
        s.add(Concert(title=event_id, event_id=event_id, created_by=99))
        await s.commit()


async def _quiet_ladder_notices(maker):
    async with maker() as s:
        return list((await s.execute(
            select(Notification).where(Notification.kind == "quiet_ladder")
        )).scalars())


@pytest.mark.asyncio
async def test_a_quiet_concert_gets_a_notice_from_a_real_tick(maker):
    """The wiring itself: deleting the whole round-watch block from tick would
    leave this green nowhere else -- reconcile only runs when tick calls it."""
    await _add_quiet_concert(maker)
    await loop_mod.tick(FakeBot())

    notices = await _quiet_ladder_notices(maker)
    assert len(notices) == 1
    assert notices[0].user_id == ADMIN_ID
    assert "quiet-one" in notices[0].body


@pytest.mark.asyncio
async def test_a_failing_pass_does_not_break_delivery(maker, monkeypatch):
    """The reason round watch gets its own try/except and its own commit,
    below reminder delivery. A DM already on the wire must not be undone by
    the least important operation in the tick."""
    async with maker() as s:
        s.add(User(discord_id=1, username="reiji"))
        await s.flush()
        s.add(Notification(user_id=1, body="hello", kind="ops_alert"))
        await s.commit()

    async def boom(*_a, **_kw):
        raise RuntimeError("round watch exploded")

    monkeypatch.setattr(loop_mod, "run_quiet_ladder_pass", boom)

    assert await loop_mod.tick(FakeBot()) == 1  # tick survives, delivery ran

    async with maker() as s:
        note = (await s.execute(
            select(Notification).where(Notification.kind == "ops_alert")
        )).scalar_one()
        assert note.sent_at_utc is not None


@pytest.mark.asyncio
async def test_a_failing_pass_leaves_no_partial_stamp(maker, monkeypatch):
    """tick's except clause must actually roll the transaction back, not just
    swallow the exception -- a stamp written just before the pass raised must
    not survive, or the concert becomes permanently un-announceable
    (quiet_since_utc set, no notice ever queued to explain why). The fake
    reproduces the real shape -- do some work, THEN raise -- rather than
    raising immediately, so this cannot pass merely because nothing ran."""
    await _add_quiet_concert(maker, "quiet-two")

    async def stamp_then_boom(session, now):
        concert = (await session.execute(
            select(Concert).where(Concert.event_id == "quiet-two")
        )).scalar_one()
        concert.quiet_since_utc = now
        await session.flush()
        raise RuntimeError("round watch exploded after doing some work")

    monkeypatch.setattr(loop_mod, "run_quiet_ladder_pass", stamp_then_boom)
    await loop_mod.tick(FakeBot())

    async with maker() as s:
        stamp = (await s.execute(
            select(Concert.quiet_since_utc).where(Concert.event_id == "quiet-two")
        )).scalar_one()
        assert stamp is None
