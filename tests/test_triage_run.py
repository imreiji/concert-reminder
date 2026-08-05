"""TriageRun: a request queue of one, and the service CRUD around it.

Mirrors DiscoveryState's request/stamp shape (see test_discovery_sweep.py),
but a triage run is a ROW per request rather than a single-row state table --
each run keeps its own counts, so /admin/discoveries/triage can show a
history rather than only "last run"."""

from datetime import UTC, datetime, timedelta

import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import service
from app.db.models import Base, PendingDraft, TriageRun
from app.db.service import ensure_user

NOW = datetime(2026, 8, 5, 3, 0, tzinfo=UTC)
ADMIN_ID = 900001


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_request_triage_is_idempotent_while_one_is_pending(db):
    async with db() as s:
        await ensure_user(s, ADMIN_ID, "admin")
        first = await service.request_triage(s, NOW, ADMIN_ID)
        second = await service.request_triage(s, NOW + timedelta(minutes=1), ADMIN_ID)
        assert first.id == second.id
        rows = (await s.execute(select(TriageRun))).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "requested"


async def test_pending_picks_the_oldest_requested_row(db):
    async with db() as s:
        await ensure_user(s, ADMIN_ID, "admin")
        run = await service.request_triage(s, NOW, ADMIN_ID)
        run.status = "failed"  # simulate a finished one, then request again
        await s.flush()
        newer = await service.request_triage(s, NOW + timedelta(hours=1), ADMIN_ID)
        assert (await service.pending_triage_run(s)).id == newer.id
        assert (await service.latest_triage_run(s)).id == newer.id


async def test_mark_triage_failed_refetches_by_id(db):
    async with db() as s:
        await ensure_user(s, ADMIN_ID, "admin")
        run = await service.request_triage(s, NOW, ADMIN_ID)
        run_id = run.id
        await s.commit()
    async with db() as s:  # fresh session: the loop's post-rollback state
        await service.mark_triage_failed(s, run_id, NOW, "boom " * 100)
        await s.commit()
    async with db() as s:
        row = await service.get_triage_run(s, run_id)
        assert row.status == "failed"
        assert row.finished_at == NOW
        assert len(row.error) <= 300


async def test_pending_draft_texts_excludes_committed_and_discarded(db):
    async with db() as s:
        await ensure_user(s, ADMIN_ID, "admin")
        s.add(PendingDraft(draft_text="open one", title="a", created_by=ADMIN_ID))
        s.add(PendingDraft(draft_text="done", title="b", created_by=ADMIN_ID,
                           committed_at=NOW))
        s.add(PendingDraft(draft_text="gone", title="c", created_by=ADMIN_ID,
                           discarded_at=NOW))
        await s.flush()
        assert await service.pending_draft_texts(s) == ["open one"]
