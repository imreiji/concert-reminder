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
from app.db.models import Base, Concert, ConcertDay, ConcertTag, Round, Tag, TagKind
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
    assert client.get("/concerts/import").status_code == 303
    assert client.post("/concerts/import/preview", data={"url": GRADUATION_URL}).status_code == 303
    assert client.post("/concerts/import/commit", data={"title": "X"}).status_code == 303


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


def test_preview_language_chip_targets_import_form(client):
    """The preview (and its parse-failure re-render) is served from POST-only
    /concerts/import/preview, so the header language form must aim its `next`
    at GET-able /concerts/import instead of request.url.path."""
    login_as(client, EDITOR_ID, "reiji")
    mock_fetch(client, load("ramen_graduation_concert.html"))
    r = client.post("/concerts/import/preview", data={"url": GRADUATION_URL})
    assert 'name="next" value="/concerts/import"' in r.text
    mock_fetch(client, "<html><body>not an event</body></html>")
    r = client.post("/concerts/import/preview", data={"url": GRADUATION_URL})
    assert 'name="next" value="/concerts/import"' in r.text


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
            "round_label_zh": ["Day 1 Lottery", "Day 2 Lottery"],
            "round_label_en": ["Day 1 Lottery", "Day 2 Lottery"],
            "day_label_zh": ["Day 1", "Day 2"], "day_label_en": ["Day 1", "Day 2"],
            "title": "Hasunosora 103rd Class Graduation Concert",
            "title_en": "Hasunosora 103rd Class Graduation Concert",
            "title_zh": "莲之空103期毕业演唱会",
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
            "round_label_zh": ["Nationwide lottery"], "round_label_en": ["Nationwide lottery"],
            "day_label_zh": ["Osaka", "Tokyo"], "day_label_en": ["Osaka", "Tokyo"],
            "title": "Two Leg Tour", "title_en": "Two Leg Tour", "title_zh": "两地巡演",
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
            "round_label_zh": ["Fan club presale"], "round_label_en": ["Fan club presale"],
            "day_label_zh": ["Day 1"], "day_label_en": ["Day 1"], "title": "Whole Event Round",
            "title_en": "Whole Event Round", "title_zh": "全场轮次",
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
            "title": "Some Concert", "title_en": "Some Concert", "title_zh": "某场演出",
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
    # Both variants, not just EN: import_commit holds the title to the same
    # all-three rule create_concert does, so a form offering only title_en
    # would be unsubmittable the moment an editor typed an English title.
    assert 'name="title_zh"' in r.text
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
            "title_zh": "详细演出（中文）",
            "organizer": "Some Organizer",
            "kind": "tour",
            # All three notes variants: import_commit holds Notes to the same
            # all-or-nothing rule create_concert does, so a Japanese-only note
            # is a 422 here now (see test_variant_enforcement.py).
            "notes": "spotted on ramen.events",
            "notes_en": "spotted on ramen.events (EN)",
            "notes_zh": "在 ramen.events 上发现",
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
    assert c.notes_en == "spotted on ramen.events (EN)"
    assert c.notes_zh == "在 ramen.events 上发现"


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
              "title_en": "Hasunosora 103rd Class Graduation Concert",
              "title_zh": "莲之空103期毕业演唱会",
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


# ── Per-leg venue picker ─────────────────────────────────────────────────
#
# The import path used to emit free-text venue only, so every imported
# concert landed with venue_tag_id NULL on every leg -- and since a
# concert's VENUE tags are DERIVED from its legs (sync_concert_venue_tags),
# the rollup computed an empty set and the concert showed no venue anywhere
# and was invisible to Discover's region filter. These cover the picker that
# closes that gap.


async def _venue_tag(db, name: str, **kw) -> int:
    async with db() as s:
        tag = Tag(name=name, kind=TagKind.VENUE, **kw)
        s.add(tag)
        await s.commit()
        return tag.id


async def test_commit_with_venue_tag_rolls_up_to_concert_venue_tags(client):
    """The actual bug, end to end: a leg carrying a VENUE tag must produce a
    concert-level VENUE tag via sync_concert_venue_tags."""
    login_as(client, EDITOR_ID, "reiji")
    venue_id = await _venue_tag(client.db, "Nippon Budokan", region="Kanto")

    r = client.post(
        "/concerts/import/commit",
        data={
            "round_label_zh": ["Lottery"], "round_label_en": ["Lottery"], "day_label_zh": ["Day 1"],
            "day_label_en": ["Day 1"], "title": "Budokan Show",
            "title_en": "Budokan Show", "title_zh": "武道馆演出",
            "day_key": ["d0"],
            "day_label": ["Day 1"],
            "day_starts_at": ["2027-01-23T17:00"],
            "day_venue_tag_id": [str(venue_id)],
            "round_label": ["Lottery"],
            "round_kind": ["lottery_round"],
            "round_opens_at": ["2026-10-14T12:00"],
            "round_closes_at": ["2026-11-08T23:59"],
            "round_results_at": [""],
            "round_payment_at": [""],
            "round_url": [""],
        },
    )
    assert r.status_code == 303

    days = await _all(client.db, ConcertDay)
    assert [d.venue_tag_id for d in days] == [venue_id]

    async with client.db() as s:
        concert = (await s.execute(select(Concert))).scalars().one()
        rows = list(
            (
                await s.execute(
                    select(ConcertTag).where(ConcertTag.concert_id == concert.id)
                )
            ).scalars()
        )
    assert venue_id in {row.tag_id for row in rows}


async def test_commit_without_any_venue_tag_field_still_works(client):
    """The minimal-client contract: a submitter that omits day_venue_tag_id
    ENTIRELY is padded to blanks, not 422'd by the strict zip."""
    login_as(client, EDITOR_ID, "reiji")
    r = client.post(
        "/concerts/import/commit",
        data={
            "round_label_zh": ["Lottery"], "round_label_en": ["Lottery"],
            "day_label_zh": ["Day 1", "Day 2"], "day_label_en": ["Day 1", "Day 2"],
            "title": "No Venue Show", "title_en": "No Venue Show", "title_zh": "无场馆演出",
            "day_label": ["Day 1", "Day 2"],
            "day_starts_at": ["2027-01-23T17:00", "2027-01-24T15:30"],
            "round_label": ["Lottery"],
            "round_kind": ["lottery_round"],
            "round_opens_at": ["2026-10-14T12:00"],
            "round_closes_at": ["2026-11-08T23:59"],
            "round_results_at": [""],
            "round_payment_at": [""],
            "round_url": [""],
        },
    )
    assert r.status_code == 303
    days = await _all(client.db, ConcertDay)
    assert [d.venue_tag_id for d in days] == [None, None]


async def test_preview_renders_a_venue_select_on_leg_and_template_rows(client):
    login_as(client, EDITOR_ID, "reiji")
    await _venue_tag(client.db, "Nippon Budokan")
    mock_fetch(client, load("ramen_graduation_concert.html"))
    body = client.post("/concerts/import/preview", data={"url": GRADUATION_URL}).text

    # One select per parsed leg AND one in the blank-row <template>.
    assert body.count('name="day_venue_tag_id"') >= 2
    assert "data-venue-select" in body
    assert "data-new-venue" in body
    # The inline create dialog rides along so a missing venue can be minted.
    assert 'id="venue-create"' in body
    # Never value="0" -- resolve_day_venue_tags 422s on it.
    assert 'value="0"' not in body


async def test_preview_preselects_a_matching_venue_tag(client):
    login_as(client, EDITOR_ID, "reiji")
    venue_id = await _venue_tag(client.db, "Nippon Budokan")
    mock_fetch(client, load("ramen_graduation_concert.html"))
    body = client.post("/concerts/import/preview", data={"url": GRADUATION_URL}).text

    assert f'value="{venue_id}" selected' in body


async def test_preview_blank_template_row_never_preselects_a_venue(client):
    """A cloned trailing row carrying a venue but no start time reaches
    validation and 422s -- the template's select must stay on "" ."""
    login_as(client, EDITOR_ID, "reiji")
    venue_id = await _venue_tag(client.db, "Nippon Budokan")
    mock_fetch(client, load("ramen_graduation_concert.html"))
    body = client.post("/concerts/import/preview", data={"url": GRADUATION_URL}).text

    template = body.split('<template id="day-row-template">', 1)[1].split("</template>", 1)[0]
    assert f'value="{venue_id}" selected' not in template
    assert 'value=""' in template


async def test_preview_without_a_matching_tag_leaves_select_empty_and_hints(client):
    login_as(client, EDITOR_ID, "reiji")
    await _venue_tag(client.db, "Saitama Super Arena")
    mock_fetch(client, load("ramen_graduation_concert.html"))
    body = client.post("/concerts/import/preview", data={"url": GRADUATION_URL}).text

    assert "selected" not in body.split('name="day_venue_tag_id"', 1)[1].split("</select>", 1)[0]
    # The scraped name still reaches the editor: the hint plus the free text.
    assert "Nippon Budokan" in body


async def test_preview_venue_hint_appears_once_for_multi_leg_import(client):
    """venue_hint/matched_venue_tag_id are computed once per parse (a single
    scraped venue_name for the whole event), not once per leg -- so the hint
    must render exactly once even though the graduation fixture parses to
    two days/legs. Regression guard for the hint being duplicated per leg."""
    login_as(client, EDITOR_ID, "reiji")
    await _venue_tag(client.db, "Saitama Super Arena")
    mock_fetch(client, load("ramen_graduation_concert.html"))
    body = client.post("/concerts/import/preview", data={"url": GRADUATION_URL}).text

    assert body.count("No venue tag matches") == 1


@pytest.mark.parametrize(
    "tag_name, scraped",
    [
        ("Nippon Budokan", "nippon budokan"),
        ("Nippon Budokan", "  Nippon Budokan  "),
        ("日本武道館", "　日本武道館　"),  # U+3000 ideographic space
        ("日本武道館", "日本武道館　"),
    ],
)
async def test_venue_match_is_case_and_whitespace_insensitive(db, tag_name, scraped):
    """Trimming must handle U+3000, not just U+0020 -- venue text pasted from
    Japanese sites carries it, and that exact mismatch bit the earlier
    migration."""
    from app.db.service import match_venue_tag_id

    async with db() as s:
        s.add(Tag(name=tag_name, kind=TagKind.VENUE))
        await s.commit()
        tags = list((await s.execute(select(Tag))).scalars())
    assert match_venue_tag_id(scraped, tags) is not None


async def test_venue_match_returns_none_when_nothing_matches(db):
    from app.db.service import match_venue_tag_id

    async with db() as s:
        s.add(Tag(name="Nippon Budokan", kind=TagKind.VENUE))
        await s.commit()
        tags = list((await s.execute(select(Tag))).scalars())
    assert match_venue_tag_id("Saitama Super Arena", tags) is None
    assert match_venue_tag_id(None, tags) is None
    assert match_venue_tag_id("   ", tags) is None
