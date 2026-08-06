"""The tick dispatches a picked-up run on its kind: "complete" to
run_completion, anything else to run_triage -- the same pickup shape
test_triage_tick.py pins, extended to a second run kind. Reuses that file's
fixtures and monkeypatching style verbatim (there is no shared `session`/
`tick_env` fixture in this suite; each of these tick tests builds its own
in-memory engine, exactly as test_triage_tick.py does)."""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.db.session as session_mod
import app.scheduler.loop as loop_mod
from app.config import settings
from app.db.models import Base, TriageRun
from app.draft_completion import CompletionReport
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


async def _request(maker, kind: str = "classify") -> int:
    async with maker() as s:
        run = TriageRun(requested_at=datetime.now(UTC), kind=kind)
        s.add(run)
        await s.commit()
        return run.id


async def _status(maker, run_id: int) -> str:
    async with maker() as s:
        run = await s.get(TriageRun, run_id)
        return run.status


@pytest.mark.asyncio
async def test_a_requested_completion_run_is_dispatched_to_run_completion(maker, monkeypatch):
    calls = []

    async def fake_run_completion(session, run, now, **kw):
        calls.append(run.id)
        run.status = "done"
        return CompletionReport()

    async def never(*a, **kw):
        raise AssertionError("a completion run must not go to run_triage")

    monkeypatch.setattr(loop_mod, "run_completion", fake_run_completion)
    monkeypatch.setattr(loop_mod, "run_triage", never)
    monkeypatch.setattr(settings, "triage_enabled", True)

    run_id = await _request(maker, kind="complete")
    await loop_mod.tick(FakeBot())
    assert calls == [run_id]


@pytest.mark.asyncio
async def test_a_classify_run_still_goes_to_run_triage(maker, monkeypatch):
    calls = []

    async def fake_run_triage(session, run, now, **kw):
        calls.append(run.id)
        run.status = "done"
        return TriageReport()

    async def never(*a, **kw):
        raise AssertionError("a classify run must not go to run_completion")

    monkeypatch.setattr(loop_mod, "run_triage", fake_run_triage)
    monkeypatch.setattr(loop_mod, "run_completion", never)
    monkeypatch.setattr(settings, "triage_enabled", True)

    run_id = await _request(maker, kind="classify")
    await loop_mod.tick(FakeBot())
    assert calls == [run_id]


@pytest.mark.asyncio
async def test_a_dead_completion_run_is_marked_failed_on_a_cleaned_transaction(maker, monkeypatch):
    # The failure mode this exists for: a rollback restores the row to
    # "requested", and a dead run then re-fires fifteen fetches and fifteen
    # paid calls every sixty seconds forever.
    async def explode(session, run, now, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(loop_mod, "run_completion", explode)
    monkeypatch.setattr(settings, "triage_enabled", True)

    run_id = await _request(maker, kind="complete")
    await loop_mod.tick(FakeBot())

    assert await _status(maker, run_id) == "failed"
