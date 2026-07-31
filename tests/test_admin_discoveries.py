"""The discovery review surface: admin-only, and it writes only dismissals."""

import datetime as dt
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base, Concert, ConcertDay, DiscoveredEvent, DiscoveryState, Tag
from app.db.session import get_session
from app.domain.types import TagKind
from app.web import auth
from app.web.app import create_app

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
        eventernote_event_id="464372", title="Anniversary Day 2",
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
                eventernote_event_id=f"90{n:02d}", title=f"Show {n}",
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
    assert client.post(f"/admin/discoveries/{lead_id}/dismiss").status_code == 303
    assert "Anniversary Day 2" not in client.get("/admin/discoveries").text
    async with client.db() as s:
        row = (await s.execute(select(DiscoveredEvent))).scalar_one()
        assert row.dismissed_at is not None, "dismissed, never deleted"


async def test_dismissing_an_unknown_lead_is_a_404(client):
    login_as(client, ADMIN_ID, "reiji")
    assert client.post("/admin/discoveries/999/dismiss").status_code == 404


async def test_dismissing_twice_is_a_404(client):
    """dismiss_lead answers False for an already-dismissed row, and the route
    must report the write that did not happen rather than a cheerful 303."""
    await _seed(client)
    login_as(client, ADMIN_ID, "reiji")
    lead_id = await _lead_id(client)
    client.post(f"/admin/discoveries/{lead_id}/dismiss")
    assert client.post(f"/admin/discoveries/{lead_id}/dismiss").status_code == 404


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
