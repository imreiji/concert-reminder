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
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
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
from app.db.service import attach_tag, ensure_user
from app.db.session import get_session
from app.domain.types import RoundKind, TagKind
from app.web import auth
from app.web.app import create_app

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


async def add_day(db, concert_id, label, *, days_ahead=60, venue=None, cancelled=False):
    async with db() as s:
        d = ConcertDay(
            concert_id=concert_id, label=label, venue=venue, cancelled=cancelled,
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
    # The label says where the members came from, which quietly explains the
    # expansion rule on the one page an editor would wonder about it.
    assert "8 members" in panel
    assert "Aqours group tag" in panel


async def test_no_performers_panel_when_the_concert_has_neither_group_nor_artists(client):
    cid = await seed_concert(client.db)
    await add_tag(client.db, cid, "Yokohama Arena", TagKind.VENUE)
    login(client)

    assert 'class="performers"' not in client.get("/concerts/np").text


# ── header: links and actions ────────────────────────────────────────────


async def test_the_source_link_names_ramen_events(client):
    """"source" said nothing; the source is ramen.events."""
    await seed_concert(client.db, source_url="https://ramen.events/e/1")
    login(client)

    body = client.get("/concerts/np").text
    links = body.split('class="links"', 1)[1].split("</p>", 1)[0]
    assert "ramen.events" in links
    assert ">source<" not in links


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
    cid = await seed_concert(client.db, venue="Header Venue")
    await add_day(client.db, cid, "Osaka", days_ahead=30, venue="Osaka-jo Hall")
    await add_day(client.db, cid, "Tokyo", days_ahead=31, venue="Tokyo Dome")
    login(client)

    body = client.get("/concerts/np").text
    head = body.split('class="chead"', 1)[1].split("</header>", 1)[0]
    assert "Header Venue" not in head
    assert "Osaka-jo Hall" not in head
