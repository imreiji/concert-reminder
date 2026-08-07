"""Character tags: a fifth kind, and who voices them."""

from sqlalchemy import select

from app.db.models import Tag
from app.domain.types import TagKind


def test_character_is_a_tag_kind():
    assert TagKind.CHARACTER.value == "character"


async def test_a_character_records_who_voices_her(db):
    async with db() as s:
        seiyuu = Tag(name="今井麻美", kind=TagKind.ARTIST, slug="asami-imai")
        s.add(seiyuu)
        await s.flush()
        s.add(Tag(name="如月千早", kind=TagKind.CHARACTER, slug="chihaya-kisaragi",
                  voiced_by_tag_id=seiyuu.id))
        await s.commit()

    async with db() as s:
        chihaya = (await s.execute(
            select(Tag).where(Tag.slug == "chihaya-kisaragi")
        )).scalar_one()
        assert chihaya.voiced_by_tag_id == seiyuu.id


async def test_deleting_the_seiyuu_leaves_the_character(db):
    """SET NULL, never CASCADE: a character outlives her voice actor's tag,
    exactly as a leg outlives its venue tag."""
    async with db() as s:
        seiyuu = Tag(name="今井麻美", kind=TagKind.ARTIST, slug="asami-imai")
        s.add(seiyuu)
        await s.flush()
        s.add(Tag(name="如月千早", kind=TagKind.CHARACTER, slug="chihaya-kisaragi",
                  voiced_by_tag_id=seiyuu.id))
        await s.commit()
        await s.delete(seiyuu)
        await s.commit()

    async with db() as s:
        chihaya = (await s.execute(
            select(Tag).where(Tag.slug == "chihaya-kisaragi")
        )).scalar_one()
        assert chihaya is not None, "the character must survive"
        assert chihaya.voiced_by_tag_id is None


async def test_voiced_by_defaults_to_none(db):
    async with db() as s:
        s.add(Tag(name="天海春香", kind=TagKind.CHARACTER, slug="haruka-amami"))
        await s.commit()
        row = (await s.execute(select(Tag).where(Tag.slug == "haruka-amami"))).scalar_one()
        assert row.voiced_by_tag_id is None
