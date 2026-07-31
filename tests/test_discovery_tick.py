"""The scheduler hook: gated by a flag, held to a day, and unable to hurt delivery."""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.db.session as session_mod
import app.scheduler.loop as loop_mod
from app.config import Settings, settings
from app.db.models import Base, DiscoveryState, Notification, User
from app.discovery import SweepReport


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
    yield m
    await engine.dispose()


def _recorder(monkeypatch):
    calls = []

    async def fake_sweep(session, now, **kw):
        calls.append(now)
        return SweepReport()

    monkeypatch.setattr(loop_mod, "run_sweep", fake_sweep)
    return calls


def test_the_flag_ships_off():
    """Nothing reaches a third-party site until an operator turns it on."""
    assert Settings.model_fields["discovery_enabled"].default is False


@pytest.mark.asyncio
async def test_the_flag_off_means_no_sweep(maker, monkeypatch):
    monkeypatch.setattr(settings, "discovery_enabled", False)
    calls = _recorder(monkeypatch)
    await loop_mod.tick(FakeBot())
    assert calls == []


@pytest.mark.asyncio
async def test_the_flag_on_sweeps_and_commits_it(maker, monkeypatch):
    """The real sweep, against an empty tag table -- so nothing is fetched, and
    what is being asserted is that tick ran it and committed its own work."""
    monkeypatch.setattr(settings, "discovery_enabled", True)
    await loop_mod.tick(FakeBot())
    async with maker() as s:
        state = (await s.execute(select(DiscoveryState))).scalar_one()
        assert state.last_run_at is not None


@pytest.mark.asyncio
async def test_a_sweep_that_already_ran_today_is_not_repeated(maker, monkeypatch):
    """86 third-party fetches once a day, not once a minute."""
    monkeypatch.setattr(settings, "discovery_enabled", True)
    async with maker() as s:
        s.add(DiscoveryState(id=1, last_run_at=datetime.now(UTC) - timedelta(hours=2)))
        await s.commit()
    calls = _recorder(monkeypatch)
    await loop_mod.tick(FakeBot())
    assert calls == []


@pytest.mark.asyncio
async def test_a_failing_sweep_leaves_the_delivered_notice_marked_sent(maker, monkeypatch):
    """The reason the sweep gets its own try/except and its own commit. The DM
    is already on the wire by the time it runs; a sweep that raised into the
    delivery transaction would roll `sent_at_utc` back and the next tick would
    send the same notice again. The least important operation in a tick must
    never be able to undo the most important one."""
    monkeypatch.setattr(settings, "discovery_enabled", True)
    async with maker() as s:
        s.add(User(discord_id=1, username="reiji"))
        await s.flush()
        s.add(Notification(user_id=1, body="hello", kind="ops_alert"))
        await s.commit()

    async def boom(*_a, **_kw):
        raise RuntimeError("sweep exploded")

    monkeypatch.setattr(loop_mod, "run_sweep", boom)

    assert await loop_mod.tick(FakeBot()) == 1  # tick survives

    async with maker() as s:
        note = (await s.execute(select(Notification))).scalar_one()
        assert note.sent_at_utc is not None
