"""Attach-time chaining: character -> seiyuu, and group -> character -> seiyuu."""

import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Concert, ConcertTag, Tag, TagMember
from app.db.service import attach_tag
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


async def _seed(s):
    """765PRO ALLSTARS containing two characters, each with a seiyuu."""
    concert = Concert(title="im@s live", event_id="imas-1")
    imai = Tag(name="今井麻美", kind=TagKind.ARTIST, slug="asami-imai")
    nakamura = Tag(name="中村繪里子", kind=TagKind.ARTIST, slug="eriko-nakamura")
    s.add_all([concert, imai, nakamura])
    await s.flush()
    chihaya = Tag(name="如月千早", kind=TagKind.CHARACTER, slug="chihaya",
                  voiced_by_tag_id=imai.id)
    haruka = Tag(name="天海春香", kind=TagKind.CHARACTER, slug="haruka",
                 voiced_by_tag_id=nakamura.id)
    group = Tag(name="765PRO ALLSTARS", kind=TagKind.GROUP, slug="765pro")
    s.add_all([chihaya, haruka, group])
    await s.flush()
    s.add_all([
        TagMember(group_tag_id=group.id, member_tag_id=chihaya.id),
        TagMember(group_tag_id=group.id, member_tag_id=haruka.id),
    ])
    await s.flush()
    return concert, group, chihaya, haruka, imai, nakamura


async def _attached(s, concert_id):
    return set((await s.execute(
        select(ConcertTag.tag_id).where(ConcertTag.concert_id == concert_id)
    )).scalars())


async def test_attaching_a_character_attaches_her_seiyuu(db):
    async with db() as s:
        concert, _g, chihaya, _h, imai, _n = await _seed(s)
        added = await attach_tag(s, concert.id, chihaya)
        assert imai.id in {t.id for t in added}, "the seiyuu must be RETURNED too"
        assert imai.id in await _attached(s, concert.id)


async def test_attaching_a_group_reaches_the_seiyuu_through_its_characters(db):
    """The chained step. Without it a group-credited show misses every seiyuu
    follower, which is the entire point of the feature."""
    async with db() as s:
        concert, group, chihaya, haruka, imai, nakamura = await _seed(s)
        await attach_tag(s, concert.id, group)
        got = await _attached(s, concert.id)
        assert {group.id, chihaya.id, haruka.id, imai.id, nakamura.id} <= got


async def test_attaching_a_seiyuu_never_attaches_her_characters(db):
    """Deliberately asymmetric -- she plays events with no im@s connection."""
    async with db() as s:
        concert, _g, chihaya, _h, imai, _n = await _seed(s)
        await attach_tag(s, concert.id, imai)
        assert chihaya.id not in await _attached(s, concert.id)


async def test_the_seiyuu_is_attached_even_when_expansion_is_off(db):
    """expand=False exists so the creation form's explicit artist list is not
    overridden. Attaching the seiyuu overrides nothing -- it is a consequence
    of the character being present, and without it a concert created through
    that form would never match her followers."""
    async with db() as s:
        concert, _g, chihaya, _h, imai, _n = await _seed(s)
        await attach_tag(s, concert.id, chihaya, expand=False)
        assert imai.id in await _attached(s, concert.id)


async def test_a_character_with_no_seiyuu_attaches_cleanly(db):
    async with db() as s:
        concert, *_ = await _seed(s)
        orphan = Tag(name="???", kind=TagKind.CHARACTER, slug="orphan")
        s.add(orphan)
        await s.flush()
        added = await attach_tag(s, concert.id, orphan)
        assert [t.id for t in added] == [orphan.id]


async def test_an_already_attached_seiyuu_is_not_added_twice(db):
    async with db() as s:
        concert, _g, chihaya, _h, imai, _n = await _seed(s)
        await attach_tag(s, concert.id, imai)
        added = await attach_tag(s, concert.id, chihaya)
        assert imai.id not in {t.id for t in added}
        assert len(await _attached(s, concert.id)) == 2
