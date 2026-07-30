"""Tag handles: a stable identity that is not the name.

Spec: docs/superpowers/specs/2026-07-29-tag-handles-design.md
"""

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Tag
from app.db.service import assign_tag_slug
from app.domain.types import TagKind


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")  # match production

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _add(s, **kw) -> Tag:
    tag = Tag(**kw)
    s.add(tag)
    await assign_tag_slug(s, tag)
    return tag


async def test_handle_comes_from_the_english_name(db):
    async with db() as s:
        tag = await _add(s, name="蓮ノ空", name_en="Hasunosora", kind=TagKind.GROUP)
        await s.commit()
        assert tag.slug == "hasunosora"


async def test_handle_falls_back_to_the_canonical_name(db):
    async with db() as s:
        tag = await _add(s, name="Zepp Haneda", kind=TagKind.VENUE)
        await s.commit()
        assert tag.slug == "zepp-haneda"


async def test_japanese_only_name_gets_an_honest_placeholder(db):
    """NOT "concert" -- that is slugify's fallback and would be a lie here.

    The kind is the base, numbered by the same de-duplication as any other
    collision. The spec asked for `{kind}-{id}`; see assign_tag_slug's docstring
    for why the id is not obtainable here and buys nothing.
    """
    async with db() as s:
        first = await _add(s, name="蓮ノ空", kind=TagKind.ARTIST)
        second = await _add(s, name="スクールアイドル", kind=TagKind.ARTIST)
        venue = await _add(s, name="市民ホール", kind=TagKind.VENUE)
        await s.commit()
        assert first.slug == "artist"
        assert second.slug == "artist-2"
        assert venue.slug == "venue"


async def test_colliding_handles_get_a_numeric_suffix(db):
    async with db() as s:
        a = await _add(s, name="Yuki Sato", kind=TagKind.ARTIST)
        b = await _add(s, name="Yuki Sato", kind=TagKind.ARTIST)
        c = await _add(s, name="yuki sato", kind=TagKind.ARTIST)
        await s.commit()
        assert [a.slug, b.slug, c.slug] == ["yuki-sato", "yuki-sato-2", "yuki-sato-3"]


async def test_two_performers_may_share_a_name(db):
    """The requirement that killed kind-scoped name uniqueness."""
    async with db() as s:
        await _add(s, name="Yuki Sato", kind=TagKind.ARTIST)
        await _add(s, name="Yuki Sato", kind=TagKind.ARTIST)
        await s.commit()
    async with db() as s:
        rows = list((await s.execute(select(Tag).where(Tag.name == "Yuki Sato"))).scalars())
        assert len(rows) == 2
        assert len({r.slug for r in rows}) == 2


async def test_a_venue_may_share_a_name_with_a_group(db):
    """The owner ruling that was documented but never implemented -- this is
    the shape that used to 500."""
    async with db() as s:
        await _add(s, name="Aqours", kind=TagKind.GROUP)
        await _add(s, name="Aqours", kind=TagKind.VENUE)
        await s.commit()
    async with db() as s:
        assert len(list((await s.execute(select(Tag))).scalars())) == 2


async def test_the_handle_itself_is_still_unique(db):
    from sqlalchemy.exc import IntegrityError

    async with db() as s:
        s.add(Tag(name="A", kind=TagKind.ARTIST, slug="dup"))
        s.add(Tag(name="B", kind=TagKind.ARTIST, slug="dup"))
        with pytest.raises(IntegrityError):
            await s.commit()
