"""GET /concerts/{event_id} — the reader page.

The page answers one question the old six-column table could not: where do
*you* stand on this concert. It leads with lineage and performers (the group
is what a reader recognises; the title is often a long subtitle), states the
nearest moment that needs them, and then renders one section per leg with
that leg's own date, doors and venue.

Two things these tests deliberately pin:

  * The header carries NO date range and NO single venue. On a tour with
    different cities a header venue is not merely repetitive, it is wrong —
    it disagrees with the legs underneath it. Dates and venues live on legs.
  * The performers panel reflects MATERIALISED membership (invariant 3):
    attaching a GROUP tag captures its members at that moment, editors prune
    non-performers, and later membership edits never rewrite the concert. So
    a concert can legitimately show eight of a nine-member group, and show a
    member the group no longer has.
"""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload
from sqlalchemy.pool import StaticPool

from app.db.models import (
    Base,
    Concert,
    ConcertDay,
    ConcertTag,
    Round,
    Tag,
    TagMember,
    User,
)
from app.db.service import (
    _FOLD_KINDS,
    attach_tag,
    ensure_user,
    performer_clusters,
    record_round_day_result,
    record_round_outcome,
)
from app.db.session import get_session
from app.domain.types import LegResult, LotteryOutcome, RoundKind, TagKind
from app.web import auth
from app.web.app import create_app, fold_count_label

USER = 4242
EDITOR = 777


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


@pytest.fixture()
def client(db, monkeypatch):
    app = create_app()

    async def override_session():
        async with db() as s:
            yield s

    app.dependency_overrides[get_session] = override_session

    async def fake_exchange(code):
        return "tok"

    monkeypatch.setattr(auth, "exchange_code", fake_exchange)
    c = TestClient(app, follow_redirects=False)
    c.db = db
    c.monkeypatch = monkeypatch
    return c


def login(client, discord_id: int = USER, name: str = "reiji"):
    async def fake_identity(token):
        return {"id": str(discord_id), "username": name, "global_name": name, "avatar": None}

    client.monkeypatch.setattr(auth, "fetch_identity", fake_identity)
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    client.get(f"/auth/callback?code=x&state={state}")


async def make_editor(db, discord_id: int = EDITOR, name: str = "editor"):
    async with db() as s:
        await ensure_user(s, discord_id, name)
        u = await s.get(User, discord_id)
        u.is_editor = True
        await s.commit()


# ── seeding ──────────────────────────────────────────────────────────────


async def seed_concert(db, *, title="Nobody's Perfect", event_id="np", **kw):
    async with db() as s:
        await ensure_user(s, USER, "reiji")
        c = Concert(title=title, event_id=event_id, created_by=USER, **kw)
        s.add(c)
        await s.commit()
        return c.id


async def add_tag(db, concert_id, name, kind, *, expand=True, parent_id=None):
    """Create (or reuse) a tag and attach it through the real service path, so
    GROUP expansion behaves exactly as it does in the app."""
    async with db() as s:
        tag = Tag(name=name, kind=kind, parent_id=parent_id)
        s.add(tag)
        await s.flush()
        await attach_tag(s, concert_id, tag, expand=expand)
        await s.commit()
        return tag.id


async def add_group_with_members(db, concert_id, group_name, member_names, *, parent_id=None):
    """A GROUP tag with members, attached WITH expansion — the ordinary path
    an editor takes, which materialises the members onto the concert."""
    async with db() as s:
        group = Tag(name=group_name, kind=TagKind.GROUP, parent_id=parent_id)
        s.add(group)
        await s.flush()
        members = [Tag(name=n, kind=TagKind.ARTIST) for n in member_names]
        s.add_all(members)
        await s.flush()
        for m in members:
            s.add(TagMember(group_tag_id=group.id, member_tag_id=m.id))
        await s.flush()
        await attach_tag(s, concert_id, group, expand=True)
        await s.commit()
        return group.id, [m.id for m in members]


async def prune_member(db, concert_id, tag_id):
    """What an editor does when a member is not performing: the concert_tags
    row goes, the group membership does not."""
    async with db() as s:
        row = await s.get(ConcertTag, {"concert_id": concert_id, "tag_id": tag_id})
        await s.delete(row)
        await s.commit()


async def add_member_to_group_later(db, group_id, name):
    """A membership edit made AFTER the concert was tagged. Invariant 3 says
    it must not reach the concert."""
    async with db() as s:
        t = Tag(name=name, kind=TagKind.ARTIST)
        s.add(t)
        await s.flush()
        s.add(TagMember(group_tag_id=group_id, member_tag_id=t.id))
        await s.commit()


async def add_venue_tag(db, name, **kw):
    """A VENUE tag on its own, NOT attached to any concert -- a leg points at
    one through ConcertDay.venue_tag_id, which is independent of the concert's
    own tag list."""
    async with db() as s:
        t = Tag(name=name, kind=TagKind.VENUE, **kw)
        s.add(t)
        await s.commit()
        return t.id


async def add_day(db, concert_id, label, *, days_ahead=60, cancelled=False,
                  venue_tag_id=None):
    async with db() as s:
        d = ConcertDay(
            concert_id=concert_id, label=label, cancelled=cancelled,
            venue_tag_id=venue_tag_id,
            starts_at_utc=datetime.now(UTC) + timedelta(days=days_ahead),
            doors_at_utc=datetime.now(UTC) + timedelta(days=days_ahead, hours=-1),
        )
        s.add(d)
        await s.commit()
        return d.id


async def add_round(db, concert_id, label, *, applies_to=None, opens=None, closes=None,
                    kind=RoundKind.LOTTERY_ROUND, results=None, payment=None):
    async with db() as s:
        r = Round(
            concert_id=concert_id, label=label, kind=kind, applies_to=applies_to,
            opens_at_utc=opens, closes_at_utc=closes,
            results_at_utc=results, payment_deadline_at_utc=payment,
        )
        s.add(r)
        await s.commit()
        return r.id


async def set_outcome(db, round_id, outcome, user_id=USER):
    """Through the one service path, so the sequence rule applies here too."""
    async with db() as s:
        await record_round_outcome(s, user_id, round_id, outcome)
        await s.commit()


async def set_day_result(db, round_id, day_id, result, user_id=USER):
    """One leg of one round resolved, through the one per-day write path."""
    async with db() as s:
        await record_round_day_result(s, user_id, round_id, day_id, result)
        await s.commit()


# ── header: lineage ──────────────────────────────────────────────────────


async def test_lineage_renders_franchise_then_group(client):
    cid = await seed_concert(client.db)
    fid = await add_tag(client.db, cid, "Love Live! Sunshine!!", TagKind.FRANCHISE)
    await add_tag(client.db, cid, "Aqours", TagKind.GROUP, parent_id=fid)
    login(client)

    body = client.get("/concerts/np").text
    lineage = body.split('class="lineage"', 1)[1].split("</p>", 1)[0]
    assert "Love Live! Sunshine!!" in lineage
    # The group is the recognisable half, so it carries the accent markup.
    assert "<b>Aqours</b>" in lineage


async def test_lineage_renders_the_group_alone_when_there_is_no_franchise(client):
    cid = await seed_concert(client.db)
    await add_tag(client.db, cid, "Aqours", TagKind.GROUP)
    login(client)

    body = client.get("/concerts/np").text
    lineage = body.split('class="lineage"', 1)[1].split("</p>", 1)[0]
    assert "<b>Aqours</b>" in lineage
    assert "·" not in lineage  # nothing to separate from


async def test_no_lineage_line_at_all_when_there_is_neither(client):
    cid = await seed_concert(client.db)
    await add_tag(client.db, cid, "Nana Mizuki", TagKind.ARTIST)
    login(client)

    assert 'class="lineage"' not in client.get("/concerts/np").text


async def test_the_title_drops_the_group_the_lineage_already_carries(client):
    cid = await seed_concert(
        client.db, title="Aqours 9th LoveLive! — Nobody's Perfect", event_id="np9"
    )
    await add_tag(client.db, cid, "Aqours", TagKind.GROUP)
    login(client)

    body = client.get("/concerts/np9").text
    h1 = body.split("<h1", 1)[1].split("</h1>", 1)[0]
    assert "9th LoveLive!" in h1
    assert "Aqours" not in h1


# ── header: performers ───────────────────────────────────────────────────


async def test_the_performers_panel_shows_materialised_membership(client):
    """Nine members attached, one pruned, one added to the group afterwards.
    The panel must show the eight that are really on the concert — not the
    group's current membership (invariant 3)."""
    cid = await seed_concert(client.db)
    names = [f"Member {i}" for i in range(1, 10)]
    gid, member_ids = await add_group_with_members(client.db, cid, "Aqours", names)
    await prune_member(client.db, cid, member_ids[0])
    await add_member_to_group_later(client.db, gid, "Joined Later")
    login(client)

    body = client.get("/concerts/np").text
    panel = body.split('class="performers"', 1)[1].split("<!-- /performers -->", 1)[0]
    assert "Member 1" not in panel          # pruned: stays pruned
    assert "Member 9" in panel              # the rest are still there
    assert "Joined Later" not in panel      # a later membership edit never reaches here
    assert "Aqours" in panel                # the group chip leads
    # The header counts the performers actually on the bill -- the composed
    # "N members, from the X group tags" line it replaced said the same thing
    # twice now that every cluster is labelled with its group.
    assert "8 performers" in panel


async def test_no_performers_panel_when_the_concert_has_neither_group_nor_artists(client):
    cid = await seed_concert(client.db)
    await add_tag(client.db, cid, "Yokohama Arena", TagKind.VENUE)
    login(client)

    assert 'class="performers"' not in client.get("/concerts/np").text


async def _set_eventernote_url(db, tag_id, url):
    async with db() as s:
        t = await s.get(Tag, tag_id)
        t.eventernote_url = url
        await s.commit()


def _performers_panel(client):
    body = client.get("/concerts/np").text
    return body.split('class="performers"', 1)[1].split("<!-- /performers -->", 1)[0]


async def test_performer_chip_links_to_eventernote_when_set(client):
    cid = await seed_concert(client.db)
    tid = await add_tag(client.db, cid, "Solo Star", TagKind.ARTIST)
    await _set_eventernote_url(client.db, tid, "https://www.eventernote.com/actors/1234")
    login(client)

    panel = _performers_panel(client)
    assert '<a class="chip" href="https://www.eventernote.com/actors/1234"' in panel
    assert "Solo Star" in panel


async def test_performer_chip_without_url_is_a_span_not_a_link(client):
    cid = await seed_concert(client.db)
    await add_tag(client.db, cid, "No Link Star", TagKind.ARTIST)
    login(client)

    panel = _performers_panel(client)
    # Dimmed (demo's .nolink) with a tooltip explaining why, but still never
    # a dead <a>.
    assert '<span class="chip nolink" title="No eventernote link yet">No Link Star</span>' in panel
    assert "No Link Star</a>" not in panel  # never a dead link


async def test_group_chip_links_to_eventernote_when_set(client):
    cid = await seed_concert(client.db)
    gid, _ = await add_group_with_members(client.db, cid, "Aqours", ["M1", "M2"])
    await _set_eventernote_url(client.db, gid, "https://www.eventernote.com/actors/999")
    login(client)

    panel = _performers_panel(client)
    assert '<a class="chip grp" href="https://www.eventernote.com/actors/999"' in panel
    assert "Aqours" in panel


# ── header: performer clusters (service derivation) ──────────────────────
#
# The panel groups its chips by group, and the grouping is derived in
# db/service.py rather than in the template: Tag.members is a lazy
# self-referential m2m, so walking it during async rendering is a
# MissingGreenlet 500. These pin the derivation itself; the rendering is
# pinned by the panel tests above.


async def make_group(db, name, member_names):
    """A GROUP tag plus its ARTIST members, attached to NOTHING.

    A member name repeated across two calls resolves to the SAME tag with
    two memberships — which is exactly the performer-in-two-groups case."""
    async with db() as s:
        group = Tag(name=name, kind=TagKind.GROUP)
        s.add(group)
        await s.flush()
        ids = {}
        for n in member_names:
            res = await s.execute(select(Tag).where(Tag.name == n))
            t = res.scalar_one_or_none()
            if t is None:
                t = Tag(name=n, kind=TagKind.ARTIST)
                s.add(t)
                await s.flush()
            s.add(TagMember(group_tag_id=group.id, member_tag_id=t.id))
            ids[n] = t.id
        await s.commit()
        return group.id, ids


async def make_artist(db, name):
    """A solo ARTIST tag in no group at all, attached to nothing."""
    return await make_tag(db, name, TagKind.ARTIST)


async def make_tag(db, name, kind):
    """A tag of any kind, attached to nothing."""
    async with db() as s:
        t = Tag(name=name, kind=kind)
        s.add(t)
        await s.commit()
        return t.id


async def attach_existing(db, concert_id, tag_ids):
    """Attach already-created tags with NO expansion. The materialised
    concert_tags set is this derivation's input, so each test states it
    exactly instead of letting attach_tag decide it."""
    async with db() as s:
        for tid in tag_ids:
            s.add(ConcertTag(concert_id=concert_id, tag_id=tid))
        await s.commit()


async def clusters_for(db, concert_id):
    async with db() as s:
        res = await s.execute(
            select(Concert)
            .where(Concert.id == concert_id)
            .options(selectinload(Concert.tags))
        )
        return await performer_clusters(s, res.scalar_one())


def cluster_names(clusters):
    return [
        (c.group.name if c.group else None, [a.name for a in c.artists])
        for c in clusters
    ]


async def test_clusters_hold_each_groups_attached_members(db):
    """Groups in concert.tags' order, and artists in it too INSIDE each
    cluster: "Riko" is created and attached first but sorts second, so this
    fails if the derivation ever keeps insertion order instead.

    The VENUE and FRANCHISE tags are here because every real concert page
    carries them — VENUE derived from the legs, franchise usually typed —
    and neither is a performer: one must not open a cluster of its own, nor
    fall into the trailer. Widening the two `kind is` filters to a `kind in`
    is the cheapest regression anyone could introduce here, and the equality
    below is what catches it."""
    cid = await seed_concert(db)
    b_id, b_members = await make_group(db, "Bearies", ["Mari"])
    a_id, a_members = await make_group(db, "Aqours", ["Riko", "Chika"])
    venue = await make_tag(db, "Zepp Haneda", TagKind.VENUE)
    franchise = await make_tag(db, "Love Live!", TagKind.FRANCHISE)
    await attach_existing(
        db, cid, [b_id, a_id, venue, franchise, *b_members.values(), *a_members.values()]
    )

    assert cluster_names(await clusters_for(db, cid)) == [
        ("Aqours", ["Chika", "Riko"]),
        ("Bearies", ["Mari"]),
    ]


async def test_a_concert_with_no_groups_is_one_trailer(db):
    """A solo-artist bill: no group to cluster under, but the performers must
    still come out — the panel renders this list and nothing else."""
    cid = await seed_concert(db)
    solo = await make_artist(db, "Solo Star")
    await attach_existing(db, cid, [solo])

    assert cluster_names(await clusters_for(db, cid)) == [(None, ["Solo Star"])]


async def test_a_performer_in_two_groups_appears_in_both(db):
    """Owner decision 1: the repetition is information, not a bug."""
    cid = await seed_concert(db)
    a_id, a_members = await make_group(db, "Aqours", ["Shared Star", "Chika"])
    b_id, b_members = await make_group(db, "Bearies", ["Shared Star", "Mari"])
    shared = a_members["Shared Star"]
    assert b_members["Shared Star"] == shared  # one tag, two memberships
    await attach_existing(db, cid, [a_id, b_id, shared, a_members["Chika"], b_members["Mari"]])

    clusters = await clusters_for(db, cid)
    ids = {c.group.id: [a.id for a in c.artists] for c in clusters if c.group}
    assert shared in ids[a_id]
    assert shared in ids[b_id]


async def test_ungrouped_artists_land_in_the_trailer(db):
    cid = await seed_concert(db)
    gid, members = await make_group(db, "Aqours", ["Chika"])
    solo = await make_artist(db, "Solo Star")
    await attach_existing(db, cid, [gid, members["Chika"], solo])

    clusters = await clusters_for(db, cid)
    assert clusters[-1].group is None
    assert [a.id for a in clusters[-1].artists] == [solo]


async def test_the_trailer_is_omitted_when_every_artist_is_grouped(db):
    cid = await seed_concert(db)
    gid, members = await make_group(db, "Aqours", ["Chika", "Riko"])
    await attach_existing(db, cid, [gid, *members.values()])

    clusters = await clusters_for(db, cid)
    assert clusters
    assert all(c.group is not None for c in clusters)


async def test_a_group_with_no_attached_members_keeps_its_label(db):
    """The group IS on the bill; dropping the row would hide an attached tag,
    and an empty row says the line-up was never listed (or pruned to none)."""
    cid = await seed_concert(db)
    full_id, full_members = await make_group(db, "Aqours", ["Chika"])
    empty_id, _ = await make_group(db, "Bearies", ["Mari"])
    await attach_existing(db, cid, [full_id, empty_id, full_members["Chika"]])

    clusters = await clusters_for(db, cid)
    assert any(
        c.group is not None and c.group.id == empty_id and c.artists == ()
        for c in clusters
    )


async def test_a_member_whose_group_is_not_attached_stays_in_the_trailer(db):
    """The artist really is in a group — but that group is not on this bill,
    so there is no cluster to put her in."""
    cid = await seed_concert(db)
    absent_id, members = await make_group(db, "Bearies", ["Mari"])
    gid, on_bill = await make_group(db, "Aqours", ["Chika"])
    await attach_existing(db, cid, [gid, on_bill["Chika"], members["Mari"]])

    clusters = await clusters_for(db, cid)
    assert all(c.group is None or c.group.id != absent_id for c in clusters)
    assert clusters[-1].group is None
    assert [a.id for a in clusters[-1].artists] == [members["Mari"]]


async def test_membership_loads_in_one_query(db):
    """One batched read over tag_members however many groups are attached —
    what stops a per-group group_members() loop creeping back in."""
    cid = await seed_concert(db)
    attach = []
    for name, member in [("Aqours", "Chika"), ("Bearies", "Mari"), ("Cerise", "Kanon")]:
        gid, members = await make_group(db, name, [member])
        attach += [gid, members[member]]
    await attach_existing(db, cid, attach)

    async with db() as s:
        res = await s.execute(
            select(Concert)
            .where(Concert.id == cid)
            .options(selectinload(Concert.tags))
        )
        concert = res.scalar_one()

        queries: list[str] = []

        def _count(conn, cursor, statement, parameters, context, executemany):
            queries.append(statement)

        event.listen(s.bind.sync_engine, "before_cursor_execute", _count)
        try:
            clusters = await performer_clusters(s, concert)
        finally:
            event.remove(s.bind.sync_engine, "before_cursor_execute", _count)

    assert len(clusters) == 3  # three groups, or the count measures nothing
    assert sum(1 for q in queries if "tag_members" in q) == 1


# ── header: performer clusters (rendering) ───────────────────────────────
#
# The panel renders one labelled block per cluster instead of the flat
# "every group chip, then every artist chip" row it used to. These pin the
# markup; the derivation feeding it is pinned above.


async def _seed_two_groups_sharing_a_member(db, concert_id):
    """Aqours + Bearies with one performer in both — three distinct
    performers occupying four cluster seats."""
    a_id, a_members = await make_group(db, "Aqours", ["Shared Member", "Chika"])
    b_id, b_members = await make_group(db, "Bearies", ["Shared Member", "Mari"])
    await attach_existing(
        db,
        concert_id,
        [a_id, b_id, a_members["Shared Member"], a_members["Chika"], b_members["Mari"]],
    )


async def test_the_performing_panel_groups_chips_under_their_group(client):
    """Two groups on one bill: each performer sits inside her own group's
    block. The flat row this replaced put both group chips first and then
    every artist after them, so a reader could not tell who was in which."""
    cid = await seed_concert(client.db)
    await add_group_with_members(client.db, cid, "Aqours", ["Chika", "Riko"])
    await add_group_with_members(client.db, cid, "Bearies", ["Mari"])
    login(client)

    panel = _performers_panel(client)
    blocks = panel.split('class="pcluster"')[1:]
    assert len(blocks) == 2
    aqours = next(b for b in blocks if ">Aqours<" in b)
    bearies = next(b for b in blocks if ">Bearies<" in b)
    assert ">Chika<" in aqours and ">Riko<" in aqours and ">Mari<" not in aqours
    assert ">Mari<" in bearies and ">Chika<" not in bearies


async def test_a_two_group_performer_appears_under_both(client):
    """Owner decision 1, at the markup: the repetition is information."""
    cid = await seed_concert(client.db)
    await _seed_two_groups_sharing_a_member(client.db, cid)
    login(client)

    body = _performers_panel(client)
    assert body.count(">Shared Member<") == 2


async def test_the_header_counts_distinct_performers_not_the_sum(client):
    """Three performers filling four cluster seats. The header counts the
    attached ARTIST tags, so summing the clusters — which would say 4 — is
    exactly the shape this forbids."""
    cid = await seed_concert(client.db)
    await _seed_two_groups_sharing_a_member(client.db, cid)
    login(client)

    panel = _performers_panel(client)
    assert "3 performers" in panel
    assert "4 performers" not in panel


async def test_the_trailer_block_renders_without_a_label_row(client):
    """The `{% if cluster.group %}` branch, from the other side. A performer
    in no attached group belongs to nobody, so her block must carry NO
    `.pclabel` -- an empty label row would read as an unnamed group, and
    borrowing the neighbouring group's label would be a lie."""
    cid = await seed_concert(client.db)
    await add_group_with_members(client.db, cid, "Aqours", ["Chika"])
    await add_tag(client.db, cid, "Guest Star", TagKind.ARTIST)
    login(client)

    panel = _performers_panel(client)
    blocks = panel.split('class="pcluster"')[1:]
    assert len(blocks) == 2
    trailer = next(b for b in blocks if ">Guest Star<" in b)
    assert "pclabel" not in trailer
    assert ">Aqours<" not in trailer
    # ...and the labelled one still has its row, or the assertion above
    # would pass on a panel that lost every label.
    assert sum(1 for b in blocks if "pclabel" in b) == 1


async def test_a_groups_only_bill_shows_the_label_but_no_count(client):
    """Owner ruling, 2026-07-27: at zero the count disappears rather than
    reading "0 performers". A group attached with none of its members
    attached is a line-up nobody has listed yet -- the label row says that
    already, and a zero would say the opposite."""
    cid = await seed_concert(client.db)
    gid, _members = await make_group(client.db, "Aqours", ["Chika", "Riko"])
    await attach_existing(client.db, cid, [gid])
    login(client)

    panel = _performers_panel(client)
    assert ">Aqours<" in panel          # the label row survives
    assert "performer" not in panel     # no count, singular or plural
    assert "0 performer" not in panel
    # ...and no empty chip row under it. An empty div still pays .pclabel's
    # bottom margin (measured: 5.6px), which makes the gap after a member-less
    # group 20px where every other cluster boundary is 14.4px. `:empty` cannot
    # reach it -- the template's indentation puts whitespace inside the div.
    assert "chiprow" not in panel


async def test_a_concert_page_with_groups_renders(client):
    """The MissingGreenlet guard. Tag.members is a lazy self-referential m2m:
    a template that walked it during async rendering would answer 500 here,
    which is the whole reason the clustering is derived in the route."""
    cid = await seed_concert(client.db)
    await add_group_with_members(client.db, cid, "Aqours", ["Chika", "Riko"])
    login(client)

    r = client.get("/concerts/np")
    assert r.status_code == 200
    assert ">Chika<" in r.text


# ── header: links and actions ────────────────────────────────────────────


async def test_the_source_link_names_ramen_events(client):
    """"source" said nothing; the source is ramen.events."""
    await seed_concert(client.db, source_url="https://ramen.events/e/1")
    login(client)

    body = client.get("/concerts/np").text
    links = body.split('class="links"', 1)[1].split("</p>", 1)[0]
    assert "ramen.events" in links
    assert ">source<" not in links


async def test_notes_render_when_only_a_variant_is_filled(client):
    """The guard used to test `concert.notes` (the Japanese column) while the
    body rendered `loc(concert, "notes")` -- so notes filled ONLY in
    `notes_en` (original left NULL) rendered nothing at all for an EN
    viewer."""
    await seed_concert(client.db, notes=None, notes_en="Doors open early.")
    login(client)

    client.cookies.set("lang", "en")
    body = client.get("/concerts/np").text
    assert "Doors open early." in body


async def test_a_non_editor_sees_no_editor_controls(client):
    await seed_concert(client.db)
    login(client)

    body = client.get("/concerts/np").text
    assert "Edit event" not in body
    assert "Export YAML" not in body
    assert "/concerts/np/edit" not in body


async def test_an_editor_sees_edit_and_export_in_the_header(client):
    await seed_concert(client.db)
    await make_editor(client.db)
    login(client, EDITOR, "editor")

    body = client.get("/concerts/np").text
    assert "Edit event" in body
    assert "Export YAML" in body


async def test_the_header_carries_no_date_range_and_no_single_venue(client):
    """A tour's legs disagree with any single header summary, so there is no
    header summary."""
    cid = await seed_concert(client.db)
    osaka = await add_venue_tag(client.db, "Osaka-jo Hall")
    tokyo = await add_venue_tag(client.db, "Tokyo Dome")
    await add_day(client.db, cid, "Osaka", days_ahead=30, venue_tag_id=osaka)
    await add_day(client.db, cid, "Tokyo", days_ahead=31, venue_tag_id=tokyo)
    login(client)

    body = client.get("/concerts/np").text
    head = body.split('class="chead"', 1)[1].split("</header>", 1)[0]
    assert "Osaka-jo Hall" not in head


# ── body: legs ───────────────────────────────────────────────────────────


async def test_a_two_venue_concert_renders_two_different_venues(client):
    """The reason the header venue had to go: on a tour the legs disagree
    with any single summary, so each leg carries its own."""
    cid = await seed_concert(client.db)
    osaka = await add_venue_tag(client.db, "Osaka-jo Hall")
    tokyo = await add_venue_tag(client.db, "Tokyo Dome")
    await add_day(client.db, cid, "Osaka", days_ahead=30, venue_tag_id=osaka)
    await add_day(client.db, cid, "Tokyo", days_ahead=31, venue_tag_id=tokyo)
    login(client)

    body = client.get("/concerts/np").text
    assert "Osaka-jo Hall" in body
    assert "Tokyo Dome" in body


async def test_a_leg_venue_renders_from_its_tag_in_each_locale(client):
    """The leg's venue is a real VENUE tag now, so one entry renders in three
    languages -- `loc()` off the tag, not the leg's free text."""
    cid = await seed_concert(client.db)
    vid = await add_venue_tag(
        client.db, "Kアリーナ横浜", name_en="K Arena Yokohama", name_zh="K竞技场横滨",
        city="横浜", city_en="Yokohama", city_zh="横滨",
    )
    await add_day(client.db, cid, "Day 1", days_ahead=30, venue_tag_id=vid)
    login(client)

    client.cookies.set("lang", "en")
    en = client.get("/concerts/np").text
    assert "K Arena Yokohama" in en
    assert "Yokohama" in en
    assert "Kアリーナ横浜" not in en

    client.cookies.set("lang", "zh")
    zh = client.get("/concerts/np").text
    assert "K竞技场横滨" in zh
    assert "K Arena Yokohama" not in zh

    client.cookies.set("lang", "ja")
    ja = client.get("/concerts/np").text
    assert "Kアリーナ横浜" in ja
    assert "横浜" in ja


async def test_changing_a_legs_venue_tag_changes_what_the_page_renders(client):
    """The stale-render bug this task closes: the page used to resolve the
    venue from the leg's FREE TEXT by name, so re-pointing a leg at a new
    venue tag left the OLD name on screen with no UI path to correct it -- a
    confidently wrong venue, not a missing one. The venue now resolves from
    the leg's VENUE tag alone, so a re-point takes effect immediately."""
    cid = await seed_concert(client.db)
    old = await add_venue_tag(client.db, "Osaka-jo Hall")
    new = await add_venue_tag(client.db, "Tokyo Dome")
    day_id = await add_day(
        client.db, cid, "Day 1", days_ahead=30, venue_tag_id=old
    )
    login(client)
    assert "Osaka-jo Hall" in client.get("/concerts/np").text

    async with client.db() as s:
        day = await s.get(ConcertDay, day_id)
        day.venue_tag_id = new
        await s.commit()

    body = client.get("/concerts/np").text
    assert "Tokyo Dome" in body
    assert "Osaka-jo Hall" not in body


async def test_a_leg_venue_does_not_lazy_load(client):
    """ConcertDay.venue_tag is lazy="raise" on purpose: a lazy load during
    async rendering is a MissingGreenlet 500, which this project has shipped
    once. A missing eager load fails loudly here instead of in production."""
    cid = await seed_concert(client.db)
    vid = await add_venue_tag(client.db, "Zepp Haneda")
    await add_day(client.db, cid, "Day 1", days_ahead=30, venue_tag_id=vid)
    login(client)

    assert client.get("/concerts/np").status_code == 200


async def test_a_cancelled_leg_is_dimmed_but_keeps_its_own_date_and_rounds(client):
    """Invariant 2: a cancelled leg is flagged, never deleted, because
    applies_to depends on the row still existing. So it renders -- dimmed and
    badged -- with its rounds still visible."""
    cid = await seed_concert(client.db)
    dead = await add_day(client.db, cid, "Osaka", days_ahead=30, cancelled=True)
    await add_day(client.db, cid, "Tokyo", days_ahead=31)
    await add_round(client.db, cid, "Osaka presale", applies_to=[dead])
    login(client)

    body = client.get("/concerts/np").text
    assert "leg off" in body        # dimmed, not dropped
    assert "Cancelled" in body      # and badged as such
    assert "Osaka presale" in body  # its rounds are not hidden with it


async def test_the_concert_page_says_the_event_is_cancelled(client):
    """Every leg cancelled: the show is off, and the page says so ONCE, in the
    "needs attention" callout shape (.banner.dgr), before the round list. The
    legs each carry their own Cancelled badge already, but a reader scanning
    three badged legs is being asked to add them up -- the concert-level fact
    is not the sum of the leg-level ones."""
    cid = await seed_concert(client.db)
    d1 = await add_day(client.db, cid, "Osaka", days_ahead=30, cancelled=True)
    await add_day(client.db, cid, "Tokyo", days_ahead=31, cancelled=True)
    await add_round(client.db, cid, "Osaka presale", applies_to=[d1])
    login(client)

    body = client.get("/concerts/np").text
    assert "banner dgr" in body
    assert "This event is cancelled." in body
    # Before the round list, where the reader meets it first.
    assert body.index("This event is cancelled.") < body.index('id="concert-rounds"')
    # And the page asks for nothing: both capture gates are shut on every
    # round, so no button offers an answer `record_round_outcome` would then
    # refuse to take back -- and the catch-up dialog has nothing to ask about.
    assert "I have applied" not in body
    assert 'id="resultDlg"' not in body


async def test_a_live_concert_page_has_no_cancelled_banner(client):
    """One leg down is not the show being off. The cancelled leg keeps its own
    badge; the concert-level banner stays away."""
    cid = await seed_concert(client.db)
    await add_day(client.db, cid, "Osaka", days_ahead=30, cancelled=True)
    await add_day(client.db, cid, "Tokyo", days_ahead=31)
    login(client)

    body = client.get("/concerts/np").text
    assert "This event is cancelled." not in body
    assert "banner dgr" not in body
    assert "Cancelled" in body  # the leg badge is untouched


async def test_a_round_naming_only_cancelled_legs_sits_under_those_legs(client):
    """The all-legs group is not "everything not leg-specific". A round that
    names one cancelled leg is a fact about THAT leg, and belongs under it."""
    cid = await seed_concert(client.db)
    dead = await add_day(client.db, cid, "Osaka", days_ahead=30, cancelled=True)
    await add_day(client.db, cid, "Tokyo", days_ahead=31)
    await add_round(client.db, cid, "Osaka only", applies_to=[dead])
    login(client)

    body = client.get("/concerts/np").text
    # From the cancelled leg's own section to the start of the next one.
    osaka = body.split("Osaka", 1)[1].split('class="leg"', 1)[0]
    assert "Osaka only" in osaka
    assert "All legs" not in body


async def test_a_round_covering_every_live_leg_renders_under_each_leg(client):
    """The separate all-legs section is gone: a round covering both legs is a
    fact about each of them, and the viewer's standing on it is per-leg now
    (won Saturday, lost Sunday), so each leg reads as a complete story."""
    cid = await seed_concert(client.db)
    d1 = await add_day(client.db, cid, "Day 1", days_ahead=30)
    d2 = await add_day(client.db, cid, "Day 2", days_ahead=31)
    await add_round(client.db, cid, "Fan club presale", applies_to=[d1, d2])
    login(client)

    body = client.get("/concerts/np").text
    # Scoped past "Next for you", which legitimately names this round too.
    legs = body.split("<!-- /standing -->", 1)[-1]
    assert legs.count("Fan club presale") == 2
    assert "All legs" not in legs


async def test_no_horizontal_scroll_table_wrapper_remains(client):
    cid = await seed_concert(client.db)
    d1 = await add_day(client.db, cid, "Day 1", days_ahead=30)
    await add_round(client.db, cid, "R1", applies_to=[d1])
    login(client)

    body = client.get("/concerts/np").text
    assert "table-scroll" not in body
    assert "<table" not in body


# ── body: capture actions, reusing the shared rules ──────────────────────


def round_block(body: str, label: str) -> str:
    """Everything from a round's label to the end of its row."""
    return body.split(label, 1)[1].split("<!-- /rnd -->", 1)[0]


async def test_a_round_that_has_not_opened_offers_no_capture_actions(client):
    cid = await seed_concert(client.db)
    d1 = await add_day(client.db, cid, "Day 1", days_ahead=60)
    await add_round(
        client.db, cid, "Future round", applies_to=[d1],
        opens=datetime.now(UTC) + timedelta(days=5),
        closes=datetime.now(UTC) + timedelta(days=15),
    )
    login(client)

    block = round_block(client.get("/concerts/np").text, "Future round")
    assert "Not open yet" in block
    assert "I have applied" not in block


async def test_an_open_round_with_no_outcome_offers_both_capture_actions(client):
    cid = await seed_concert(client.db)
    d1 = await add_day(client.db, cid, "Day 1", days_ahead=60)
    await add_round(
        client.db, cid, "Open round", applies_to=[d1],
        opens=datetime.now(UTC) - timedelta(days=1),
        closes=datetime.now(UTC) + timedelta(days=15),
    )
    login(client)

    block = round_block(client.get("/concerts/np").text, "Open round")
    assert "I have applied" in block
    assert "Not applying" in block


async def test_applied_with_the_result_not_due_offers_nothing_to_do(client):
    cid = await seed_concert(client.db)
    d1 = await add_day(client.db, cid, "Day 1", days_ahead=60)
    rid = await add_round(
        client.db, cid, "Waiting round", applies_to=[d1],
        opens=datetime.now(UTC) - timedelta(days=5),
        closes=datetime.now(UTC) + timedelta(days=2),
        results=datetime.now(UTC) + timedelta(days=9),
    )
    await set_outcome(client.db, rid, LotteryOutcome.APPLIED)
    login(client)

    block = round_block(client.get("/concerts/np").text, "Waiting round")
    assert "Nothing to do" in block
    assert "I won" not in block


async def test_applied_with_the_result_due_offers_won_and_lost(client):
    cid = await seed_concert(client.db)
    d1 = await add_day(client.db, cid, "Day 1", days_ahead=60)
    rid = await add_round(
        client.db, cid, "Decided round", applies_to=[d1],
        opens=datetime.now(UTC) - timedelta(days=10),
        closes=datetime.now(UTC) - timedelta(days=3),
        results=datetime.now(UTC) - timedelta(hours=1),
    )
    await set_outcome(client.db, rid, LotteryOutcome.APPLIED)
    login(client)

    block = round_block(client.get("/concerts/np").text, "Decided round")
    assert "I won" in block
    assert "I lost" in block


async def test_a_won_round_offers_paid(client):
    cid = await seed_concert(client.db)
    d1 = await add_day(client.db, cid, "Day 1", days_ahead=60)
    rid = await add_round(
        client.db, cid, "Won round", applies_to=[d1],
        opens=datetime.now(UTC) - timedelta(days=10),
        closes=datetime.now(UTC) - timedelta(days=3),
        payment=datetime.now(UTC) + timedelta(days=4),
    )
    await set_outcome(client.db, rid, LotteryOutcome.APPLIED)
    await set_outcome(client.db, rid, LotteryOutcome.WON)
    login(client)

    block = round_block(client.get("/concerts/np").text, "Won round")
    assert ">Paid<" in block


# ── body: covered rounds and per-day results ─────────────────────────────


def legs_of(body: str) -> str:
    """The page past the standing strip and short of the catch-up dialog --
    both legitimately name a round too, and these tests are about the leg
    sections between them.

    The dialog cut matters as much as the strip cut: it renders the UNFILTERED
    capture macro (every leg's questions plus the whole-round shortcuts), so a
    leg assertion reading to the end of the body would find "Won — Day 1"
    inside Day 2's section and pass or fail for the wrong reason."""
    return body.split("<!-- /standing -->", 1)[-1].split('id="resultDlg"', 1)[0]


def leg_sections(body: str) -> dict[str, str]:
    """Each leg's section keyed by its heading, from that heading to the next.

    A round covering both legs renders under EACH of them, so "the block for
    that round" is now ambiguous -- stopping at the first occurrence would
    silently test Day 1's copy and call it the page. Every per-leg assertion
    goes through this instead."""
    sections = {}
    for chunk in legs_of(body).split('class="leg-heading')[1:]:
        heading = chunk.split(">", 1)[1].split("<", 1)[0].strip()
        sections[heading] = chunk
    return sections


async def test_a_covered_round_states_it_and_offers_no_buttons(client):
    """Once some round has secured this leg, a later round selling the same
    leg has nothing left to ask: it renders quietly, with a note saying why
    rather than buttons whose answer is already known."""
    cid = await seed_concert(client.db)
    d1 = await add_day(client.db, cid, "Day 1", days_ahead=60)
    won = await add_round(
        client.db, cid, "FC lottery", applies_to=[d1],
        opens=datetime.now(UTC) - timedelta(days=30),
        closes=datetime.now(UTC) - timedelta(days=10),
        results=datetime.now(UTC) - timedelta(days=5),
    )
    await add_round(
        client.db, cid, "General sale", applies_to=[d1],
        opens=datetime.now(UTC) - timedelta(days=1),
        closes=datetime.now(UTC) + timedelta(days=7),
    )
    await set_outcome(client.db, won, LotteryOutcome.WON)
    login(client)

    block = round_block(legs_of(client.get("/concerts/np").text), "General sale")
    assert "Covered" in block
    assert "<form" not in block
    assert "I have applied" not in block


async def multi_leg_lottery(db, concert_id):
    """A two-leg round the viewer is in, whose results are out -- the shape
    every per-leg assertion below is about."""
    d1 = await add_day(db, concert_id, "Day 1", days_ahead=60)
    d2 = await add_day(db, concert_id, "Day 2", days_ahead=61)
    rid = await add_round(
        db, concert_id, "Fan club lottery", applies_to=[d1, d2],
        opens=datetime.now(UTC) - timedelta(days=10),
        closes=datetime.now(UTC) - timedelta(days=3),
        results=datetime.now(UTC) - timedelta(hours=1),
    )
    await set_outcome(db, rid, LotteryOutcome.APPLIED)
    return d1, d2, rid


async def test_a_leg_card_asks_about_that_leg_and_no_other(client):
    """A round covering both legs renders under BOTH of them, and each copy
    asks only about the leg it is sitting under. Repeating all three
    questions per section would put nine forms on a three-leg concert, and
    "Won — Day 2" under Day 1's heading is a mode error besides.

    The whole-round shortcuts (Won/Lost all, Lost the rest) belong to the
    unfiltered caller -- Home's rows and the catch-up dialog -- not to a card
    that is scoped to one leg."""
    cid = await seed_concert(client.db)
    _d1, _d2, rid = await multi_leg_lottery(client.db, cid)
    login(client)

    body = client.get("/concerts/np").text
    sections = leg_sections(body)
    assert set(sections) == {"Day 1", "Day 2"}
    # The round is a fact about each leg, so its label appears under both.
    assert all("Fan club lottery" in s for s in sections.values())

    assert "Won — Day 1" in sections["Day 1"]
    assert "Lost — Day 1" in sections["Day 1"]
    assert "Not going — Day 1" in sections["Day 1"]
    assert "Day 2" not in sections["Day 1"].split("Fan club lottery", 1)[1]

    assert "Won — Day 2" in sections["Day 2"]
    assert "Lost — Day 2" in sections["Day 2"]
    assert "Not going — Day 2" in sections["Day 2"]
    assert "Won — Day 1" not in sections["Day 2"]

    # Scoped to the leg sections, not the whole page: the catch-up dialog at
    # the foot is the unfiltered caller and DOES carry these (see
    # test_the_catch_up_dialog_carries_the_whole_round_shortcuts).
    assert "Won (all)" not in legs_of(body)
    assert "Lost (all)" not in legs_of(body)
    assert "Lost the rest" not in legs_of(body)
    # The single-leg pair is gone: it could only have lied about one of them.
    assert ">I won<" not in body
    assert ">I lost<" not in body
    assert f"/rounds/{rid}/day-result" in body


async def test_a_resolved_leg_asks_nothing_while_its_sibling_pends(client):
    """Saturday answered, Sunday not: Saturday's card falls silent (its pill
    already says how it went) and only Sunday still asks."""
    cid = await seed_concert(client.db)
    d1, _d2, rid = await multi_leg_lottery(client.db, cid)
    await set_day_result(client.db, rid, d1, LegResult.WON)
    login(client)

    sections = leg_sections(client.get("/concerts/np").text)
    assert "day-result" not in sections["Day 1"]
    assert "Won — Day 2" in sections["Day 2"]
    assert "Lost — Day 2" in sections["Day 2"]


async def test_each_leg_pills_its_own_result_after_a_split_decision(client):
    """Won Saturday, lost Sunday. The round-level outcome is WON, so a pill
    read off it would tell Sunday it had a ticket. `leg_result` is that leg's
    own answer and outranks the round wherever it has one."""
    cid = await seed_concert(client.db)
    d1, d2, rid = await multi_leg_lottery(client.db, cid)
    await set_day_result(client.db, rid, d1, LegResult.WON)
    await set_day_result(client.db, rid, d2, LegResult.LOST)
    login(client)

    sections = leg_sections(client.get("/concerts/np").text)
    assert '<span class="pill p-danger">Won</span>' in sections["Day 1"]
    assert '<span class="pill p-quiet">Lost</span>' in sections["Day 2"]
    assert '<span class="pill p-danger">Won</span>' not in sections["Day 2"]


async def test_a_secured_single_leg_round_still_pills_secured(client):
    """The per-leg pill must not flatten PAID into WON: a leg cannot carry
    the payment distinction, so a won leg defers to the round for it."""
    cid = await seed_concert(client.db)
    d1 = await add_day(client.db, cid, "Day 1", days_ahead=60)
    rid = await add_round(
        client.db, cid, "Paid round", applies_to=[d1],
        opens=datetime.now(UTC) - timedelta(days=10),
        closes=datetime.now(UTC) - timedelta(days=3),
    )
    await set_outcome(client.db, rid, LotteryOutcome.WON)
    await set_outcome(client.db, rid, LotteryOutcome.PAID)
    login(client)

    assert '<span class="pill p-ok">Secured</span>' in leg_sections(
        client.get("/concerts/np").text
    )["Day 1"]


async def test_a_concert_with_no_legs_heads_its_rounds_plainly(client):
    """The all-legs section is gone. The only thing left in the second list is
    a round on a concert with no legs at all, and it needs a heading that does
    not claim legs the concert does not have."""
    cid = await seed_concert(client.db)
    await add_round(
        client.db, cid, "Dateless round",
        closes=datetime.now(UTC) + timedelta(days=7),
    )
    login(client)

    body = client.get("/concerts/np").text
    assert "Dateless round" in body
    assert ">Rounds<" in body
    assert "All legs" not in body


# ── body: "Next for you" ─────────────────────────────────────────────────


async def test_next_for_you_is_absent_with_no_standing_and_nothing_open(client):
    """An empty urgency panel is worse than no panel at all."""
    cid = await seed_concert(client.db)
    d1 = await add_day(client.db, cid, "Day 1", days_ahead=60)
    await add_round(
        client.db, cid, "Closed round", applies_to=[d1],
        opens=datetime.now(UTC) - timedelta(days=30),
        closes=datetime.now(UTC) - timedelta(days=10),
    )
    login(client)

    assert "Next for you" not in client.get("/concerts/np").text


async def test_next_for_you_names_the_round_that_is_open_now(client):
    cid = await seed_concert(client.db)
    d1 = await add_day(client.db, cid, "Day 1", days_ahead=60)
    await add_round(
        client.db, cid, "Lottery round 1", applies_to=[d1],
        opens=datetime.now(UTC) - timedelta(days=1),
        closes=datetime.now(UTC) + timedelta(days=6),
    )
    login(client)

    body = client.get("/concerts/np").text
    standing = body.split("Next for you", 1)[1].split("<!-- /standing -->", 1)[0]
    assert "Lottery round 1" in standing


async def test_next_for_you_appears_on_standing_alone(client):
    """Applied and waiting IS standing, even with nothing left to press."""
    cid = await seed_concert(client.db)
    d1 = await add_day(client.db, cid, "Day 1", days_ahead=60)
    rid = await add_round(
        client.db, cid, "Lottery round 1", applies_to=[d1],
        opens=datetime.now(UTC) - timedelta(days=10),
        closes=datetime.now(UTC) - timedelta(days=1),
        results=datetime.now(UTC) + timedelta(days=4),
    )
    await set_outcome(client.db, rid, LotteryOutcome.APPLIED)
    login(client)

    assert "Next for you" in client.get("/concerts/np").text


# ── capture posts back to THIS page's fragment ───────────────────────────


async def test_recording_an_outcome_swaps_the_concert_pages_own_rounds(client):
    """No new write path (invariant 2) -- the same POST /rounds/{id}/outcome
    Home uses. It just has to answer with THIS page's fragment: replying with
    Home's deadline rows would splice Home's content into the concert page,
    and the out-of-band #board swap would silently hit nothing here.

    The surface comes from HX-Current-URL, which htmx sends on every request,
    so the shared `_capture_actions.html` macro needs no per-surface field."""
    cid = await seed_concert(client.db)
    d1 = await add_day(client.db, cid, "Day 1", days_ahead=60)
    rid = await add_round(
        client.db, cid, "Lottery round 1", applies_to=[d1],
        opens=datetime.now(UTC) - timedelta(days=1),
        closes=datetime.now(UTC) + timedelta(days=6),
    )
    login(client)

    r = client.post(
        f"/rounds/{rid}/outcome",
        data={"outcome": "applied"},
        headers={"HX-Request": "true", "HX-Current-URL": "http://testserver/concerts/np"},
    )
    assert r.status_code == 200
    assert 'id="concert-rounds"' in r.text     # the declared hx-target
    assert "Lottery round 1" in r.text
    assert 'id="deadline-rows"' not in r.text  # not Home's fragment
    # The header's "Next for you" strip rides along out of band (C1), or it
    # would show the stale round until reload.
    assert 'id="concert-standing"' in r.text
    assert "hx-swap-oob" in r.text
    assert "Applied" in r.text                 # and the write really happened


async def test_recording_without_htmx_returns_to_the_concert(client):
    """The forms carry a real method/action, so a JS-less browser navigates
    here. Sending it to Home would lose the reader's place -- the Referer is
    the only thing that says where they were, and a missing one falls back to
    Home exactly as before."""
    cid = await seed_concert(client.db)
    d1 = await add_day(client.db, cid, "Day 1", days_ahead=60)
    rid = await add_round(
        client.db, cid, "Lottery round 1", applies_to=[d1],
        opens=datetime.now(UTC) - timedelta(days=1),
        closes=datetime.now(UTC) + timedelta(days=6),
    )
    login(client)

    r = client.post(
        f"/rounds/{rid}/outcome", data={"outcome": "applied"},
        headers={"Referer": "http://testserver/concerts/np"},
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/concerts/np"

    r = client.post(f"/rounds/{rid}/outcome", data={"outcome": "won"})
    assert r.status_code == 303
    assert r.headers["location"] == "/"


async def test_a_day_result_press_swaps_this_pages_rounds_and_strip(client):
    """The per-leg write answers exactly like the round-level one: this page's
    rounds region as the declared target, plus the header strip out of band.
    Anything less leaves the strip naming a round the reader just resolved."""
    cid = await seed_concert(client.db)
    d1, _d2, rid = await multi_leg_lottery(client.db, cid)
    login(client)

    r = client.post(
        f"/rounds/{rid}/day-result",
        data={"result": "won", "day_id": d1},
        headers={"HX-Request": "true", "HX-Current-URL": "http://testserver/concerts/np"},
    )
    assert r.status_code == 200
    assert 'id="concert-rounds"' in r.text     # the declared hx-target
    assert "Fan club lottery" in r.text
    assert 'id="deadline-rows"' not in r.text  # not Home's fragment
    assert 'id="concert-standing"' in r.text   # the OOB strip contract
    assert "hx-swap-oob" in r.text
    # And the write really happened: Day 1 now pills its own result. The
    # rounds fragment comes FIRST in the response (the strip trails it as the
    # oob swap), so the leg sections are everything ahead of the strip.
    rounds_fragment = r.text.split('id="concert-standing"', 1)[0]
    assert '<span class="pill p-danger">Won</span>' in leg_sections(rounds_fragment)["Day 1"]


# ── the catch-up dialog ──────────────────────────────────────────────────
#
# Opening a concert page whose results are out and unrecorded is exactly the
# moment to ask, so the page asks -- once, in a dialog, over the leg sections
# that will still be there when it closes. It is also the page's ONLY
# unfiltered caller of the capture macro, so the whole-round shortcuts are
# pinned here and nowhere else.


def dialog_of(body: str) -> str:
    return body.split('id="resultDlg"', 1)[1].split("</dialog>", 1)[0]


async def test_a_pending_multi_leg_result_opens_the_catch_up_dialog(client):
    cid = await seed_concert(client.db)
    _d1, _d2, _rid = await multi_leg_lottery(client.db, cid)
    login(client)

    body = client.get("/concerts/np").text
    assert 'id="resultDlg"' in body
    dlg = dialog_of(body)
    # It names what it is asking about, and asks about EVERY unresolved leg.
    assert "Fan club lottery" in dlg
    assert "Won — Day 1" in dlg
    assert "Won — Day 2" in dlg


async def test_the_catch_up_dialog_carries_the_whole_round_shortcuts(client):
    """The unfiltered macro call. "Won (all)" / "Lost (all)" answer a whole
    round in one press, and the dialog is now the only place on this page they
    render -- a leg card is scoped to one leg, where they would be a button
    about the others."""
    cid = await seed_concert(client.db)
    _d1, _d2, _rid = await multi_leg_lottery(client.db, cid)
    login(client)

    dlg = dialog_of(client.get("/concerts/np").text)
    assert "Won (all)" in dlg
    assert "Lost (all)" in dlg


async def test_the_dialog_offers_lost_the_rest_once_a_leg_is_won(client):
    """With a ticket in hand the round is no longer losable as a whole, so
    "Lost (all)" gives way to "Lost the rest" -- the same swap the macro makes
    for Home's rows."""
    cid = await seed_concert(client.db)
    d1, _d2, rid = await multi_leg_lottery(client.db, cid)
    await set_day_result(client.db, rid, d1, LegResult.WON)
    login(client)

    dlg = dialog_of(client.get("/concerts/np").text)
    assert "Lost the rest" in dlg
    assert "Lost (all)" not in dlg


async def test_the_dialog_withdraws_won_all_once_a_leg_is_answered(client):
    """A LOST leg turns the no-rows-means-all fallback off, so a whole-round
    WON press would leave a WON round with zero WON legs -- securing nothing,
    and thrown away entirely by the next "Lost — Day 2". "Lost (all)" stays:
    with no leg won, losing the whole round is still an honest thing to say."""
    cid = await seed_concert(client.db)
    d1, _d2, rid = await multi_leg_lottery(client.db, cid)
    await set_day_result(client.db, rid, d1, LegResult.LOST)
    login(client)

    dlg = dialog_of(client.get("/concerts/np").text)
    assert "Won (all)" not in dlg
    assert "Won — Day 2" in dlg
    assert "Lost (all)" in dlg


async def test_no_dialog_once_every_leg_is_resolved(client):
    """Nothing left to ask, so nothing pops up. A dialog that reopened on every
    visit to a settled concert would be the page's most annoying feature."""
    cid = await seed_concert(client.db)
    d1, d2, rid = await multi_leg_lottery(client.db, cid)
    await set_day_result(client.db, rid, d1, LegResult.WON)
    await set_day_result(client.db, rid, d2, LegResult.LOST)
    login(client)

    assert 'id="resultDlg"' not in client.get("/concerts/np").text


async def test_no_dialog_before_the_results_are_due(client):
    """Applied, results not out yet: there is no answer to give."""
    cid = await seed_concert(client.db)
    d1 = await add_day(client.db, cid, "Day 1", days_ahead=60)
    d2 = await add_day(client.db, cid, "Day 2", days_ahead=61)
    rid = await add_round(
        client.db, cid, "Fan club lottery", applies_to=[d1, d2],
        opens=datetime.now(UTC) - timedelta(days=10),
        closes=datetime.now(UTC) + timedelta(days=3),
        results=datetime.now(UTC) + timedelta(days=5),
    )
    await set_outcome(client.db, rid, LotteryOutcome.APPLIED)
    login(client)

    assert 'id="resultDlg"' not in client.get("/concerts/np").text


async def test_no_dialog_with_no_standing_at_all(client):
    """An open round nobody entered asks for an application, not a result --
    that question belongs on the leg card, not in a modal."""
    cid = await seed_concert(client.db)
    d1 = await add_day(client.db, cid, "Day 1", days_ahead=60)
    await add_round(
        client.db, cid, "Open round", applies_to=[d1],
        opens=datetime.now(UTC) - timedelta(days=1),
        closes=datetime.now(UTC) + timedelta(days=6),
    )
    login(client)

    assert 'id="resultDlg"' not in client.get("/concerts/np").text


async def test_a_single_leg_result_still_gets_the_dialog(client):
    """A one-night concert has no per-day questions, but the reader is just as
    overdue to say how it went -- so the dialog carries the flat pair."""
    cid = await seed_concert(client.db)
    d1 = await add_day(client.db, cid, "Day 1", days_ahead=60)
    rid = await add_round(
        client.db, cid, "Decided round", applies_to=[d1],
        opens=datetime.now(UTC) - timedelta(days=10),
        closes=datetime.now(UTC) - timedelta(days=3),
        results=datetime.now(UTC) - timedelta(hours=1),
    )
    await set_outcome(client.db, rid, LotteryOutcome.APPLIED)
    login(client)

    dlg = dialog_of(client.get("/concerts/np").text)
    assert ">I won<" in dlg
    assert ">I lost<" in dlg
    assert f"/rounds/{rid}/outcome" in dlg


async def test_the_dialog_posts_to_this_pages_rounds_region(client):
    """Its buttons are the same macro the leg cards use, so they must swap the
    same target -- a dialog answering with Home's fragments would splice Home
    into this page."""
    cid = await seed_concert(client.db)
    _d1, _d2, rid = await multi_leg_lottery(client.db, cid)
    login(client)

    dlg = dialog_of(client.get("/concerts/np").text)
    assert f'hx-post="/rounds/{rid}/day-result"' in dlg
    assert 'hx-target="#concert-rounds"' in dlg
    # Plain-POST fallback intact: the forms carry a real method/action too.
    assert f'action="/rounds/{rid}/day-result"' in dlg


# ── Task 4: follow toggle CSS, performer-chip centring, reminders redesign ─


def _read_style_css() -> str:
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    return (root / "src" / "app" / "web" / "static" / "style.css").read_text(encoding="utf-8")


def test_style_gives_follow_a_green_pill_when_on_and_dim_outline_when_off():
    """`.follow` shipped with zero CSS -- the button rendered as an unstyled
    default. Pin the on/off treatment the demo specifies: a green "covered"
    pill with a check glyph when on, a dim outline otherwise."""
    css = _read_style_css()
    assert ".follow" in css
    assert ".follow.on" in css
    assert "var(--ok-wash)" in css  # the green wash fill when following
    assert "\\2713" in css  # the check glyph, added via content so no markup change was needed
    # The unfollowed state stays dim/outlined, whether or not .quiet also
    # applies to the same button.
    assert ".follow:not(.on)" in css or ".btn.quiet.follow" in css


def test_style_centers_performer_chip_names():
    """The owner's explicit ask: performer chip labels must be centred, not
    left-aligned like the default .chip."""
    css = _read_style_css()
    block = css.split(".performers .chip {", 1)[1].split("}", 1)[0]
    assert "justify-content: center" in block
    assert "text-align: center" in block


def test_style_dims_a_performer_chip_with_no_eventernote_link():
    css = _read_style_css()
    assert ".performers .chip.nolink" in css
    block = css.split(".performers .chip.nolink", 1)[1].split("}", 1)[0]
    assert "opacity: .75" in block


async def test_following_button_carries_the_reminder_caption(client):
    """A followed concert's toggle explains what following means -- the
    demo's "You will be reminded about every round below." caption, ported
    into `_following_toggle.html` next to the button."""
    await seed_concert(client.db)
    login(client)
    client.post("/concerts/np/subscription", data={"state": "subscribed"})

    body = client.get("/concerts/np").text
    toggle = body.split('id="following-toggle"', 1)[1].split("</div>", 1)[0]
    assert "btn follow on" in toggle
    assert "You will be reminded about every round below." in toggle


async def test_unfollowed_toggle_carries_no_reminder_caption(client):
    """The caption promises reminders; it would be false when not following,
    so it only renders in the following branch."""
    await seed_concert(client.db)
    login(client)

    body = client.get("/concerts/np").text
    toggle = body.split('id="following-toggle"', 1)[1].split("</div>", 1)[0]
    assert "btn quiet follow" in toggle
    assert "You will be reminded about every round below." not in toggle


async def test_a_dead_concerts_follow_toggle_promises_nothing(client):
    """The caption promises a reminder for every round below. On a concert
    whose every leg is cancelled the scheduler plans none of them (task 1),
    and the page carries a "this event is cancelled" banner a few lines above
    -- so the promise is both false and visibly self-contradicting. The dead
    concert's toggle states the fact instead. The button itself keeps working:
    following is still a real preference (invariant 8)."""
    cid = await seed_concert(client.db)
    await add_day(client.db, cid, "Osaka", days_ahead=30, cancelled=True)
    await add_day(client.db, cid, "Tokyo", days_ahead=31, cancelled=True)
    login(client)
    client.post("/concerts/np/subscription", data={"state": "subscribed"})

    body = client.get("/concerts/np").text
    toggle = body.split('id="following-toggle"', 1)[1].split("</div>", 1)[0]
    assert "btn follow on" in toggle  # still a working toggle
    assert "You will be reminded about every round below." not in toggle
    assert "This event is cancelled, so no reminders will be sent." in toggle

    # The htmx swap re-renders this partial ALONE, off following_toggle_context
    # -- so the fact has to live in that context, not in the page's. Pin it:
    # the copy swapped back in after a toggle must not revert to the promise.
    frag = client.post(
        "/concerts/np/subscription", data={"state": "subscribed"},
        headers={"HX-Request": "true"},
    ).text
    assert "This event is cancelled, so no reminders will be sent." in frag
    assert "You will be reminded about every round below." not in frag


async def test_a_live_concerts_follow_toggle_is_unchanged(client):
    """One leg down is not the show being off: the reminders are still coming,
    so the caption stays exactly as it was."""
    cid = await seed_concert(client.db)
    await add_day(client.db, cid, "Osaka", days_ahead=30, cancelled=True)
    await add_day(client.db, cid, "Tokyo", days_ahead=31)
    login(client)
    client.post("/concerts/np/subscription", data={"state": "subscribed"})

    body = client.get("/concerts/np").text
    toggle = body.split('id="following-toggle"', 1)[1].split("</div>", 1)[0]
    assert "You will be reminded about every round below." in toggle
    assert "no reminders will be sent" not in toggle


async def test_a_dead_concerts_unfollow_dialog_promises_nothing(client):
    """The caption's staleness ran one dialog deeper. Holding a won ticket
    turns the toggle into a heavy confirmation, and its copy named a payment
    reminder the planner stopped planning at task 1 -- plus a payment moment
    that will not arrive. A dead concert gets its own sentence, and it does not
    inherit the live branches' claim that unfollowing removes the won mark: the
    opt-out never deletes a RoundOutcome (invariant 8)."""
    cid = await seed_concert(client.db)
    await add_day(client.db, cid, "Osaka", days_ahead=30, cancelled=True)
    await add_day(client.db, cid, "Tokyo", days_ahead=31, cancelled=True)
    rid = await add_round(
        client.db, cid, "Lottery R1", payment=datetime.now(UTC) + timedelta(days=10)
    )
    await set_outcome(client.db, rid, LotteryOutcome.WON)
    login(client)
    client.post("/concerts/np/subscription", data={"state": "subscribed"})

    body = client.get("/concerts/np").text
    assert "unfollowConfirm" in body  # still the heavy confirmation
    assert "remove that mark and the payment reminder" not in body
    assert "this event is cancelled, so no reminders will be sent" in body
    assert "Stopping following does not remove that mark" in body

    # Same reason as the caption: the POST swaps this partial in ALONE, so the
    # fact has to come from following_toggle_context, not the page's.
    frag = client.post(
        "/concerts/np/subscription", data={"state": "subscribed"},
        headers={"HX-Request": "true"},
    ).text
    assert "this event is cancelled, so no reminders will be sent" in frag
    assert "remove that mark and the payment reminder" not in frag


async def test_a_live_concerts_unfollow_dialog_is_unchanged(client):
    """The payment reminder is real while any leg stands, so the dialog keeps
    naming it -- and the moment it is due."""
    cid = await seed_concert(client.db)
    await add_day(client.db, cid, "Osaka", days_ahead=30, cancelled=True)
    await add_day(client.db, cid, "Tokyo", days_ahead=31)
    rid = await add_round(
        client.db, cid, "Lottery R1", payment=datetime.now(UTC) + timedelta(days=10)
    )
    await set_outcome(client.db, rid, LotteryOutcome.WON)
    login(client)
    client.post("/concerts/np/subscription", data={"state": "subscribed"})

    body = client.get("/concerts/np").text
    assert "remove that mark and the payment reminder" in body
    assert "this event is cancelled, so no reminders will be sent" not in body


async def test_the_legacy_meta_grid_block_is_gone(client):
    """The demo's header is lineage -> h1 -> tags -> links only; the old
    title_en/organizer/categories/performers_text dl duplicated what the
    performers panel below already shows."""
    await seed_concert(
        client.db,
        title_en="EN Title", organizer="Some Org", categories="Live",
        performers_text="Someone",
    )
    login(client)

    body = client.get("/concerts/np").text
    assert "meta-grid" not in body
    # The old dl duplicated the free-text organizer/categories; those are gone
    # from the header now. (title_en is no longer a proxy for the removed grid:
    # UGC localization intentionally surfaces it in the title/h1 for EN viewers.)
    assert "Some Org" not in body
    assert "meta-grid" not in body and ">Live<" not in body


async def test_reminders_section_uses_the_row_based_layout(client):
    """Demo shape: a `.rows`/`.row` list (not the old flex `<ul><li>`), each
    row carrying a small "Remove" action -- same delete route, new markup."""
    await seed_concert(client.db)
    login(client)
    client.post("/concerts/np/rules", data={"anchor": "closes", "days_before": 3})

    body = client.get("/concerts/np").text
    rules = body.split('id="rules"', 1)[1].split("</div>\n</article>", 1)[0]
    assert '<div class="rows">' in rules
    assert '<div class="row"' in rules
    assert 'action="/rules/1/delete"' in rules
    assert 'hx-post="/rules/1/delete"' in rules
    assert 'hx-target="#rules"' in rules
    assert ">Remove<" in rules


async def test_add_a_reminder_is_a_reveal_not_an_always_open_form(client):
    """The old fragment always showed a live number-input + <select>. The
    redesign hides that behind an "Add a reminder" affordance -- ported here
    as a native <details> disclosure (no JS at all, so invariant 7's on*
    concerns don't even arise) rather than an always-visible form."""
    await seed_concert(client.db)
    login(client)

    body = client.get("/concerts/np").text
    rules = body.split('id="rules"', 1)[1].split("</div>\n</article>", 1)[0]
    assert "<details" in rules
    assert "Add a reminder" in rules
    # The add-rule form must be NESTED inside the reveal, not sitting bare
    # alongside it.
    details_at = rules.index("<details")
    input_at = rules.index('name="days_before"')
    assert details_at < input_at
    # Same route/field names/htmx wiring as before -- presentation only.
    assert 'action="/concerts/np/rules"' in rules
    assert 'hx-post="/concerts/np/rules"' in rules
    assert 'name="anchor"' in rules


async def test_reminders_note_names_the_default_preset(client):
    """Demo: "From your default preset — <name>"."""
    await seed_concert(client.db)
    login(client)
    client.post("/presets", data={"name": "Standard"})
    async with client.db() as s:
        from app.db.models import ReminderPreset

        preset = (await s.execute(select(ReminderPreset))).scalar_one()
        preset.is_default = True
        await s.commit()

    body = client.get("/concerts/np").text
    rules = body.split('id="rules"', 1)[1].split("</div>\n</article>", 1)[0]
    assert "From your default preset" in rules
    assert "Standard" in rules


async def test_round_label_renders_in_the_viewers_language(client):
    """`Round.label_en`/`label_zh` are true locale variants now (Task 3), not
    a gloss shown to everyone alongside the Japanese label -- the negative
    assertions are the point: they pin that only ONE label reaches each
    viewer, and that zh never falls back through en to the original
    (invariant: no cross-locale chaining, see i18n.loc_field)."""
    async with client.db() as s:
        await ensure_user(s, USER, "reiji")
        concert = Concert(title="T", event_id="rl1", created_by=USER)
        s.add(concert)
        await s.flush()
        s.add(ConcertDay(
            concert_id=concert.id, label="Day 1",
            starts_at_utc=datetime(2026, 8, 1, 9, tzinfo=UTC),
        ))
        s.add(Round(
            concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND,
            label="1次先行抽選", label_en="1st-round lottery", label_zh="第一轮先行",
            closes_at_utc=datetime(2026, 7, 1, 9, tzinfo=UTC),
        ))
        await s.commit()
    login(client)

    client.cookies.set("lang", "en")
    en = client.get("/concerts/rl1")
    assert "1st-round lottery" in en.text
    assert "1次先行抽選" not in en.text, "the Japanese label must not leak to an EN viewer"

    client.cookies.set("lang", "zh")
    zh = client.get("/concerts/rl1")
    assert "第一轮先行" in zh.text
    assert "1st-round lottery" not in zh.text, "no cross-locale chaining"

    client.cookies.set("lang", "ja")
    ja = client.get("/concerts/rl1")
    assert "1次先行抽選" in ja.text
    assert "1st-round lottery" not in ja.text


async def test_leg_label_renders_in_the_viewers_language(client):
    """`ConcertDay.label_en`/`label_zh` are true locale variants now (Task 4),
    same shape as test_round_label_renders_in_the_viewers_language above --
    the negative assertions are the point: they pin that only ONE label
    reaches each viewer, and that zh never falls back through en to the
    original (invariant: no cross-locale chaining, see i18n.loc_field)."""
    async with client.db() as s:
        await ensure_user(s, USER, "reiji")
        concert = Concert(title="T", event_id="ll1", created_by=USER)
        s.add(concert)
        await s.flush()
        s.add(ConcertDay(
            concert_id=concert.id, label="2日目 夜公演",
            label_en="Day 2 evening", label_zh="第二天 夜场",
            starts_at_utc=datetime(2026, 8, 1, 9, tzinfo=UTC),
        ))
        await s.commit()
    login(client)

    client.cookies.set("lang", "en")
    en = client.get("/concerts/ll1")
    assert "Day 2 evening" in en.text
    assert "2日目 夜公演" not in en.text, "the Japanese label must not leak to an EN viewer"

    client.cookies.set("lang", "zh")
    zh = client.get("/concerts/ll1")
    assert "第二天 夜场" in zh.text
    assert "Day 2 evening" not in zh.text, "no cross-locale chaining"

    client.cookies.set("lang", "ja")
    ja = client.get("/concerts/ll1")
    assert "2日目 夜公演" in ja.text
    assert "Day 2 evening" not in ja.text


# ── the per-leg fold ─────────────────────────────────────────────────────
#
# A mid-campaign leg is mostly settled history. One <details class="moreround">
# per leg keeps the rounds that still bear on this reader in front of them and
# puts the rest one click away -- per leg, so expanding one story never expands
# the others. The rule itself is pinned in tests/test_concert_rows.py; these
# pin what the page does with it.


def fold_of(section: str) -> str:
    """One leg section's fold, from the <details> to the end of the section."""
    assert 'class="moreround"' in section, "expected a fold in this leg section"
    return section.split('class="moreround"', 1)[1]


async def settled_leg(db, cid):
    """A leg part-way through a campaign: one lost round, one that closed
    without the reader, and one still open."""
    d1 = await add_day(db, cid, "Day 1", days_ahead=60)
    lost = await add_round(
        db, cid, "Early lottery", applies_to=[d1],
        opens=datetime.now(UTC) - timedelta(days=30),
        closes=datetime.now(UTC) - timedelta(days=20),
        results=datetime.now(UTC) - timedelta(days=15),
    )
    await add_round(
        db, cid, "Missed sale", applies_to=[d1],
        opens=datetime.now(UTC) - timedelta(days=10),
        closes=datetime.now(UTC) - timedelta(days=2),
    )
    await add_round(
        db, cid, "Open round", applies_to=[d1],
        opens=datetime.now(UTC) - timedelta(days=1),
        closes=datetime.now(UTC) + timedelta(days=7),
    )
    await set_outcome(db, lost, LotteryOutcome.APPLIED)
    await set_outcome(db, lost, LotteryOutcome.LOST)
    return d1


async def test_settled_rounds_fold_behind_a_per_leg_summary(client):
    cid = await seed_concert(client.db)
    await settled_leg(client.db, cid)
    login(client)

    section = leg_sections(client.get("/concerts/np").text)["Day 1"]
    assert 'class="moreround"' in section
    # The summary counts the whole fold; the chips explain the part they can.
    assert "+2 more rounds" in section
    assert "1 lost" in section
    # What still bears on the reader stays above the fold.
    assert section.index("Open round") < section.index('class="moreround"')


async def test_a_folded_rounds_capture_form_stays_in_the_dom(client):
    """The fold is presentation, never filtering. A round that opened and
    closed with nothing recorded can still be answered -- the form is behind
    the disclosure, not removed from the page."""
    cid = await seed_concert(client.db)
    await settled_leg(client.db, cid)
    login(client)

    fold = fold_of(leg_sections(client.get("/concerts/np").text)["Day 1"])
    block = round_block(fold, "Missed sale")
    assert "I have applied" in block
    assert 'name="outcome"' in block


async def test_no_fold_when_every_round_still_bears_on_you(client):
    cid = await seed_concert(client.db)
    d1 = await add_day(client.db, cid, "Day 1", days_ahead=60)
    await add_round(
        client.db, cid, "Open round", applies_to=[d1],
        opens=datetime.now(UTC) - timedelta(days=1),
        closes=datetime.now(UTC) + timedelta(days=7),
    )
    login(client)

    assert "moreround" not in client.get("/concerts/np").text


async def test_a_secured_leg_shows_its_receipt_outside_the_fold(client):
    """Owner decision 2: the round that got you in never collapses to a
    summary line -- you can see which one it was without expanding."""
    cid = await seed_concert(client.db)
    d1 = await add_day(client.db, cid, "Day 1", days_ahead=60)
    won = await add_round(
        client.db, cid, "FC lottery", applies_to=[d1],
        opens=datetime.now(UTC) - timedelta(days=30),
        closes=datetime.now(UTC) - timedelta(days=20),
        payment=datetime.now(UTC) + timedelta(days=10),
    )
    await add_round(
        client.db, cid, "General sale", applies_to=[d1],
        opens=datetime.now(UTC) - timedelta(days=1),
        closes=datetime.now(UTC) + timedelta(days=7),
    )
    await set_outcome(client.db, won, LotteryOutcome.APPLIED)
    await set_outcome(client.db, won, LotteryOutcome.WON)
    login(client)

    section = leg_sections(client.get("/concerts/np").text)["Day 1"]
    assert section.index("FC lottery") < section.index('class="moreround"')
    assert "General sale" in fold_of(section)
    assert "1 covered" in section


async def test_answering_a_folded_round_swaps_that_fold_back_open(client):
    """The swap is an outerHTML replacement, so every <details> returns closed
    -- which would take the round the reader just answered off the screen at
    the moment they acted on it. Same server-side reopen Home's folds get."""
    cid = await seed_concert(client.db)
    await settled_leg(client.db, cid)
    login(client)

    body = client.get("/concerts/np").text
    assert '<details class="moreround">' in body  # closed on a plain GET

    missed = await _round_id(client.db, cid, "Missed sale")
    r = client.post(
        f"/rounds/{missed}/outcome", data={"outcome": "not_applied"},
        headers={"HX-Request": "true", "HX-Current-URL": "http://x/concerts/np"},
    )
    # Still folded (nothing wants the reader on a round they declined), but the
    # fold it sits in comes back open.
    assert '<details class="moreround" open>' in r.text
    # The strip rides along after the rounds region in this fragment, so cut
    # there rather than at the marker `legs_of` looks for on a full page.
    rounds_fragment = r.text.split('id="concert-standing"', 1)[0]
    assert "Missed sale" in fold_of(leg_sections(rounds_fragment)["Day 1"])


async def _round_id(db, concert_id, label):
    async with db() as s:
        return (await s.execute(
            select(Round).where(Round.concert_id == concert_id, Round.label == label)
        )).scalar_one().id


def test_every_fold_kind_has_a_chip_and_an_unknown_one_raises():
    """`_FOLD_KINDS` and `fold_count_label` are two halves of one list, in two
    files. A silent default would render a newly added kind as somebody else's
    chip and nothing would fail, so the seam raises -- the same call
    `split_slots` makes for an unknown sentence slot."""
    for kind in _FOLD_KINDS:
        assert fold_count_label(kind, 2).startswith("2 ")
    with pytest.raises(ValueError):
        fold_count_label("cancelled", 1)
