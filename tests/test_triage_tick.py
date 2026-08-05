"""The scheduler hook for AI triage: gated by a flag, picked up as a request
row, and unable to hurt delivery -- the same shape test_discovery_tick.py
pins for the sweep, with a TriageRun standing in for DiscoveryState."""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.db.session as session_mod
import app.scheduler.loop as loop_mod
from app.config import settings
from app.db.models import Base, Notification, TriageRun, User
from app.triage import TriageReport


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


async def _request(maker) -> int:
    async with maker() as s:
        run = TriageRun(requested_at=datetime.now(UTC))
        s.add(run)
        await s.commit()
        return run.id


async def _status(maker, run_id: int) -> str:
    async with maker() as s:
        run = await s.get(TriageRun, run_id)
        return run.status


def _recorder(monkeypatch):
    calls = []

    async def fake_triage(session, run, now, **kw):
        calls.append(run.id)
        run.status = "done"
        return TriageReport()

    monkeypatch.setattr(loop_mod, "run_triage", fake_triage)
    return calls


@pytest.mark.asyncio
async def test_flag_off_means_no_pickup(maker, monkeypatch):
    """A stranded request stays visible, not eaten: the flag gates the
    scheduler's own pickup, and with it off a requested row must survive a
    tick untouched, exactly as run_triage must never be called."""
    monkeypatch.setattr(settings, "triage_enabled", False)
    calls = _recorder(monkeypatch)
    run_id = await _request(maker)
    await loop_mod.tick(FakeBot())
    assert calls == []
    assert await _status(maker, run_id) == "requested"


@pytest.mark.asyncio
async def test_a_requested_run_is_picked_up_and_committed(maker, monkeypatch):
    monkeypatch.setattr(settings, "triage_enabled", True)
    calls = _recorder(monkeypatch)
    run_id = await _request(maker)
    await loop_mod.tick(FakeBot())
    assert calls == [run_id]
    assert await _status(maker, run_id) == "done"


@pytest.mark.asyncio
async def test_a_run_that_raises_is_marked_failed_not_retried(maker, monkeypatch):
    monkeypatch.setattr(settings, "triage_enabled", True)

    async def boom(session, run, now, **_kw):
        raise RuntimeError("triage exploded")

    monkeypatch.setattr(loop_mod, "run_triage", boom)
    run_id = await _request(maker)

    await loop_mod.tick(FakeBot())

    async with maker() as s:
        run = await s.get(TriageRun, run_id)
        assert run.status == "failed"
        assert run.finished_at is not None

    calls = _recorder(monkeypatch)
    await loop_mod.tick(FakeBot())
    assert calls == [], "a failed run must not re-fire on the next tick"


@pytest.mark.asyncio
async def test_a_triage_failure_does_not_break_delivery(maker, monkeypatch):
    """The reason triage gets its own try/except and its own commit. A DM
    already on the wire must not be undone by the least important operation
    in the tick."""
    monkeypatch.setattr(settings, "triage_enabled", True)

    async with maker() as s:
        s.add(User(discord_id=1, username="reiji"))
        await s.flush()
        s.add(Notification(user_id=1, body="hello", kind="ops_alert"))
        await s.commit()

    async def boom(session, run, now, **_kw):
        raise RuntimeError("triage exploded")

    monkeypatch.setattr(loop_mod, "run_triage", boom)
    await _request(maker)

    assert await loop_mod.tick(FakeBot()) == 1  # tick survives, delivery ran

    async with maker() as s:
        note = (await s.execute(select(Notification))).scalar_one()
        assert note.sent_at_utc is not None
