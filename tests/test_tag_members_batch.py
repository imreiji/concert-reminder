"""One query for every group's members, not one query per group.

/tags and /preferences each built their member map with a dict comprehension
over group_members(), which is 65 round trips on the live catalogue.
"""

from app.db.models import Tag, TagKind, TagMember
from app.db.service import members_by_group


async def _group(session, name, slug):
    g = Tag(name=name, name_en=name, kind=TagKind.GROUP, slug=slug)
    session.add(g)
    await session.flush()
    return g


async def test_members_by_group_returns_each_groups_members(session):
    a = await _group(session, "Aqours", "aqours")
    b = await _group(session, "Liella", "liella")
    m1 = Tag(name="伊波杏樹", name_en="Anju Inami", kind=TagKind.ARTIST, slug="anju")
    m2 = Tag(name="逢田梨香子", name_en="Rikako Aida", kind=TagKind.ARTIST, slug="rikako")
    session.add_all([m1, m2])
    await session.flush()
    session.add_all([
        TagMember(group_tag_id=a.id, member_tag_id=m1.id),
        TagMember(group_tag_id=a.id, member_tag_id=m2.id),
        TagMember(group_tag_id=b.id, member_tag_id=m1.id),
    ])
    await session.flush()

    got = await members_by_group(session, [a.id, b.id])
    assert [t.name for t in got[a.id]] == ["伊波杏樹", "逢田梨香子"]
    assert [t.name for t in got[b.id]] == ["伊波杏樹"]


async def test_members_by_group_gives_an_empty_list_for_a_memberless_group(session):
    """Callers index this map per group. A group with no members must yield an
    empty list, not a KeyError and not a missing key.

    Mutation this must fail against: building the dict only from the rows the
    query returns, which silently drops every memberless group.
    """
    g = await _group(session, "Empty", "empty")
    got = await members_by_group(session, [g.id])
    assert got == {g.id: []}


async def test_members_by_group_handles_an_empty_id_list(session):
    """A catalogue with no groups must not emit `IN ()`."""
    assert await members_by_group(session, []) == {}
