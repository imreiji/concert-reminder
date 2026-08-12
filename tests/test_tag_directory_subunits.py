"""On /tags, a subunit's members render under the subunit and nowhere else.

Owner ruling 2026-08-12, THIS PAGE ONLY. The 2026-08-01 spec kept the
repetition on the concert page because a bill must be a truthful lineup and
what it shows depends on which other tags are attached. A catalogue has no
"attached", so that reasoning does not transfer.
"""

from app.db.models import Tag, TagKind, TagMember
from app.db.service import tag_directory_context


async def _tag(session, name, kind, slug, **kw):
    t = Tag(name=name, name_en=name, kind=kind, slug=slug, **kw)
    session.add(t)
    await session.flush()
    return t


async def test_a_subunit_member_leaves_its_parents_row(session):
    parent = await _tag(session, "765PRO ALLSTARS", TagKind.GROUP, "765pro")
    sub = await _tag(session, "竜宮小町", TagKind.GROUP, "ryuguu", parent_id=parent.id)
    shared = await _tag(session, "秋月律子", TagKind.CHARACTER, "ritsuko")
    only_parent = await _tag(session, "天海春香", TagKind.CHARACTER, "haruka")
    session.add_all([
        TagMember(group_tag_id=parent.id, member_tag_id=shared.id),
        TagMember(group_tag_id=parent.id, member_tag_id=only_parent.id),
        TagMember(group_tag_id=sub.id, member_tag_id=shared.id),
    ])
    await session.flush()

    ctx = await tag_directory_context(session)
    rows = {g.name: [m.name for m in members] for g, members, _d in ctx["no_franchise_groups"]}
    assert rows["竜宮小町"] == ["秋月律子"], "the subunit keeps her"
    assert rows["765PRO ALLSTARS"] == ["天海春香"], (
        "and the parent drops her -- she renders in the subunit and nowhere else"
    )


async def test_a_parent_whose_members_are_all_in_subunits_renders_empty(session):
    """6 live groups become empty rows. They must still RENDER -- the row is
    the group, and the concert page's own ruling is that an empty member area
    shows the label row silently rather than '0 performers'.

    Mutation this must fail against: dropping a group whose de-duped member
    list is empty.
    """
    parent = await _tag(session, "SideM", TagKind.GROUP, "sidem")
    sub = await _tag(session, "Jupiter", TagKind.GROUP, "jupiter", parent_id=parent.id)
    m = await _tag(session, "天ヶ瀬冬馬", TagKind.CHARACTER, "touma")
    session.add_all([
        TagMember(group_tag_id=parent.id, member_tag_id=m.id),
        TagMember(group_tag_id=sub.id, member_tag_id=m.id),
    ])
    await session.flush()

    ctx = await tag_directory_context(session)
    rows = {g.name: members for g, members, _d in ctx["no_franchise_groups"]}
    assert "SideM" in rows, "the empty parent still renders"
    assert rows["SideM"] == []
    assert [m.name for m in rows["Jupiter"]] == ["天ヶ瀬冬馬"]


async def test_seiyuu_of_maps_characters_to_their_performer(session):
    """The template needs the seiyuu for split pills, and Tag.voiced_by is NOT
    a loaded relationship -- touching it during async rendering is a
    MissingGreenlet 500. The context resolves it from the loaded tag list.
    """
    seiyuu = await _tag(session, "若林直美", TagKind.ARTIST, "naomi")
    await _tag(session, "秋月律子", TagKind.CHARACTER, "ritsuko",
               voiced_by_tag_id=seiyuu.id)
    await _tag(session, "三浦あずさ", TagKind.CHARACTER, "azusa")

    ctx = await tag_directory_context(session)
    by_name = {t.name: t for t in [seiyuu]}
    got = {cid: (s.name if s else None) for cid, s in ctx["seiyuu_of"].items()}
    assert "若林直美" in got.values()
    assert None in got.values(), "a character with no seiyuu maps to None, not a KeyError"
    assert len(got) == 2, "CHARACTER tags only -- artists and groups are not keys"
    assert by_name  # silence the unused-name linter
