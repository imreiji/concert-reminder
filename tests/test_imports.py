"""URL-import routes: editor gating, host allowlist, preview/commit round trip.

fetch_ramen_html is monkeypatched to return a saved fixture -- same
approach test_auth.py uses for exchange_code/fetch_identity -- so these
tests never hit the network.
"""

from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base, Concert, ConcertDay, Round
from app.db.session import get_session
from app.web import auth
from app.web.app import create_app
from app.web.routes import imports as import_routes

EDITOR_ID, FAN_ID = 42, 777
FIXTURES = Path(__file__).parent / "fixtures"
GRADUATION_URL = "https://ramen.events/hasunosora-103rd-class-graduation-concert/"
WELCOMING_URL = "https://ramen.events/hasunosora-106th-class-welcoming-concert/"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


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


def mock_fetch(client, html: str) -> None:
    async def fake_fetch(url: str) -> str:
        return html

    client.monkeypatch.setattr(import_routes, "fetch_ramen_html", fake_fetch)


async def _all(db, model):
    async with db() as s:
        return list((await s.execute(select(model))).scalars())


# ── Editor gating ────────────────────────────────────────────────────────


def test_anonymous_is_rejected(client):
    assert client.get("/concerts/import").status_code == 401
    assert client.post("/concerts/import/preview", data={"url": GRADUATION_URL}).status_code == 401
    assert client.post("/concerts/import/commit", data={"title": "X"}).status_code == 401


def test_non_editor_is_forbidden(client):
    login_as(client, FAN_ID, "fan")
    assert client.get("/concerts/import").status_code == 403
    assert client.post("/concerts/import/preview", data={"url": GRADUATION_URL}).status_code == 403
    assert client.post("/concerts/import/commit", data={"title": "X"}).status_code == 403


def test_editor_sees_import_page(client):
    """Logged-in GET render test, per the 'every page needs one' convention."""
    login_as(client, EDITOR_ID, "reiji")
    r = client.get("/concerts/import")
    assert r.status_code == 200
    assert "ramen.events" in r.text


# ── Host allowlist (SSRF guard) ─────────────────────────────────────────


def test_non_ramen_events_host_rejected_before_fetch(client):
    login_as(client, EDITOR_ID, "reiji")

    async def boom(url: str) -> str:
        raise AssertionError("fetch_ramen_html must not be called for a disallowed host")

    client.monkeypatch.setattr(import_routes, "fetch_ramen_html", boom)
    r = client.post("/concerts/import/preview", data={"url": "https://evil.example/x"})
    assert r.status_code == 400


def test_non_https_rejected(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.post(
        "/concerts/import/preview", data={"url": "http://ramen.events/some-event/"}
    )
    assert r.status_code == 400


# ── Preview ──────────────────────────────────────────────────────────────


def test_preview_renders_parsed_draft(client):
    login_as(client, EDITOR_ID, "reiji")
    mock_fetch(client, load("ramen_graduation_concert.html"))
    r = client.post("/concerts/import/preview", data={"url": GRADUATION_URL})
    assert r.status_code == 200
    assert "103rd Class Graduation" in r.text
    assert 'value="2027-01-23T17:00"' in r.text  # day prefilled
    assert 'value="2026-10-14T12:00"' in r.text  # round opens prefilled


def test_preview_of_event_with_no_rounds_shows_warning(client):
    login_as(client, EDITOR_ID, "reiji")
    mock_fetch(client, load("ramen_welcoming_concert.html"))
    r = client.post("/concerts/import/preview", data={"url": WELCOMING_URL})
    assert r.status_code == 200
    assert "no lottery" in r.text


def test_fetch_failure_rerenders_form_with_error_not_500(client):
    from fastapi import HTTPException

    async def fail(url: str) -> str:
        raise HTTPException(status_code=502, detail="fetch failed: HTTP 404")

    login_as(client, EDITOR_ID, "reiji")
    client.monkeypatch.setattr(import_routes, "fetch_ramen_html", fail)
    r = client.post("/concerts/import/preview", data={"url": GRADUATION_URL})
    assert r.status_code == 200  # friendly re-render, not a raw 502
    assert "Couldn" in r.text


def test_unparseable_page_rerenders_form_with_error(client):
    login_as(client, EDITOR_ID, "reiji")
    mock_fetch(client, "<html><body>not an event</body></html>")
    r = client.post("/concerts/import/preview", data={"url": GRADUATION_URL})
    assert r.status_code == 200
    assert "Couldn" in r.text


# ── Commit ───────────────────────────────────────────────────────────────


async def test_commit_creates_concert_days_and_rounds(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.post(
        "/concerts/import/commit",
        data={
            "title": "Hasunosora 103rd Class Graduation Concert",
            "day_label": ["Day 1", "Day 2"],
            "day_starts_at": ["2027-01-23T17:00", "2027-01-24T15:30"],
            "round_label": ["Day 1 Lottery", "Day 2 Lottery"],
            "round_kind": ["lottery_round", "lottery_round"],
            "round_opens_at": ["2026-10-14T12:00", "2026-09-25T12:00"],
            "round_closes_at": ["2026-11-08T23:59", "2026-11-08T23:59"],
            "round_results_at": ["", ""],
            "round_payment_at": ["", ""],
            "round_url": ["", ""],
        },
    )
    assert r.status_code == 303
    # event_id isn't a field the import form collects -- auto-suggested from
    # the title (slugified) via generate_event_id.
    assert r.headers["location"] == "/concerts/hasunosora-103rd-class-graduation-concert"

    concerts = await _all(client.db, Concert)
    assert len(concerts) == 1 and concerts[0].title == "Hasunosora 103rd Class Graduation Concert"
    days = await _all(client.db, ConcertDay)
    assert [d.label for d in days] == ["Day 1", "Day 2"]
    rounds = await _all(client.db, Round)
    assert len(rounds) == 2


async def test_commit_tolerates_blank_trailing_rows(client):
    """The JS lets an editor add then not fill a row -- a fully blank row
    from the repeatable UI shouldn't become a junk day/round."""
    login_as(client, EDITOR_ID, "reiji")
    r = client.post(
        "/concerts/import/commit",
        data={
            "title": "Some Concert",
            "day_label": [""],
            "day_starts_at": [""],
            "round_label": [""],
            "round_kind": ["other"],
            "round_opens_at": [""],
            "round_closes_at": [""],
            "round_results_at": [""],
            "round_payment_at": [""],
            "round_url": [""],
        },
    )
    assert r.status_code == 303
    assert await _all(client.db, ConcertDay) == []
    assert await _all(client.db, Round) == []


def test_commit_requires_editor(client):
    login_as(client, FAN_ID, "fan")
    r = client.post("/concerts/import/commit", data={"title": "X"})
    assert r.status_code == 403


# ── fetch_ramen_html hardening: redirect host re-check, size cap ─────────
# These call fetch_ramen_html directly (no route/DB involved) using
# httpx.MockTransport, so the redirect-following and streaming logic itself
# is exercised rather than the monkeypatched version the routes use above.


async def test_fetch_ramen_html_follows_same_host_redirect():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old-slug/":
            return httpx.Response(302, headers={"location": "https://ramen.events/new-slug/"})
        return httpx.Response(200, text="<html>ok</html>")

    html = await import_routes.fetch_ramen_html(
        "https://ramen.events/old-slug/", transport=httpx.MockTransport(handler)
    )
    assert html == "<html>ok</html>"


async def test_fetch_ramen_html_rejects_redirect_off_allowlisted_host():
    """A redirect to a non-ramen.events host must be blocked even though the
    original URL passed _check_host -- this is the exact SSRF gap a bare
    follow_redirects=True (no per-hop re-check) would leave open."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://internal.example/steal"})

    with pytest.raises(HTTPException) as exc_info:
        await import_routes.fetch_ramen_html(
            "https://ramen.events/redirect-me/", transport=httpx.MockTransport(handler)
        )
    assert exc_info.value.status_code == 400


async def test_fetch_ramen_html_aborts_oversized_response(monkeypatch):
    monkeypatch.setattr(import_routes, "MAX_RESPONSE_BYTES", 10)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="x" * 1000)

    with pytest.raises(HTTPException) as exc_info:
        await import_routes.fetch_ramen_html(
            "https://ramen.events/big-page/", transport=httpx.MockTransport(handler)
        )
    assert exc_info.value.status_code == 502
