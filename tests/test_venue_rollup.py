"""The concert's VENUE tags are derived from its legs, never typed."""
from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Concert, ConcertDay, ConcertTag, Tag
from app.db.service import sync_concert_venue_tags
from app.domain.types import TagKind


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    # Production registers this too; cascades silently do not fire without it.
    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _venue_tag_ids(session, concert_id):
    rows = (await session.execute(
        select(ConcertTag.tag_id)
        .join(Tag, Tag.id == ConcertTag.tag_id)
        .where(ConcertTag.concert_id == concert_id, Tag.kind == TagKind.VENUE)
    )).scalars()
    return set(rows)


async def _setup(session, venue_names):
    tags = [Tag(name=n, kind=TagKind.VENUE) for n in venue_names]
    session.add_all(tags)
    concert = Concert(title="T", event_id="ev1")
    session.add(concert)
    await session.flush()
    return concert, tags


async def test_rollup_unions_leg_venues(db):
    async with db() as session:
        concert, tags = await _setup(session, ["Zepp Haneda", "Zepp Namba"])
        for i, tag in enumerate(tags):
            session.add(ConcertDay(
                concert_id=concert.id, label=f"Day {i + 1}",
                starts_at_utc=datetime(2026, 8, i + 1, 9, tzinfo=UTC),
                venue_tag_id=tag.id,
            ))
        await session.flush()

        await sync_concert_venue_tags(session, concert.id)

        assert await _venue_tag_ids(session, concert.id) == {t.id for t in tags}


async def test_rollup_removes_a_venue_no_leg_uses(db):
    """The bug this fixes: Concert.venue was written once at creation and the
    edit route never re-derived it, so a changed venue stayed stale forever."""
    async with db() as session:
        concert, tags = await _setup(session, ["Old Hall", "New Hall"])
        old, new = tags
        session.add(ConcertTag(concert_id=concert.id, tag_id=old.id))
        session.add(ConcertDay(
            concert_id=concert.id, label="Day 1",
            starts_at_utc=datetime(2026, 8, 1, 9, tzinfo=UTC),
            venue_tag_id=new.id,
        ))
        await session.flush()

        await sync_concert_venue_tags(session, concert.id)

        assert await _venue_tag_ids(session, concert.id) == {new.id}


async def test_rollup_leaves_non_venue_tags_alone(db):
    """Group-tag expansion (invariant 3) materializes members deliberately;
    the venue rollup must never touch them."""
    async with db() as session:
        concert, _ = await _setup(session, ["Zepp Haneda"])
        group = Tag(name="Hasunosora", kind=TagKind.GROUP)
        session.add(group)
        await session.flush()
        session.add(ConcertTag(concert_id=concert.id, tag_id=group.id))
        await session.flush()

        await sync_concert_venue_tags(session, concert.id)

        all_ids = set((await session.execute(
            select(ConcertTag.tag_id).where(ConcertTag.concert_id == concert.id)
        )).scalars())
        assert group.id in all_ids


async def test_rollup_with_no_leg_venues_clears_them(db):
    async with db() as session:
        concert, tags = await _setup(session, ["Old Hall"])
        session.add(ConcertTag(concert_id=concert.id, tag_id=tags[0].id))
        session.add(ConcertDay(
            concert_id=concert.id, label="Day 1",
            starts_at_utc=datetime(2026, 8, 1, 9, tzinfo=UTC), venue_tag_id=None,
        ))
        await session.flush()

        await sync_concert_venue_tags(session, concert.id)

        assert await _venue_tag_ids(session, concert.id) == set()
