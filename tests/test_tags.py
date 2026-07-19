"""Tag tests: creation gating, THE group-expansion semantics, filtering, sorting.

The expansion rules were agreed explicitly and are the contract Phase 10's
notifications will rely on, so they get exhaustive coverage:
  1. attaching a group tag materializes its members onto the concert
  2. editors can then remove individual members (not performing)
  3. later edits / membership changes never re-add removed members
  4. detach + re-attach of the group DOES re-expand (it's "newly added" again)
"""

import re

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base, Concert, ConcertDay, ConcertTag, Round, Tag, TagMember, User
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

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

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


async def test_rename_tag_round_trips(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Hasunosora", "kind": "franchise"})
    r = client.post("/tags/1/edit", data={"name": "Hasunosora Idols"})
    assert r.status_code == 303

    async with client.db() as s:
        tag = await s.get(Tag, 1)
        assert tag.name == "Hasunosora Idols"


def test_rename_tag_rejects_case_insensitive_duplicate(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Hasunosora", "kind": "franchise"})
    client.post("/tags", data={"name": "Gakumas", "kind": "franchise"})
    r = client.post("/tags/2/edit", data={"name": "hasunosora"})
    assert r.status_code == 409


def test_rename_tag_to_its_own_current_name_is_a_noop(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Hasunosora", "kind": "franchise"})
    r = client.post("/tags/1/edit", data={"name": "Hasunosora"})
    assert r.status_code == 303


async def test_edit_tag_without_name_field_leaves_name_unchanged(client):
    """Backward compatibility: the venue-only edit form that existed before
    this feature never sends `name` at all."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "K Arena", "kind": "venue"})
    r = client.post("/tags/1/edit", data={"region": "Kanto"})
    assert r.status_code == 303

    async with client.db() as s:
        tag = await s.get(Tag, 1)
        assert tag.name == "K Arena"
        assert tag.region == "Kanto"


# ── Tags page rendering: search, hierarchy, unified dialogs ──────────────


def test_tags_page_renders_hierarchy_and_search_box(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Hasunosora", "kind": "franchise"})
    client.post("/tags", data={"name": "Liella", "kind": "group", "parent_id": 1})
    client.post("/tags", data={"name": "Kaho", "kind": "artist"})
    client.post("/tags/2/members", data={"member_tag_id": 3})
    client.post("/tags", data={"name": "K Arena", "kind": "venue"})

    r = client.get("/tags")
    assert r.status_code == 200
    assert 'placeholder="Search tags…"' in r.text
    assert "Hasunosora" in r.text and "Liella" in r.text
    assert "Kaho" in r.text and "K Arena" in r.text
    # every tag gets its own dialog
    assert 'id="tag-dialog-1"' in r.text  # Hasunosora
    assert 'id="tag-dialog-2"' in r.text  # Liella
    assert 'dialog.picker' not in r.text  # sanity: that's a CSS selector, not markup
    assert 'class="picker"' in r.text
    # hierarchy container must have sub-box class for group/member styling
    assert 'class="tags-page sub-box"' in r.text


def test_tags_page_solo_artist_bucket(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Solo Artist", "kind": "artist"})
    r = client.get("/tags")
    assert r.status_code == 200
    assert "Solo artists" in r.text
    assert "Solo Artist" in r.text


def test_tags_page_viewer_sees_no_edit_dialogs(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Hasunosora", "kind": "franchise"})
    login_as(client, VIEWER_ID, "viewer")
    r = client.get("/tags")
    assert r.status_code == 200
    assert 'id="tag-dialog-1"' not in r.text


def test_tags_page_renders_one_dialog_even_for_artist_in_multiple_groups(client):
    """Regression guard: an artist that belongs to two different groups
    must still get exactly one <dialog id="tag-dialog-{id}"> in the
    rendered page, not one per group it appears under."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Group A", "kind": "group"})
    client.post("/tags", data={"name": "Group B", "kind": "group"})
    client.post("/tags", data={"name": "Shared Member", "kind": "artist"})
    client.post("/tags/1/members", data={"member_tag_id": 3})
    client.post("/tags/2/members", data={"member_tag_id": 3})

    r = client.get("/tags")
    assert r.status_code == 200
    assert r.text.count('id="tag-dialog-3"') == 1


def test_new_tag_form_includes_parent_visibility_script(client):
    """The kind/parent select-hiding is JS-only, client-side behavior --
    not server-testable via HTTP. This just confirms the toggle script and
    its target elements are actually present in the rendered page."""
    login_as(client, EDITOR_ID, "reiji")
    r = client.get("/tags")
    assert "new-tag-kind" in r.text and "new-tag-parent" in r.text
    assert "syncParentVisibility" in r.text


# ── Retroactive-apply confirmation flow ──────────────────────────────────


def create_active_concert_with_group(client, event_id, group_tag_id):
    """A concert with one live future leg, tagged with the given group --
    the shape active_concerts_missing_member requires to count a concert
    as an eligible retroactive-apply target."""
    return client.post(
        "/concerts",
        data={
            "title": event_id, "event_id": event_id, "group_tags": [group_tag_id],
            "day_label": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_city": [""], "day_venue": [""], "day_venue_address": [""], "day_doors_at": [""],
            "day_cancelled": ["false"],
        },
    )


def test_add_member_redirects_straight_to_tags_when_nothing_eligible(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Liella", "kind": "group"})
    client.post("/tags", data={"name": "Sumire", "kind": "artist"})
    r = client.post("/tags/1/members", data={"member_tag_id": 2})
    assert r.status_code == 303
    assert r.headers["location"] == "/tags"


def test_add_member_redirects_to_confirmation_when_something_eligible(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Liella", "kind": "group"})
    client.post("/tags", data={"name": "Sumire", "kind": "artist"})
    create_active_concert_with_group(client, "liella-live", 1)
    r = client.post("/tags/1/members", data={"member_tag_id": 2})
    assert r.status_code == 303
    assert r.headers["location"] == "/tags/1/members/2/retroactive-apply"


def test_confirmation_page_lists_eligible_concert_titles(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Liella", "kind": "group"})
    client.post("/tags", data={"name": "Sumire", "kind": "artist"})
    create_active_concert_with_group(client, "liella-live", 1)
    client.post("/tags/1/members", data={"member_tag_id": 2})
    r = client.get("/tags/1/members/2/retroactive-apply")
    assert r.status_code == 200
    assert "liella-live" in r.text
    assert "Sumire" in r.text


def test_confirmation_page_handles_nothing_eligible_gracefully(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Liella", "kind": "group"})
    client.post("/tags", data={"name": "Sumire", "kind": "artist"})
    # Establish the membership first: the route only serves pairs that really
    # are (group, member). Nothing is eligible, so add_member goes back to
    # /tags -- but the page still has to render if revisited directly.
    client.post("/tags/1/members", data={"member_tag_id": 2})
    r = client.get("/tags/1/members/2/retroactive-apply")
    assert r.status_code == 200
    assert "Nothing to apply" in r.text


async def test_apply_to_all_attaches_tag_and_notifies_subscriber(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Liella", "kind": "group"})
    client.post("/tags", data={"name": "Sumire", "kind": "artist"})
    create_active_concert_with_group(client, "liella-live", 1)

    login_as(client, VIEWER_ID, "viewer")
    client.post("/subscriptions", data={"tag_id": 2, "notify": "true"})
    login_as(client, EDITOR_ID, "reiji")

    client.post("/tags/1/members", data={"member_tag_id": 2})
    r = client.post("/tags/1/members/2/retroactive-apply")
    assert r.status_code == 303
    assert r.headers["location"] == "/tags"

    async with client.db() as s:
        from app.db.models import Notification

        concert_tags = (await s.execute(select(ConcertTag))).scalars().all()
        assert any(ct.tag_id == 2 for ct in concert_tags)  # Sumire attached
        notes = (await s.execute(select(Notification))).scalars().all()
        assert any(n.user_id == VIEWER_ID for n in notes)


# ── Retroactive-apply relationship validation ────────────────────────────
#
# The route's own UI promises "add this group member to its concerts". Without
# these checks an editor could POST any (group_id, member_id) pair -- e.g. a
# venue tag against every active concert carrying a franchise tag -- and
# bulk-attach it in one request, fanning out a Notification per subscriber.


def test_mismatched_pair_404s_on_get_and_post_and_attaches_nothing(client):
    """Group 1 and artist 3 are real tags, but 3 is not a member of 1."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Liella", "kind": "group"})
    client.post("/tags", data={"name": "Sumire", "kind": "artist"})
    client.post("/tags", data={"name": "Unrelated", "kind": "artist"})
    create_active_concert_with_group(client, "liella-live", 1)

    assert client.get("/tags/1/members/3/retroactive-apply").status_code == 404
    assert client.post("/tags/1/members/3/retroactive-apply").status_code == 404

    r = client.get("/concerts/liella-live")
    assert "Unrelated" not in r.text


def test_missing_group_404s(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Sumire", "kind": "artist"})
    assert client.get("/tags/999/members/1/retroactive-apply").status_code == 404
    assert client.post("/tags/999/members/1/retroactive-apply").status_code == 404


async def test_non_group_group_id_404s(client):
    """A TagMember row alone isn't enough -- the 'group' must be a GROUP tag.
    Inserted directly because add_member itself refuses a non-group parent."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Hasunosora", "kind": "franchise"})
    client.post("/tags", data={"name": "Sumire", "kind": "artist"})
    async with client.db() as s:
        s.add(TagMember(group_tag_id=1, member_tag_id=2))
        await s.commit()

    assert client.get("/tags/1/members/2/retroactive-apply").status_code == 404
    assert client.post("/tags/1/members/2/retroactive-apply").status_code == 404


async def test_legitimate_add_member_then_apply_still_works(client):
    """The real flow: add_member creates the TagMember row and redirects here,
    so the strict check must pass end to end and still attach."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Liella", "kind": "group"})
    client.post("/tags", data={"name": "Sumire", "kind": "artist"})
    create_active_concert_with_group(client, "liella-live", 1)

    redirect = client.post("/tags/1/members", data={"member_tag_id": 2})
    target = redirect.headers["location"]
    assert client.get(target).status_code == 200
    assert client.post(target).status_code == 303

    async with client.db() as s:
        concert_tags = (await s.execute(select(ConcertTag))).scalars().all()
        assert any(ct.tag_id == 2 for ct in concert_tags)


def test_confirmation_page_requires_editor(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Liella", "kind": "group"})
    client.post("/tags", data={"name": "Sumire", "kind": "artist"})

    login_as(client, VIEWER_ID, "viewer")
    assert client.get("/tags/1/members/2/retroactive-apply").status_code == 403
    assert client.post("/tags/1/members/2/retroactive-apply").status_code == 403


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


def test_index_search_filters_by_title(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "Hasu Live", "event_id": "hasu-live"})
    client.post("/concerts", data={"title": "Gakumas Live", "event_id": "gakumas-live"})

    filtered = client.get("/?q=hasu").text
    assert "Hasu Live" in filtered and "Gakumas Live" in filtered
    hasu_tile = filtered[filtered.rindex('<a class="tile"', 0, filtered.index("Hasu Live")):]
    gakumas_tile = filtered[filtered.rindex('<a class="tile"', 0, filtered.index("Gakumas Live")):]
    assert 'style="display:none"' not in hasu_tile.split("</a>", 1)[0]
    assert 'style="display:none"' in gakumas_tile.split("</a>", 1)[0]


def test_index_search_is_case_insensitive_and_matches_title_en(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={"title": "はすのそら 5th", "event_id": "c", "title_en": "Hasunosora 5th"},
    )
    r = client.get("/?q=HASUNOSORA").text
    tile = r[r.index('<a class="tile"'):]
    assert 'style="display:none"' not in tile.split("</a>", 1)[0]


def test_index_search_combines_with_tag_filter_as_and(client):
    """Search AND tag filter together -- a concert must satisfy both to show,
    matching the "combined AND" requirement (not either/or)."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Hasunosora", "kind": "franchise"})
    client.post("/concerts", data={
        "title": "Hasu Live", "event_id": "hasu-live", "franchise_tags": [1],
    })
    client.post("/concerts", data={"title": "Hasu Anniversary", "event_id": "hasu-anni"})

    # matches the search text but NOT the tag -> hidden
    r = client.get("/?tag=1&q=anniversary").text
    anni_tile = r[r.rindex('<a class="tile"', 0, r.index("Hasu Anniversary")):]
    assert 'style="display:none"' in anni_tile.split("</a>", 1)[0]

    # matches both -> shown
    r = client.get("/?tag=1&q=live").text
    live_tile = r[r.rindex('<a class="tile"', 0, r.index("Hasu Live")):]
    assert 'style="display:none"' not in live_tile.split("</a>", 1)[0]


def test_index_search_box_prefills_from_query_param(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.get("/?q=hasunosora")
    assert 'id="concert-search" value="hasunosora"' in r.text


def test_index_search_matches_artist_tag_name(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Kozue Otomune", "kind": "artist"})
    client.post("/concerts", data={
        "title": "Some Show", "event_id": "some-show", "artist_tags": [1],
    })
    client.post("/concerts", data={"title": "Other Show", "event_id": "other-show"})

    filtered = client.get("/?q=kozue").text
    some_tile = filtered[filtered.rindex('<a class="tile"', 0, filtered.index("Some Show")):]
    other_tile = filtered[filtered.rindex('<a class="tile"', 0, filtered.index("Other Show")):]
    assert 'style="display:none"' not in some_tile.split("</a>", 1)[0]
    assert 'style="display:none"' in other_tile.split("</a>", 1)[0]


def test_index_search_matches_group_tag_name(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Liella", "kind": "group"})
    client.post("/concerts", data={
        "title": "Some Show", "event_id": "some-show", "group_tags": [1],
    })
    filtered = client.get("/?q=liella").text
    tile = filtered[filtered.index('<a class="tile"'):]
    assert 'style="display:none"' not in tile.split("</a>", 1)[0]


def test_index_search_matches_franchise_tag_name(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Gakumas", "kind": "franchise"})
    client.post("/concerts", data={
        "title": "Some Show", "event_id": "some-show", "franchise_tags": [1],
    })
    filtered = client.get("/?q=gakumas").text
    tile = filtered[filtered.index('<a class="tile"'):]
    assert 'style="display:none"' not in tile.split("</a>", 1)[0]


def test_index_search_matches_venue_tag_name(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Yokohama Arena", "kind": "venue"})
    client.post("/concerts", data={
        "title": "Some Show", "event_id": "some-show", "venue_tags": [1],
    })
    filtered = client.get("/?q=yokohama").text
    tile = filtered[filtered.index('<a class="tile"'):]
    assert 'style="display:none"' not in tile.split("</a>", 1)[0]


async def test_index_search_falls_back_to_free_text_venue_when_no_venue_tag(client):
    """Concert.venue is a legacy top-level field the current creation form
    doesn't expose (only per-day ConcertDay.venue is settable through the
    UI) -- set it directly at the DB layer, matching how other tests reach
    fields the form doesn't cover."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "Some Show", "event_id": "some-show"})
    async with client.db() as s:
        from app.db.models import Concert as ConcertModel

        concert = (await s.execute(
            select(ConcertModel).where(ConcertModel.event_id == "some-show")
        )).scalar_one()
        concert.venue = "Nippon Budokan"
        await s.commit()

    filtered = client.get("/?q=budokan").text
    tile = filtered[filtered.index('<a class="tile"'):]
    assert 'style="display:none"' not in tile.split("</a>", 1)[0]


async def test_index_search_ignores_free_text_venue_when_venue_tag_exists(client):
    """The free-text-venue fallback only applies when NO VENUE tag is
    attached -- if a VENUE tag exists, stale/mismatched free-text venue
    text must not spuriously match. The positive assertion below (search
    still finds the concert by its actual VENUE tag name) is what proves
    search is functioning at all for this concert -- without it, this
    test would pass identically whether the exclusion works correctly or
    search is silently broken."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Yokohama Arena", "kind": "venue"})
    client.post("/concerts", data={
        "title": "Some Show", "event_id": "some-show", "venue_tags": [1],
    })
    async with client.db() as s:
        from app.db.models import Concert as ConcertModel

        concert = (await s.execute(
            select(ConcertModel).where(ConcertModel.event_id == "some-show")
        )).scalar_one()
        concert.venue = "Stale Old Name"
        await s.commit()

    stale_filtered = client.get("/?q=stale").text
    stale_tile = stale_filtered[stale_filtered.index('<a class="tile"'):]
    assert 'style="display:none"' in stale_tile.split("</a>", 1)[0]

    # proves search actually works for this concert (not just silently
    # broken) -- it still finds it by the VENUE tag's real name
    tag_name_filtered = client.get("/?q=yokohama").text
    tag_name_tile = tag_name_filtered[tag_name_filtered.index('<a class="tile"'):]
    assert 'style="display:none"' not in tag_name_tile.split("</a>", 1)[0]


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


async def test_index_hides_concert_whose_only_leg_is_cancelled(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={
            "title": "All Cancelled", "event_id": "all-cancelled",
            "day_label": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_city": [""], "day_venue": [""], "day_venue_address": [""], "day_doors_at": [""],
        },
    )
    client.post("/concerts", data={"title": "Still Here", "event_id": "still-here"})
    async with client.db() as s:
        from app.db.models import ConcertDay

        day_id = (await s.execute(select(ConcertDay))).scalar_one().id
    client.post(
        "/concerts/all-cancelled/edit",
        data={
            "title": "All Cancelled", "event_id": "all-cancelled",
            "day_id": [str(day_id)], "day_label": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_city": [""], "day_venue": [""], "day_venue_address": [""], "day_doors_at": [""],
            "day_cancelled": ["true"],
        },
    )
    r = client.get("/").text
    assert "All Cancelled" not in r
    assert "Still Here" in r
    # still reachable directly by event_id even though hidden from the index
    assert client.get("/concerts/all-cancelled").status_code == 200


async def test_index_keeps_concert_with_zero_days_visible(client):
    """A concert that simply has no legs entered yet (e.g. freshly created,
    or duplicated as a template) is unaffected -- only a concert whose
    EXISTING legs are all cancelled gets hidden."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "No Dates Yet", "event_id": "no-dates-yet"})
    assert "No Dates Yet" in client.get("/").text


async def test_index_open_upcoming_bucket_shown_first(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={
            "title": "Open Round Show", "event_id": "open-show",
            "day_label": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_city": [""], "day_venue": [""], "day_venue_address": [""], "day_doors_at": [""],
            "round_label": ["R1"], "round_kind": ["lottery_round"],
            "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
            "round_results_at": [""], "round_payment_at": [""], "round_label_en": [""],
            "round_url": [""], "round_notes": [""], "round_leg": [""],
        },
    )
    client.post("/concerts", data={"title": "No Open Round", "event_id": "no-open-round"})

    r = client.get("/").text
    assert "Open &amp; upcoming" in r
    open_heading_pos = r.index("Open &amp; upcoming")
    upcoming_heading_pos = r.index(">Upcoming<")
    open_show_pos = r.index("Open Round Show")
    no_open_pos = r.index("No Open Round")
    assert open_heading_pos < open_show_pos < upcoming_heading_pos < no_open_pos


async def test_index_round_with_only_results_date_is_not_open(client):
    """A round with only results_at set (no opens/closes) never counts as
    "open" -- you can't apply to it, it's a pending action on an
    already-closed round."""
    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={
            "title": "Results Only Show", "event_id": "results-only",
            "round_label": ["R1"], "round_kind": ["lottery_round"],
            "round_opens_at": [""], "round_closes_at": [""],
            "round_results_at": ["2099-06-25T23:59"], "round_payment_at": [""],
            "round_label_en": [""], "round_url": [""], "round_notes": [""], "round_leg": [""],
        },
    )
    r = client.get("/").text
    assert "Open &amp; upcoming" not in r


async def test_index_sort_key_ignores_cancelled_leg_date(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={
            "title": "Mixed Legs Show", "event_id": "mixed-legs",
            "day_label": ["Day 1", "Day 2"],
            "day_starts_at": ["2099-06-01T18:00", "2099-09-01T18:00"],
            "day_city": ["", ""], "day_venue": ["", ""],
            "day_venue_address": ["", ""], "day_doors_at": ["", ""],
        },
    )
    client.post("/concerts", data={
        "title": "Between Show", "event_id": "between-show",
        "day_label": ["Day 1"], "day_starts_at": ["2099-07-01T18:00"],
        "day_city": [""], "day_venue": [""], "day_venue_address": [""], "day_doors_at": [""],
    })
    async with client.db() as s:
        from app.db.models import Concert as ConcertModel

        mixed = (await s.execute(
            select(ConcertModel).where(ConcertModel.event_id == "mixed-legs")
        )).scalar_one()
        days = sorted(
            (await s.execute(
                select(ConcertDay).where(ConcertDay.concert_id == mixed.id)
            )).scalars(),
            key=lambda d: d.starts_at_utc,
        )
        day1_id, day2_id = days[0].id, days[1].id  # June (will be cancelled), September

    client.post(
        "/concerts/mixed-legs/edit",
        data={
            "title": "Mixed Legs Show", "event_id": "mixed-legs",
            "day_id": [str(day1_id), str(day2_id)],
            "day_label": ["Day 1", "Day 2"],
            "day_starts_at": ["2099-06-01T18:00", "2099-09-01T18:00"],
            "day_city": ["", ""], "day_venue": ["", ""],
            "day_venue_address": ["", ""], "day_doors_at": ["", ""],
            "day_cancelled": ["true", "false"],
        },
    )
    r = client.get("/?sort=event").text
    # Mixed Legs Show's only LIVE date is September, after Between Show's
    # July date -- if the sort key still used the cancelled June date,
    # Mixed Legs would incorrectly sort before Between Show.
    assert r.index("Between Show") < r.index("Mixed Legs Show")


async def test_index_shows_chronological_deadline_list(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={
            "title": "Deadline Show", "event_id": "deadline-show",
            "round_label": ["最速先行"], "round_kind": ["lottery_round"],
            "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
            "round_results_at": [""], "round_payment_at": [""], "round_label_en": [""],
            "round_url": [""], "round_notes": [""], "round_leg": [""],
        },
    )
    r = client.get("/").text
    assert "Deadline Show" in r
    assert "最速先行" in r
    assert "closes" in r


async def test_index_deadline_list_excludes_cancelled_round(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={
            "title": "C", "event_id": "c",
            "day_label": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_city": [""], "day_venue": [""], "day_venue_address": [""], "day_doors_at": [""],
            "round_label": ["R1"], "round_kind": ["lottery_round"],
            "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
            "round_results_at": [""], "round_payment_at": [""], "round_label_en": [""],
            "round_url": [""], "round_notes": [""], "round_leg": ["Day 1"],
        },
    )
    async with client.db() as s:
        day_id = (await s.execute(select(ConcertDay))).scalar_one().id
        round_id = (await s.execute(select(Round))).scalar_one().id

    client.post(
        "/concerts/c/edit",
        data={
            "title": "C", "event_id": "c",
            "day_id": [str(day_id)], "day_label": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_city": [""], "day_venue": [""], "day_venue_address": [""], "day_doors_at": [""],
            "day_cancelled": ["true"],
            "round_id": [str(round_id)], "round_label": ["R1"], "round_kind": ["lottery_round"],
            "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
            "round_results_at": [""], "round_payment_at": [""], "round_label_en": [""],
            "round_url": [""], "round_notes": [""], "round_leg": ["Day 1"],
        },
    )
    r = client.get("/").text
    assert "R1" not in r


async def test_index_deadline_list_carries_tag_and_search_attributes(client):
    """Tag filter and free-text search apply to the chronological deadline
    list the same way they apply to tiles -- via data-tags/data-search
    attributes the existing client-side JS reads. This test checks the
    attributes are rendered correctly (server-side), matching how this
    file's existing tile tests verify data-tags/data-search rather than
    simulating JS execution."""
    login_as(client, EDITOR_ID, "reiji")
    async with client.db() as s:
        tag = Tag(name="Test Artist", kind=TagKind.ARTIST, created_by=EDITOR_ID)
        s.add(tag)
        await s.flush()
        tag_id = tag.id
        await s.commit()

    client.post(
        "/concerts",
        data={
            "title": "Tagged Deadline Show", "event_id": "tagged-deadline",
            "round_label": ["R1"], "round_kind": ["lottery_round"],
            "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
            "round_results_at": [""], "round_payment_at": [""], "round_label_en": [""],
            "round_url": [""], "round_notes": [""], "round_leg": [""],
        },
    )
    async with client.db() as s:
        from app.db.models import Concert as ConcertModel
        from app.db.service import attach_tag

        concert = (await s.execute(
            select(ConcertModel).where(ConcertModel.event_id == "tagged-deadline")
        )).scalar_one()
        await attach_tag(s, concert.id, tag)
        await s.commit()

    r = client.get("/").text
    li_start = r.index("<li", r.index("deadline-list"))
    li_end = r.index("</li>", li_start)
    li_html = r[li_start:li_end]
    assert f'data-tags="{tag_id}"' in li_html
    assert 'data-search="tagged deadline show test artist"' in li_html




# ── Inline confirm() handler injection ───────────────────────────────────

# The browser HTML-decodes an attribute value BEFORE parsing it as JS, so
# Jinja's &#39; escaping is undone by the time confirm()'s argument is read:
# a name interpolated straight into onsubmit is executable regardless.
HANDLER_PAYLOAD = "'); alert(1); //"
_ON_ATTR_RE = re.compile(r"\son[a-z]+\s*=\s*\"([^\"]*)\"")


def test_delete_confirm_does_not_execute_a_hostile_tag_name(client):
    login_as(client, EDITOR_ID, "reiji")
    assert client.post("/tags", data={"name": HANDLER_PAYLOAD, "kind": "artist"}).status_code == 303
    html = client.get("/tags").text
    for attr in _ON_ATTR_RE.findall(html):
        assert "alert(1)" not in attr, f"payload sits raw in an inline handler: {attr}"


def test_delete_confirm_still_names_the_tag_and_deletes_it(client):
    """The data-attribute refactor must not change what the editor sees."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Hasunosora", "kind": "artist"})
    html = client.get("/tags").text
    assert 'data-tag-name="Hasunosora"' in html  # the confirm() reads this
    assert 'action="/tags/1/delete"' in html
    assert client.post("/tags/1/delete").status_code == 303
    assert "Hasunosora" not in client.get("/tags").text


def test_rename_longer_than_the_creation_cap_is_rejected(client):
    """create_tag caps name at 100; the rename route must match it."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Hasunosora", "kind": "artist"})
    assert client.post("/tags/1/edit", data={"name": "x" * 101}).status_code == 422
    assert client.post("/tags/1/edit", data={"name": "x" * 100}).status_code == 303
