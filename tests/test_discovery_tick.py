"""The scheduler hook: gated by a flag, held to a day, and unable to hurt delivery."""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.db.service as service
import app.db.session as session_mod
import app.scheduler.loop as loop_mod
from app.config import Settings, settings
from app.db.models import Base, DiscoveryState, Notification, User
from app.discovery import SweepReport


@pytest.fixture(autouse=True)
def _no_calendar_feeds(monkeypatch):
    """This file is about the scheduler hook (the flag, the daily clock, the
    manual request), not the calendar pass -- that behavior is pinned in
    test_discovery_sweep.py. A few tests here call the REAL run_sweep (not the
    `_recorder` stub below) against an empty tag table; without this they would
    also walk the real CALENDAR_FEEDS roster and hit the network guard."""
    monkeypatch.setattr("app.discovery.CALENDAR_FEEDS", ())


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


@pytest.mark.asyncio
async def test_a_failing_sweep_still_counts_as_todays_sweep(maker, monkeypatch):
    """run_sweep stamps its clock in a `finally`, but the handler's rollback
    would take that stamp with it -- so the handler re-stamps on the clean
    transaction. Without this the sweep that died runs again 60 seconds later
    and dies the same way, forever."""
    monkeypatch.setattr(settings, "discovery_enabled", True)

    async def boom(*_a, **_kw):
        raise RuntimeError("sweep exploded")

    monkeypatch.setattr(loop_mod, "run_sweep", boom)

    await loop_mod.tick(FakeBot())

    async with maker() as s:
        state = (await s.execute(select(DiscoveryState))).scalar_one()
        assert state.last_run_at is not None


# -- The manual sweep request -------------------------------------------------


async def _request(maker, when=None):
    async with maker() as s:
        await s.merge(DiscoveryState(id=1, sweep_requested_at=when or datetime.now(UTC)))
        await s.commit()


async def _requested(maker) -> bool:
    async with maker() as s:
        state = (await s.execute(select(DiscoveryState))).scalar_one()
        return state.sweep_requested_at is not None


@pytest.mark.asyncio
async def test_a_request_sweeps_with_the_flag_off(maker, monkeypatch):
    """Deliberate: the flag gates the AUTOMATIC daily job, so an operator can
    scrape on demand without committing to a schedule. Without this, the button
    would do nothing at all on the default configuration."""
    monkeypatch.setattr(settings, "discovery_enabled", False)
    calls = _recorder(monkeypatch)
    await _request(maker)
    await loop_mod.tick(FakeBot())
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_request_ignores_the_daily_clock(maker, monkeypatch):
    """The other half: with the flag ON but the 24h clock not yet up,
    discovery_due is False and the sweep still runs. `requested or due`, never
    `requested and due` -- otherwise the button is only pressable on the one day
    of the week it would have swept anyway."""
    monkeypatch.setattr(settings, "discovery_enabled", True)
    calls = _recorder(monkeypatch)
    async with maker() as s:
        s.add(DiscoveryState(
            id=1,
            last_run_at=datetime.now(UTC) - timedelta(hours=2),
            sweep_requested_at=datetime.now(UTC),
        ))
        await s.commit()
    await loop_mod.tick(FakeBot())
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_no_request_and_no_flag_still_sweeps_nothing(maker, monkeypatch):
    """The control for both tests above: reading the request on every tick must
    not become a reason to sweep on every tick."""
    monkeypatch.setattr(settings, "discovery_enabled", False)
    calls = _recorder(monkeypatch)
    async with maker() as s:
        s.add(DiscoveryState(id=1))
        await s.commit()
    await loop_mod.tick(FakeBot())
    assert calls == []


@pytest.mark.asyncio
async def test_a_completed_sweep_clears_the_request(maker, monkeypatch):
    """The real sweep against an empty tag table, so what is asserted is the
    clearing and not the fetching."""
    monkeypatch.setattr(settings, "discovery_enabled", False)
    await _request(maker)
    await loop_mod.tick(FakeBot())
    assert not await _requested(maker)


@pytest.mark.asyncio
async def test_a_cleared_request_does_not_sweep_again_next_tick(maker, monkeypatch):
    """What the clearing is FOR, stated end to end: one press, one sweep. The
    flag is off, so the 24h clock is not what stops the second tick."""
    monkeypatch.setattr(settings, "discovery_enabled", False)
    await _request(maker)
    await loop_mod.tick(FakeBot())
    calls = _recorder(monkeypatch)
    await loop_mod.tick(FakeBot())
    assert calls == []


@pytest.mark.asyncio
async def test_a_request_survives_neither_a_sweep_that_raised_nor_the_rollback(
    maker, monkeypatch
):
    """THE rollback path. run_sweep clears the request in its own `finally` --
    and the handler's rollback takes that clear with it, exactly as it takes
    last_run_at. So the handler's re-stamp must clear it again on the clean
    transaction. Without that, a sweep dying on a malformed third-party page
    re-runs every 60 seconds forever: 86 fetches a minute at a third party.

    The fake reproduces the real shape -- stamp (as run_sweep's finally does),
    then raise -- rather than raising immediately, because raising immediately
    would pass even if run_sweep's own clear were the only one."""
    monkeypatch.setattr(settings, "discovery_enabled", False)

    async def stamp_then_boom(session, now, **_kw):
        await service.stamp_discovery_run(session, now, fetched=3, failed=0)
        raise RuntimeError("sweep exploded after doing some work")

    monkeypatch.setattr(loop_mod, "run_sweep", stamp_then_boom)
    await _request(maker)

    await loop_mod.tick(FakeBot())

    assert not await _requested(maker), "the re-stamp cleared it after the rollback"
    async with maker() as s:
        state = (await s.execute(select(DiscoveryState))).scalar_one()
        assert state.last_fetched is None, "a sweep that raised reports no counts"


@pytest.mark.asyncio
async def test_a_failed_manual_sweep_is_not_retried_every_tick(maker, monkeypatch):
    """The consequence of the test above, asserted as behaviour: the second tick
    must not sweep again."""
    monkeypatch.setattr(settings, "discovery_enabled", False)

    async def boom(*_a, **_kw):
        raise RuntimeError("sweep exploded")

    monkeypatch.setattr(loop_mod, "run_sweep", boom)
    await _request(maker)
    await loop_mod.tick(FakeBot())

    calls = _recorder(monkeypatch)
    await loop_mod.tick(FakeBot())
    assert calls == []
