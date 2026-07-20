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


def test_import_form_matches_design_system(client):
    """Task 2: the paste-a-URL screen ports demo lines 1118-1147 -- the
    `.lede` heading block, the `.fld` labelled input, and the SSRF-guard
    reassurance as a `.callout`. The POST target and the pattern attribute
    (the actual SSRF guard) must survive the restyle byte-for-byte."""
    login_as(client, EDITOR_ID, "reiji")
    r = client.get("/concerts/import")
    assert r.status_code == 200
    assert '<div class="lede">' in r.text
    assert '<label class="fld">' in r.text
    assert 'class="callout"' in r.text
    # SSRF guard: unchanged POST target and pattern (do not loosen either).
    assert 'action="/concerts/import/preview"' in r.text
    assert 'pattern="https://ramen\\.events/.*"' in r.text


def test_import_form_error_uses_warn_callout(client):
    """The parse-failure branch (hit via POST /preview re-rendering this
    same template) keeps warning about the failure, now as a `.callout warn`
    instead of the old plain `<p class="import-error">`."""
    login_as(client, EDITOR_ID, "reiji")
    mock_fetch(client, "<html><body>not an event</body></html>")
    r = client.post("/concerts/import/preview", data={"url": GRADUATION_URL})
    assert r.status_code == 200
    assert 'class="callout warn"' in r.text
    assert "Couldn" in r.text


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


def test_preview_shows_new_round_kind_labels(client):
    login_as(client, EDITOR_ID, "reiji")
    mock_fetch(client, load("ramen_graduation_concert.html"))
    r = client.post("/concerts/import/preview", data={"url": GRADUATION_URL})
    assert "First come, first served" in r.text
    assert "Overseas tour package" in r.text


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


async def test_commit_binds_a_round_to_multiple_legs(client):
    """The preview's leg chips let one round apply to SEVERAL legs -- an
    expression the old flat form had no field for. Each day carries a
    client-side `day_key`; a round's `round_legs` value references those keys,
    and import_commit resolves them to the real ConcertDay ids the flush hands
    out, exactly like create_concert (key_to_day_id + parse_round_legs)."""
    login_as(client, EDITOR_ID, "reiji")
    r = client.post(
        "/concerts/import/commit",
        data={
            "title": "Two Leg Tour",
            "day_key": ["d0", "d1"],
            "day_label": ["Osaka", "Tokyo"],
            "day_starts_at": ["2027-01-23T17:00", "2027-01-24T15:30"],
            "round_label": ["Nationwide lottery"],
            "round_kind": ["lottery_round"],
            "round_opens_at": ["2026-10-14T12:00"],
            "round_closes_at": ["2026-11-08T23:59"],
            "round_results_at": [""],
            "round_payment_at": [""],
            "round_url": [""],
            "round_legs": ["d0 d1"],
        },
    )
    assert r.status_code == 303
    days = await _all(client.db, ConcertDay)
    rounds = await _all(client.db, Round)
    assert len(rounds) == 1
    assert rounds[0].applies_to is not None
    assert set(rounds[0].applies_to) == {d.id for d in days}


async def test_commit_round_with_no_legs_is_all_legs(client):
    """A round whose chips select nothing binds to no specific leg --
    applies_to stays None (the all-legs / "General" convention), matching
    apply_round_fields and what concert_round_rows reads."""
    login_as(client, EDITOR_ID, "reiji")
    r = client.post(
        "/concerts/import/commit",
        data={
            "title": "Whole Event Round",
            "day_key": ["d0"],
            "day_label": ["Day 1"],
            "day_starts_at": ["2027-01-23T17:00"],
            "round_label": ["Fan club presale"],
            "round_kind": ["lottery_round"],
            "round_opens_at": ["2026-10-14T12:00"],
            "round_closes_at": ["2026-11-08T23:59"],
            "round_results_at": [""],
            "round_payment_at": [""],
            "round_url": [""],
            "round_legs": [""],
        },
    )
    assert r.status_code == 303
    rounds = await _all(client.db, Round)
    assert len(rounds) == 1
    assert rounds[0].applies_to is None


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


def test_preview_carries_editable_source_url(client):
    """The commit POST is a fresh request, so the only way it learns which page
    the draft came from is a field on the preview form. It is now an editable
    <input type="url"> in the Details fold (was a hidden field), pre-filled with
    the parsed page; it still round-trips and is re-validated on commit."""
    login_as(client, EDITOR_ID, "reiji")
    mock_fetch(client, load("ramen_graduation_concert.html"))
    r = client.post("/concerts/import/preview", data={"url": GRADUATION_URL})
    assert f'name="source_url" type="url" value="{GRADUATION_URL}"' in r.text


def test_preview_shows_kind_selector_and_details_fold(client):
    """The .ebar carries a concert Kind selector and the preview grows a
    Details-and-links fold (title EN, organizer, editable source URL, notes),
    mirroring concert_new.html."""
    login_as(client, EDITOR_ID, "reiji")
    mock_fetch(client, load("ramen_graduation_concert.html"))
    r = client.post("/concerts/import/preview", data={"url": GRADUATION_URL})
    assert r.status_code == 200
    assert 'name="kind"' in r.text        # concert Kind selector in the .ebar
    assert "Details and links" in r.text  # the new fold
    assert 'name="title_en"' in r.text
    assert 'name="organizer"' in r.text


async def test_commit_persists_details_fold_fields(client):
    """Committing with an edited Title EN / organizer / kind / notes persists
    them, set on the concert exactly as create_concert does."""
    login_as(client, EDITOR_ID, "reiji")
    r = client.post(
        "/concerts/import/commit",
        data={
            "title": "Detailed Concert",
            "title_en": "Detailed Concert (English)",
            "organizer": "Some Organizer",
            "kind": "tour",
            "notes": "spotted on ramen.events",
        },
    )
    assert r.status_code == 303
    concerts = await _all(client.db, Concert)
    assert len(concerts) == 1
    c = concerts[0]
    assert c.title_en == "Detailed Concert (English)"
    assert c.organizer == "Some Organizer"
    assert c.kind is not None and c.kind.value == "tour"
    assert c.notes == "spotted on ramen.events"


async def test_commit_edited_source_url_still_revalidates(client):
    """The Source URL is now editor-editable, but a bad scheme is still
    rejected -- it goes through form_url on commit (invariant 7), same as when
    it was a hidden field."""
    login_as(client, EDITOR_ID, "reiji")
    r = client.post(
        "/concerts/import/commit",
        data={"title": "Bad URL", "source_url": "javascript:alert(1)"},
    )
    assert r.status_code == 422
    assert await _all(client.db, Concert) == []


async def test_commit_persists_source_url(client):
    """import_preview already computes and displays the source URL; without
    this the attribution was dropped on the floor at commit time, even though
    the manual create/edit path stores it."""
    login_as(client, EDITOR_ID, "reiji")
    r = client.post(
        "/concerts/import/commit",
        data={"title": "Hasunosora 103rd Class Graduation Concert",
              "source_url": GRADUATION_URL},
    )
    assert r.status_code == 303
    concerts = await _all(client.db, Concert)
    assert [c.source_url for c in concerts] == [GRADUATION_URL]


async def test_commit_rejects_tampered_source_url(client):
    """_check_host pinned the *fetch* to ramen.events, but the URL round-trips
    through the client in a hidden field before coming back on the commit
    POST, so it is attacker-controlled and must be re-validated here."""
    login_as(client, EDITOR_ID, "reiji")
    r = client.post(
        "/concerts/import/commit",
        data={"title": "Tampered", "source_url": "javascript:alert(1)"},
    )
    assert r.status_code == 422
    assert await _all(client.db, Concert) == []


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
