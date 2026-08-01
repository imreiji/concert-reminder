"""Pruning a character takes its seiyuu -- unless someone else still needs her."""

import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Concert, ConcertTag, Tag
from app.db.service import attach_tag, detach_tag
from app.domain.types import TagKind


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


async def _attached(s, concert_id):
    return set((await s.execute(
        select(ConcertTag.tag_id).where(ConcertTag.concert_id == concert_id)
    )).scalars())


async def _two_roles(s):
    """One seiyuu voicing TWO characters -- the case the refinement exists for."""
    concert = Concert(title="im@s", event_id="imas-1")
    imai = Tag(name="今井麻美", kind=TagKind.ARTIST, slug="asami-imai")
    s.add_all([concert, imai])
    await s.flush()
    a = Tag(name="如月千早", kind=TagKind.CHARACTER, slug="chihaya",
            voiced_by_tag_id=imai.id)
    b = Tag(name="別の役", kind=TagKind.CHARACTER, slug="other-role",
            voiced_by_tag_id=imai.id)
    s.add_all([a, b])
    await s.flush()
    return concert, imai, a, b


async def test_pruning_a_character_detaches_her_seiyuu(db):
    async with db() as s:
        concert, imai, a, _b = await _two_roles(s)
        await attach_tag(s, concert.id, a)
        await detach_tag(s, concert.id, a.id)
        assert await _attached(s, concert.id) == set()


async def test_the_seiyuu_stays_when_another_character_still_needs_her(db):
    """Two roles, one voice. Pruning one must not remove the other's performer."""
    async with db() as s:
        concert, imai, a, b = await _two_roles(s)
        await attach_tag(s, concert.id, a)
        await attach_tag(s, concert.id, b)
        await detach_tag(s, concert.id, a.id)
        got = await _attached(s, concert.id)
        assert b.id in got
        assert imai.id in got, "the surviving character still needs her"


async def test_pruning_an_artist_touches_nothing_else(db):
    async with db() as s:
        concert, imai, a, _b = await _two_roles(s)
        await attach_tag(s, concert.id, a)
        await detach_tag(s, concert.id, imai.id)
        assert await _attached(s, concert.id) == {a.id}


async def test_pruning_a_character_with_no_seiyuu_is_a_plain_detach(db):
    async with db() as s:
        concert, *_ = await _two_roles(s)
        orphan = Tag(name="???", kind=TagKind.CHARACTER, slug="orphan")
        s.add(orphan)
        await s.flush()
        await attach_tag(s, concert.id, orphan)
        await detach_tag(s, concert.id, orphan.id)
        assert await _attached(s, concert.id) == set()
