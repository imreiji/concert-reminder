"""Scheme validation for every editor-supplied URL.

`<input type="url">` accepts `javascript:alert(1)` -- it is a syntactically
valid absolute URL -- and those values are rendered straight into `href`
attributes, so a stored one executes in-origin for whoever clicks it. These
tests pin both the pure validator and the HTTP boundaries that must refuse
(422, not silently drop) such a value.
"""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base, Concert, Round, Tag
from app.db.session import get_session
from app.domain.types import TagKind
from app.domain.urls import UnsafeUrlError, clean_url
from app.web import auth
from app.web.app import create_app

EDITOR_ID = 42


# ── Unit: the validator itself ───────────────────────────────────────────


@pytest.mark.parametrize("raw", [
    "http://example.com",
    "https://example.com/path?q=1#frag",
    "HTTPS://Example.COM/Path",
])
def test_valid_urls_pass_through_unchanged(raw):
    assert clean_url(raw) == raw


def test_surrounding_whitespace_is_stripped():
    assert clean_url("  https://example.com  ") == "https://example.com"


def test_leading_and_trailing_whitespace_trimmed_both_ends():
    # Includes NUL/tab/newline, not just plain spaces, on both sides.
    assert clean_url("\t\x00 https://example.com \r\n") == "https://example.com"


def test_interior_space_is_preserved():
    # A browser percent-encodes an internal space; it does not delete it.
    # Deleting it here would silently store a different URL than the one
    # a browser actually resolves.
    assert clean_url("https://ex.com/a b") == "https://ex.com/a b"


@pytest.mark.parametrize("raw", ["", "   ", "\t\n", None])
def test_blank_becomes_none(raw):
    assert clean_url(raw) is None


@pytest.mark.parametrize("raw", [
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "vbscript:msgbox(1)",
    "file:///etc/passwd",
    "//evil.com",
    "/relative/path",
    "example.com",          # no scheme at all -- would render as a relative link
    "https://",             # scheme but no host
])
def test_disallowed_urls_raise(raw):
    with pytest.raises(UnsafeUrlError):
        clean_url(raw)


@pytest.mark.parametrize("raw", [
    "JaVaScRiPt:alert(1)",          # scheme match must be case-insensitive
    "  javascript:alert(1)",        # leading whitespace
    "\tjavascript:alert(1)",
    "\x00javascript:alert(1)",      # leading NUL
    "java\tscript:alert(1)",        # embedded tab -- browsers strip it
    "java\nscript:alert(1)",
    "java\rscript:alert(1)",
    "jav\x01ascript:alert(1)",
])
def test_scheme_evasions_are_rejected(raw):
    with pytest.raises(UnsafeUrlError):
        clean_url(raw)


# ── HTTP: the routes must answer 422 and persist nothing ─────────────────


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


async def test_concert_create_rejects_javascript_url(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/concerts", data={
        "title": "C", "event_id": "c", "official_url": "javascript:alert(1)",
    })
    assert r.status_code == 422
    async with client.db() as s:
        assert (await s.execute(select(Concert))).scalars().first() is None


async def test_concert_edit_rejects_javascript_url(client):
    login_as(client, EDITOR_ID, "reiji")
    assert client.post("/concerts", data={
        "title": "C", "event_id": "c", "official_url": "https://example.com",
    }).status_code == 303

    r = client.post("/concerts/c/edit", data={
        "new_event_id": "c", "title": "Changed", "official_url": "javascript:alert(1)",
    })
    assert r.status_code == 422
    async with client.db() as s:
        concert = (await s.execute(select(Concert))).scalars().one()
        # Proves rollback actually happened, not just that we posted the same
        # value back: if the title had been mutated and committed before the
        # URL check failed, this would catch it.
        assert concert.title == "C"  # unchanged
        assert concert.official_url == "https://example.com"  # unchanged


async def test_tag_edit_rejects_javascript_location_url(client):
    login_as(client, EDITOR_ID, "reiji")
    assert client.post("/tags", data={
        "name": "Budokan", "kind": "venue", "location_url": "https://maps.example/budokan",
    }).status_code == 303

    r = client.post("/tags/1/edit", data={"location_url": "javascript:alert(1)"})
    assert r.status_code == 422
    async with client.db() as s:
        tag = (await s.execute(select(Tag).where(Tag.kind == TagKind.VENUE))).scalars().one()
        assert tag.location_url == "https://maps.example/budokan"  # unchanged


async def test_tag_create_rejects_javascript_location_url(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/tags", data={
        "name": "Budokan", "kind": "venue", "location_url": "javascript:alert(1)",
    })
    assert r.status_code == 422
    async with client.db() as s:
        assert (await s.execute(select(Tag))).scalars().first() is None


async def test_round_url_rejects_javascript_url(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/concerts", data={
        "title": "C", "event_id": "c",
        "round_label": ["R1"], "round_kind": ["lottery_round"],
        "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
        "round_results_at": [""], "round_payment_at": [""],
        "round_label_en": [""], "round_url": ["javascript:alert(1)"],
        "round_notes": [""], "round_leg": [""],
    })
    assert r.status_code == 422
    async with client.db() as s:
        assert (await s.execute(select(Concert))).scalars().first() is None


async def test_import_commit_rejects_javascript_round_url(client):
    # The import commit route builds its rounds through the same
    # build_round -> apply_round_fields path as manual entry. This pins that
    # it really does: bypassing the chokepoint here would write the concert
    # and its round with a javascript: href.
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/concerts/import/commit", data={
        "title": "Imported", "source_url": "https://ramen.events/e/1",
        "round_label": ["R1"], "round_kind": ["lottery_round"],
        "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
        "round_results_at": [""], "round_payment_at": [""],
        "round_url": ["javascript:alert(1)"],
    })
    assert r.status_code == 422
    async with client.db() as s:
        assert (await s.execute(select(Concert))).scalars().first() is None
        assert (await s.execute(select(Round))).scalars().first() is None


async def test_edit_page_inplace_round_url_rejects_javascript_url(client):
    # Updating an EXISTING round takes apply_round_fields' other caller (the
    # id-preserving reconciliation branch), not build_round. Same chokepoint,
    # different entry -- so it needs its own guard.
    login_as(client, EDITOR_ID, "reiji")
    assert client.post("/concerts", data={
        "title": "C", "event_id": "c",
        "round_label": ["R1"], "round_kind": ["lottery_round"],
        "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
        "round_results_at": [""], "round_payment_at": [""],
        "round_label_en": [""], "round_url": ["https://example.com/lottery"],
        "round_notes": [""], "round_leg": [""],
    }).status_code == 303

    async with client.db() as s:
        round_id = (await s.execute(select(Round))).scalars().one().id

    r = client.post("/concerts/c/edit", data={
        "event_id": "c", "title": "C",
        "round_id": [str(round_id)],
        "round_label": ["R1"], "round_label_en": [""], "round_kind": ["lottery_round"],
        "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
        "round_results_at": [""], "round_payment_at": [""],
        "round_url": ["javascript:alert(1)"],
        "round_notes": [""], "round_leg": [""],
    })
    assert r.status_code == 422
    async with client.db() as s:
        round_ = (await s.execute(select(Round))).scalars().one()
        assert round_.id == round_id  # updated in place, not recreated
        assert round_.url == "https://example.com/lottery"  # unchanged
