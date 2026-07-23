"""The concert's VENUE tags are derived from its legs, never typed."""
from datetime import UTC, datetime

import pytest
import pytest_asyncio
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import (
    Base,
    Concert,
    ConcertDay,
    ConcertTag,
    Notification,
    Tag,
    TagSubscription,
    User,
)
from app.db.service import sync_concert_venue_tags
from app.db.session import get_session
from app.domain.types import TagKind
from app.web import auth
from app.web.app import create_app

EDITOR_ID = 42


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
def editor_client(db, monkeypatch):
    """The signed-in-editor HTTP client, same shape as tests/test_crud.py's
    `client` + `login_as` pair (this suite has no shared conftest fixture for
    it), with the login already performed."""
    monkeypatch.setattr(settings, "editor_whitelist", str(EDITOR_ID))
    app = create_app()

    async def override_session():
        async with db() as s:
            yield s

    app.dependency_overrides[get_session] = override_session

    async def fake_exchange(code):
        return "tok"

    async def fake_identity(token):
        return {"id": str(EDITOR_ID), "username": "ed", "global_name": "ed", "avatar": None}

    monkeypatch.setattr(auth, "exchange_code", fake_exchange)
    monkeypatch.setattr(auth, "fetch_identity", fake_identity)

    c = TestClient(app, follow_redirects=False)
    r = c.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    c.get(f"/auth/callback?code=x&state={state}")
    c.db = db
    return c


async def _venue_tag_ids(session, concert_id):
    rows = (await session.execute(
        select(ConcertTag.tag_id)
        .join(Tag, Tag.id == ConcertTag.tag_id)
        .where(ConcertTag.concert_id == concert_id, Tag.kind == TagKind.VENUE)
    )).scalars()
    return set(rows)


async def _setup(session, venue_names):
    tags = [Tag(name=n, kind=TagKind.VENUE) for n in venue_names]
    session.add_all(tags)
    concert = Concert(title="T", event_id="ev1")
    session.add(concert)
    await session.flush()
    return concert, tags


async def test_rollup_unions_leg_venues(db):
    async with db() as session:
        concert, tags = await _setup(session, ["Zepp Haneda", "Zepp Namba"])
        for i, tag in enumerate(tags):
            session.add(ConcertDay(
                concert_id=concert.id, label=f"Day {i + 1}",
                starts_at_utc=datetime(2026, 8, i + 1, 9, tzinfo=UTC),
                venue_tag_id=tag.id,
            ))
        await session.flush()

        await sync_concert_venue_tags(session, concert.id)

        assert await _venue_tag_ids(session, concert.id) == {t.id for t in tags}


async def test_rollup_removes_a_venue_no_leg_uses(db):
    """The bug this fixes: Concert.venue was written once at creation and the
    edit route never re-derived it, so a changed venue stayed stale forever."""
    async with db() as session:
        concert, tags = await _setup(session, ["Old Hall", "New Hall"])
        old, new = tags
        session.add(ConcertTag(concert_id=concert.id, tag_id=old.id))
        session.add(ConcertDay(
            concert_id=concert.id, label="Day 1",
            starts_at_utc=datetime(2026, 8, 1, 9, tzinfo=UTC),
            venue_tag_id=new.id,
        ))
        await session.flush()

        await sync_concert_venue_tags(session, concert.id)

        assert await _venue_tag_ids(session, concert.id) == {new.id}


async def test_rollup_leaves_non_venue_tags_alone(db):
    """Group-tag expansion (invariant 3) materializes members deliberately;
    the venue rollup must never touch them."""
    async with db() as session:
        concert, _ = await _setup(session, ["Zepp Haneda"])
        group = Tag(name="Hasunosora", kind=TagKind.GROUP)
        session.add(group)
        await session.flush()
        session.add(ConcertTag(concert_id=concert.id, tag_id=group.id))
        await session.flush()

        await sync_concert_venue_tags(session, concert.id)

        all_ids = set((await session.execute(
            select(ConcertTag.tag_id).where(ConcertTag.concert_id == concert.id)
        )).scalars())
        assert group.id in all_ids


async def test_edit_form_rolls_up_changed_leg_venue(editor_client):
    """The end-to-end version of the staleness fix: change a leg's venue on the
    edit form and the concert's venue tags follow."""
    async with editor_client.db() as session:
        old = Tag(name="Old Hall", kind=TagKind.VENUE)
        new = Tag(name="New Hall", kind=TagKind.VENUE)
        session.add_all([old, new])
        concert = Concert(title="T", event_id="rollup1")
        session.add(concert)
        await session.flush()
        day = ConcertDay(
            concert_id=concert.id, label="Day 1",
            starts_at_utc=datetime(2026, 8, 1, 9, tzinfo=UTC), venue_tag_id=old.id,
        )
        session.add(day)
        session.add(ConcertTag(concert_id=concert.id, tag_id=old.id))
        await session.commit()

    resp = editor_client.post("/concerts/rollup1/edit", data={
        "title": "T", "event_id": "rollup1",
        "day_id": [str(day.id)], "day_key": [""],
        "day_label": ["Day 1"],
        "day_label_en": [""],
        "day_label_zh": [""], "day_starts_at": ["2026-08-01T18:00"],
        "day_venue_tag_id": [str(new.id)],
        "day_doors_at": [""], "day_cancelled": ["false"],
    })
    assert resp.status_code in (200, 303)

    async with editor_client.db() as session:
        assert await _venue_tag_ids(session, concert.id) == {new.id}


async def test_create_form_rolls_up_leg_venues(editor_client):
    """The create route is the second of the three save paths -- a concert
    created with venues on its legs carries them at the concert level too."""
    editor_client.post("/tags", data={
        "name_en": "Zepp Haneda", "name_zh": "Zepp Haneda", "name": "Zepp Haneda", "kind": "venue",
    })   # 1
    editor_client.post("/tags", data={
        "name_en": "Zepp Namba", "name_zh": "Zepp Namba", "name": "Zepp Namba", "kind": "venue",
    })    # 2
    r = editor_client.post("/concerts", data={"title_en": "Tour", "title_zh": "Tour",
        "title": "Tour", "event_id": "tour",
        "day_label": ["Day 1", "Day 2"],
        "day_label_en": ["Day 1", "Day 2"],
        "day_label_zh": ["Day 1", "Day 2"],
        "day_starts_at": ["2099-08-01T18:00", "2099-08-02T18:00"],
        "day_doors_at": ["", ""], "day_venue_tag_id": ["1", "2"],
    })
    assert r.status_code == 303

    async with editor_client.db() as session:
        concert = (await session.execute(
            select(Concert).where(Concert.event_id == "tour")
        )).scalar_one()
        assert await _venue_tag_ids(session, concert.id) == {1, 2}
        # create_concert_row no longer derives a join string; the rolled-up
        # VENUE tags above are the only answer to "where is this". Pinned so a
        # regression restoring ", ".join(...) is caught.
        assert concert.venue is None


async def test_import_commit_rolls_up_leg_venues(editor_client):
    """The third save path: the URL-import commit route builds its legs the
    same way and must run the same rollup."""
    editor_client.post("/tags", data={
        "name_en": "Zepp Haneda", "name_zh": "Zepp Haneda", "name": "Zepp Haneda", "kind": "venue",
    })   # 1
    r = editor_client.post("/concerts/import/commit", data={
        "day_label_zh": ["Day 1"], "day_label_en": ["Day 1"], "title": "Imported Show",
        "title_en": "Imported Show", "title_zh": "导入的演出",
        "day_label": ["Day 1"],
        "day_starts_at": ["2099-08-01T18:00"],
        "day_venue_tag_id": ["1"],
    })
    assert r.status_code == 303

    async with editor_client.db() as session:
        concert = (await session.execute(
            select(Concert).where(Concert.title == "Imported Show")
        )).scalar_one()
        assert await _venue_tag_ids(session, concert.id) == {1}


async def test_rollup_with_no_leg_venues_clears_them(db):
    async with db() as session:
        concert, tags = await _setup(session, ["Old Hall"])
        session.add(ConcertTag(concert_id=concert.id, tag_id=tags[0].id))
        session.add(ConcertDay(
            concert_id=concert.id, label="Day 1",
            starts_at_utc=datetime(2026, 8, 1, 9, tzinfo=UTC), venue_tag_id=None,
        ))
        await session.flush()

        await sync_concert_venue_tags(session, concert.id)

        assert await _venue_tag_ids(session, concert.id) == set()


# ── The two ends of the kind guard ───────────────────────────────────────
#
# day_venue_tag_id is the only editor-supplied tag id that does not flow
# through create_concert_row/edit_concert's resolve_tags call. Left
# unguarded, an id naming a non-VENUE tag lands in the rollup's `desired`
# set but never in its VENUE-filtered `current` set, so every save re-adds
# it -- and the second one trips ConcertTag's composite PK, a 500 that
# leaves the concert permanently unsavable. Both ends are closed: the
# route rejects it, and the rollup's own query can never see it.


async def test_create_rejects_a_non_venue_leg_venue_tag(editor_client):
    """A non-VENUE tag id posted as day_venue_tag_id is a 422, exactly like
    every other tag input (test_creation_rejects_wrong_kind_tags)."""
    editor_client.post("/tags", data={
        "name_en": "Sumire", "name_zh": "Sumire", "name": "Sumire", "kind": "artist",
    })  # 1
    r = editor_client.post("/concerts", data={"title_en": "Bad", "title_zh": "Bad",
        "title": "Bad", "event_id": "bad",
        "day_label": ["Day 1"],
        "day_label_en": ["Day 1"],
        "day_label_zh": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
        "day_doors_at": [""], "day_venue_tag_id": ["1"],
    })
    assert r.status_code == 422


async def test_create_rejects_a_nonexistent_leg_venue_tag(editor_client):
    r = editor_client.post("/concerts", data={"title_en": "Bad", "title_zh": "Bad",
        "title": "Bad", "event_id": "bad2",
        "day_label": ["Day 1"],
        "day_label_en": ["Day 1"],
        "day_label_zh": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
        "day_doors_at": [""], "day_venue_tag_id": ["999"],
    })
    assert r.status_code == 422


async def test_edit_rejects_a_non_venue_leg_venue_tag(editor_client):
    editor_client.post("/tags", data={
        "name_en": "Sumire", "name_zh": "Sumire", "name": "Sumire", "kind": "artist",
    })  # 1
    editor_client.post("/concerts", data={"title_en": "T", "title_zh": "T",
        "title": "T", "event_id": "edbad",
        "day_label": ["Day 1"],
        "day_label_en": ["Day 1"],
        "day_label_zh": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
        "day_doors_at": [""],
    })
    async with editor_client.db() as session:
        day = (await session.execute(select(ConcertDay))).scalar_one()
    r = editor_client.post("/concerts/edbad/edit", data={
        "title": "T", "event_id": "edbad",
        "day_id": [str(day.id)], "day_label": ["Day 1"],
        "day_label_en": [""],
        "day_label_zh": [""],
        "day_starts_at": ["2099-08-01T18:00"], "day_doors_at": [""],
        "day_venue_tag_id": ["1"],
    })
    assert r.status_code == 422


async def test_import_commit_rejects_a_non_venue_leg_venue_tag(editor_client):
    editor_client.post("/tags", data={
        "name_en": "Sumire", "name_zh": "Sumire", "name": "Sumire", "kind": "artist",
    })  # 1
    r = editor_client.post("/concerts/import/commit", data={
        "day_label_zh": ["Day 1"], "day_label_en": ["Day 1"], "title": "Bad Import",
        # Fully translated on purpose: import_commit's title check fires
        # before the venue-id check, so a half-translated title here would
        # give this test a 422 for the wrong reason.
        "title_en": "Bad Import", "title_zh": "错误的导入",
        "day_label": ["Day 1"],
        "day_starts_at": ["2099-08-01T18:00"],
        "day_venue_tag_id": ["1"],
    })
    assert r.status_code == 422


async def test_rollup_desired_ignores_a_non_venue_id(db):
    """Belt to the route's braces: even if a non-VENUE id somehow reaches the
    column, the rollup's `desired` query must not pick it up -- otherwise it
    is re-added on every save and the second one dies on the composite PK."""
    async with db() as session:
        concert, _ = await _setup(session, ["Zepp Haneda"])
        artist = Tag(name="Sumire", kind=TagKind.ARTIST)
        session.add(artist)
        await session.flush()
        session.add(ConcertDay(
            concert_id=concert.id, label="Day 1",
            starts_at_utc=datetime(2026, 8, 1, 9, tzinfo=UTC),
            venue_tag_id=artist.id,
        ))
        await session.flush()

        await sync_concert_venue_tags(session, concert.id)
        # The killer: a second run must not raise on the composite PK.
        await sync_concert_venue_tags(session, concert.id)

        all_ids = set((await session.execute(
            select(ConcertTag.tag_id).where(ConcertTag.concert_id == concert.id)
        )).scalars())
        assert artist.id not in all_ids


# ── The rollup is the SOLE writer of a concert's VENUE rows ──────────────
#
# Everything below pins the read side of the same rule the rollup owns: a
# concert's venue is derived from its legs, so every surface answering
# "where is this" must read the VENUE tags, and no second write path may
# touch them.


async def _subscribe(session, user_id: int, tag_id: int, *, notify: bool = True):
    """A plain notify-only subscriber: NO preset, so handle_newly_tagged never
    gives them rules on the concert and its "already has rules here" guard
    cannot mask a duplicate notice."""
    session.add(User(discord_id=user_id, username=f"u{user_id}"))
    await session.flush()
    session.add(TagSubscription(user_id=user_id, tag_id=tag_id, notify=notify))
    await session.flush()


# Finding 1: Home and the campaign board must read the VENUE tags.


async def test_home_peek_card_shows_the_leg_venue_tag(editor_client):
    """The peek grid below Home's Discover teaser. Created THROUGH the route,
    which is the only way the bug shows: create_concert_row writes
    Concert.venue = None, so a template reading that column renders nothing."""
    editor_client.post("/tags", data={
        "name_en": "Zepp Haneda", "name_zh": "Zepp Haneda", "name": "Zepp Haneda", "kind": "venue",
    })  # 1
    r = editor_client.post("/concerts", data={"title_en": "Peek Show", "title_zh": "Peek Show",
        "title": "Peek Show", "event_id": "peek-show",
        "day_label": ["Day 1"],
        "day_label_en": ["Day 1"],
        "day_label_zh": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
        "day_doors_at": [""], "day_venue_tag_id": ["1"],
    })
    assert r.status_code == 303

    assert "Zepp Haneda" in editor_client.get("/").text


async def test_board_card_shows_the_leg_venue_tag(editor_client):
    """The campaign board -- same bug, in the app's most-viewed block."""
    editor_client.post("/tags", data={
        "name_en": "Zepp Namba", "name_zh": "Zepp Namba", "name": "Zepp Namba", "kind": "venue",
    })  # 1
    r = editor_client.post("/concerts", data={"title_en": "Board Show", "title_zh": "Board Show",
        "title": "Board Show", "event_id": "board-show",
        "day_label": ["Day 1"],
        "day_label_en": ["Day 1"],
        "day_label_zh": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
        "day_doors_at": [""], "day_venue_tag_id": ["1"],
        "round_label": ["R1"], "round_kind": ["lottery_round"],
        "round_opens_at": ["2020-01-01T00:00"], "round_closes_at": ["2099-06-25T23:59"],
        "round_results_at": [""], "round_payment_at": [""],
        "round_label_en": ["R1"],
        "round_label_zh": ["R1"], "round_url": [""], "round_notes": [""], "round_leg": [""],
    })
    assert r.status_code == 303

    # Following the venue tag is what puts the concert on the editor's board --
    # and takes it OUT of the peek grid, so the assertion below can only be
    # satisfied by the board itself.
    async with editor_client.db() as session:
        session.add(TagSubscription(user_id=EDITOR_ID, tag_id=1, notify=False))
        await session.commit()

    page = editor_client.get("/").text
    board = page.split('id="board"', 1)[1].split("Coming up", 1)[0]
    assert "Zepp Namba" in board


# Finding 5: the export ROUTE, not just the pure serializer. `venue_tag` is
# lazy="raise", so a missing selectinload here is a 500 on an editor-facing
# endpoint that the pure-function tests could never see.


async def test_yaml_export_route_uses_the_leg_venue_tag(editor_client):
    async with editor_client.db() as session:
        session.add(Tag(
            name="K Arena Yokohama", kind=TagKind.VENUE,
            city="Kanagawa", address="Yokohama, Japan",
        ))
        await session.commit()
    r = editor_client.post("/concerts", data={"title_en": "Export Show", "title_zh": "Export Show",
        "title": "Export Show", "event_id": "export-show",
        "day_label": ["Day 1"],
        "day_label_en": ["Day 1"],
        "day_label_zh": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
        "day_doors_at": [""], "day_venue_tag_id": ["1"],
    })
    assert r.status_code == 303

    resp = editor_client.get("/concerts/export-show/export.yaml")
    assert resp.status_code == 200
    data = yaml.safe_load(resp.text)
    day = data["performances"][0]
    assert day["venue"] == "K Arena Yokohama"
    assert day["city"] == "Kanagawa"
    assert day["venue_address"] == "Yokohama, Japan"
    # The concert-level rollup lands in the export's `venues` list too.
    assert data["venues"] == ["K Arena Yokohama"]


# Finding 3: no second VENUE write path.
#
# The concert-level venue picker used to run its own attach/detach diff
# immediately before the rollup overwrote the result. A save that drops a
# venue chip whose leg still carries it means detach -> rollup re-attach ->
# the tag lands in `newly` a second time -> a DUPLICATE "New event" DM for
# any subscriber with notify=True and no preset (no preset means no rules,
# so handle_newly_tagged's "already has rules here" guard never fires).


async def test_a_venue_subscriber_is_not_notified_twice(editor_client):
    editor_client.post("/tags", data={
        "name_en": "Zepp Sapporo", "name_zh": "Zepp Sapporo", "name": "Zepp Sapporo",
        "kind": "venue",
    })  # 1
    async with editor_client.db() as session:
        await _subscribe(session, 9001, 1)
        await session.commit()

    r = editor_client.post("/concerts", data={"title_en": "Dup", "title_zh": "Dup",
        "title": "Dup", "event_id": "dup",
        "day_label": ["Day 1"],
        "day_label_en": ["Day 1"],
        "day_label_zh": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
        "day_doors_at": [""], "day_venue_tag_id": ["1"],
    })
    assert r.status_code == 303

    async with editor_client.db() as session:
        day = (await session.execute(select(ConcertDay))).scalar_one()

    # The save: no venue_tags in the concert-level picker, while the leg still
    # points at the same venue.
    r = editor_client.post("/concerts/dup/edit", data={
        "title": "Dup", "event_id": "dup",
        "day_id": [str(day.id)], "day_key": [""],
        "day_label": ["Day 1"],
        "day_label_en": [""],
        "day_label_zh": [""], "day_starts_at": ["2099-08-01T18:00"],
        "day_doors_at": [""], "day_cancelled": ["false"],
        "day_venue_tag_id": ["1"],
    })
    assert r.status_code in (200, 303)

    async with editor_client.db() as session:
        notices = (await session.execute(
            select(Notification).where(Notification.user_id == 9001)
        )).scalars().all()
        assert len(notices) == 1
        concert = (await session.execute(
            select(Concert).where(Concert.event_id == "dup")
        )).scalar_one()
        # ... and the venue survived the save.
        assert await _venue_tag_ids(session, concert.id) == {1}


# Finding 4: tag_id "0" is a live 500.
#
# "0".isdigit() is True, but resolve_tags skips falsy ids, so it returns []
# and the (tag,) unpack raises ValueError. It must 422 like every other bad
# id.


async def test_create_rejects_a_zero_leg_venue_tag(editor_client):
    r = editor_client.post("/concerts", data={"title_en": "Zero", "title_zh": "Zero",
        "title": "Zero", "event_id": "zero",
        "day_label": ["Day 1"],
        "day_label_en": ["Day 1"],
        "day_label_zh": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
        "day_doors_at": [""], "day_venue_tag_id": ["0"],
    })
    assert r.status_code == 422


async def test_create_rejects_a_negative_leg_venue_tag(editor_client):
    r = editor_client.post("/concerts", data={"title_en": "Neg", "title_zh": "Neg",
        "title": "Neg", "event_id": "neg",
        "day_label": ["Day 1"],
        "day_label_en": ["Day 1"],
        "day_label_zh": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
        "day_doors_at": [""], "day_venue_tag_id": ["-1"],
    })
    assert r.status_code == 422
