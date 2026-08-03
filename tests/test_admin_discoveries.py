"""The discovery review surface: admin-only, and it writes only dismissals."""

import datetime as dt
import re
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.calendars import CalendarFeed
from app.config import settings
from app.db.models import Base, Concert, ConcertDay, DiscoveredEvent, DiscoveryState, Tag
from app.db.service import PlannedDismissal, PrunePlan, apply_prune, plan_prune
from app.db.session import get_session
from app.discovery import DiscoveryFetchError
from app.domain.prune_list import PruneEntry, PruneList
from app.domain.types import DismissReason, TagKind
from app.web import auth
from app.web.app import create_app
from app.web.routes import discoveries

ADMIN_ID, EDITOR_ID = 42, 77
NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
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
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
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


def login_as(client, discord_id, name):
    async def fake_identity(token):
        return {"id": str(discord_id), "username": name, "global_name": name, "avatar": None}

    client.monkeypatch.setattr(auth, "fetch_identity", fake_identity)
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    client.get(f"/auth/callback?code=x&state={state}")


async def _seed(client, **overrides):
    fields = dict(
        source_event_id="464372", title="Anniversary Day 2",
        event_date=dt.date(2026, 11, 15), venue="Zepp Haneda",
        first_seen_at=NOW, last_seen_at=NOW,
    )
    fields.update(overrides)
    async with client.db() as s:
        s.add(DiscoveredEvent(**fields))
        await s.commit()


async def _lead_id(client):
    async with client.db() as s:
        return (await s.execute(select(DiscoveredEvent))).scalar_one().id


async def _lead_by_event_id(client, source_event_id):
    """Look a seeded row up by its EXTERNAL id, for tests that seed more than
    one lead and so cannot use `_lead_id`'s scalar_one()."""
    async with client.db() as s:
        return (await s.execute(
            select(DiscoveredEvent).where(
                DiscoveredEvent.source_event_id == source_event_id
            )
        )).scalar_one()


# ── Access ───────────────────────────────────────────────────────────────


async def test_an_editor_cannot_reach_it(client):
    """An editor is signed in and unauthorized, which IS an error (invariant 5).
    The editor whitelist is set, so this fails for the admin check specifically
    and not merely for being a stranger."""
    login_as(client, EDITOR_ID, "editor")
    assert client.get("/admin/discoveries").status_code == 403


async def test_an_editor_cannot_dismiss(client):
    """The write half is guarded too -- a page that only hides the button is
    not access control."""
    await _seed(client)
    lead_id = await _lead_id(client)
    login_as(client, EDITOR_ID, "editor")
    assert client.post(f"/admin/discoveries/{lead_id}/dismiss").status_code == 403
    async with client.db() as s:
        row = (await s.execute(select(DiscoveredEvent))).scalar_one()
        assert row.dismissed_at is None, "the refused post wrote nothing"


async def test_signed_out_is_redirected_not_403(client):
    """Being signed out is not an error (invariant 5)."""
    r = client.get("/admin/discoveries")
    assert r.status_code == 303
    assert r.headers["location"].startswith("/?next=")


# ── The page ─────────────────────────────────────────────────────────────


async def test_the_admin_sees_open_leads(client):
    await _seed(client)
    login_as(client, ADMIN_ID, "reiji")
    r = client.get("/admin/discoveries")
    assert r.status_code == 200
    body = r.text
    assert "Anniversary Day 2" in body
    assert "/events/464372" in body


async def test_the_page_renders_with_nothing_to_show(client):
    """The empty queue is the steady state, and the template must survive it --
    build_discovery_dm returns "" for no leads, so the copy block has to be
    conditional."""
    login_as(client, ADMIN_ID, "reiji")
    r = client.get("/admin/discoveries")
    assert r.status_code == 200
    assert "Nothing waiting" in r.text


async def test_the_page_names_the_artist_that_surfaced_it(client):
    async with client.db() as s:
        tag = Tag(name="Liella!", kind=TagKind.GROUP, slug="liella")
        s.add(tag)
        await s.flush()
        tag_id = tag.id
        await s.commit()
    await _seed(client, first_seen_via_tag_id=tag_id)
    login_as(client, ADMIN_ID, "reiji")
    assert "Liella!" in client.get("/admin/discoveries").text


async def test_a_lead_landing_on_an_existing_leg_is_marked(client):
    """The hint, never a suppression: same JST date, same venue name. It has to
    stay in the list AND say so."""
    async with client.db() as s:
        venue = Tag(name="Zepp Haneda", kind=TagKind.VENUE, slug="zepp-haneda")
        s.add_all([Concert(title="t", event_id="c1"), venue])
        await s.flush()
        # 2026-11-14 16:00 UTC is 2026-11-15 in JST -- the lead's date.
        s.add(ConcertDay(
            concert_id=1, label="昼公演", venue_tag_id=venue.id,
            starts_at_utc=datetime(2026, 11, 14, 16, 0, tzinfo=UTC),
        ))
        await s.commit()
    await _seed(client)
    login_as(client, ADMIN_ID, "reiji")
    body = client.get("/admin/discoveries").text
    assert "Anniversary Day 2" in body, "a hint never removes a lead"
    assert "May already have this" in body


async def test_a_lead_elsewhere_is_not_marked(client):
    """The control for the test above: without the collision the same page
    carries no mark, so that assertion is about the hint and not about the
    template always printing the words."""
    async with client.db() as s:
        venue = Tag(name="Nippon Budokan", kind=TagKind.VENUE, slug="budokan")
        s.add_all([Concert(title="t", event_id="c1"), venue])
        await s.flush()
        s.add(ConcertDay(
            concert_id=1, label="Day 1", venue_tag_id=venue.id,
            starts_at_utc=datetime(2026, 11, 14, 16, 0, tzinfo=UTC),
        ))
        await s.commit()
    await _seed(client)
    login_as(client, ADMIN_ID, "reiji")
    body = client.get("/admin/discoveries").text
    assert "Anniversary Day 2" in body
    assert "May already have this" not in body


async def test_an_announced_lead_says_when_it_was_announced(client):
    """announced_at is surfaced rather than left write-only: on a first-sweep
    backlog it is what separates "an earlier sweep already reported this" from
    "this arrived today". It does NOT mean the DM described the lead --
    mark_leads_announced stamps every fresh row, including the ones the DM only
    counted in its "+N more" -- and the page copy says so."""
    await _seed(client, announced_at=datetime(2026, 8, 3, 9, 0, tzinfo=UTC))
    login_as(client, ADMIN_ID, "reiji")
    assert "2026-08-03" in client.get("/admin/discoveries").text


async def test_a_calendar_deadline_lead_renders_the_marker_and_feed_label(client):
    """A calendar row's date is an APPLICATION DEADLINE for a deadline feed --
    it must render 申込締切, and its "via" cell must show the FEED's own
    label rather than a blank artist name (a feed is not a subscription, so
    `first_seen_via_tag_id` is NULL for these rows)."""
    client.monkeypatch.setattr(discoveries, "CALENDAR_FEEDS", (
        CalendarFeed(
            key="test-feed", label="Test Feed",
            url="https://calendar.google.com/x.ics", dates_are="deadline",
        ),
    ))
    await _seed(
        client, source_event_id="test-feed:abc123", source="test-feed",
        date_is_deadline=True, title="Deadline Show",
        event_date=dt.date(2026, 11, 20),
    )
    login_as(client, ADMIN_ID, "reiji")
    body = client.get("/admin/discoveries").text
    assert "申込締切" in body
    assert "Test Feed" in body
    assert "Deadline Show" in body


async def test_a_calendar_lead_with_an_unknown_feed_key_falls_back_to_the_raw_key(client):
    """The feed table is code-level config and can shrink -- a lead whose
    feed was later removed must still show SOMETHING in the via cell rather
    than a blank, so it falls back to the raw source key."""
    client.monkeypatch.setattr(discoveries, "CALENDAR_FEEDS", ())
    await _seed(client, source_event_id="gone-feed:xyz", source="gone-feed")
    login_as(client, ADMIN_ID, "reiji")
    body = client.get("/admin/discoveries").text
    assert "gone-feed" in body


async def test_a_calendar_lead_renders_no_eventernote_link(client):
    """A calendar lead has no Eventernote page -- its source_event_id is a
    namespaced "<feed key>:<uid>", not an Eventernote numeric id, and a link
    built from it would resolve nowhere real."""
    await _seed(client, source_event_id="test-feed:abc123", source="test-feed")
    login_as(client, ADMIN_ID, "reiji")
    body = client.get("/admin/discoveries").text
    assert "https://www.eventernote.com/events/test-feed:abc123" not in body


async def test_an_eventernote_lead_still_links_and_renders_as_before(client):
    """Pins the pre-existing shape: an Eventernote row's link and plain date
    must not change now that calendar rows share this table."""
    await _seed(client)
    login_as(client, ADMIN_ID, "reiji")
    body = client.get("/admin/discoveries").text
    assert 'href="https://www.eventernote.com/events/464372"' in body
    assert "申込締切" not in body


async def test_a_lead_never_announced_says_so(client):
    """The control: the date above comes from the column, not from the page
    printing a timestamp regardless."""
    await _seed(client)
    login_as(client, ADMIN_ID, "reiji")
    body = client.get("/admin/discoveries").text
    assert "2026-08-03" not in body
    assert "Not yet" in body


# ── The last sweep ───────────────────────────────────────────────────────


async def test_the_page_reports_a_sweep_that_failed_fetches(client):
    """A broken parser, a blocked IP and a quiet day all render the same empty
    table. The counts are the only thing that tells them apart, so they are on
    the page and not only in journalctl."""
    async with client.db() as s:
        s.add(DiscoveryState(id=1, last_run_at=NOW, last_fetched=74, last_failed=12))
        await s.commit()
    login_as(client, ADMIN_ID, "reiji")
    body = client.get("/admin/discoveries").text
    assert "74 fetched" in body
    assert "12 failed" in body
    assert "2026-07-31 12:00" in body


async def test_the_page_says_when_a_sweep_ran_out_of_time(client):
    """At the shipped budget a full sweep does NOT fit, so truncation is the
    steady state rather than a rare third case. Folded into the clean branch it
    would read "47 fetched, no failures" every single day -- the reassuring
    line, on the page built so a partial sweep would be visible."""
    async with client.db() as s:
        s.add(DiscoveryState(
            id=1, last_run_at=NOW, last_fetched=47, last_failed=0,
            last_truncated=True, sweep_cursor_tag_id=52,
        ))
        await s.commit()
    login_as(client, ADMIN_ID, "reiji")
    body = client.get("/admin/discoveries").text
    assert "stopped at its time budget" in body
    assert "47 fetched" in body
    assert "no failures" not in body, "a truncated sweep is not a clean one"


async def test_a_clean_sweep_does_not_cry_wolf(client):
    """The control: the same line with no failures must not use the
    needs-attention shape, or the one that matters stops being noticed."""
    async with client.db() as s:
        s.add(DiscoveryState(id=1, last_run_at=NOW, last_fetched=86, last_failed=0))
        await s.commit()
    login_as(client, ADMIN_ID, "reiji")
    body = client.get("/admin/discoveries").text
    assert "86 fetched, no failures" in body
    assert "failed." not in body


async def test_a_sweep_with_no_counts_says_it_did_not_finish(client):
    """NULL is UNKNOWN, not zero: the scheduler re-stamps a sweep that raised
    with no report at all. Rendering that as "0 fetched, 0 failed" would be a
    number the app never measured."""
    async with client.db() as s:
        s.add(DiscoveryState(id=1, last_run_at=NOW))
        await s.commit()
    login_as(client, ADMIN_ID, "reiji")
    body = client.get("/admin/discoveries").text
    assert "did not finish" in body
    assert "0 fetched" not in body


async def test_a_page_with_no_sweep_yet_says_so(client):
    """Before the first sweep there is no DiscoveryState row at all, and the
    page must not present that as a sweep that found nothing."""
    login_as(client, ADMIN_ID, "reiji")
    body = client.get("/admin/discoveries").text
    assert "has not run yet" in body


# ── The copy block ───────────────────────────────────────────────────────


async def test_the_page_offers_the_copy_block(client):
    await _seed(client)
    login_as(client, ADMIN_ID, "reiji")
    body = client.get("/admin/discoveries").text
    assert "add-concert" in body


async def test_the_copy_block_holds_every_lead(client):
    """The DM is capped at DM_LIST_LIMIT because Discord has a character
    budget; this page does not, so it passes all of them and no truncation
    notice appears."""
    async with client.db() as s:
        for n in range(14):
            s.add(DiscoveredEvent(
                source_event_id=f"90{n:02d}", title=f"Show {n}",
                event_date=dt.date(2026, 11, 1) + dt.timedelta(days=n),
                venue="Zepp Haneda", first_seen_at=NOW, last_seen_at=NOW,
            ))
        await s.commit()
    login_as(client, ADMIN_ID, "reiji")
    body = client.get("/admin/discoveries").text
    for n in range(14):
        assert f"https://www.eventernote.com/events/90{n:02d}" in body
    assert "more not shown" not in body


async def test_the_copy_button_reads_a_data_attribute(client):
    """Invariant 7: never interpolate user-controlled text into an inline on*
    handler -- the browser HTML-decodes the attribute before parsing it as JS,
    so Jinja's escaping does not protect you."""
    await _seed(client, title="it's a <script> \"party\"")
    login_as(client, ADMIN_ID, "reiji")
    body = client.get("/admin/discoveries").text
    assert 'onclick="navigator.clipboard.writeText(this.dataset.copy)"' in body
    handlers = [line for line in body.splitlines() if "onclick" in line]
    assert handlers, "the copy button is on the page"
    for line in handlers:
        assert "party" not in line, "the lead's text never reaches an on* handler"


# ── Dismissal ────────────────────────────────────────────────────────────


async def test_dismissing_removes_it_from_the_list(client):
    await _seed(client)
    login_as(client, ADMIN_ID, "reiji")
    lead_id = await _lead_id(client)
    r = client.post(f"/admin/discoveries/{lead_id}/dismiss", data={"reason": "other"})
    assert r.status_code == 303
    assert "Anniversary Day 2" not in client.get("/admin/discoveries").text
    async with client.db() as s:
        row = (await s.execute(select(DiscoveredEvent))).scalar_one()
        assert row.dismissed_at is not None, "dismissed, never deleted"


async def test_dismissing_an_unknown_lead_is_a_404(client):
    login_as(client, ADMIN_ID, "reiji")
    r = client.post("/admin/discoveries/999/dismiss", data={"reason": "other"})
    assert r.status_code == 404


async def test_dismissing_twice_is_a_404(client):
    """dismiss_lead answers False for an already-dismissed row, and the route
    must report the write that did not happen rather than a cheerful 303."""
    await _seed(client)
    login_as(client, ADMIN_ID, "reiji")
    lead_id = await _lead_id(client)
    client.post(f"/admin/discoveries/{lead_id}/dismiss", data={"reason": "other"})
    r = client.post(f"/admin/discoveries/{lead_id}/dismiss", data={"reason": "other"})
    assert r.status_code == 404


# ── The admin index ──────────────────────────────────────────────────────


async def test_preferences_links_it_for_an_admin(client):
    login_as(client, ADMIN_ID, "reiji")
    assert "/admin/discoveries" in client.get("/preferences").text


async def test_preferences_does_not_link_it_for_an_editor(client):
    """The admin-tools section is admin-only; an editor seeing the link would
    only get a 403."""
    login_as(client, EDITOR_ID, "editor")
    assert "/admin/discoveries" not in client.get("/preferences").text


# -- Sweep now ------------------------------------------------------------


async def _state(client):
    async with client.db() as s:
        return (await s.execute(select(DiscoveryState))).scalar_one_or_none()


def _sweep_button(body):
    """The one line carrying the Sweep now button, so `disabled` is asserted
    about THAT control and not about anything else the page may disable."""
    lines = [ln for ln in body.splitlines() if ">Sweep now<" in ln]
    assert len(lines) == 1, f"expected exactly one Sweep now button, got {len(lines)}"
    return lines[0]


async def test_an_editor_cannot_request_a_sweep(client):
    """Admin-only on the write half too -- the sweep hits a third party 86
    times, which is not an editor's decision to make."""
    login_as(client, EDITOR_ID, "editor")
    assert client.post("/admin/discoveries/sweep").status_code == 403
    assert await _state(client) is None, "the refused post wrote nothing"


async def test_signed_out_cannot_request_a_sweep(client):
    """Signed out is a redirect, not a 403 (invariant 5) -- and still no write."""
    assert client.post("/admin/discoveries/sweep").status_code == 303
    assert await _state(client) is None


async def test_the_admin_requests_a_sweep(client):
    """The button REQUESTS; it never runs the sweep. A sweep is bounded at four
    minutes, so what the route may do is write a row and redirect."""
    login_as(client, ADMIN_ID, "reiji")
    r = client.post("/admin/discoveries/sweep")
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/discoveries"
    state = await _state(client)
    assert state is not None and state.sweep_requested_at is not None


async def test_requesting_a_sweep_leaves_the_daily_clock_alone(client):
    """A request is not a run. Touching last_run_at here would report a sweep
    that has not happened and would suppress the daily one for 24h."""
    async with client.db() as s:
        s.add(DiscoveryState(id=1, last_run_at=NOW, last_fetched=86, last_failed=0))
        await s.commit()
    login_as(client, ADMIN_ID, "reiji")
    client.post("/admin/discoveries/sweep")
    state = await _state(client)
    assert state.last_run_at == NOW and state.last_fetched == 86


async def test_the_page_says_a_request_is_pending(client):
    """The POST returns instantly, so without this line a button that worked
    looks exactly like one that did nothing -- and gets pressed again."""
    login_as(client, ADMIN_ID, "reiji")
    client.post("/admin/discoveries/sweep")
    body = client.get("/admin/discoveries").text
    assert "Sweep requested" in body
    assert "disabled" in _sweep_button(body), "a second press cannot hurry the tick"


async def test_the_page_offers_the_button_when_nothing_is_pending(client):
    """The control: the pending line and the disabled button come from the
    column, not from the template printing them unconditionally."""
    login_as(client, ADMIN_ID, "reiji")
    body = client.get("/admin/discoveries").text
    assert "Sweep requested" not in body
    assert "disabled" not in _sweep_button(body)


async def test_a_swept_request_leaves_the_button_pressable_again(client):
    """The page reads the live column, so the tick clearing the request is what
    re-enables the button -- no second piece of state to fall out of step."""
    login_as(client, ADMIN_ID, "reiji")
    client.post("/admin/discoveries/sweep")
    async with client.db() as s:
        state = (await s.execute(select(DiscoveryState))).scalar_one()
        state.sweep_requested_at = None  # what stamp_discovery_run does
        await s.commit()
    body = client.get("/admin/discoveries").text
    assert "Sweep requested" not in body
    assert "disabled" not in _sweep_button(body)


# -- Sweep one tag --------------------------------------------------------
#
# The per-tag button on the Tags page posts HERE, not to routes/tags.py: every
# discovery route is admin-only and a router registers whole. It runs INLINE --
# one fetch is bounded at FETCH_DEADLINE_SECONDS and is an ordinary length for
# a request, unlike the 240s full sweep the button above merely requests.

PAGE = """
<html><body>
<li><a href="/events/464372">Anniversary Day 2</a>
    <span>2026-11-15</span><a href="/places/1">Zepp Haneda</a></li>
</body></html>
"""


async def _artist(client, **overrides):
    """One artist tag with an actor URL. Returns its id."""
    fields = dict(
        name="Liyuu", kind=TagKind.ARTIST, slug="liyuu",
        eventernote_url="https://www.eventernote.com/actors/Liyuu/34637",
    )
    fields.update(overrides)
    async with client.db() as s:
        tag = Tag(**fields)
        s.add(tag)
        await s.flush()
        tag_id = tag.id
        await s.commit()
    return tag_id


def _with_fetcher(client, fetcher):
    """Run the REAL sweep_one_tag, stubbing only the network.

    Patched at the route module's own name because `fetcher` is a default
    argument bound at def time -- monkeypatching app.discovery.fetch_actor_events
    would do nothing at all, and the test would silently reach eventernote.com.
    """
    real = discoveries.sweep_one_tag

    async def patched(session, tag, now, **kwargs):
        return await real(session, tag, now, fetcher=fetcher)

    client.monkeypatch.setattr(discoveries, "sweep_one_tag", patched)


async def _fetch_page(url, transport=None):
    return PAGE


async def _leads(client):
    async with client.db() as s:
        return (await s.execute(select(DiscoveredEvent))).scalars().all()


async def test_an_editor_cannot_sweep_one_tag(client):
    """Admin-only, like every other discovery route: it hits a third party, and
    the Tags dialog it is reached from only needs an editor."""
    tag_id = await _artist(client)
    _with_fetcher(client, _fetch_page)
    login_as(client, EDITOR_ID, "editor")
    assert client.post(f"/admin/discoveries/sweep/{tag_id}").status_code == 403
    assert await _leads(client) == [], "the refused post wrote nothing"


async def test_signed_out_cannot_sweep_one_tag(client):
    """Signed out is a redirect, not a 403 (invariant 5) -- and still no write."""
    tag_id = await _artist(client)
    _with_fetcher(client, _fetch_page)
    assert client.post(f"/admin/discoveries/sweep/{tag_id}").status_code == 303
    assert await _leads(client) == []


async def test_the_admin_sweeps_one_tag_inline(client):
    """It runs in the request, unlike the full sweep: the leads exist by the
    time the redirect is issued."""
    tag_id = await _artist(client)
    _with_fetcher(client, _fetch_page)
    login_as(client, ADMIN_ID, "reiji")
    r = client.post(f"/admin/discoveries/sweep/{tag_id}")
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/discoveries?swept=ok&new=1"
    rows = await _leads(client)
    assert len(rows) == 1 and rows[0].source_event_id == "464372"


async def test_a_swept_tag_lands_on_the_page_that_shows_its_leads(client):
    """The redirect target is the point: the operator sees what the check
    found, not the Tags dialog they pressed it from."""
    tag_id = await _artist(client)
    _with_fetcher(client, _fetch_page)
    login_as(client, ADMIN_ID, "reiji")
    location = client.post(f"/admin/discoveries/sweep/{tag_id}").headers["location"]
    body = client.get(location).text
    assert "Anniversary Day 2" in body
    assert "1 new lead" in body


async def test_a_sweep_that_finds_nothing_says_so(client):
    """Nothing-new and could-not-reach are the two answers this button exists
    to tell apart, so the quiet one needs its own words."""
    tag_id = await _artist(client)

    async def empty(url, transport=None):
        return "<html></html>"

    _with_fetcher(client, empty)
    login_as(client, ADMIN_ID, "reiji")
    location = client.post(f"/admin/discoveries/sweep/{tag_id}").headers["location"]
    assert location == "/admin/discoveries?swept=ok&new=0"
    body = client.get(location).text
    assert "nothing the catalogue does not already have" in body
    assert "Could not read" not in body


async def test_a_failed_fetch_does_not_500_and_tells_the_operator(client):
    """Eventernote timed out on 12 of 86 fetches in a live run. A 500 here would
    be the common case, and a cheerful "0 new leads" would be a lie."""
    tag_id = await _artist(client)

    async def boom(url, transport=None):
        raise DiscoveryFetchError("timed out")

    _with_fetcher(client, boom)
    login_as(client, ADMIN_ID, "reiji")
    r = client.post(f"/admin/discoveries/sweep/{tag_id}")
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/discoveries?swept=failed&new=0"
    body = client.get(r.headers["location"]).text
    assert "Could not read that artist" in body
    assert "new lead" not in body


async def test_a_tag_with_no_eventernote_url_is_reported_not_swept(client):
    """The button is hidden without a URL, but the route is reachable anyway.
    Its own status, because the remedy is to fix the URL rather than retry."""
    tag_id = await _artist(client, eventernote_url=None)

    async def never(url, transport=None):
        raise AssertionError("should not fetch")

    _with_fetcher(client, never)
    login_as(client, ADMIN_ID, "reiji")
    r = client.post(f"/admin/discoveries/sweep/{tag_id}")
    assert r.headers["location"] == "/admin/discoveries?swept=no_actor&new=0"
    body = client.get(r.headers["location"]).text
    assert "not an actor page" in body
    # LOAD-BEARING COPY, not decoration. Checking navigates away and discards
    # unsaved dialog edits, so an operator who retypes the URL without saving
    # sweeps the OLD value and lands on this identical banner -- a loop with no
    # tell. Telling them to fix it without telling them to save is the bug.
    assert "and save" in body
    assert "discards unsaved edits" in body


async def test_sweeping_an_unknown_tag_is_a_404(client):
    """Following dismiss: a read that never happened must not report a
    cheerful 303."""
    login_as(client, ADMIN_ID, "reiji")
    assert client.post("/admin/discoveries/sweep/999").status_code == 404


async def test_a_one_tag_sweep_leaves_the_daily_clock_and_cursor_alone(client):
    """Through the route, not only the function: `last_run_at` would displace
    the day's automatic sweep of all 86, and the cursor would make the next full
    sweep skip artists it never read."""
    async with client.db() as s:
        s.add(DiscoveryState(
            id=1, last_run_at=NOW, last_fetched=86, last_failed=0,
            sweep_cursor_tag_id=52,
        ))
        await s.commit()
    tag_id = await _artist(client)
    _with_fetcher(client, _fetch_page)
    login_as(client, ADMIN_ID, "reiji")
    assert client.post(f"/admin/discoveries/sweep/{tag_id}").status_code == 303
    assert len(await _leads(client)) == 1, "it really did sweep"
    state = await _state(client)
    assert state.last_run_at == NOW and state.last_fetched == 86
    assert state.sweep_cursor_tag_id == 52


async def test_a_plain_visit_carries_no_sweep_banner(client):
    """The control for every banner above: they come from the query parameter,
    not from the template printing them unconditionally."""
    login_as(client, ADMIN_ID, "reiji")
    body = client.get("/admin/discoveries").text
    assert "Checked that artist" not in body
    assert "Could not read" not in body
    assert "not an actor page" not in body
    # Also the control for the no_actor banner's "and save" clause above: it
    # proves that assertion reads the banner rather than page furniture.
    assert "and save" not in body
    assert "discards unsaved edits" not in body


async def test_an_invented_swept_code_never_reaches_the_page(client):
    """`swept` is a CLOSED vocabulary, filtered to None in the route before the
    template ever sees it -- so a hand-typed value cannot select a branch, and
    cannot be echoed by a template that later grows an else. The URL of a page
    an operator lands on ends up in logs and history, which is why only codes
    ever ride in it."""
    login_as(client, ADMIN_ID, "reiji")
    # A sentinel rather than a <script> payload: the page legitimately carries
    # both the word "script" and SVG path data, so a substring check against
    # markup would pass or fail for reasons that have nothing to do with this.
    r = client.get("/admin/discoveries?swept=zzsentinelzz&new=9")
    assert r.status_code == 200
    assert "zzsentinelzz" not in r.text
    assert "Checked that artist" not in r.text


async def test_a_negative_lead_count_reads_as_none(client):
    """`new` is a plain integer off the URL. A crafted negative would otherwise
    render "-5 new leads" -- a number the app never counted."""
    login_as(client, ADMIN_ID, "reiji")
    body = client.get("/admin/discoveries?swept=ok&new=-5").text
    assert "nothing the catalogue does not already have" in body
    # "listed below" belongs to the count branch alone. A bare "-5" check would
    # collide with the SVG path data in the page's own icons.
    assert "listed below" not in body


# -- The Tags page button -------------------------------------------------


def _tags_dialog(client, tag_id):
    """The one tag dialog, so a button is asserted about THAT tag."""
    body = client.get("/tags").text
    start = body.find(f'id="tag-dialog-{tag_id}"')
    assert start != -1, "the dialog is on the page"
    return body[start:body.find("</dialog>", start)]


async def test_the_tags_page_offers_the_button_to_an_admin(client):
    tag_id = await _artist(client)
    login_as(client, ADMIN_ID, "reiji")
    assert f"/admin/discoveries/sweep/{tag_id}" in _tags_dialog(client, tag_id)


async def test_an_editor_does_not_see_the_button(client):
    """An additional narrowing, not a replacement: the dialog is editor-gated,
    the sweep is admin-only, and a visible button that 403s would be a lie."""
    tag_id = await _artist(client)
    login_as(client, EDITOR_ID, "editor")
    assert f"/admin/discoveries/sweep/{tag_id}" not in _tags_dialog(client, tag_id)


async def test_a_tag_with_no_eventernote_url_offers_no_button(client):
    """Nothing to sweep."""
    tag_id = await _artist(client, eventernote_url=None)
    login_as(client, ADMIN_ID, "reiji")
    assert f"/admin/discoveries/sweep/{tag_id}" not in _tags_dialog(client, tag_id)


async def test_a_venue_offers_no_button(client):
    """A venue has no actor page, and the URL column is shared across kinds --
    so the kind is a condition of its own, not an implication of the URL."""
    tag_id = await _artist(
        client, name="Zepp Haneda", kind=TagKind.VENUE, slug="zepp",
        eventernote_url="https://www.eventernote.com/actors/x/1",
    )
    login_as(client, ADMIN_ID, "reiji")
    assert f"/admin/discoveries/sweep/{tag_id}" not in _tags_dialog(client, tag_id)


# ── Dismissal reasons ────────────────────────────────────────────────────


async def test_dismiss_reason_defaults_to_null_and_takes_a_taxonomy_value(db):
    """NULL is a real state: a lead dismissed before reasons existed. It is
    never backfilled, exactly as subscriptions and leg opt-outs are not."""
    from app.domain.types import DismissReason

    async with db() as s:
        s.add(DiscoveredEvent(
            source_event_id="1", title="t", event_date=dt.date(2026, 9, 1),
        ))
        await s.commit()
        row = (await s.execute(select(DiscoveredEvent))).scalar_one()
        assert row.dismiss_reason is None

        row.dismiss_reason = DismissReason.RELEASE
        await s.commit()
        assert (await s.execute(select(DiscoveredEvent))).scalar_one().dismiss_reason == "release"


def test_dismiss_reason_covers_every_taxonomy_class():
    """Eight values, one per class in docs/discovery-lead-taxonomy-2026-08-01.md
    plus `other`. LIVE is present deliberately: a real concert you do not want to
    track is a dismissal, and without a value for it every such lead lands in
    `other` and destroys the agreement rate this column exists to measure."""
    from app.domain.types import DismissReason

    assert {r.value for r in DismissReason} == {
        "live", "stage", "release", "talk",
        "festival", "fanmeet", "free", "other",
    }


async def test_dismissing_records_the_reason(client):
    await _seed(client)
    login_as(client, ADMIN_ID, "reiji")
    lead_id = await _lead_id(client)
    r = client.post(f"/admin/discoveries/{lead_id}/dismiss", data={"reason": "release"})
    assert r.status_code == 303
    async with client.db() as s:
        row = await s.get(DiscoveredEvent, lead_id)
        assert row.dismissed_at is not None
        assert row.dismiss_reason == "release"


async def test_an_unknown_reason_is_422_and_writes_nothing(client):
    """The value reaches an enum, so a hand-posted body cannot invent a class
    and pollute the very column that exists to be counted."""
    await _seed(client)
    login_as(client, ADMIN_ID, "reiji")
    lead_id = await _lead_id(client)
    r = client.post(f"/admin/discoveries/{lead_id}/dismiss", data={"reason": "nonsense"})
    assert r.status_code == 422
    async with client.db() as s:
        row = await s.get(DiscoveredEvent, lead_id)
        assert row.dismissed_at is None, "a refused reason must not dismiss"
        assert row.dismiss_reason is None


async def test_a_missing_reason_is_422(client):
    await _seed(client)
    login_as(client, ADMIN_ID, "reiji")
    lead_id = await _lead_id(client)
    r = client.post(f"/admin/discoveries/{lead_id}/dismiss", data={})
    assert r.status_code == 422
    async with client.db() as s:
        assert (await s.get(DiscoveredEvent, lead_id)).dismissed_at is None


async def test_the_page_offers_a_reason_per_taxonomy_class(client):
    """Pins the NAME and the VALUEs together on the one control that matters:
    a page carrying `value="live"` etc. somewhere is meaningless unless it is
    inside a field literally named `reason` -- that is what the dismiss route
    actually reads (`Annotated[DismissReason, Form()]`). Deleting
    name="reason" from the template left this passing before, because it only
    ever grepped the page for bare `value="..."` substrings."""
    await _seed(client)
    login_as(client, ADMIN_ID, "reiji")
    body = client.get("/admin/discoveries").text
    select_start = body.find('<select name="reason"')
    assert select_start != -1, "the dismiss reason control must be named reason"
    select_html = body[select_start:body.find("</select>", select_start)]
    for value in ("live", "stage", "release", "talk",
                  "festival", "fanmeet", "free", "other"):
        assert f'value="{value}"' in select_html, f"no way to dismiss as {value}"


async def test_dismissing_uses_the_field_name_the_page_actually_renders(client):
    """The wiring, not the markup: this reads the dismiss control's `name`
    attribute straight out of the rendered page and posts through exactly
    that key, instead of assuming it is "reason" the way every other
    dismissal test in this file does. Those tests all hardcode the field
    name, so they stayed green even with name="reason" deleted from the
    template entirely -- while every dismiss on the real page 422s, since
    FastAPI never receives a `reason` field to bind. This test fails the
    same way that mutation does: the field's name comes from the page and is
    used verbatim, so a template that stops naming its control breaks here."""
    await _seed(client)
    login_as(client, ADMIN_ID, "reiji")
    lead_id = await _lead_id(client)
    body = client.get("/admin/discoveries").text
    form_start = body.find(f'action="/admin/discoveries/{lead_id}/dismiss"')
    assert form_start != -1, "the dismiss form is on the page"
    select_start = body.find("<select", form_start)
    select_open_end = body.find(">", select_start)
    name_match = re.search(r'name="([^"]+)"', body[select_start:select_open_end])
    assert name_match, "the reason control must carry a name attribute"
    field_name = name_match.group(1)
    r = client.post(
        f"/admin/discoveries/{lead_id}/dismiss", data={field_name: "other"}
    )
    assert r.status_code == 303
    async with client.db() as s:
        row = await s.get(DiscoveredEvent, lead_id)
        assert row.dismissed_at is not None
        assert row.dismiss_reason == "other"


async def test_dismissed_counts_are_shown_so_the_column_is_readable(client):
    """open_leads hides dismissed rows, so without this the reason is
    write-only and nothing can ever be scored against it."""
    await _seed(client, source_event_id="1001")
    await _seed(client, source_event_id="1002")
    await _seed(client, source_event_id="1003")
    login_as(client, ADMIN_ID, "reiji")
    async with client.db() as s:
        ids = (await s.execute(select(DiscoveredEvent.id))).scalars().all()
    for lead_id, reason in zip(ids, ("release", "release", "stage"), strict=True):
        client.post(f"/admin/discoveries/{lead_id}/dismiss", data={"reason": reason})
    body = client.get("/admin/discoveries").text
    assert "2 release" in body
    assert "1 stage" in body


async def test_counts_ignore_pre_reason_dismissals(client):
    """A NULL reason is a real state -- dismissed before reasons existed -- and
    must not be counted as `other`, which would invent a judgment nobody made.
    With nothing BUT a NULL-reason dismissal on file, dismissed_reason_counts
    is empty, so the entire "Dismissed so far" paragraph must not render --
    there is nothing yet worth reading back."""
    await _seed(client)
    login_as(client, ADMIN_ID, "reiji")
    lead_id = await _lead_id(client)
    async with client.db() as s:
        row = await s.get(DiscoveredEvent, lead_id)
        row.dismissed_at = datetime.now(UTC)
        await s.commit()
    body = client.get("/admin/discoveries").text
    assert "Dismissed so far" not in body


async def test_counts_ignore_a_pre_reason_dismissal_mixed_with_a_reasoned_one(client):
    """The case that actually matters, and the one the NULL-only test above
    cannot reach: a pre-migration dismissal (NULL reason) sitting alongside a
    reasoned one. With the NULL filter removed from dismissed_reason_counts,
    the counts dict comes back as {None: 1, "talk": 1} and Jinja's `dictsort`
    -- which the template uses to render the paragraph -- raises TypeError
    comparing None < "talk" while trying to order the keys: a 500 on the
    page, not merely a wrong number. A single NULL row has nothing to sort
    against, which is exactly why that case alone is not enough."""
    await _seed(client, source_event_id="2001")
    await _seed(client, source_event_id="2002")
    login_as(client, ADMIN_ID, "reiji")
    async with client.db() as s:
        ids = (await s.execute(select(DiscoveredEvent.id))).scalars().all()
    pre_migration_id, reasoned_id = ids
    async with client.db() as s:
        row = await s.get(DiscoveredEvent, pre_migration_id)
        row.dismissed_at = datetime.now(UTC)
        await s.commit()
    r = client.post(
        f"/admin/discoveries/{reasoned_id}/dismiss", data={"reason": "talk"}
    )
    assert r.status_code == 303
    body = client.get("/admin/discoveries").text
    assert "Dismissed so far" in body
    assert "1 talk" in body


async def test_the_button_never_nests_inside_the_edit_form(client):
    """A <form> inside a <form> is invalid HTML and silently breaks the outer
    one's submission -- so this button is a sibling, like Delete. Asserted by
    reading the dialog rather than by eye: the failure is invisible in a
    browser until Save stops working."""
    tag_id = await _artist(client)
    login_as(client, ADMIN_ID, "reiji")
    dialog = _tags_dialog(client, tag_id)
    edit_open = dialog.find(f'id="tag-edit-{tag_id}"')
    edit_close = dialog.find("</form>", edit_open)
    sweep = dialog.find(f"/admin/discoveries/sweep/{tag_id}")
    assert -1 < edit_open < edit_close < sweep, "the sweep form starts after the edit form ends"


# ── The plan builder (plan_prune / apply_prune) ─────────────────────────────


async def test_the_plan_sorts_leads_into_dismiss_unknown_and_already(client):
    """Three buckets, all shown. An unknown id is usually a stale file and an
    already-dismissed lead is usually a re-paste -- both are worth seeing, and
    neither should stop the rest."""
    await _seed(client, source_event_id="111")
    await _seed(
        client, source_event_id="222",
        dismissed_at=NOW, dismiss_reason=DismissReason.FREE,
    )
    prune = PruneList(
        entries=(
            PruneEntry(event_id="111", reason=DismissReason.STAGE),
            PruneEntry(event_id="222", reason=DismissReason.RELEASE),
            PruneEntry(event_id="999", reason=DismissReason.OTHER),
        ),
        warnings=(),
    )
    async with client.db() as s:
        plan = await plan_prune(s, prune)

    assert [d.event_id for d in plan.to_dismiss] == ["111"]
    assert [d.event_id for d in plan.already] == ["222"]
    assert plan.unknown == ("999",)

    # `already` carries BOTH reasons: what the file asked for, and what the
    # row already holds -- the disagreement is the whole point of showing
    # this bucket at all ("file says release, already dismissed as free").
    stuck = plan.already[0]
    assert stuck.reason == DismissReason.RELEASE
    assert stuck.stored_reason == DismissReason.FREE
    # A not-yet-dismissed lead has nothing stored yet.
    assert plan.to_dismiss[0].stored_reason is None


async def test_a_lead_already_bound_to_a_concert_is_not_dismissed(client):
    """`open_leads` filters `concert_id IS NULL` -- a lead that became a
    concert between classification and apply must not silently fall into
    `to_dismiss`: dismissing it would stamp a `dismiss_reason` on a row the
    catalogue already has, polluting `dismissed_reason_counts`, which exists
    to score the classifier against real human judgments, not against a
    stale file. It must land in its OWN bucket (`catalogued`), not `already`
    either -- a catalogued lead was never dismissed at all, so folding it
    into `already` would misreport what actually happened to it."""
    await _seed(client, source_event_id="111")
    async with client.db() as s:
        concert = Concert(title="t", event_id="c1")
        s.add(concert)
        await s.flush()
        lead = (await s.execute(select(DiscoveredEvent))).scalar_one()
        lead.concert_id = concert.id
        await s.commit()

    prune = PruneList(
        entries=(PruneEntry(event_id="111", reason=DismissReason.STAGE),),
        warnings=(),
    )
    async with client.db() as s:
        plan = await plan_prune(s, prune)

    assert plan.to_dismiss == ()
    assert plan.already == ()
    assert [d.event_id for d in plan.catalogued] == ["111"]


async def test_planning_writes_nothing(client):
    """Rendering a plan must leave the queue exactly as it found it. If
    plan_prune ever grew a write, this is the test that must fail."""
    await _seed(client, source_event_id="111")
    prune = PruneList(
        entries=(PruneEntry(event_id="111", reason=DismissReason.STAGE),),
        warnings=(),
    )
    async with client.db() as s:
        await plan_prune(s, prune)
        # No commit, no flush of a write -- but even a session that DID flush
        # a stray write would show it here, in the very same session.
        assert (await s.execute(select(DiscoveredEvent))).scalar_one().dismissed_at is None

    lead = await _lead_by_event_id(client, "111")
    assert lead.dismissed_at is None
    assert lead.dismiss_reason is None


async def test_apply_dismisses_with_each_lead_s_own_reason(client):
    """Two leads under different reasons in one file -- each row gets exactly
    the reason its own entry named, not the other's."""
    await _seed(client, source_event_id="111")
    await _seed(client, source_event_id="222")
    prune = PruneList(
        entries=(
            PruneEntry(event_id="111", reason=DismissReason.STAGE),
            PruneEntry(event_id="222", reason=DismissReason.RELEASE),
        ),
        warnings=(),
    )
    async with client.db() as s:
        plan = await plan_prune(s, prune)
        written = await apply_prune(s, plan, NOW)
        await s.commit()
    assert written == 2

    reasons = {
        eid: (await _lead_by_event_id(client, eid)).dismiss_reason
        for eid in ("111", "222")
    }
    assert reasons == {"111": "stage", "222": "release"}


async def test_apply_skips_the_already_dismissed_without_restamping(client):
    """The stale-plan race apply_prune's own docstring claims to guard: a plan
    gets built, someone dismisses the lead in the meantime, and apply runs
    against the now-stale plan. plan_prune would sort an already-dismissed
    lead into `already` and apply_prune's loop only ever walks `to_dismiss`
    -- so going through plan_prune here would never touch the loop this test
    is supposed to be pinning; it would only exercise dismiss_lead's own
    early return, one call removed. This builds the PrunePlan BY HAND with
    the already-dismissed lead placed directly in `to_dismiss`, which is
    exactly what a stale plan looks like, and is what actually drives
    apply_prune's `for planned in plan.to_dismiss` loop over this lead.

    The original reason and timestamp must survive -- a re-paste (or a race)
    is not a re-decision. Asserting only "it's still dismissed" would pass
    even against an implementation that overwrites both fields with the
    file's new say-so; both must be pinned to their ORIGINAL values.

    Confirmed by mutation: with dismiss_lead's `row.dismissed_at is not None`
    guard deleted, this test fails (apply_prune re-stamps the lead as
    "stage"), where the earlier plan_prune-routed version of this test passed
    even with that guard gone."""
    original_ts = datetime(2026, 7, 1, 0, 0, tzinfo=UTC)
    await _seed(
        client, source_event_id="111",
        dismissed_at=original_ts, dismiss_reason=DismissReason.FREE,
    )
    lead = await _lead_by_event_id(client, "111")
    plan = PrunePlan(
        to_dismiss=(
            PlannedDismissal(
                lead_id=lead.id, event_id="111", title=lead.title,
                event_date=lead.event_date, reason=DismissReason.STAGE,
                stored_reason=DismissReason.FREE,
            ),
        ),
        unknown=(),
        already=(),
    )
    async with client.db() as s:
        written = await apply_prune(s, plan, NOW)
        await s.commit()
    assert written == 0

    lead = await _lead_by_event_id(client, "111")
    assert lead.dismissed_at == original_ts
    assert lead.dismiss_reason == "free"


async def test_the_discoveries_page_links_the_prune_surface(client):
    """The link lives beside the copy block, which only renders when there is
    at least one open lead."""
    await _seed(client)
    login_as(client, ADMIN_ID, "reiji")
    assert "/admin/discoveries/prune" in client.get("/admin/discoveries").text


async def test_planning_a_batch_does_not_issue_a_query_per_entry(client):
    """The property: planning a 20-entry file does not issue 20 SELECTs.
    Mirrors the statement-count style pinned for my_deadline_blocks in
    tests/test_service.py."""
    async with client.db() as s:
        for n in range(20):
            s.add(DiscoveredEvent(
                source_event_id=f"5{n:03d}", title=f"Show {n}",
                event_date=dt.date(2026, 11, 1) + dt.timedelta(days=n),
                first_seen_at=NOW, last_seen_at=NOW,
            ))
        await s.commit()

    prune = PruneList(
        entries=tuple(
            PruneEntry(event_id=f"5{n:03d}", reason=DismissReason.OTHER)
            for n in range(20)
        ),
        warnings=(),
    )

    queries: list[str] = []

    def _count(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    async with client.db() as s:
        event.listen(s.bind.sync_engine, "before_cursor_execute", _count)
        try:
            plan = await plan_prune(s, prune)
        finally:
            event.remove(s.bind.sync_engine, "before_cursor_execute", _count)

    assert len(plan.to_dismiss) == 20
    assert len(queries) == 1, f"plan_prune ran {len(queries)} queries for 20 entries"


# ── The prune surface: paste, plan, apply ───────────────────────────────────


async def test_an_editor_cannot_reach_the_prune_page(client):
    """Signed in and unauthorized IS an error (invariant 5), and the write half
    is guarded too -- a page that only hides a form is not access control."""
    login_as(client, EDITOR_ID, "editor")
    assert client.get("/admin/discoveries/prune").status_code == 403
    assert client.post("/admin/discoveries/prune", data={"text": ""}).status_code == 403
    assert client.post("/admin/discoveries/prune/apply", data={"text": ""}).status_code == 403


async def test_signed_out_cannot_reach_the_prune_page(client):
    """Being signed out is not an error (invariant 5) -- and still no write."""
    assert client.get("/admin/discoveries/prune").status_code == 303
    assert client.post("/admin/discoveries/prune", data={"text": ""}).status_code == 303
    assert client.post("/admin/discoveries/prune/apply", data={"text": ""}).status_code == 303


async def test_the_plan_page_shows_all_four_buckets(client):
    """One file naming an open lead, an already-dismissed lead (under a
    DIFFERENT reason than it is stored under), a lead that has since become a
    concert, and an id matching nothing at all -- all four buckets must
    render, and the already-dismissed lead must show BOTH what the file says
    and what is actually stored, since that disagreement is the whole point
    of the stored_reason column.

    The "999" assertion is deliberately NOT a bare substring check: the
    pasted text is echoed twice elsewhere on this page (the hidden `text`
    input and the bottom textarea both contain the raw YAML, ids included),
    so `"999" in body` would pass even if the "Not in the queue" section
    rendered nothing at all -- confirmed by mutation, deleting that section's
    `<h2>` entirely and re-running this test still left it green before this
    fix. Asserting the rendered `<code>999</code>` fragment only comes from
    that section's own loop, never from the raw pasted text, so it actually
    proves the section renders."""
    await _seed(client, source_event_id="111", title="Open Lead")
    await _seed(
        client, source_event_id="222", title="Already Gone",
        dismissed_at=NOW, dismiss_reason=DismissReason.FREE,
    )
    async with client.db() as s:
        concert = Concert(title="t", event_id="c1")
        s.add(concert)
        await s.flush()
        concert_id = concert.id
        await s.commit()
    await _seed(
        client, source_event_id="333", title="Now A Concert",
        concert_id=concert_id,
    )
    login_as(client, ADMIN_ID, "reiji")
    text = (
        "dismiss:\n"
        "  stage:\n"
        "    - '111'\n"
        "  release:\n"
        "    - '222'\n"
        "    - '999'\n"
        "  live:\n"
        "    - '333'\n"
    )
    body = client.post("/admin/discoveries/prune", data={"text": text}).text
    assert "Open Lead" in body
    assert "Already Gone" in body
    assert "Now A Concert" in body
    assert "<code>999</code>" in body, \
        "the unknown-id fragment, not merely '999' anywhere on the page"
    assert "1 id(s) match no open lead" in body, "the section's own prose, not just the id"
    assert "1 lead(s) are now tracked concerts" in body, "the catalogued section's own prose"
    assert "Release event" in body, "what the file asked for, for the already-dismissed lead"
    assert "No ticket at all" in body, "what the row actually holds -- the disagreement"

    # Previewing writes nothing, for any of the four buckets.
    lead = await _lead_by_event_id(client, "111")
    assert lead.dismissed_at is None
    lead = await _lead_by_event_id(client, "222")
    assert lead.dismissed_at is not None and lead.dismiss_reason == "free", \
        "the already-dismissed row's own reason must survive a mere preview"
    lead = await _lead_by_event_id(client, "333")
    assert lead.dismissed_at is None, "a catalogued lead must never be dismissed"


async def test_the_unknown_bucket_prose_is_not_vacuously_satisfied_by_the_pasted_text(client):
    """The regression test for the mutation the review caught: this asserts
    the SAME "<code>999</code>" fragment `test_the_plan_page_shows_all_four_
    buckets` does, but isolates it from every other bucket so a future
    change cannot accidentally satisfy it from some other section's markup.
    A file naming ONLY an unknown id renders no `to_dismiss`, `already` or
    `catalogued` content at all -- if the fragment still appeared here, it
    could only be coming from the "Not in the queue" section itself, or from
    the raw pasted text echoed in the hidden input/textarea. The `<code>`
    wrapper rules out the latter: neither echo wraps the id in a `<code>`
    tag."""
    login_as(client, ADMIN_ID, "reiji")
    text = "dismiss:\n  other:\n    - '999'\n"
    body = client.post("/admin/discoveries/prune", data={"text": text}).text
    assert "<code>999</code>" in body
    assert "1 id(s) match no open lead" in body


async def test_previewing_writes_nothing(client):
    """The whole point of plan-before-apply. Post to the PLAN route and assert
    the lead is untouched."""
    await _seed(client, source_event_id="111")
    login_as(client, ADMIN_ID, "reiji")
    text = "dismiss:\n  stage:\n    - '111'\n"
    r = client.post("/admin/discoveries/prune", data={"text": text})
    assert r.status_code == 200
    assert "Anniversary Day 2" in r.text
    lead = await _lead_by_event_id(client, "111")
    assert lead.dismissed_at is None
    assert lead.dismiss_reason is None


async def test_a_bad_file_shows_the_error_and_writes_nothing(client):
    """Both the plan route and the apply route must refuse an unparseable
    file the same way _plan_or_error does for tags.yaml -- render the error,
    write nothing."""
    await _seed(client, source_event_id="111")
    login_as(client, ADMIN_ID, "reiji")
    bad = "dismiss: [unterminated"

    r = client.post("/admin/discoveries/prune", data={"text": bad})
    assert r.status_code == 200
    assert "could not be read" in r.text

    r = client.post("/admin/discoveries/prune/apply", data={"text": bad})
    assert r.status_code == 200
    assert "could not be read" in r.text

    lead = await _lead_by_event_id(client, "111")
    assert lead.dismissed_at is None


async def test_apply_dismisses_and_reports(client):
    """The straightforward success path: the reported count and the actual
    write agree."""
    await _seed(client, source_event_id="111", title="Lead A")
    login_as(client, ADMIN_ID, "reiji")
    text = "dismiss:\n  stage:\n    - '111'\n"
    r = client.post("/admin/discoveries/prune/apply", data={"text": text})
    assert r.status_code == 200
    assert "Dismissed 1 lead" in r.text
    lead = await _lead_by_event_id(client, "111")
    assert lead.dismissed_at is not None
    assert lead.dismiss_reason == "stage"


async def test_apply_does_not_reoffer_a_stale_plan(client):
    """After applying, the page must not re-render the PLAN it just acted
    on: the plan's lead ids are now gone, but the plan form still carried a
    live submit button pointed at /admin/discoveries/prune/apply. Pressing it
    again would re-apply against a stale plan and report "Dismissed 0
    leads", which reads exactly like the first press silently failed --
    when dismissal is PERMANENT and irreversible, that false signal is worse
    than useless. /admin/import/tags/apply avoids this by rendering
    `report=` instead of `plan=`; this route must do the same."""
    await _seed(client, source_event_id="111", title="Lead A")
    login_as(client, ADMIN_ID, "reiji")
    text = "dismiss:\n  stage:\n    - '111'\n"
    body = client.post("/admin/discoveries/prune/apply", data={"text": text}).text
    assert "Dismissed 1 lead" in body
    assert 'action="/admin/discoveries/prune/apply"' not in body, \
        "no submit button may point back at apply once this page has already applied"
    assert "Will dismiss" not in body, "the stale plan heading must not reappear"


async def test_the_plan_page_shows_parser_warnings(client):
    """`parse_prune_list`'s one warning -- an id listed twice under the same
    reason -- is the tell of a sloppy agent file and must not be silently
    dropped between parsing and rendering, the same way admin_import_tags.html
    surfaces its own parser's warnings."""
    await _seed(client, source_event_id="111")
    login_as(client, ADMIN_ID, "reiji")
    text = "dismiss:\n  stage:\n    - '111'\n    - '111'\n"
    body = client.post("/admin/discoveries/prune", data={"text": text}).text
    assert "listed twice under" in body
    assert "duplicate ignored" in body


async def test_a_prune_list_over_the_size_cap_is_refused(client):
    """Mirrors admin.py's tags.yaml cap: a paste this large is either a
    mistake or something worth a second look, not something this route
    should try to parse. 443 real leads is roughly 6KB, so the cap is not
    documented on the page itself -- nobody legitimate is expected to hit
    it, and a number nobody can reach is just noise in the copy."""
    await _seed(client, source_event_id="111")
    login_as(client, ADMIN_ID, "reiji")
    huge = "dismiss:\n  stage:\n" + ("    # padding\n" * 40_000)
    assert len(huge) > 200_000

    r = client.post("/admin/discoveries/prune", data={"text": huge})
    assert r.status_code == 200
    assert "too large" in r.text

    r = client.post("/admin/discoveries/prune/apply", data={"text": huge})
    assert r.status_code == 200
    assert "too large" in r.text

    lead = await _lead_by_event_id(client, "111")
    assert lead.dismissed_at is None


async def test_the_plan_page_shows_the_surfacing_artist_tag(client):
    """The single highest-value addition for making a batch of 300
    dismissals reviewable: which artist's Eventernote page surfaced each
    lead, the same information /admin/discoveries itself shows via
    `_artist_names`. Present across all three real-row buckets, not only
    `to_dismiss`."""
    async with client.db() as s:
        tag = Tag(name="Liella!", kind=TagKind.GROUP, slug="liella")
        s.add(tag)
        await s.flush()
        tag_id = tag.id
        await s.commit()
    await _seed(client, source_event_id="111", first_seen_via_tag_id=tag_id)
    login_as(client, ADMIN_ID, "reiji")
    text = "dismiss:\n  stage:\n    - '111'\n"
    body = client.post("/admin/discoveries/prune", data={"text": text}).text
    assert "Liella!" in body


async def test_a_calendar_lead_in_the_plan_shows_the_marker_and_feed_label_with_no_link(client):
    """The prune plan's "Will dismiss" section is a THIRD surface over the
    same DiscoveredEvent rows admin_discoveries.html and the DM digest
    render -- it must not build a broken Eventernote link from a calendar
    lead's namespaced "<feed key>:<uid>" id, and must show the feed's own
    label (there is no tag to look up) plus the 申込締切 marker on a
    deadline-dated row."""
    client.monkeypatch.setattr(discoveries, "CALENDAR_FEEDS", (
        CalendarFeed(
            key="test-feed", label="Test Feed",
            url="https://calendar.google.com/x.ics", dates_are="deadline",
        ),
    ))
    await _seed(
        client, source_event_id="test-feed:abc123", source="test-feed",
        date_is_deadline=True, title="Calendar Lead",
    )
    login_as(client, ADMIN_ID, "reiji")
    text = "dismiss:\n  stage:\n    - 'test-feed:abc123'\n"
    body = client.post("/admin/discoveries/prune", data={"text": text}).text
    assert "Calendar Lead" in body
    assert "申込締切" in body
    assert "Test Feed" in body
    assert "https://www.eventernote.com/events/test-feed:abc123" not in body


async def test_an_eventernote_lead_in_the_plan_renders_as_before(client):
    """Pins the pre-existing shape: an Eventernote lead's "Will dismiss" row
    must keep its real link and carry no deadline marker, now that a
    calendar lead can appear in the very same plan."""
    await _seed(client, source_event_id="111", title="Lead A")
    login_as(client, ADMIN_ID, "reiji")
    text = "dismiss:\n  stage:\n    - '111'\n"
    body = client.post("/admin/discoveries/prune", data={"text": text}).text
    assert 'href="https://www.eventernote.com/events/111"' in body
    assert "申込締切" not in body


async def test_the_plan_page_explains_how_to_exclude_a_lead(client):
    """The only way to keep a single misclassified lead out of the batch is
    to remove its id from the pasted file and preview again -- nothing else
    on the page offers a per-row skip, so the copy has to say so."""
    await _seed(client, source_event_id="111")
    login_as(client, ADMIN_ID, "reiji")
    body = client.get("/admin/discoveries/prune").text
    assert "remove its id from the pasted list" in body


async def test_apply_reparses_and_ignores_injected_ids(client):
    """The browser sends the FILE back, never a list of ids -- this is the
    property that makes the apply route safe to expose at all, so the test
    has to attack every plausible way a forged post could smuggle a second
    lead in, in ONE request:

    - `lead_id`, `event_id`, `dismiss`, `reason` -- plausible field names a
      naive implementation might read directly to decide what to dismiss.
      prune_apply reads none of them; only the bound `text` parameter is
      ever touched.
    - a REPEATED `text` field -- the forged occurrence goes FIRST and the
      real file naming lead A goes LAST. FastAPI resolves a scalar `Form`
      field via Starlette's `ImmutableMultiDict`, which is built from a dict
      comprehension that keeps the LAST value for a repeated key -- so an
      attacker trying to sneak an extra `text` occurrence past whichever one
      the server actually binds would place theirs first, hoping either
      order is read, or that both get merged. Ordering it this way means the
      test would catch a route that (a) bound the FIRST occurrence instead
      of the last, (b) read `request.form().getlist("text")` and looped over
      every value, or (c) concatenated them -- any of which would dismiss
      lead B, which the file itself never named.

    This is genuinely adversarial rather than vacuous specifically because
    of that ordering choice: a version of this test with the real text
    first and the forged one last would still pass today (nothing reads the
    other fields at all), but would NOT catch bug (a) above, since the
    winning `text` would happen to be the real one by accident of order
    rather than because the route re-parses correctly. Placing the legitimate
    file LAST forces the assertion to depend on the actual binding rule, not
    on which one a test author happened to type first.
    """
    await _seed(client, source_event_id="111", title="Lead A")
    await _seed(client, source_event_id="222", title="Lead B")
    lead_b_id = (await _lead_by_event_id(client, "222")).id
    login_as(client, ADMIN_ID, "reiji")

    forged_text = "dismiss:\n  live:\n    - '222'\n"
    real_text = "dismiss:\n  stage:\n    - '111'\n"
    # httpx's `data=` is a Mapping, not a list of pairs -- a repeated form key
    # is expressed by mapping it to a LIST, which `encode_urlencoded_data`
    # expands into one key=value pair per item, in list order. So `text` maps
    # to [forged, real] to put two `text` fields on the wire in that order.
    data = {
        "lead_id": str(lead_b_id),
        "event_id": "222",
        "dismiss": "222",
        "reason": "other",
        "text": [forged_text, real_text],
    }
    r = client.post("/admin/discoveries/prune/apply", data=data)
    assert r.status_code == 200

    lead_a = await _lead_by_event_id(client, "111")
    lead_b = await _lead_by_event_id(client, "222")
    assert lead_a.dismissed_at is not None, "the file's own entry was applied"
    assert lead_a.dismiss_reason == "stage"
    assert lead_b.dismissed_at is None, "no other field may select a lead to dismiss"
