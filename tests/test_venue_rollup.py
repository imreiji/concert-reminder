"""The concert's VENUE tags are derived from its legs, never typed."""
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base, Concert, ConcertDay, ConcertTag, Tag
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
        "day_label": ["Day 1"], "day_starts_at": ["2026-08-01T18:00"],
        "day_venue_tag_id": [str(new.id)],
        "day_doors_at": [""], "day_cancelled": ["false"],
    })
    assert resp.status_code in (200, 303)

    async with editor_client.db() as session:
        assert await _venue_tag_ids(session, concert.id) == {new.id}


async def test_create_form_rolls_up_leg_venues(editor_client):
    """The create route is the second of the three save paths -- a concert
    created with venues on its legs carries them at the concert level too."""
    editor_client.post("/tags", data={"name": "Zepp Haneda", "kind": "venue"})   # 1
    editor_client.post("/tags", data={"name": "Zepp Namba", "kind": "venue"})    # 2
    r = editor_client.post("/concerts", data={
        "title": "Tour", "event_id": "tour",
        "day_label": ["Day 1", "Day 2"],
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
    editor_client.post("/tags", data={"name": "Zepp Haneda", "kind": "venue"})   # 1
    r = editor_client.post("/concerts/import/commit", data={
        "title": "Imported Show",
        "day_label": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
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
    editor_client.post("/tags", data={"name": "Sumire", "kind": "artist"})  # 1
    r = editor_client.post("/concerts", data={
        "title": "Bad", "event_id": "bad",
        "day_label": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
        "day_doors_at": [""], "day_venue_tag_id": ["1"],
    })
    assert r.status_code == 422


async def test_create_rejects_a_nonexistent_leg_venue_tag(editor_client):
    r = editor_client.post("/concerts", data={
        "title": "Bad", "event_id": "bad2",
        "day_label": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
        "day_doors_at": [""], "day_venue_tag_id": ["999"],
    })
    assert r.status_code == 422


async def test_edit_rejects_a_non_venue_leg_venue_tag(editor_client):
    editor_client.post("/tags", data={"name": "Sumire", "kind": "artist"})  # 1
    editor_client.post("/concerts", data={
        "title": "T", "event_id": "edbad",
        "day_label": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
        "day_doors_at": [""],
    })
    async with editor_client.db() as session:
        day = (await session.execute(select(ConcertDay))).scalar_one()
    r = editor_client.post("/concerts/edbad/edit", data={
        "title": "T", "event_id": "edbad",
        "day_id": [str(day.id)], "day_label": ["Day 1"],
        "day_starts_at": ["2099-08-01T18:00"], "day_doors_at": [""],
        "day_venue_tag_id": ["1"],
    })
    assert r.status_code == 422


async def test_import_commit_rejects_a_non_venue_leg_venue_tag(editor_client):
    editor_client.post("/tags", data={"name": "Sumire", "kind": "artist"})  # 1
    r = editor_client.post("/concerts/import/commit", data={
        "title": "Bad Import",
        "day_label": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
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
