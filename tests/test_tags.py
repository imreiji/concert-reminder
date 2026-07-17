"""Tag tests: creation gating, THE group-expansion semantics, filtering, sorting.

The expansion rules were agreed explicitly and are the contract Phase 10's
notifications will rely on, so they get exhaustive coverage:
  1. attaching a group tag materializes its members onto the concert
  2. editors can then remove individual members (not performing)
  3. later edits / membership changes never re-add removed members
  4. detach + re-attach of the group DOES re-expand (it's "newly added" again)
"""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base, Concert, ConcertTag, Tag, TagMember, User
from app.db.service import attach_tag, detach_tag
from app.db.session import get_session
from app.domain.types import TagKind
from app.web import auth
from app.web.app import create_app

EDITOR_ID, VIEWER_ID = 42, 777


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


# ── Service-level: expansion semantics ───────────────────────────────────


async def seed_group(s) -> tuple[Tag, Tag, Tag, Concert]:
    s.add(User(discord_id=EDITOR_ID, username="reiji"))
    await s.flush()
    group = Tag(name="Hasunosora", kind=TagKind.GROUP, created_by=EDITOR_ID)
    a1 = Tag(name="Kozue Otomune", kind=TagKind.ARTIST, created_by=EDITOR_ID)
    a2 = Tag(name="Kaho Hinoshita", kind=TagKind.ARTIST, created_by=EDITOR_ID)
    concert = Concert(title="Hasunosora 6th", event_id="hasunosora-6th", created_by=EDITOR_ID)
    s.add_all([group, a1, a2, concert])
    await s.flush()
    s.add_all([
        TagMember(group_tag_id=group.id, member_tag_id=a1.id),
        TagMember(group_tag_id=group.id, member_tag_id=a2.id),
    ])
    await s.flush()
    return group, a1, a2, concert


async def tag_ids_on(s, concert_id) -> set[int]:
    res = await s.execute(select(ConcertTag.tag_id).where(ConcertTag.concert_id == concert_id))
    return set(res.scalars())


async def test_attaching_group_materializes_members(db):
    async with db() as s:
        group, a1, a2, concert = await seed_group(s)
        added = await attach_tag(s, concert.id, group)
        assert {t.id for t in added} == {group.id, a1.id, a2.id}
        assert await tag_ids_on(s, concert.id) == {group.id, a1.id, a2.id}


async def test_removed_member_stays_removed(db):
    """Rule 3: pruning a non-performer is permanent across ordinary edits."""
    async with db() as s:
        group, a1, a2, concert = await seed_group(s)
        await attach_tag(s, concert.id, group)
        await detach_tag(s, concert.id, a1.id)          # a1 isn't performing
        await attach_tag(s, concert.id, group)          # group ALREADY attached: no-op
        assert await tag_ids_on(s, concert.id) == {group.id, a2.id}


async def test_reattaching_group_reexpands(db):
    """Rule 4: detach + re-attach counts as 'newly added' -> members return."""
    async with db() as s:
        group, a1, a2, concert = await seed_group(s)
        await attach_tag(s, concert.id, group)
        await detach_tag(s, concert.id, a1.id)
        await detach_tag(s, concert.id, group.id)
        await attach_tag(s, concert.id, group)
        assert await tag_ids_on(s, concert.id) == {group.id, a1.id, a2.id}


async def test_membership_changes_do_not_rewrite_existing_concerts(db):
    async with db() as s:
        group, a1, a2, concert = await seed_group(s)
        await attach_tag(s, concert.id, group)
        newbie = Tag(name="Guest Artist", kind=TagKind.ARTIST, created_by=EDITOR_ID)
        s.add(newbie)
        await s.flush()
        s.add(TagMember(group_tag_id=group.id, member_tag_id=newbie.id))
        await s.flush()
        assert newbie.id not in await tag_ids_on(s, concert.id)  # untouched


# ── HTTP-level: gating, filtering, sorting ───────────────────────────────


@pytest.fixture()
def client(db, monkeypatch):
    monkeypatch.setattr(settings, "editor_whitelist", str(EDITOR_ID))
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


def login_as(client, discord_id: int, name: str):
    async def fake_identity(token):
        return {"id": str(discord_id), "username": name, "global_name": name, "avatar": None}

    client.monkeypatch.setattr(auth, "fetch_identity", fake_identity)
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    client.get(f"/auth/callback?code=x&state={state}")


def test_tag_creation_is_editor_only(client):
    login_as(client, VIEWER_ID, "viewer")
    assert client.post("/tags", data={"name": "X", "kind": "artist"}).status_code == 403
    login_as(client, EDITOR_ID, "reiji")
    assert client.post("/tags", data={"name": "X", "kind": "artist"}).status_code == 303


def test_duplicate_tag_names_rejected_case_insensitively(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Hasunosora", "kind": "franchise"})
    assert client.post("/tags", data={"name": "hasunosora", "kind": "artist"}).status_code == 409


def test_groups_cannot_contain_groups(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "G1", "kind": "group"})
    client.post("/tags", data={"name": "G2", "kind": "group"})
    r = client.post("/tags/1/members", data={"member_tag_id": 2})
    assert r.status_code == 422


def test_index_filters_by_tag(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Hasunosora", "kind": "franchise"})
    client.post("/concerts", data={
        "title": "Hasu Live", "event_id": "hasu-live", "franchise_tags": [1],
    })
    client.post("/concerts", data={"title": "Gakumas Live", "event_id": "gakumas-live"})

    everything = client.get("/").text
    assert "Hasu Live" in everything and "Gakumas Live" in everything

    filtered = client.get("/?tag=1").text
    # Client-side filtering: every tile is always in the DOM (tagged with its
    # own tag ids) so JS can toggle visibility instantly with no round trip --
    # the non-matching tile is still present, just server-marked hidden for
    # the initial (no-JS) render.
    assert "Hasu Live" in filtered and "Gakumas Live" in filtered
    hasu_tile = filtered[filtered.rindex('<a class="tile"', 0, filtered.index("Hasu Live")):]
    gakumas_tile = filtered[filtered.rindex('<a class="tile"', 0, filtered.index("Gakumas Live")):]
    assert 'style="display:none"' not in hasu_tile.split("</a>", 1)[0]
    assert 'style="display:none"' in gakumas_tile.split("</a>", 1)[0]


def test_index_sorts_by_earliest_event_day(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={
        "title": "AAA Later Show", "event_id": "aaa",
        "day_label": ["Day 1"], "day_starts_at": ["2099-12-01T18:00"],
        "day_city": [""], "day_venue": [""], "day_venue_address": [""], "day_doors_at": [""],
    })
    client.post("/concerts", data={
        "title": "BBB Sooner Show", "event_id": "bbb",
        "day_label": ["Day 1"], "day_starts_at": ["2099-06-01T18:00"],
        "day_city": [""], "day_venue": [""], "day_venue_address": [""], "day_doors_at": [""],
    })

    by_event = client.get("/?sort=event").text
    assert by_event.index("BBB Sooner Show") < by_event.index("AAA Later Show")
    by_added = client.get("/?sort=added").text
    assert by_added.index("BBB Sooner Show") < by_added.index("AAA Later Show")  # newest first


async def test_edit_page_can_attach_a_new_tag_to_an_existing_concert(client):
    """Tags are no longer attached post-creation via a dedicated endpoint --
    the rich edit page's picker (submitted atomically, same as creation) is
    the only way now. Checking a group tag there auto-populates its members
    client-side (untestable without JS), so this submits the member id
    explicitly, same as the creation form always has."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Hasunosora", "kind": "group"})
    client.post("/tags", data={"name": "Kozue", "kind": "artist"})
    client.post("/tags/1/members", data={"member_tag_id": 2})
    client.post("/concerts", data={"title": "6th Live", "event_id": "6th-live"})

    r = client.post(
        "/concerts/6th-live/edit",
        data={
            "event_id": "6th-live", "title": "6th Live",
            "group_tags": [1], "artist_tags": [2],
        },
    )
    assert r.status_code == 303
    async with client.db() as s:
        assert await tag_ids_on(s, 1) == {1, 2}


async def test_edit_resubmission_does_not_reexpand_pruned_group_member(client):
    """Rule 3 through the edit page specifically: a group tag that's already
    attached and stays checked across an edit must NOT be re-expanded, or a
    previously-pruned non-performer would silently come back just because
    the editor changed something unrelated (like the title)."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Hasunosora", "kind": "group"})  # 1
    client.post("/tags", data={"name": "Kozue", "kind": "artist"})      # 2
    client.post("/tags", data={"name": "Kaho", "kind": "artist"})       # 3
    client.post("/tags/1/members", data={"member_tag_id": 2})
    client.post("/tags/1/members", data={"member_tag_id": 3})

    # created with the group but only Kozue checked -- Kaho pruned
    client.post(
        "/concerts",
        data={"title": "6th Live", "event_id": "6th-live", "group_tags": [1], "artist_tags": [2]},
    )
    async with client.db() as s:
        assert await tag_ids_on(s, 1) == {1, 2}

    # unrelated edit: rename the title, resubmit the SAME tag selection
    r = client.post(
        "/concerts/6th-live/edit",
        data={
            "event_id": "6th-live", "title": "6th Live (renamed)",
            "group_tags": [1], "artist_tags": [2],
        },
    )
    assert r.status_code == 303
    async with client.db() as s:
        assert await tag_ids_on(s, 1) == {1, 2}  # Kaho (3) still pruned


# ── Phase 11: tag-driven creation form ───────────────────────────────────


async def test_creation_form_respects_explicit_artist_selection(client):
    """expand=False path: editor unchecked an artist at creation -> not attached,
    even though the group tag is. The checkbox list is authoritative."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "LoveLive", "kind": "franchise"})
    client.post("/tags", data={"name": "Hasunosora", "kind": "group", "parent_id": 1})
    client.post("/tags", data={"name": "Kozue", "kind": "artist"})
    client.post("/tags", data={"name": "Kaho", "kind": "artist"})
    client.post("/tags/2/members", data={"member_tag_id": 3})
    client.post("/tags/2/members", data={"member_tag_id": 4})
    client.post("/tags", data={"name": "Yokohama Arena", "kind": "venue"})

    # create with group but ONLY Kozue checked (Kaho not performing)
    r = client.post("/concerts", data={
        "title": "6th Live", "event_id": "6th-live", "franchise_tags": [1], "group_tags": [2],
        "artist_tags": [3], "venue_tags": [5],
    })
    assert r.status_code == 303

    async with client.db() as s:
        ids = await tag_ids_on(s, 1)
        assert ids == {1, 2, 3, 5}  # franchise+group+Kozue+venue; NO Kaho
        c = (await s.execute(select(Concert))).scalar_one()
        assert c.franchise == "LoveLive" and c.venue == "Yokohama Arena"


def test_creation_rejects_wrong_kind_tags(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Kozue", "kind": "artist"})
    r = client.post("/concerts", data={"title": "X", "franchise_tags": [1]})  # artist as franchise
    assert r.status_code == 422


def test_group_parent_must_be_franchise(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Kozue", "kind": "artist"})
    r = client.post("/tags", data={"name": "G", "kind": "group", "parent_id": 1})
    assert r.status_code == 422


async def test_creation_supports_multiple_groups_and_franchises(client):
    """Collab events: two franchises, two groups, union of artists minus prunes."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "LoveLive", "kind": "franchise"})     # 1
    client.post("/tags", data={"name": "Idolmaster", "kind": "franchise"})   # 2
    client.post("/tags", data={"name": "Hasunosora", "kind": "group", "parent_id": 1})  # 3
    client.post("/tags", data={"name": "Gakumas", "kind": "group", "parent_id": 2})     # 4
    client.post("/tags", data={"name": "Kozue", "kind": "artist"})           # 5
    client.post("/tags", data={"name": "Saki", "kind": "artist"})            # 6
    client.post("/tags/3/members", data={"member_tag_id": 5})
    client.post("/tags/4/members", data={"member_tag_id": 6})

    r = client.post("/concerts", data={
        "title": "Godo Live", "event_id": "godo-live",
        "franchise_tags": [1, 2], "group_tags": [3, 4], "artist_tags": [5, 6],
    })
    assert r.status_code == 303
    async with client.db() as s:
        assert await tag_ids_on(s, 1) == {1, 2, 3, 4, 5, 6}
        c = (await s.execute(select(Concert))).scalar_one()
        assert c.franchise == "LoveLive, Idolmaster"

async def test_multiple_venues_attach_and_join(client):
    """Tour legs: two venues on one event; display string joins, tiles say Multiple."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Yokohama Arena", "kind": "venue"})   # 1
    client.post("/tags", data={"name": "K-Arena", "kind": "venue"})          # 2
    r = client.post("/concerts", data={"title": "Tour", "event_id": "tour", "venue_tags": [1, 2]})
    assert r.status_code == 303
    async with client.db() as s:
        assert await tag_ids_on(s, 1) == {1, 2}
        c = (await s.execute(select(Concert))).scalar_one()
        assert c.venue == "Yokohama Arena, K-Arena"
    assert "Multiple" in client.get("/").text  # tile shows Multiple, not the join


