"""The Performing panel: pairing, nesting, and the standalone seiyuu."""

from sqlalchemy import event, select
from sqlalchemy.orm import selectinload

from app.db.models import Concert, Tag, TagMember
from app.db.service import attach_tag, performer_clusters
from app.domain.types import TagKind


async def _reload(s, concert_id):
    return (await s.execute(
        select(Concert).where(Concert.id == concert_id)
        .options(selectinload(Concert.tags))
    )).scalar_one()


async def _imas(s):
    concert = Concert(title="im@s", event_id="imas-1")
    imai = Tag(name="今井麻美", kind=TagKind.ARTIST, slug="asami-imai")
    s.add_all([concert, imai])
    await s.flush()
    chihaya = Tag(name="如月千早", kind=TagKind.CHARACTER, slug="chihaya",
                  voiced_by_tag_id=imai.id)
    parent = Tag(name="765PRO ALLSTARS", kind=TagKind.GROUP, slug="765pro")
    s.add_all([chihaya, parent])
    await s.flush()
    sub = Tag(name="竜宮小町", kind=TagKind.GROUP, slug="ryuguu", parent_id=parent.id)
    s.add(sub)
    await s.flush()
    s.add_all([
        TagMember(group_tag_id=parent.id, member_tag_id=chihaya.id),
        TagMember(group_tag_id=sub.id, member_tag_id=chihaya.id),
    ])
    await s.flush()
    return concert, parent, sub, chihaya, imai


async def test_a_character_and_her_seiyuu_pair_into_one_entry(db):
    async with db() as s:
        concert, parent, _sub, chihaya, imai = await _imas(s)
        await attach_tag(s, concert.id, parent)
        clusters = await performer_clusters(s, await _reload(s, concert.id))
        entries = [e for c in clusters for e in c.performers]
        paired = [e for e in entries if e.seiyuu is not None]
        assert [(e.tag.id, e.seiyuu.id) for e in paired] == [(chihaya.id, imai.id)]
        assert imai.id not in [e.tag.id for e in entries], \
            "the seiyuu must not ALSO appear as her own entry"


async def test_a_seiyuu_attached_by_herself_is_listed_as_herself(db):
    """Owner rule: not under the group, just herself."""
    async with db() as s:
        concert, _p, _sub, _chihaya, imai = await _imas(s)
        await attach_tag(s, concert.id, imai)
        clusters = await performer_clusters(s, await _reload(s, concert.id))
        assert [c.group for c in clusters] == [None], "trailer only"
        assert [e.tag.id for e in clusters[0].performers] == [imai.id]
        assert clusters[0].performers[0].seiyuu is None


async def test_a_subunit_nests_under_its_parent_when_both_are_attached(db):
    async with db() as s:
        concert, parent, sub, _c, _i = await _imas(s)
        await attach_tag(s, concert.id, parent)
        await attach_tag(s, concert.id, sub)
        clusters = [c for c in await performer_clusters(s, await _reload(s, concert.id))
                    if c.group is not None]
        assert [(c.group.id, c.depth) for c in clusters] == [(parent.id, 0), (sub.id, 1)]


async def test_a_subunit_alone_renders_like_an_ordinary_group(db):
    """Owner rule: no parent attached, no nesting."""
    async with db() as s:
        concert, _parent, sub, _c, _i = await _imas(s)
        await attach_tag(s, concert.id, sub)
        clusters = [c for c in await performer_clusters(s, await _reload(s, concert.id))
                    if c.group is not None]
        assert [(c.group.id, c.depth) for c in clusters] == [(sub.id, 0)]


async def test_a_character_whose_seiyuu_is_not_attached_is_a_plain_entry(db):
    async with db() as s:
        concert, _p, _sub, chihaya, imai = await _imas(s)
        chihaya.voiced_by_tag_id = None      # nobody to pair with
        await s.flush()
        await attach_tag(s, concert.id, chihaya)
        clusters = await performer_clusters(s, await _reload(s, concert.id))
        entry = clusters[0].performers[0]
        assert entry.tag.id == chihaya.id and entry.seiyuu is None


# ── the parent that is NOT a cluster ─────────────────────────────────────


async def test_a_group_under_an_attached_franchise_stays_a_root(db):
    """The nesting rule reads "parent GROUP", not "parent tag". A group's
    parent_id is USUALLY a FRANCHISE (Aqours under Love Live!) and that
    franchise is usually attached too -- but a franchise opens no cluster, so
    a group under it is a root.

    The real bill is here (franchise + group + that group's own subunit)
    because the shorter one cannot tell the two questions apart: with only one
    group, asking `parent_id in attached_ids` leaves it rootless, the
    cycle-fallback sweep emits it at depth 0 anyway, and the answer comes out
    identical. Put a subunit on the bill and the difference is visible -- the
    subunit sorts first, so the mutant emits IT at depth 0 and the parent
    after it, un-nested and out of order."""
    async with db() as s:
        concert = Concert(title="LL", event_id="ll-1")
        franchise = Tag(name="Love Live!", kind=TagKind.FRANCHISE, slug="love-live")
        s.add_all([concert, franchise])
        await s.flush()
        group = Tag(name="Zephyr", kind=TagKind.GROUP, slug="zephyr",
                    parent_id=franchise.id)
        s.add(group)
        await s.flush()
        # Sorts BEFORE its parent, so a fallback emission order shows up.
        sub = Tag(name="Azalea", kind=TagKind.GROUP, slug="azalea", parent_id=group.id)
        chika = Tag(name="Chika", kind=TagKind.ARTIST, slug="chika")
        s.add_all([sub, chika])
        await s.flush()
        s.add(TagMember(group_tag_id=group.id, member_tag_id=chika.id))
        await s.flush()
        await attach_tag(s, concert.id, franchise)
        await attach_tag(s, concert.id, group)
        await attach_tag(s, concert.id, sub)

        clusters = await performer_clusters(s, await _reload(s, concert.id))
        assert [(c.group.id if c.group else None, c.depth) for c in clusters] == [
            (group.id, 0), (sub.id, 1)
        ]
        assert [e.tag.id for e in clusters[0].performers] == [chika.id]


# ── the walk keeps every cluster ─────────────────────────────────────────


async def test_a_grandchild_subunit_is_not_dropped(db):
    """A parent-first walk that only emits a root's DIRECT children loses the
    third rung entirely -- the group vanishes from the page rather than
    merely rendering flat. Depth stays 1: the rail is one indent, not a
    ladder."""
    async with db() as s:
        concert = Concert(title="deep", event_id="deep-1")
        s.add(concert)
        await s.flush()
        top = Tag(name="A top", kind=TagKind.GROUP, slug="a-top")
        s.add(top)
        await s.flush()
        mid = Tag(name="B mid", kind=TagKind.GROUP, slug="b-mid", parent_id=top.id)
        s.add(mid)
        await s.flush()
        low = Tag(name="C low", kind=TagKind.GROUP, slug="c-low", parent_id=mid.id)
        s.add(low)
        await s.flush()
        for g in (top, mid, low):
            await attach_tag(s, concert.id, g)

        clusters = await performer_clusters(s, await _reload(s, concert.id))
        assert [(c.group.id, c.depth) for c in clusters] == [
            (top.id, 0), (mid.id, 1), (low.id, 1)
        ]


async def test_a_parent_cycle_still_renders_both_groups(db):
    """Two groups each naming the other as parent have no root between them,
    so a walk that starts only from roots emits neither. `apply_tag_import`
    sets parent_id and is not cycle-guarded, so this shape is reachable from
    a hand-edited catalogue file -- and a display function must never answer
    "no performers" to it."""
    async with db() as s:
        concert = Concert(title="cycle", event_id="cycle-1")
        s.add(concert)
        await s.flush()
        a = Tag(name="A ring", kind=TagKind.GROUP, slug="a-ring")
        b = Tag(name="B ring", kind=TagKind.GROUP, slug="b-ring")
        s.add_all([a, b])
        await s.flush()
        a.parent_id = b.id
        b.parent_id = a.id
        await s.flush()
        await attach_tag(s, concert.id, a)
        await attach_tag(s, concert.id, b)

        clusters = await performer_clusters(s, await _reload(s, concert.id))
        assert {c.group.id for c in clusters} == {a.id, b.id}


async def test_a_group_that_is_its_own_parent_still_renders(db):
    """The 1-cycle, which is why the walk carries no separate self-parent
    guard: it is a cycle, and the sweep that rescues cycles rescues this
    too. Nothing above may recurse into it either."""
    async with db() as s:
        concert = Concert(title="self", event_id="self-1")
        s.add(concert)
        await s.flush()
        g = Tag(name="Ouroboros", kind=TagKind.GROUP, slug="ouroboros")
        s.add(g)
        await s.flush()
        g.parent_id = g.id
        await s.flush()
        await attach_tag(s, concert.id, g)

        clusters = await performer_clusters(s, await _reload(s, concert.id))
        assert [(c.group.id, c.depth) for c in clusters] == [(g.id, 0)]


# ── cost ─────────────────────────────────────────────────────────────────


async def test_nesting_adds_no_query(db):
    """The parent lookup comes off the already-loaded `concert.tags`, never a
    SELECT of its own: `tag_members` stays the ONE statement this function
    issues however many groups, subunits and characters are attached.

    Two things make this measure anything at all, and both were found by
    mutation -- a naive `session.get(Tag, g.parent_id)` per group survived
    the test without them:

    * The third group is on the bill for the ABSENT parent it names. A
      parent that IS attached costs nothing to fetch, since `concert.tags`
      just put it in the identity map, so a bill where every parent is
      present cannot see the lookup. An absent parent is a real SELECT --
      and it is the common shape anyway: a subunit on a bill its parent
      group is not on.
    * The measuring session is a FRESH one. Seed and measure in the same
      session and every tag ever created sits in that identity map, absent
      or not, and the same lookup is free again."""
    async with db() as s:
        concert, parent, sub, _c, _i = await _imas(s)
        absent = Tag(name="欠席グループ", kind=TagKind.GROUP, slug="absent")
        s.add(absent)
        await s.flush()
        orphan = Tag(name="孤立ユニット", kind=TagKind.GROUP, slug="orphan",
                     parent_id=absent.id)
        s.add(orphan)
        await s.flush()
        await attach_tag(s, concert.id, parent)
        await attach_tag(s, concert.id, sub)
        await attach_tag(s, concert.id, orphan)
        await s.commit()
        cid, parent_id, sub_id, orphan_id = concert.id, parent.id, sub.id, orphan.id

    async with db() as s:
        concert = await _reload(s, cid)

        statements: list[str] = []

        def _count(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(s.bind.sync_engine, "before_cursor_execute", _count)
        try:
            clusters = await performer_clusters(s, concert)
        finally:
            event.remove(s.bind.sync_engine, "before_cursor_execute", _count)

    # Three groups, one nesting and a paired character, or the count measures
    # nothing.
    assert [(c.group.id if c.group else None, c.depth) for c in clusters] == [
        (parent_id, 0), (sub_id, 1), (orphan_id, 0)
    ]
    assert any(e.seiyuu is not None for c in clusters for e in c.performers)
    assert len(statements) == 1, statements
    assert "tag_members" in statements[0]
