"""Pruning a character takes its seiyuu -- unless someone else still needs her."""

from sqlalchemy import select

from app.db.models import Concert, ConcertTag, Tag
from app.db.service import attach_tag, detach_tag
from app.domain.types import TagKind


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


async def test_a_non_character_carrying_a_voiced_by_link_cascades_nothing(db):
    """The `kind is not CHARACTER` half of the guard, pinned.

    Every other test here leaves it unreachable: the only tag with no
    voiced_by_tag_id is also the only non-character, so removing the kind check
    changes no outcome. The catalogue round-trip makes the state real -- voiced_by
    joins COMPARABLE_FIELDS with NO kind restriction and a blank DB value is an
    auto-applied fill, so a hand-edited file carrying `voiced_by:` on an artist
    row writes exactly this shape. Detaching such a tag must stay a plain detach.
    """
    async with db() as s:
        concert, imai, _a, _b = await _two_roles(s)
        stray = Tag(name="迷子", kind=TagKind.ARTIST, slug="stray",
                    voiced_by_tag_id=imai.id)
        s.add(stray)
        await s.flush()
        await attach_tag(s, concert.id, stray)
        await attach_tag(s, concert.id, imai)

        await detach_tag(s, concert.id, stray.id)
        assert await _attached(s, concert.id) == {imai.id}, (
            "only a CHARACTER cascades her seiyuu off the bill"
        )


async def test_pruning_a_character_with_no_seiyuu_is_a_plain_detach(db):
    async with db() as s:
        concert, *_ = await _two_roles(s)
        orphan = Tag(name="???", kind=TagKind.CHARACTER, slug="orphan")
        s.add(orphan)
        await s.flush()
        await attach_tag(s, concert.id, orphan)
        await detach_tag(s, concert.id, orphan.id)
        assert await _attached(s, concert.id) == set()
