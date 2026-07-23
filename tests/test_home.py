"""GET / — the personal Home page.

Home renders four blocks in order: Closes next, Your campaigns (the board),
Coming up (the deadline rows), and the discovery teaser. The board is
read-only by design; every capture action lives on a Coming up row, because a
row is exactly ONE round on ONE leg where "applied" has a single meaning,
while a board card is a whole campaign with a multi-rung ladder.

Signed out the page is the hero and nothing else -- the same gate the old
index already had, plus a door out to /discover.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import (
    Base,
    Concert,
    ConcertDay,
    ConcertTag,
    Round,
    RoundQualifier,
    Tag,
    TagSubscription,
    User,
)
from app.db.service import DEADLINE_ROWS_LIMIT, record_round_outcome
from app.db.session import get_session
from app.domain.board import OPEN_COLUMN_LIMIT
from app.domain.timezones import fmt_dual_lines
from app.domain.types import LotteryOutcome, RoundKind, TagKind
from app.web import auth
from app.web.app import create_app

USER = 4242

# What htmx puts on every request it makes; the outcome route branches on it.
HX = {"HX-Request": "true"}


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
    """Small builder so each test says only what it cares about. Everything
    it makes is tracked: one subscribed ARTIST tag on every concert, which is
    what `tracked_concert_ids` derives "tracked" from until
    ConcertSubscription exists."""

    def __init__(self, session, tag):
        self.s = session
        self.tag = tag

    async def concert(self, event_id, title=None, day_offset=60):
        c = Concert(
            title=title or event_id, event_id=event_id, created_by=USER
        )
        self.s.add(c)
        await self.s.flush()
        self.s.add(ConcertTag(concert_id=c.id, tag_id=self.tag.id))
        if day_offset is not None:
            self.s.add(ConcertDay(
                concert_id=c.id, label="Day 1",
                starts_at_utc=datetime.now(UTC) + timedelta(days=day_offset),
            ))
        await self.s.flush()
        return c

    async def round(
        self, concert, label, *, opens=None, closes=None, results=None, payment=None
    ):
        r = Round(
            concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label=label,
            opens_at_utc=opens, closes_at_utc=closes, results_at_utc=results,
            payment_deadline_at_utc=payment,
        )
        self.s.add(r)
        await self.s.flush()
        return r

    async def open_round(self, concert, label="Round 1", days=7):
        now = datetime.now(UTC)
        return await self.round(
            concert, label, opens=now - timedelta(days=1), closes=now + timedelta(days=days)
        )


async def seeded(db, build):
    """Run `build(seed)` inside one session and commit."""
    async with db() as s:
        s.add(User(discord_id=USER, username="reiji"))
        await s.flush()
        tag = Tag(name="Aqours", kind=TagKind.ARTIST)
        s.add(tag)
        await s.flush()
        s.add(TagSubscription(user_id=USER, tag_id=tag.id))
        await s.flush()
        out = await build(Seed(s, tag))
        await s.commit()
        return out


# ── signed out ───────────────────────────────────────────────────────────


def test_signed_out_home_renders_the_landing(client):
    """Signed out, Home is a real landing page (Task 1 of the onboarding
    build): hero/promise, a "how it works", the illustrative campaign board,
    a Discover taste, and Sign-in-with-Discord CTAs (hero + foot)."""
    r = client.get("/")
    assert r.status_code == 200
    assert "dekimasen deshita" in r.text
    assert "How it works" in r.text
    assert "The whole campaign, one board" in r.text
    assert "Discover what's on" in r.text
    # Two CTAs: hero + foot.
    assert r.text.count('href="/auth/login"') >= 2
    # None of the personal blocks leak to a signed-out visitor.
    assert "Your campaigns" not in r.text
    assert "Up next" not in r.text


async def test_signed_out_landing_board_thesis_is_static_not_a_live_query(client):
    """The four-column board on the landing is an illustrative sample -- there
    is no user signed out, so it must not be a query against real data (the
    board_cards/tracked_concert_ids path). Seeding a concert must not make it
    appear inside the thesis board specifically, since that board is fixed
    sample copy (it may legitimately appear elsewhere, e.g. the separate
    Discover-taste section, which IS real data)."""
    async def build(seed):
        c = await seed.concert("aqours-live", title="Should Not Appear In Thesis")
        await seed.open_round(c, "FC lottery")

    await seeded(client.db, build)

    html = client.get("/").text
    thesis_html = html.split("The whole campaign, one board", 1)[1].split(
        "Discover what's on", 1
    )[0]
    assert "Should Not Appear In Thesis" not in thesis_html
    # The fixed illustrative sample from the demo is present verbatim.
    assert "Bloom in the Summer" in thesis_html


async def test_signed_out_landing_discover_taste_surfaces_real_public_concerts(client):
    """The Discover taste section reuses the same public-catalogue helpers
    /discover itself calls -- a real concert should appear there."""
    async def build(seed):
        c = await seed.concert("aqours-live", title="Real Public Concert")
        await seed.open_round(c, "FC lottery")

    await seeded(client.db, build)

    html = client.get("/").text
    assert "Real Public Concert" in html


async def test_signed_out_landing_catalogue_stat_line_reflects_real_counts(client):
    async def build(seed):
        c = await seed.concert("aqours-live", title="Aqours Live")
        await seed.open_round(c, "FC lottery")

    await seeded(client.db, build)

    html = client.get("/").text
    assert '<div class="n">1</div><div class="l">concerts tracked</div>' in html


async def test_signed_out_landing_card_has_tagrow_and_date(client):
    """The landing's Discover-taste cards get their own treatment: a separate
    day-month date line and a .tagrow of franchise + region minichips (the
    Discover tile's own derivation), which the shared signed-in peek card
    does not carry."""
    async def build(seed):
        fr = Tag(name="Love Live!", kind=TagKind.FRANCHISE)
        vn = Tag(name="Saitama Super Arena", kind=TagKind.VENUE, region="Kanto")
        seed.s.add_all([fr, vn])
        await seed.s.flush()
        c = await seed.concert("aqours-9th", title="9th LoveLive")
        seed.s.add(ConcertTag(concert_id=c.id, tag_id=fr.id))
        seed.s.add(ConcertTag(concert_id=c.id, tag_id=vn.id))
        await seed.open_round(c, "FC lottery")

    await seeded(client.db, build)

    html = client.get("/").text  # signed out
    assert 'class="tagrow"' in html
    assert '<span class="minichip">Love Live!</span>' in html
    assert '<span class="minichip">Kanto</span>' in html
    assert 'class="when num"' in html  # the separate day-month date line


async def test_signed_in_peek_grid_keeps_its_shared_card(client):
    """The landing card treatment is landing-only: the shared signed-in peek
    grid keeps its merged venue-date card and emits no .tagrow, even for a
    concert carrying a franchise + region."""
    async def build(seed):
        fr = Tag(name="Love Live!", kind=TagKind.FRANCHISE)
        vn = Tag(name="Saitama Super Arena", kind=TagKind.VENUE, region="Kanto")
        seed.s.add_all([fr, vn])
        await seed.s.flush()
        # Untracked (no subscribed tag), so it lands in the peek grid.
        c = Concert(title="Untracked", event_id="untracked", created_by=USER)
        seed.s.add(c)
        await seed.s.flush()
        seed.s.add(ConcertTag(concert_id=c.id, tag_id=fr.id))
        seed.s.add(ConcertTag(concert_id=c.id, tag_id=vn.id))
        seed.s.add(ConcertDay(
            concert_id=c.id, label="Day 1",
            starts_at_utc=datetime.now(UTC) + timedelta(days=60),
        ))
        await seed.open_round(c, "FC lottery")

    await seeded(client.db, build)
    login(client)

    html = client.get("/").text  # signed in
    assert 'id="peek"' in html
    assert "Untracked" in html            # it IS in the peek grid
    assert 'class="tagrow"' not in html   # but with no landing tagrow


# ── signed in ────────────────────────────────────────────────────────────


async def test_signed_in_home_renders_all_four_blocks(client):
    """The logged-in GET render test CLAUDE.md requires for every page -- a
    missing one shipped a 500 once."""
    async def build(seed):
        c = await seed.concert("aqours-live", title="Aqours 9th LoveLive")
        await seed.open_round(c, "FC lottery")

    await seeded(client.db, build)
    login(client)

    r = client.get("/")
    assert r.status_code == 200
    assert "Up next" in r.text
    assert "Your campaigns" in r.text
    assert "Coming up" in r.text
    assert "Discover" in r.text
    assert "Aqours 9th LoveLive" in r.text


async def test_concert_appears_in_the_column_for_its_most_advanced_outcome(client):
    """WON outranks a round the user could still enter, so the card renders in
    the Won column and NOT in Open now."""
    async def build(seed):
        c = await seed.concert("won-one", title="Won concert")
        won = await seed.round(
            c, "R1",
            opens=datetime.now(UTC) - timedelta(days=30),
            closes=datetime.now(UTC) - timedelta(days=10),
        )
        await seed.open_round(c, "R2")
        await record_round_outcome(seed.s, USER, won.id, LotteryOutcome.WON)

        other = await seed.concert("open-one", title="Open concert")
        await seed.open_round(other, "R1")

    await seeded(client.db, build)
    login(client)

    html = client.get("/").text
    assert 'data-column="won" data-event-id="won-one"' in html
    assert 'data-column="open" data-event-id="won-one"' not in html
    assert 'data-column="open" data-event-id="open-one"' in html


async def test_base_paid_plus_won_upgrade_lands_in_won_pay_column(client):
    """A concert whose base ticket is PAID but whose (eligible) upgrade is WON
    and unpaid owes money -- it belongs in the Won column, not Secured, through
    board_cards' column_for wiring."""
    async def build(seed):
        c = await seed.concert("upgrade-owe", title="Upgrade owed")
        base = await seed.open_round(c, "FC lottery")
        up = Round(
            concert_id=c.id, kind=RoundKind.UPGRADE, label="Seat upgrade",
            opens_at_utc=datetime.now(UTC) - timedelta(days=2),
            closes_at_utc=datetime.now(UTC) - timedelta(days=1),
            payment_deadline_at_utc=datetime.now(UTC) + timedelta(days=3),
        )
        seed.s.add(up)
        await seed.s.flush()
        seed.s.add(RoundQualifier(upgrade_round_id=up.id, qualifying_round_id=base.id))
        await seed.s.flush()
        await record_round_outcome(seed.s, USER, base.id, LotteryOutcome.WON)
        await record_round_outcome(seed.s, USER, base.id, LotteryOutcome.PAID)
        await record_round_outcome(seed.s, USER, up.id, LotteryOutcome.WON)

    await seeded(client.db, build)
    login(client)

    html = client.get("/").text
    assert 'data-column="won" data-event-id="upgrade-owe"' in html
    assert 'data-column="secured" data-event-id="upgrade-owe"' not in html


async def test_open_column_renders_the_cap_and_reports_the_true_remainder(client):
    async def build(seed):
        for i in range(15):
            c = await seed.concert(f"open-{i:02d}")
            await seed.open_round(c, "R1", days=2 + i)

    await seeded(client.db, build)
    login(client)

    html = client.get("/").text
    assert html.count('data-column="open" data-event-id=') == OPEN_COLUMN_LIMIT == 12
    # The remainder comes from the PRE-cap count board_cards returns, not from
    # the truncated list -- "+3 more", never "+0 more".
    assert "+3 more" in html


# ── Coming up: the capture surface ───────────────────────────────────────


async def test_open_round_with_no_outcome_offers_both_capture_actions(client):
    async def build(seed):
        c = await seed.concert("aqours-live", title="Aqours Live")
        await seed.open_round(c, "FC lottery")

    await seeded(client.db, build)
    login(client)

    html = client.get("/").text
    assert ">I have applied</button>" in html
    assert ">Not applying</button>" in html
    assert "Nothing to do" not in html


async def test_applied_row_shows_nothing_to_do_and_no_buttons(client):
    async def build(seed):
        c = await seed.concert("aqours-live", title="Aqours Live")
        r = await seed.open_round(c, "FC lottery")
        await record_round_outcome(seed.s, USER, r.id, LotteryOutcome.APPLIED)

    await seeded(client.db, build)
    login(client)

    html = client.get("/").text
    assert "Nothing to do" in html
    assert ">I have applied</button>" not in html
    assert ">Not applying</button>" not in html
    assert ">Paid<" not in html


async def test_paid_is_offered_only_from_won(client):
    """Two concerts, identical but for the recorded outcome: only the WON one
    gets a Paid button, and it gets nothing else."""
    async def build(seed):
        won_c = await seed.concert("won-one", title="Won concert")
        r = await seed.open_round(won_c, "R1")
        await record_round_outcome(seed.s, USER, r.id, LotteryOutcome.WON)

        open_c = await seed.concert("open-one", title="Open concert")
        await seed.open_round(open_c, "R1")

    await seeded(client.db, build)
    login(client)

    html = client.get("/").text
    assert html.count('value="paid"') == 1
    assert html.count('value="applied"') == 1  # the untouched concert only


async def test_no_outcome_and_no_won_means_no_paid_button_anywhere(client):
    async def build(seed):
        c = await seed.concert("aqours-live", title="Aqours Live")
        await seed.open_round(c, "R1")

    await seeded(client.db, build)
    login(client)

    assert 'value="paid"' not in client.get("/").text


async def test_a_row_with_no_round_id_renders_no_capture_actions(client):
    """EVENT_START rows come from a ConcertDay, not a round -- there is
    nothing to record against, so the row must render no form rather than one
    that posts to /rounds/None/outcome."""
    async def build(seed):
        # A concert whose only future moment is the show itself.
        await seed.concert("day-only", title="Day only concert", day_offset=30)

    await seeded(client.db, build)
    login(client)

    html = client.get("/").text
    assert "Day only concert" in html          # the row is rendered
    assert "/rounds/None/outcome" not in html  # but with no broken form
    assert ">I have applied</button>" not in html
    assert ">Not applying</button>" not in html


# ── "Up next": the header must not claim a moment the body contradicts ────


async def test_up_next_names_an_opening_as_an_opening(client):
    """The block picks the nearest actionable moment, which is often an OPEN,
    not a close. It used to be headed "Closes next" while the body underneath
    read "Opens ..." -- the first thing a signed-in user reads, stating
    something false. The header is now moment-agnostic and the body names
    which kind of moment it is."""
    async def build(seed):
        c = await seed.concert("soon-open", title="Not open yet")
        await seed.round(
            c, "FC lottery",
            opens=datetime.now(UTC) + timedelta(days=2),
            closes=datetime.now(UTC) + timedelta(days=9),
        )

    await seeded(client.db, build)
    login(client)

    html = client.get("/").text
    assert "Up next" in html
    assert "Closes next" not in html
    # The body still names the moment, and it is the open, not the close.
    head = html.split("Up next", 1)[1][:400]
    assert "Opens" in head


async def test_up_next_falls_back_to_an_event_start_without_calling_it_a_close(client):
    """The rows[0] fallback can land on an EVENT_START row. Whatever it lands
    on, the header may not assert it is a close."""
    async def build(seed):
        await seed.concert("day-only", title="Show only", day_offset=30)

    await seeded(client.db, build)
    login(client)

    html = client.get("/").text
    assert "Up next" in html
    assert "Closes next" not in html


# ── a round that has not opened yet is not something you can have applied to ──


async def test_a_round_that_has_not_opened_offers_no_capture_actions(client):
    """upcoming_deadlines emits one row per FUTURE anchor, so a single round
    with opens/closes/results ahead of it produces three rows. None of them
    may offer capture: you cannot have applied to a round that has not
    opened, and recording APPLIED is a one-way write (record_round_outcome
    refuses to overwrite a starting state)."""
    now = datetime.now(UTC)

    async def build(seed):
        c = await seed.concert("future-round", title="Future round concert")
        await seed.round(
            c, "FC lottery",
            opens=now + timedelta(days=2),
            closes=now + timedelta(days=9),
            results=now + timedelta(days=20),
        )

    await seeded(client.db, build)
    login(client)

    html = client.get("/").text
    assert "Future round concert" in html
    # One row per future anchor: three for this round (opens/closes/results),
    # plus the show itself. That multiplication is exactly why the capture
    # gate has to exist -- four rows, four independent button pairs.
    rows_html = html.split('id="deadline-rows"', 1)[1]
    assert rows_html.count("Future round concert</a>") == 4
    for label in ("opens", "closes", "results announced"):
        assert label in rows_html
    assert ">I have applied</button>" not in html
    assert ">Not applying</button>" not in html
    assert 'value="applied"' not in html
    assert 'value="not_applied"' not in html


async def test_an_open_round_still_offers_capture(client):
    """The guard above must not swallow the normal case: a round whose open
    has passed is capturable on every one of its future-anchor rows."""
    async def build(seed):
        c = await seed.concert("open-now", title="Open now concert")
        await seed.open_round(c, "FC lottery")

    await seeded(client.db, build)
    login(client)

    html = client.get("/").text
    assert ">I have applied</button>" in html


# ── WON / LOST must be reachable from the web, not only from a DM ─────────


async def test_applied_row_offers_won_and_lost_once_results_have_landed(client):
    """Without this the web can never leave APPLIED: record_round_outcome
    refuses to overwrite a starting state, so the row read "Nothing to do"
    forever and the card sat in the Applied column with no web-side exit.
    For a dm_blocked user, or a deploy with bot_enabled=False, that made the
    four-column board a two-column one."""
    now = datetime.now(UTC)

    async def build(seed):
        c = await seed.concert("results-in", title="Results in concert")
        r = await seed.round(
            c, "FC lottery",
            opens=now - timedelta(days=30),
            closes=now - timedelta(days=10),
            results=now - timedelta(days=1),
            payment=now + timedelta(days=5),
        )
        await record_round_outcome(seed.s, USER, r.id, LotteryOutcome.APPLIED)
        return r

    round_ = await seeded(client.db, build)
    login(client)

    html = client.get("/").text
    assert ">I won</button>" in html
    assert ">I lost</button>" in html
    assert 'value="won"' in html
    assert 'value="lost"' in html
    # And pressing one actually writes -- WON can be set regardless of state.
    r = client.post(
        f"/rounds/{round_.id}/outcome", data={"outcome": "won"}, headers=HX
    )
    assert r.status_code == 200
    assert 'data-column="won" data-event-id="results-in"' in r.text
    # From WON the only remaining action is Paid, which is unchanged.
    assert ">Paid</button>" in r.text
    assert ">I won</button>" not in r.text


async def test_applied_row_withholds_won_and_lost_until_results_are_due(client):
    """The other half of the rule: while the result moment is still ahead
    there is nothing to report, so the row stays quiet."""
    now = datetime.now(UTC)

    async def build(seed):
        c = await seed.concert("results-pending", title="Results pending concert")
        r = await seed.round(
            c, "FC lottery",
            opens=now - timedelta(days=1),
            closes=now + timedelta(days=7),
            results=now + timedelta(days=21),
        )
        await record_round_outcome(seed.s, USER, r.id, LotteryOutcome.APPLIED)

    await seeded(client.db, build)
    login(client)

    html = client.get("/").text
    assert ">I won</button>" not in html
    assert ">I lost</button>" not in html
    assert "Nothing to do" in html


# ── the Discover teaser counts what the link actually leads to ────────────


async def test_teaser_count_excludes_fully_cancelled_concerts(client):
    """/discover hides a concert whose every existing leg is cancelled, so
    counting every Concert row overstated what the link leads to."""
    async def build(seed):
        await seed.concert("live-one", title="Live concert")
        dead = await seed.concert("dead-one", title="Dead concert", day_offset=None)
        seed.s.add(ConcertDay(
            concert_id=dead.id, label="Day 1", cancelled=True,
            starts_at_utc=datetime.now(UTC) + timedelta(days=40),
        ))
        await seed.s.flush()

    await seeded(client.db, build)
    login(client)

    html = client.get("/").text
    assert "1 concert in the catalogue" in html
    assert "2 concerts in the catalogue" not in html


async def test_times_render_dual_jst_first(client):
    async def build(seed):
        c = await seed.concert("aqours-live", title="Aqours Live")
        return await seed.open_round(c, "FC lottery")

    round_ = await seeded(client.db, build)
    login(client)

    html = client.get("/").text
    async with client.db() as s:
        db_user = await s.get(User, USER)
        tz = db_user.timezone
    # The web now renders the two-line shape (Task 2 demo reconciliation): a
    # bold weekday+day+month line over a "HH:MM JST · HH:MM <zone>" time line,
    # not the flat fmt_dual string. Invariant 1 still holds -- both zones, JST
    # first -- so we assert against the two-line formatter's output.
    date_line, time_line = fmt_dual_lines(round_.closes_at_utc, tz)
    assert date_line in html
    assert time_line in html
    assert "JST" in time_line and time_line.index("JST") < time_line.index("·")


# ── the two limits must agree ────────────────────────────────────────────


async def test_home_and_the_outcome_fragment_render_the_same_row_count(client):
    """POST /rounds/{id}/outcome swaps the Coming up fragment back in. If Home
    renders a different number of rows than the fragment does, the swap
    silently changes the list length -- so both go through the one
    DEADLINE_ROWS_LIMIT default."""
    async def build(seed):
        rounds = []
        for i in range(DEADLINE_ROWS_LIMIT + 5):
            c = await seed.concert(f"c-{i:02d}", title=f"Concert {i:02d}", day_offset=None)
            rounds.append(await seed.open_round(c, "R1", days=1 + i))
        return rounds[0].id

    round_id = await seeded(client.db, build)
    login(client)

    home_rows = client.get("/").text.count('class="row"')
    assert home_rows == DEADLINE_ROWS_LIMIT

    fragment = client.post(
        f"/rounds/{round_id}/outcome", data={"outcome": "applied"}, headers=HX,
    )
    assert fragment.status_code == 200
    assert fragment.text.count('class="row"') == home_rows


# ── the board moves when you record an outcome ───────────────────────────


async def test_outcome_response_carries_both_the_rows_and_an_oob_board(client):
    """The whole point of the swap: the Coming up row AND the board card both
    update from ONE response. A response with only the rows looks like it
    worked while the card sits in its old column until a reload -- which is
    exactly how this shipped."""
    async def build(seed):
        c = await seed.concert("aqours-live", title="Aqours Live")
        return (await seed.open_round(c, "FC lottery")).id

    round_id = await seeded(client.db, build)
    login(client)

    r = client.post(f"/rounds/{round_id}/outcome", data={"outcome": "applied"}, headers=HX)
    assert r.status_code == 200
    # the primary swap: the Coming up fragment
    assert 'id="deadline-rows"' in r.text
    # the out-of-band swap: the board, targeting the container id Home renders
    assert 'id="board"' in r.text
    assert 'hx-swap-oob="true"' in r.text


async def test_recording_an_outcome_moves_the_card_between_columns(client):
    """Not just "a board came back" -- the card must actually be in its NEW
    column. A test asserting only the presence of the fragment would let the
    column logic regress silently."""
    async def build(seed):
        c = await seed.concert("aqours-live", title="Aqours Live")
        return (await seed.open_round(c, "FC lottery")).id

    round_id = await seeded(client.db, build)
    login(client)

    before = client.get("/").text
    assert 'data-column="open" data-event-id="aqours-live"' in before

    r = client.post(f"/rounds/{round_id}/outcome", data={"outcome": "applied"}, headers=HX)
    assert 'data-column="applied" data-event-id="aqours-live"' in r.text
    assert 'data-column="open" data-event-id="aqours-live"' not in r.text
    # and the same board comes back on a full reload, so the swap is not a lie
    assert 'data-column="applied" data-event-id="aqours-live"' in client.get("/").text


async def test_the_board_summary_counts_are_swapped_too(client):
    """The "N open · N awaiting results" line sits outside the board, so it
    needs its own OOB fragment -- otherwise the card moves and the counts
    above it stay stale."""
    async def build(seed):
        c = await seed.concert("aqours-live", title="Aqours Live")
        return (await seed.open_round(c, "FC lottery")).id

    round_id = await seeded(client.db, build)
    login(client)

    r = client.post(f"/rounds/{round_id}/outcome", data={"outcome": "applied"}, headers=HX)
    assert 'id="board-summary"' in r.text
    assert "<strong>0 open</strong>" in r.text
    assert "1 awaiting results" in r.text


async def test_without_htmx_the_outcome_post_redirects_back_to_home(client):
    """JS disabled: the form is a real POST with a real action, so it must end
    at a whole page rather than a bare fragment rendered as the document."""
    async def build(seed):
        c = await seed.concert("aqours-live", title="Aqours Live")
        return (await seed.open_round(c, "FC lottery")).id

    round_id = await seeded(client.db, build)
    login(client)

    r = client.post(f"/rounds/{round_id}/outcome", data={"outcome": "applied"})
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    # the write still happened -- the fallback is presentation only
    assert 'data-column="applied" data-event-id="aqours-live"' in client.get("/").text


# ── the card eyebrow ─────────────────────────────────────────────────────


async def test_board_card_shows_the_artist_eyebrow(client):
    """board_cards must eager-load Concert.tags: touching concert.tags in a
    template with a lazy relationship raises MissingGreenlet under async
    rendering rather than warning."""
    async def build(seed):
        # Title deliberately shares no substring with the seeded ARTIST tag
        # ("Aqours"), so finding the tag name on the card can only mean the
        # eyebrow rendered it.
        c = await seed.concert("ninth-live", title="9th LoveLive")
        await seed.open_round(c, "FC lottery")

    await seeded(client.db, build)
    login(client)

    html = client.get("/").text
    card = html.split('data-event-id="ninth-live"')[1].split("</a>")[0]
    assert "Aqours" in card


# ── the follow-up dialog ─────────────────────────────────────────────────


async def test_not_applying_carries_its_concert_title_in_a_data_attribute(client):
    """Invariant 7: the title is user-controlled text, so it reaches the
    dialog through a data- attribute read via dataset -- never interpolated
    into an inline on* handler. The attribute name is deliberately NOT
    data-name, which base.html's shared filterChips() selector claims."""
    async def build(seed):
        c = await seed.concert("aqours-live", title="Aqours Live")
        await seed.open_round(c, "FC lottery")

    await seeded(client.db, build)
    login(client)

    html = client.get("/").text
    assert 'data-prune-title="Aqours Live"' in html
    assert "<dialog" in html
    assert "onclick=\"" not in html.split('data-prune-title')[1][:400]


# ── Task 3: peek grid, foot-note, teaser open-round count ──────────────────


async def test_peek_grid_shows_four_untracked_discover_cards(client):
    """The teaser's peek grid is a door out to Discover, not the board -- it
    must show concerts the user does NOT already track."""
    async def build(seed):
        tracked = await seed.concert("tracked-one", title="Tracked concert")
        await seed.open_round(tracked, "R1")
        for i in range(5):
            seed.s.add(Concert(title=f"Peek {i}", event_id=f"peek-{i}", created_by=USER))
        await seed.s.flush()

    await seeded(client.db, build)
    login(client)

    html = client.get("/").text
    assert 'id="peek"' in html
    peek_html = html.split('id="peek"', 1)[1].split("</div>", 1)[0]
    assert peek_html.count('class="card"') == 4
    assert "Tracked concert" not in peek_html


async def test_foot_note_paragraph_renders(client):
    async def build(seed):
        c = await seed.concert("aqours-live", title="Aqours Live")
        await seed.open_round(c, "FC lottery")

    await seeded(client.db, build)
    login(client)

    html = client.get("/").text
    assert 'class="foot-note"' in html
    assert "Home answers" in html


async def test_teaser_names_the_open_round_count_alongside_the_catalogue_count(client):
    async def build(seed):
        open_c = await seed.concert("open-one", title="Open concert")
        await seed.open_round(open_c)
        closed_c = await seed.concert("closed-one", title="Closed concert", day_offset=None)
        await seed.round(
            closed_c, "R1",
            opens=datetime.now(UTC) - timedelta(days=30),
            closes=datetime.now(UTC) - timedelta(days=10),
        )

    await seeded(client.db, build)
    login(client)

    html = client.get("/").text
    assert "1 with a round still open" in html


# ── Task 3: the Won column card's accent border ─────────────────────────────


def test_style_gives_the_won_column_card_an_accent_border():
    text = (
        Path(__file__).resolve().parents[1] / "src/app/web/static/style.css"
    ).read_text(encoding="utf-8")
    assert '[data-column="won"]' in text
    assert "border-left: 3px solid var(--accent)" in text


# ── Task 3: the countdown pill's tone follows urgency, not just column ──────


async def test_two_open_cards_get_different_pill_tones_by_urgency(client):
    async def build(seed):
        urgent = await seed.concert("urgent-one", title="Urgent concert")
        await seed.round(
            urgent, "R1",
            opens=datetime.now(UTC) - timedelta(hours=1),
            closes=datetime.now(UTC) + timedelta(hours=6),
        )
        distant = await seed.concert("distant-one", title="Distant concert")
        await seed.round(
            distant, "R1",
            opens=datetime.now(UTC) - timedelta(hours=1),
            closes=datetime.now(UTC) + timedelta(days=20),
        )

    await seeded(client.db, build)
    login(client)

    html = client.get("/").text
    urgent_card = html.split('data-event-id="urgent-one"')[1].split("</a>")[0]
    distant_card = html.split('data-event-id="distant-one"')[1].split("</a>")[0]
    assert "pill p-danger" in urgent_card
    assert "pill p-quiet" in distant_card


# ── Task 3: the board-card artist eyebrow ───────────────────────────────────


async def test_board_card_artist_name_carries_the_eyebrow_class(client):
    async def build(seed):
        c = await seed.concert("ninth-live", title="9th LoveLive")
        await seed.open_round(c, "FC lottery")

    await seeded(client.db, build)
    login(client)

    html = client.get("/").text
    card = html.split('data-event-id="ninth-live"')[1].split("</a>")[0]
    assert 'class="eyebrow"' in card
    assert "Aqours" in card.split('class="eyebrow"')[1].split("</span>")[0]


# ── Task 3: performance dates render day-month, not the old ISO+JST form ────


async def test_board_and_deadline_row_dates_render_day_month(client):
    """Performance DATES (a concert's start day) are a fact about the world,
    not a deadline -- day-month with no year, no weekday, no zone label. The
    deadline TIME column keeps the dual JST/local render from Task 2; only
    this venue/date context line changes."""
    async def build(seed):
        c = await seed.concert("aqours-live", title="Aqours Live", day_offset=None)
        seed.s.add(ConcertDay(
            concert_id=c.id, label="Day 1",
            starts_at_utc=datetime(2026, 10, 12, 10, 0, tzinfo=UTC),
        ))
        await seed.s.flush()
        await seed.open_round(c, "FC lottery")

    await seeded(client.db, build)
    login(client)

    html = client.get("/").text
    assert "12 Oct" in html
    assert "2026-10-12 JST" not in html


# ── Task 3: the two-tier "Up next" countdown ────────────────────────────────


async def test_up_next_countdown_is_two_tier(client):
    """Big number + a small unit caption underneath (demo .next .big/.unit),
    not one flat countdown span."""
    async def build(seed):
        c = await seed.concert("aqours-live", title="Aqours Live")
        await seed.open_round(c, "FC lottery")

    await seeded(client.db, build)
    login(client)

    html = client.get("/").text
    assert "data-countdown-big" in html
    assert 'class="unit"' in html


# ── Task 5: Coming up localizes with no template change ──────────────────


async def test_coming_up_rows_render_the_localized_round_label(client):
    """End-to-end proof that resolving the label at the SERVICE copy site is
    enough: `_deadline_rows.html` renders a bare `{{ d.label }}` with no loc()
    of its own, because UpcomingDeadline.label already carries the viewer's
    variant by the time the template sees it. If this ever regresses, the
    fix belongs in `upcoming_deadlines`, not here."""
    async def build(seed):
        c = await seed.concert("aqours-live", title="Aqours Live", day_offset=None)
        r = await seed.open_round(c, "1次先行抽選")
        r.label_zh = "第一轮抽选"
        r.label_en = "1st-round lottery"

    await seeded(client.db, build)
    login(client)

    client.cookies.set("lang", "zh")
    assert "第一轮抽选" in client.get("/").text

    client.cookies.set("lang", "en")
    html_en = client.get("/").text
    assert "1st-round lottery" in html_en
    assert "第一轮抽选" not in html_en
