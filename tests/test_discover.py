"""GET /discover -- the public catalogue.

Discover answers "what's on". It is the only content page in the app with no
login requirement (alongside /healthz and the legal pages), so the signed-out
render is the important test here: an anonymous visitor must be able to browse
the whole catalogue, and must see NO personal standing anywhere on the page.

Signed in, every card carries exactly one status pill merging the event's round
state with the user's standing -- the standing REPLACES the countdown rather
than sitting beside it, and its colour says who owes the next move: ok = you
are covered, danger = you owe an action, quiet = you have no standing.
"""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Concert, ConcertDay, Round, User
from app.db.service import record_round_outcome
from app.db.session import get_session
from app.domain.types import LotteryOutcome, RoundKind
from app.web import auth
from app.web.app import create_app

USER = 5151


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")  # match production: cascades must fire

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture()
def client(db, monkeypatch):
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


def login(client, discord_id: int = USER, name: str = "reiji"):
    async def fake_identity(token):
        return {"id": str(discord_id), "username": name, "global_name": name, "avatar": None}

    client.monkeypatch.setattr(auth, "fetch_identity", fake_identity)
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    client.get(f"/auth/callback?code=x&state={state}")


# ── seeding ──────────────────────────────────────────────────────────────


class Seed:
    """Discover shows the WHOLE catalogue, tracked or not, so nothing here
    subscribes to a tag -- unlike Home's fixture, which must."""

    def __init__(self, session):
        self.s = session

    async def concert(self, event_id, title=None, day_offset=60):
        c = Concert(title=title or event_id, event_id=event_id, created_by=USER)
        self.s.add(c)
        await self.s.flush()
        if day_offset is not None:
            self.s.add(ConcertDay(
                concert_id=c.id, label="Day 1",
                starts_at_utc=datetime.now(UTC) + timedelta(days=day_offset),
            ))
        await self.s.flush()
        return c

    async def round(
        self, concert, label="R1", *, kind=RoundKind.LOTTERY_ROUND,
        opens=None, closes=None, payment=None,
    ):
        r = Round(
            concert_id=concert.id, kind=kind, label=label,
            opens_at_utc=opens, closes_at_utc=closes, payment_deadline_at_utc=payment,
        )
        self.s.add(r)
        await self.s.flush()
        return r

    async def open_round(self, concert, label="R1", *, days=4, kind=RoundKind.LOTTERY_ROUND):
        now = datetime.now(UTC)
        return await self.round(
            concert, label, kind=kind,
            opens=now - timedelta(days=1), closes=now + timedelta(days=days),
        )


async def seeded(db, build):
    async with db() as s:
        s.add(User(discord_id=USER, username="reiji"))
        await s.flush()
        out = await build(Seed(s))
        await s.commit()
        return out


def tile_for(html: str, event_id: str) -> str:
    """The one tile's markup, so a pill assertion cannot accidentally match
    some other concert's pill elsewhere on the page."""
    start = html.index(f'href="/concerts/{event_id}"')
    return html[start:html.index("</a>", start)]


# ── public access ────────────────────────────────────────────────────────


async def test_signed_out_discover_renders_the_catalogue(client):
    """The important one: /discover is public. current_user, not
    require_user -- an anonymous visitor resolves to None and still browses."""
    async def build(seed):
        c = await seed.concert("aqours-live", title="Aqours 9th LoveLive")
        await seed.open_round(c, "FC lottery")

    await seeded(client.db, build)

    r = client.get("/discover")
    assert r.status_code == 200
    assert "Aqours 9th LoveLive" in r.text
    # the sidebar the page exists for
    assert "Filter by tag" in r.text


async def test_signed_out_discover_shows_no_personal_standing(client):
    """The event's round state renders; the user's standing cannot, because
    there is no user. A concert someone else applied to must look untouched."""
    async def build(seed):
        c = await seed.concert("aqours-live", title="Aqours Live")
        r = await seed.open_round(c, "FC lottery")
        await record_round_outcome(seed.s, USER, r.id, LotteryOutcome.APPLIED)

    await seeded(client.db, build)

    html = client.get("/discover").text
    assert "Applied" not in html
    assert "Secured" not in html
    assert "Won" not in html
    # but the event state is still there
    assert "Closes in" in html


async def test_signed_in_discover_renders(client):
    """The logged-in GET render test CLAUDE.md requires for every page."""
    async def build(seed):
        c = await seed.concert("aqours-live", title="Aqours Live")
        await seed.open_round(c, "FC lottery")

    await seeded(client.db, build)
    login(client)

    r = client.get("/discover")
    assert r.status_code == 200
    assert "Aqours Live" in r.text


# ── the status pill, one per card ────────────────────────────────────────


async def test_applied_pill_is_the_round_kind_and_green(client):
    async def build(seed):
        c = await seed.concert("applied-one", title="Applied concert")
        r = await seed.open_round(c, "FC lottery", kind=RoundKind.GENERAL_SALE)
        await record_round_outcome(seed.s, USER, r.id, LotteryOutcome.APPLIED)

    await seeded(client.db, build)
    login(client)

    tile = tile_for(client.get("/discover").text, "applied-one")
    assert "· Applied" in tile
    assert "General sale" in tile
    assert "p-ok" in tile
    # the standing REPLACES the countdown; it does not sit beside it
    assert "Closes in" not in tile


async def test_won_pill_shows_the_payment_date_and_is_urgent(client):
    async def build(seed):
        c = await seed.concert("won-one", title="Won concert")
        now = datetime.now(UTC)
        r = await seed.round(
            c, "R1", opens=now - timedelta(days=30), closes=now - timedelta(days=10),
            payment=datetime(2099, 7, 22, 12, 0, tzinfo=UTC),
        )
        await record_round_outcome(seed.s, USER, r.id, LotteryOutcome.APPLIED)
        await record_round_outcome(seed.s, USER, r.id, LotteryOutcome.WON)

    await seeded(client.db, build)
    login(client)

    tile = tile_for(client.get("/discover").text, "won-one")
    assert "Won" in tile and "pay by 22 Jul" in tile
    assert "p-danger" in tile


async def test_secured_pill_is_green(client):
    async def build(seed):
        c = await seed.concert("paid-one", title="Paid concert")
        r = await seed.open_round(c, "R1")
        await record_round_outcome(seed.s, USER, r.id, LotteryOutcome.APPLIED)
        await record_round_outcome(seed.s, USER, r.id, LotteryOutcome.WON)
        await record_round_outcome(seed.s, USER, r.id, LotteryOutcome.PAID)

    await seeded(client.db, build)
    login(client)

    tile = tile_for(client.get("/discover").text, "paid-one")
    assert "Secured" in tile
    assert "p-ok" in tile


async def test_no_standing_pill_counts_down_to_the_close(client):
    async def build(seed):
        c = await seed.concert("open-one", title="Open concert")
        await seed.open_round(c, "R1", days=4)

    await seeded(client.db, build)
    login(client)

    tile = tile_for(client.get("/discover").text, "open-one")
    assert "Closes in 4d" in tile
    assert "Lottery round" in tile
    assert "p-quiet" in tile


async def test_no_standing_pill_counts_down_to_the_open(client):
    async def build(seed):
        c = await seed.concert("soon-one", title="Soon concert")
        now = datetime.now(UTC)
        await seed.round(
            c, "R1", kind=RoundKind.GENERAL_SALE,
            opens=now + timedelta(days=13, hours=1), closes=now + timedelta(days=20),
        )

    await seeded(client.db, build)
    login(client)

    tile = tile_for(client.get("/discover").text, "soon-one")
    assert "General sale" in tile
    assert "Opens in 13d" in tile
    assert "p-quiet" in tile


async def test_all_rounds_closed_pill(client):
    async def build(seed):
        c = await seed.concert("closed-one", title="Closed concert")
        now = datetime.now(UTC)
        await seed.round(
            c, "R1", opens=now - timedelta(days=30), closes=now - timedelta(days=10)
        )

    await seeded(client.db, build)
    login(client)

    tile = tile_for(client.get("/discover").text, "closed-one")
    assert "All rounds closed" in tile
    assert "p-quiet" in tile


async def test_won_outranks_an_open_round_on_another_rung(client):
    """Same precedence the board uses: money owed outranks a round you could
    still enter, so the pill reads Won, not a countdown."""
    async def build(seed):
        c = await seed.concert("ladder", title="Ladder concert")
        now = datetime.now(UTC)
        r1 = await seed.round(
            c, "R1", opens=now - timedelta(days=30), closes=now - timedelta(days=10),
            payment=now + timedelta(days=3),
        )
        await seed.open_round(c, "R2")
        await record_round_outcome(seed.s, USER, r1.id, LotteryOutcome.APPLIED)
        await record_round_outcome(seed.s, USER, r1.id, LotteryOutcome.WON)

    await seeded(client.db, build)
    login(client)

    tile = tile_for(client.get("/discover").text, "ladder")
    assert "Won" in tile
    assert "Closes in" not in tile


# ── the round-status facet ───────────────────────────────────────────────


async def test_facet_marks_each_card_with_its_round_status(client):
    """The facet filters client-side off data-status, the same way the tag
    filter reads data-tags -- so the attribute is what the test asserts."""
    async def build(seed):
        now = datetime.now(UTC)
        await seed.open_round(await seed.concert("is-open"), "R1")
        await seed.round(
            await seed.concert("is-soon"), "R1",
            opens=now + timedelta(days=5), closes=now + timedelta(days=9),
        )
        await seed.concert("is-none")

    await seeded(client.db, build)
    login(client)

    html = client.get("/discover").text
    assert 'data-status="open"' in tile_for(html, "is-open")
    assert 'data-status="soon"' in tile_for(html, "is-soon")
    assert 'data-status="none"' in tile_for(html, "is-none")


async def test_facet_filters_server_side_without_js(client):
    """The facet links are real hrefs, so with JS off the server does the
    filtering: a non-matching tile renders hidden and the matching one does
    not. Same degradation contract as the tag chips and the search form."""
    async def build(seed):
        await seed.open_round(await seed.concert("is-open"), "R1")
        await seed.concert("is-none")

    await seeded(client.db, build)
    login(client)

    html = client.get("/discover?status=open").text
    assert 'style="display:none"' not in tile_for(html, "is-open")
    assert 'style="display:none"' in tile_for(html, "is-none")


async def test_facet_links_render_all_three_options(client):
    await seeded(client.db, lambda seed: seed.concert("c1"))
    login(client)

    html = client.get("/discover").text
    assert "Open now" in html
    assert "Opening soon" in html
    assert "Not tracking" in html


# ── the Next deadline sort ───────────────────────────────────────────────


async def test_next_deadline_sort_orders_by_soonest_deadline(client):
    """Deliberately seeded so event-date order and next-deadline order
    disagree: the far-off show closes first."""
    async def build(seed):
        soon_close = await seed.concert("closes-first", title="Closes first", day_offset=300)
        await seed.open_round(soon_close, "R1", days=2)
        late_close = await seed.concert("closes-later", title="Closes later", day_offset=10)
        await seed.open_round(late_close, "R1", days=30)

    await seeded(client.db, build)
    login(client)

    by_next = client.get("/discover?sort=next").text
    assert by_next.index("Closes first") < by_next.index("Closes later")
    by_event = client.get("/discover?sort=event").text
    assert by_event.index("Closes later") < by_event.index("Closes first")


async def test_next_deadline_sort_puts_concerts_with_no_deadline_last(client):
    async def build(seed):
        await seed.concert("no-dates", title="No dates", day_offset=None)
        await seed.open_round(await seed.concert("has-round", title="Has round"), "R1")

    await seeded(client.db, build)
    login(client)

    html = client.get("/discover?sort=next").text
    assert html.index("Has round") < html.index("No dates")


async def test_next_deadline_sort_link_is_offered(client):
    await seeded(client.db, lambda seed: seed.concert("c1"))
    login(client)

    assert 'data-sort="next"' in client.get("/discover").text
