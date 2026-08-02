"""Task 2 + Task 3: PendingDraft persists a multi-draft paste as a work batch,
and the paste/list/preview/commit routes that let an editor triage it.

Not step state -- /setup deliberately holds none, since every screen there
re-derives current DB truth. A pending draft is different: fifty to a hundred
concerts, each needing a human to read a preview, is not one sitting. Rows
outlive the request that created them so a closed tab never loses the batch.

The first half of this file (Task 2) exercises `db/service.py` directly, in
an isolated in-memory engine, with no HTTP involved. The second half (Task 3,
below the "web routes" banner) drives the same behaviour through
`routes/imports.py`'s POST /batch, GET /pending, GET /pending/{id} and
POST /pending/{id}/discard, plus the pending_id hook on POST /commit -- same
fixture shape as tests/test_draft_import.py, which owns the single-document
paste path this one builds on.
"""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base, Concert, PendingDraft, User
from app.db.service import (
    create_pending_drafts,
    delete_user,
    discard_pending_draft,
    mark_pending_committed,
    pending_drafts,
)
from app.db.session import get_session
from app.domain.draft import ParsedConcert
from app.domain.yaml_import import DraftBatch, ParsedDraft
from app.web import auth
from app.web.app import create_app

USER_A = 111
USER_B = 222
NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")  # match production: cascades must fire

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _batch(*titles: str) -> DraftBatch:
    drafts = tuple(
        ParsedDraft(
            text=f"title: {title}\n",
            parsed=ParsedConcert(title=title, venue_name=None),
        )
        for title in titles
    )
    return DraftBatch(drafts=drafts)


async def _seed_user(session, discord_id: int) -> None:
    session.add(User(discord_id=discord_id, username=f"user{discord_id}"))
    await session.flush()


async def test_create_pending_drafts_makes_one_row_per_document_with_own_text(db):
    async with db() as s:
        await _seed_user(s, USER_A)
        rows = await create_pending_drafts(s, _batch("A", "B", "C"), USER_A)

        assert len(rows) == 3
        assert [r.title for r in rows] == ["A", "B", "C"]
        assert [r.draft_text for r in rows] == [
            "title: A\n", "title: B\n", "title: C\n",
        ]
        assert all(r.created_by == USER_A for r in rows)


async def test_pending_drafts_scoped_to_pasting_user(db):
    async with db() as s:
        await _seed_user(s, USER_A)
        await _seed_user(s, USER_B)
        await create_pending_drafts(s, _batch("mine"), USER_A)
        await create_pending_drafts(s, _batch("theirs"), USER_B)

        mine = await pending_drafts(s, USER_A)
        theirs = await pending_drafts(s, USER_B)

        assert [r.title for r in mine] == ["mine"]
        assert [r.title for r in theirs] == ["theirs"]


async def test_pending_drafts_excludes_committed_and_discarded_rows(db):
    async with db() as s:
        await _seed_user(s, USER_A)
        rows = await create_pending_drafts(
            s, _batch("keep", "commit-me", "discard-me"), USER_A
        )
        concert = Concert(title="X", event_id="x-1")
        s.add(concert)
        await s.flush()

        assert await mark_pending_committed(s, rows[1].id, concert.id, NOW) is True
        assert await discard_pending_draft(s, rows[2].id, NOW) is True

        remaining = await pending_drafts(s, USER_A)
        assert [r.title for r in remaining] == ["keep"]


async def test_mark_pending_committed_stamps_committed_at_and_concert_id(db):
    async with db() as s:
        await _seed_user(s, USER_A)
        rows = await create_pending_drafts(s, _batch("A"), USER_A)
        concert = Concert(title="X", event_id="x-1")
        s.add(concert)
        await s.flush()

        ok = await mark_pending_committed(s, rows[0].id, concert.id, NOW)
        assert ok is True

        row = await s.get(PendingDraft, rows[0].id)
        assert row.committed_at == NOW
        assert row.concert_id == concert.id


async def test_discard_pending_draft_stamps_discarded_at_leaves_concert_id_null(db):
    async with db() as s:
        await _seed_user(s, USER_A)
        rows = await create_pending_drafts(s, _batch("A"), USER_A)

        ok = await discard_pending_draft(s, rows[0].id, NOW)
        assert ok is True

        row = await s.get(PendingDraft, rows[0].id)
        assert row.discarded_at == NOW
        assert row.concert_id is None


async def test_committing_already_committed_row_returns_false_without_restamping(db):
    """The same double-submit rule `dismiss_lead` follows: a second commit
    must not silently rewrite which concert a draft claims to have produced.
    Asserting only "still committed" would pass even if the implementation
    overwrote the row -- capture the ORIGINAL values and compare."""
    async with db() as s:
        await _seed_user(s, USER_A)
        rows = await create_pending_drafts(s, _batch("A"), USER_A)
        concert1 = Concert(title="X", event_id="x-1")
        concert2 = Concert(title="Y", event_id="y-1")
        s.add_all([concert1, concert2])
        await s.flush()

        assert await mark_pending_committed(s, rows[0].id, concert1.id, NOW) is True

        row = await s.get(PendingDraft, rows[0].id)
        original_committed_at = row.committed_at
        original_concert_id = row.concert_id
        assert original_concert_id == concert1.id

        second = await mark_pending_committed(s, rows[0].id, concert2.id, LATER)
        assert second is False

        await s.refresh(row)
        assert row.committed_at == original_committed_at
        assert row.concert_id == original_concert_id


async def test_deleting_the_concert_leaves_the_row_with_concert_id_null(db):
    """Requires PRAGMA foreign_keys=ON: without it this cascade never fires
    and the row would keep pointing at a concert that no longer exists."""
    async with db() as s:
        await _seed_user(s, USER_A)
        rows = await create_pending_drafts(s, _batch("A"), USER_A)
        concert = Concert(title="X", event_id="x-1")
        s.add(concert)
        await s.flush()
        assert await mark_pending_committed(s, rows[0].id, concert.id, NOW) is True

        row = await s.get(PendingDraft, rows[0].id)
        await s.delete(concert)
        await s.flush()
        await s.refresh(row)

        assert row is not None, "deleting the concert must not delete the pending draft"
        assert row.concert_id is None


async def test_deleting_the_user_removes_their_pending_drafts(db):
    """Self-serve erasure (invariant 5, POST /me/delete -> service.delete_user)
    is a bare `session.delete(user)` relying entirely on ondelete= clauses to
    do the right thing. A pending draft is the pasting editor's own
    un-actioned working text -- personal data, not shared catalogue -- so it
    must go with them the way reminder_rules/web_sessions/etc. do, rather than
    dangle or block the delete outright.

    Requires PRAGMA foreign_keys=ON: without it SQLite enforces no FK action
    at all, `created_by` would happily reference a deleted user, and this
    test would pass for the wrong reason -- the exact trap the fixture rule
    exists for.
    """
    async with db() as s:
        await _seed_user(s, USER_A)
        rows = await create_pending_drafts(s, _batch("A", "B"), USER_A)
        row_ids = [r.id for r in rows]

        assert await delete_user(s, USER_A) is True

        remaining = await s.execute(
            select(PendingDraft).where(PendingDraft.id.in_(row_ids))
        )
        assert remaining.scalars().all() == []


# ── Task 3: the web routes ─────────────────────────────────────────────────
#
# POST /concerts/import/batch, GET /concerts/import/pending,
# GET /concerts/import/pending/{id}, POST /concerts/import/pending/{id}/discard,
# and the pending_id hook on POST /concerts/import/commit. Same fixture shape
# as tests/test_draft_import.py (which owns the single-document paste path).

EDITOR_A, EDITOR_B, FAN_ID = 42, 43, 777

# A minimal but commit-able draft: the trilingual title trio is what
# import_commit's require_variants demands, and nothing else is mandatory --
# the same minimal shape test_draft_import.py's own commit tests use.
ALPHA_DRAFT = "title: Alpha\ntitle_en: Alpha\ntitle_zh: Alpha\n"


def _one_doc(title: str) -> str:
    return f"title: {title}\ntitle_en: {title}\ntitle_zh: {title}\n"


@pytest.fixture()
def client(db, monkeypatch):
    monkeypatch.setattr(settings, "editor_whitelist", f"{EDITOR_A},{EDITOR_B}")
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


async def _sole_pending_row(db) -> PendingDraft:
    async with db() as s:
        return (await s.execute(select(PendingDraft))).scalar_one()


# ── Gating ───────────────────────────────────────────────────────────────


def test_a_non_editor_cannot_reach_any_of_it(client):
    """All five routes, GET and POST. A page that hides a form is not access
    control."""
    login_as(client, FAN_ID, "fan")
    assert client.post(
        "/concerts/import/batch", data={"draft": ALPHA_DRAFT}
    ).status_code == 403
    assert client.get("/concerts/import/pending").status_code == 403
    assert client.get("/concerts/import/pending/1").status_code == 403
    assert client.post("/concerts/import/pending/1/discard").status_code == 403
    assert client.post("/concerts/import/commit", data={
        "title": "X", "title_en": "X", "title_zh": "X", "pending_id": "1",
    }).status_code == 403


# ── POST /batch ────────────────────────────────────────────────────────────


async def test_a_batch_of_three_becomes_three_pending_rows(client, db):
    login_as(client, EDITOR_A, "a")
    batch = "\n---\n".join(_one_doc(t) for t in ("Alpha", "Beta", "Gamma"))
    r = client.post("/concerts/import/batch", data={"draft": batch})
    assert r.status_code == 303
    assert r.headers["location"] == "/concerts/import/pending"

    async with db() as s:
        rows = (await s.execute(select(PendingDraft))).scalars().all()
    assert sorted(row.title for row in rows) == ["Alpha", "Beta", "Gamma"]


async def test_a_bad_document_does_not_stop_the_good_ones(client, db):
    """Two rows created, one error named. All-or-nothing here would undo Task 1."""
    login_as(client, EDITOR_A, "a")
    batch = "\n---\n".join([
        _one_doc("Good One"),
        "- not\n- a\n- mapping",
        _one_doc("Good Two"),
    ])
    r = client.post("/concerts/import/batch", data={"draft": batch})
    # Errors re-render the paste form (200), not a redirect -- the two good
    # documents are already committed to the pending table by the time this
    # response is built.
    assert r.status_code == 200
    assert "document 2" in r.text

    async with db() as s:
        rows = (await s.execute(select(PendingDraft))).scalars().all()
    assert sorted(row.title for row in rows) == ["Good One", "Good Two"]


# ── GET /pending ─────────────────────────────────────────────────────────


async def test_the_list_is_scoped_to_its_owner(client):
    """Another editor's batch must not appear -- two editors triaging at once
    is the expected case, not an exotic one."""
    login_as(client, EDITOR_A, "a")
    client.post("/concerts/import/batch", data={"draft": _one_doc("Mine")})

    login_as(client, EDITOR_B, "b")
    client.post("/concerts/import/batch", data={"draft": _one_doc("Theirs")})

    r_b = client.get("/concerts/import/pending")
    assert "Theirs" in r_b.text
    assert "Mine" not in r_b.text

    login_as(client, EDITOR_A, "a")
    r_a = client.get("/concerts/import/pending")
    assert "Mine" in r_a.text
    assert "Theirs" not in r_a.text


async def test_a_committed_row_leaves_the_list(client, db):
    login_as(client, EDITOR_A, "a")
    client.post("/concerts/import/batch", data={"draft": ALPHA_DRAFT})
    row = await _sole_pending_row(db)

    client.post("/concerts/import/commit", data={
        "title": "Alpha", "title_en": "Alpha", "title_zh": "Alpha",
        "pending_id": str(row.id),
    })

    assert "Alpha" not in client.get("/concerts/import/pending").text


# ── GET /pending/{id} ────────────────────────────────────────────────────


async def test_review_route_renders_the_pending_id_hidden_field(client, db):
    login_as(client, EDITOR_A, "a")
    client.post("/concerts/import/batch", data={"draft": ALPHA_DRAFT})
    row = await _sole_pending_row(db)

    r = client.get(f"/concerts/import/pending/{row.id}")
    assert r.status_code == 200
    assert f'name="pending_id" value="{row.id}"' in r.text


def test_review_route_404s_on_another_editors_row(client):
    """Ownership 404s, never 403 -- invariant 5: don't confirm to a caller
    that another editor's id exists."""
    login_as(client, EDITOR_A, "a")
    client.post("/concerts/import/batch", data={"draft": ALPHA_DRAFT})

    login_as(client, EDITOR_B, "b")
    assert client.get("/concerts/import/pending/1").status_code == 404
    assert client.post("/concerts/import/pending/1/discard").status_code == 404


# ── POST /pending/{id}/discard ───────────────────────────────────────────


async def test_discarding_a_pending_row_removes_it_from_the_list(client, db):
    login_as(client, EDITOR_A, "a")
    client.post("/concerts/import/batch", data={"draft": ALPHA_DRAFT})
    row = await _sole_pending_row(db)

    r = client.post(f"/concerts/import/pending/{row.id}/discard")
    assert r.status_code == 303
    assert r.headers["location"] == "/concerts/import/pending"
    assert "Alpha" not in client.get("/concerts/import/pending").text

    async with db() as s:
        refreshed = await s.get(PendingDraft, row.id)
        assert refreshed.discarded_at is not None


# ── POST /commit with pending_id ────────────────────────────────────────


async def test_committing_a_pending_draft_returns_to_the_list_not_the_concert(client, db):
    login_as(client, EDITOR_A, "a")
    client.post("/concerts/import/batch", data={"draft": ALPHA_DRAFT})
    row = await _sole_pending_row(db)

    r = client.post("/concerts/import/commit", data={
        "title": "Alpha", "title_en": "Alpha", "title_zh": "Alpha",
        "pending_id": str(row.id),
    })
    assert r.status_code == 303
    assert r.headers["location"] == "/concerts/import/pending"


async def test_committing_without_a_pending_id_behaves_exactly_as_before(client):
    """The single-draft path (test_draft_import.py) is what everything else
    already builds on -- pin that an absent pending_id changes nothing."""
    login_as(client, EDITOR_A, "a")
    r = client.post("/concerts/import/commit", data={
        "title": "Solo", "title_en": "Solo", "title_zh": "Solo",
    })
    assert r.status_code == 303
    assert r.headers["location"] == "/concerts/solo"


async def test_committing_stamps_the_row_with_its_concert(client, db):
    login_as(client, EDITOR_A, "a")
    client.post("/concerts/import/batch", data={"draft": ALPHA_DRAFT})
    row = await _sole_pending_row(db)

    client.post("/concerts/import/commit", data={
        "title": "Alpha", "title_en": "Alpha", "title_zh": "Alpha",
        "pending_id": str(row.id),
    })

    async with db() as s:
        refreshed = await s.get(PendingDraft, row.id)
        concert = (await s.execute(select(Concert))).scalar_one()
        assert refreshed.committed_at is not None
        assert refreshed.concert_id == concert.id


async def test_the_same_draft_committed_twice_does_not_make_two_concerts(client, db):
    """event_id is unique and already answers 409; pin that it still fires
    through the pending path rather than being bypassed by it."""
    login_as(client, EDITOR_A, "a")
    client.post("/concerts/import/batch", data={"draft": ALPHA_DRAFT})
    row = await _sole_pending_row(db)

    data = {
        "title": "Alpha", "title_en": "Alpha", "title_zh": "Alpha",
        "event_id": "alpha-live", "pending_id": str(row.id),
    }
    first = client.post("/concerts/import/commit", data=data)
    assert first.status_code == 303

    second = client.post("/concerts/import/commit", data=data)
    assert second.status_code == 409

    async with db() as s:
        concerts = (await s.execute(select(Concert))).scalars().all()
    assert len(concerts) == 1


async def test_committing_a_foreign_pending_id_does_not_stamp_someone_elses_row(client, db):
    """A tampered hidden field must not let one editor mark another editor's
    triage row committed -- the ownership check in import_commit treats a
    foreign or missing id as though pending_id had never been sent."""
    login_as(client, EDITOR_A, "a")
    client.post("/concerts/import/batch", data={"draft": ALPHA_DRAFT})

    login_as(client, EDITOR_B, "b")
    r = client.post("/concerts/import/commit", data={
        "title": "Bravo", "title_en": "Bravo", "title_zh": "Bravo",
        "pending_id": "1",  # Editor A's row
    })
    assert r.status_code == 303
    assert r.headers["location"] != "/concerts/import/pending"

    async with db() as s:
        row = await s.get(PendingDraft, 1)
        assert row.committed_at is None
        assert row.concert_id is None
