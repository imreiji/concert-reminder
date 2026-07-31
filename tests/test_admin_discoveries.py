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
from app.db.models import Base, Concert, ConcertDay, DiscoveredEvent, Tag
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
    backlog it is what separates "the DM already named this" from "this arrived
    today"."""
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
