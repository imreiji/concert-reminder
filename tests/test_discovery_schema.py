"""The discovery tables: an event is identified by its Eventernote id."""

import datetime as dt

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, ConcertDay, DiscoveredEvent


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


async def test_a_lead_round_trips(db):
    async with db() as s:
        s.add(DiscoveredEvent(
            source_event_id="464372",
            title="ラブライブ！フェス Day.2",
            event_date=dt.date(2026, 11, 15),
            venue="バンテリンドーム ナゴヤ",
        ))
        await s.commit()
    async with db() as s:
        row = (await s.execute(select(DiscoveredEvent))).scalar_one()
        assert row.event_date == dt.date(2026, 11, 15)
        assert row.announced_at is None and row.dismissed_at is None
        assert row.concert_id is None


async def test_the_event_id_is_unique(db):
    """One event, one row -- the anniversary concert lists nine catalogue tags
    as performers, and without this the maintainer hears about it nine times."""
    async with db() as s:
        s.add(DiscoveredEvent(
            source_event_id="1", title="a", event_date=dt.date(2026, 1, 1), venue=""
        ))
        await s.commit()
    async with db() as s:
        s.add(DiscoveredEvent(
            source_event_id="1", title="b", event_date=dt.date(2026, 1, 2), venue=""
        ))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_event_date_is_a_plain_date_not_a_datetime(db):
    """The list gives a calendar day and no time. Inventing midnight would put a
    fake deadline-shaped value into a schema where every datetime is an aware
    UTC instant (invariant 1)."""
    async with db() as s:
        s.add(DiscoveredEvent(
            source_event_id="2", title="a", event_date=dt.date(2026, 3, 4), venue=""
        ))
        await s.commit()
        row = (await s.execute(select(DiscoveredEvent))).scalar_one()
        assert type(row.event_date) is dt.date


async def test_a_leg_can_carry_its_eventernote_event_id(db):
    assert ConcertDay.eventernote_event_id is not None
