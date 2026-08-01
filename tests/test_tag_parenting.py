"""parent_id after widening: subunits, characters, and no loops."""

import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Tag
from app.db.service import would_create_tag_cycle
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


async def _chain(s, *names):
    """Create GROUP tags parented in a chain: names[0] is the root."""
    made = []
    for i, name in enumerate(names):
        tag = Tag(name=name, kind=TagKind.GROUP, slug=name,
                  parent_id=made[i - 1].id if i else None)
        s.add(tag)
        await s.flush()
        made.append(tag)
    return made


async def test_a_tag_may_not_be_its_own_parent(db):
    async with db() as s:
        (a,) = await _chain(s, "a")
        assert await would_create_tag_cycle(s, a.id, a.id) is True


async def test_a_tag_may_not_be_parented_to_its_own_descendant(db):
    """a > b > c. Making a's parent c would close the loop."""
    async with db() as s:
        a, b, c = await _chain(s, "a", "b", "c")
        assert await would_create_tag_cycle(s, a.id, c.id) is True


async def test_an_unrelated_parent_is_fine(db):
    async with db() as s:
        a, b = await _chain(s, "a", "b")
        other = Tag(name="other", kind=TagKind.GROUP, slug="other")
        s.add(other)
        await s.flush()
        assert await would_create_tag_cycle(s, other.id, b.id) is False


async def test_the_walk_terminates_on_pre_existing_bad_data(db):
    """If a loop somehow already exists in the table, the guard must return
    rather than spin forever -- a guard that hangs is worse than none."""
    async with db() as s:
        a, b = await _chain(s, "a", "b")
        a.parent_id = b.id          # a > b > a, written behind the guard's back
        await s.flush()
        other = Tag(name="other", kind=TagKind.GROUP, slug="other")
        s.add(other)
        await s.flush()
        assert await would_create_tag_cycle(s, other.id, a.id) is False
