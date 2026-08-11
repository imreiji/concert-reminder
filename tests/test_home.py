"""GET / — the personal Home page.

Home renders four blocks in order: Closes next, Your campaigns (the board),
Coming up (the deadline rows), and the discovery teaser. The board is
read-only by design; every capture action lives on a Coming up row, because a
row is exactly ONE round on ONE leg where "applied" has a single meaning,
while a board card is a whole campaign with a multi-rung ladder.

Signed out the page is the hero and nothing else -- the same gate the old
index already had, plus a door out to /discover.
"""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import (
    Concert,
    ConcertDay,
    ConcertTag,
    Round,
    RoundQualifier,
    Tag,
    TagSubscription,
    User,
)
from app.db.service import (
    DEADLINE_ROWS_LIMIT,
    VISIBLE_BLOCKS,
    my_deadline_blocks,
    my_deadline_rows,
    record_round_day_result,
    record_round_outcome,
)
from app.db.session import get_session
from app.domain.board import OPEN_COLUMN_LIMIT
from app.domain.timezones import fmt_dual_lines
from app.domain.types import Anchor, LegResult, LotteryOutcome, RoundKind, TagKind
from app.web import auth
from app.web.app import create_app

USER = 4242

# What htmx puts on every request it makes; the outcome route branches on it.
HX = {"HX-Request": "true"}


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
    assert '<div class="n">1</div><div class="l">events tracked</div>' in html


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


async def test_board_card_ladder_is_capped_with_a_plain_text_remainder(client):
    """A long campaign renders only the two rungs that matter -- the one that
    explains the column and the next actionable one -- plus a plain-text count
    of what was hidden. It is NOT a <details>: a board card must not expand,
    because uniform card height is what makes the columns scannable and
    nothing on a card is interactive anyway. The kept rungs also keep their
    ORIGINAL ladder numbers, since a todo rung's mark IS its position."""
    async def build(seed):
        now = datetime.now(UTC)
        c = await seed.concert("long-ladder", title="Long ladder")
        r1 = await seed.round(c, "R1", opens=now - timedelta(days=30),
                              closes=now - timedelta(days=25))
        r2 = await seed.round(c, "R2", opens=now - timedelta(days=20),
                              closes=now - timedelta(days=15))
        r3 = await seed.round(c, "R3", opens=now - timedelta(days=10),
                              closes=now - timedelta(days=5))
        await seed.round(c, "R4", opens=now + timedelta(days=5),
                         closes=now + timedelta(days=10))
        await seed.round(c, "R5", opens=now + timedelta(days=15),
                         closes=now + timedelta(days=20))
        await record_round_outcome(seed.s, USER, r1.id, LotteryOutcome.LOST)
        await record_round_outcome(seed.s, USER, r2.id, LotteryOutcome.LOST)
        await record_round_outcome(seed.s, USER, r3.id, LotteryOutcome.APPLIED)

    await seeded(client.db, build)
    login(client)

    html = client.get("/").text
    card = html.split('data-event-id="long-ladder"', 1)[1].split("</a>", 1)[0]

    assert card.count('class="rung') == 2
    assert ">R3<" in card and ">R4<" in card
    assert ">R1<" not in card and ">R5<" not in card
    # Original 1-based ladder position, not a renumbering of what survived.
    assert '<span class="rmark m-todo">4</span>' in card
    # One plain-text remainder, no disclosure widget anywhere on the card.
    assert card.count('class="rmore"') == 1
    # The app's existing fold vocabulary, shared with the Coming up summary --
    # NOT "earlier", since the hidden set can include later rungs too.
    assert "+3 more rounds" in card
    assert "<details" not in card and "<summary" not in card


async def test_board_card_marks_a_declined_round_as_skipped(client):
    """A round the viewer pressed "Not applying" on gets its OWN mark and
    label on the card -- the same "skipped" vocabulary the concert page's fold
    chip uses -- not the blank todo mark of a round that has not opened."""
    async def build(seed):
        now = datetime.now(UTC)
        c = await seed.concert("declined", title="Declined then open")
        r1 = await seed.round(c, "FC presale", opens=now - timedelta(days=20),
                              closes=now - timedelta(days=10))
        await seed.open_round(c, "General presale")
        await record_round_outcome(seed.s, USER, r1.id, LotteryOutcome.NOT_APPLIED)

    await seeded(client.db, build)
    login(client)

    html = client.get("/").text
    card = html.split('data-event-id="declined"', 1)[1].split("</a>", 1)[0]

    assert 'class="rmark m-skip"' in card
    assert ">skipped<" in card
    # Not the mark it used to borrow, and not lost's either.
    assert 'class="rmark m-todo"' not in card
    assert "m-lost" not in card
    # The open round still gets its own rung, un-hidden.
    assert ">General presale<" in card


async def test_the_board_marks_a_cancelled_card(client):
    """The card that survives a cancellation is a record of what the reader
    holds, so it must say the show is off -- in the same badge vocabulary the
    concert page's cancelled legs already use, and on that card only."""
    async def build(seed):
        alive = await seed.concert("alive", title="Alive concert")
        await seed.open_round(alive, "Live round")
        dead = await seed.concert("dead", title="Dead concert", day_offset=None)
        day = ConcertDay(
            concert_id=dead.id, label="Day 1", cancelled=True,
            starts_at_utc=datetime.now(UTC) + timedelta(days=40),
        )
        seed.s.add(day)
        await seed.s.flush()
        # Leg-bound, the ordinary shape: the round is implicitly cancelled
        # with its leg, and the card must still carry it.
        won = await seed.open_round(dead, "1次先行")
        won.applies_to = [day.id]
        await seed.s.flush()
        await record_round_outcome(seed.s, USER, won.id, LotteryOutcome.WON)

    await seeded(client.db, build)
    login(client)

    board = client.get("/").text.split('id="board"', 1)[1]
    dead_card = board.split('data-event-id="dead"', 1)[1].split("</a>", 1)[0]
    alive_card = board.split('data-event-id="alive"', 1)[1].split("</a>", 1)[0]

    assert '<span class="badge cancelled">Cancelled</span>' in dead_card
    assert "Cancelled" not in alive_card
    # Exactly one badge on the whole board -- the live card gets none.
    assert board.count('class="badge cancelled"') == 1
    # The rung that explains the column is on the card even though its leg --
    # and so the round itself -- is cancelled.
    assert ">1次先行<" in dead_card
    # ... and the card makes no claim about time: no countdown pill, while the
    # live card still has one.
    assert "data-countdown" not in dead_card
    assert "data-countdown" in alive_card


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


async def test_home_rows_offer_no_outcome_correction(client):
    """The un-answer is a CONCERT-PAGE affordance: `capture_actions` defaults
    `correctable` to False and only `_round_rows.html` passes True, so Home's
    rendered markup is byte-identical to what it was before that feature.

    That is not tidiness. Home drops LOST and NOT_APPLIED rounds from "Coming
    up" entirely, so a correction offered here would be unreachable for
    exactly the rounds that need one -- and a destructive action does not
    belong in a one-tap flow.

    Three rows in the three states that WOULD render it on the concert page
    (applied-with-results-in, won, secured), so the assertion cannot pass
    merely because nothing was eligible.

    Mutation this catches: defaulting `correctable` to True, or passing it
    from `_deadline_rows.html`."""
    now = datetime.now(UTC)

    async def build(seed):
        for event_id, states in (
            ("applied-one", (LotteryOutcome.APPLIED,)),
            ("won-one", (LotteryOutcome.WON,)),
            ("paid-one", (LotteryOutcome.WON, LotteryOutcome.PAID)),
        ):
            c = await seed.concert(event_id, title=event_id)
            r = await seed.round(
                c, "FC lottery",
                opens=now - timedelta(days=30),
                closes=now - timedelta(days=10),
                results=now - timedelta(days=1),
                payment=now + timedelta(days=5),
            )
            for state in states:
                await record_round_outcome(seed.s, USER, r.id, state)

    await seeded(client.db, build)
    login(client)

    html = client.get("/").text
    # The three rows really are on the page and really are in those branches.
    assert ">I won</button>" in html   # applied, results in
    assert ">Paid</button>" in html    # won
    assert "Nothing to do" in html     # paid
    assert "/outcome/clear" not in html
    assert ">Change</button>" not in html


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


async def test_coming_up_drops_a_round_the_viewer_already_holds_a_ticket_for(client):
    """Once a round is won, later rounds selling the same leg are noise --
    the reminder planner has always dropped them, and Coming up now agrees."""
    now = datetime.now(UTC)

    async def build(seed):
        c = await seed.concert("covered", title="Covered concert", day_offset=60)
        won = await seed.round(
            c, "FC lottery", opens=now - timedelta(days=30), closes=now - timedelta(days=5),
            results=now - timedelta(days=1),
        )
        await seed.round(
            c, "General sale", opens=now - timedelta(days=1), closes=now + timedelta(days=7)
        )
        return won

    won = await seeded(client.db, build)
    login(client)

    # Scoped to the Coming up rows: the board legitimately keeps naming the
    # concert's open round, which is a campaign summary, not a question.
    rows = client.get("/").text.split('id="deadline-rows"', 1)[1]
    assert "General sale" in rows

    async with client.db() as s:
        await record_round_outcome(s, USER, won.id, LotteryOutcome.WON)
        await s.commit()

    rows = client.get("/").text.split('id="deadline-rows"', 1)[1]
    assert "General sale" not in rows


async def test_a_dead_concert_has_no_coming_up_rows(client):
    """Every leg cancelled means the show is not happening, so nothing about
    it is a question the reader can still answer. A General round survives
    `is_round_cancelled` (it is tied to no leg), which is exactly why Coming
    up needs the concert-level rule as well -- otherwise the row sits there
    offering an APPLIED press `record_round_outcome` will never let the
    reader take back."""
    now = datetime.now(UTC)

    async def build(seed):
        alive = await seed.concert("alive", title="Alive concert")
        await seed.round(alive, "Live round", closes=now + timedelta(days=3))
        dead = await seed.concert("dead", title="Dead concert")
        await seed.round(dead, "General sale", closes=now + timedelta(days=2))
        for d in (await seed.s.execute(
            select(ConcertDay).where(ConcertDay.concert_id == dead.id)
        )).scalars():
            d.cancelled = True

    await seeded(client.db, build)
    login(client)

    rows = client.get("/").text.split('id="deadline-rows"', 1)[1]
    assert "Live round" in rows       # the control
    assert "Dead concert" not in rows
    assert "General sale" not in rows


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
    """You cannot have applied to a round that has not opened, and recording
    APPLIED is a one-way write (record_round_outcome refuses to overwrite a
    starting state) -- so its row offers nothing to press.

    The gate predates the block layer, where it mattered four times over:
    upcoming_deadlines emits one row per FUTURE anchor, so this round's
    opens/closes/results each carried their own button pair. Blocks collapse a
    round to its soonest anchor, so the same round is now ONE member line --
    the multiplication is gone, the gate on that line is not."""
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
    rows_html = html.split('id="deadline-rows"', 1)[1]
    # Named once, in the block header -- not once per row as the flat list did.
    assert rows_html.count("Future round concert</a>") == 1
    # Two member lines: the round's soonest anchor, and the show itself.
    assert rows_html.count('class="row"') == 2
    assert "opens" in rows_html
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


async def test_a_multi_leg_row_asks_leg_by_leg(client):
    """Coming up shares `_capture_actions.html` with the concert page, so a
    round covering two legs asks about each of them here too. Its rows are a
    DIFFERENT dataclass (DeadlineRow, which carries no `covered` -- a covered
    row is dropped upstream instead), so this also pins that the shared macro
    renders against both shapes."""
    now = datetime.now(UTC)

    async def build(seed):
        c = await seed.concert("two-legs", title="Two leg concert", day_offset=60)
        second = ConcertDay(
            concert_id=c.id, label="Day 2",
            starts_at_utc=now + timedelta(days=61),
        )
        seed.s.add(second)
        await seed.s.flush()
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

    rows = client.get("/").text.split('id="deadline-rows"', 1)[1]
    assert "Won — Day 1" in rows
    assert "Lost — Day 2" in rows
    assert "Not going — Day 1" in rows
    assert f"/rounds/{round_.id}/day-result" in rows
    assert ">I won</button>" not in rows


async def test_a_partially_won_multi_leg_row_offers_lost_the_rest(client):
    """One leg secured turns the remaining question into "lost the rest?" --
    and the won leg stops being asked about."""
    now = datetime.now(UTC)

    async def build(seed):
        c = await seed.concert("part-win", title="Partial win concert", day_offset=60)
        second = ConcertDay(
            concert_id=c.id, label="Day 2",
            starts_at_utc=now + timedelta(days=61),
        )
        seed.s.add(second)
        await seed.s.flush()
        r = await seed.round(
            c, "FC lottery",
            opens=now - timedelta(days=30),
            closes=now - timedelta(days=10),
            results=now - timedelta(days=1),
            payment=now + timedelta(days=5),
        )
        await record_round_outcome(seed.s, USER, r.id, LotteryOutcome.APPLIED)
        first = (await seed.s.execute(
            select(ConcertDay.id)
            .where(ConcertDay.concert_id == c.id)
            .order_by(ConcertDay.starts_at_utc)
        )).scalars().first()
        await record_round_day_result(seed.s, USER, r.id, first, LegResult.WON)

    await seeded(client.db, build)
    login(client)

    rows = client.get("/").text.split('id="deadline-rows"', 1)[1]
    assert "Lost the rest" in rows
    assert "Lost — Day 2" in rows
    assert "Won — Day 1" not in rows
    assert "Won (all)" not in rows


async def test_a_lost_leg_withdraws_the_whole_round_won_shortcut(client):
    """Once ANY leg is answered the round is being resolved leg by leg, and a
    whole-round WON write would create a WON round with zero WON legs: it
    secures nothing, and the next "Lost — Day 2" settles the round LOST and
    erases the win. So the shortcut goes and the per-leg buttons stay -- the
    same answer `_apply_press` gives in the DM."""
    now = datetime.now(UTC)

    async def build(seed):
        c = await seed.concert("lost-first", title="Lost first concert", day_offset=60)
        seed.s.add(ConcertDay(
            concert_id=c.id, label="Day 2", starts_at_utc=now + timedelta(days=61),
        ))
        await seed.s.flush()
        r = await seed.round(
            c, "FC lottery",
            opens=now - timedelta(days=30),
            closes=now - timedelta(days=10),
            results=now - timedelta(days=1),
            payment=now + timedelta(days=5),
        )
        await record_round_outcome(seed.s, USER, r.id, LotteryOutcome.APPLIED)
        first = (await seed.s.execute(
            select(ConcertDay.id)
            .where(ConcertDay.concert_id == c.id)
            .order_by(ConcertDay.starts_at_utc)
        )).scalars().first()
        await record_round_day_result(seed.s, USER, r.id, first, LegResult.LOST)

    await seeded(client.db, build)
    login(client)

    rows = client.get("/").text.split('id="deadline-rows"', 1)[1]
    assert "Won (all)" not in rows
    assert "Won — Day 2" in rows
    assert "Lost — Day 2" in rows
    # No leg is won, so the whole round is still honestly losable.
    assert "Lost (all)" in rows


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
    assert "1 event in the catalogue" in html
    assert "2 events in the catalogue" not in html


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


async def test_home_and_the_outcome_fragment_render_the_same_block_count(client):
    """POST /rounds/{id}/outcome swaps the Coming up fragment back in. If Home
    renders a different number of BLOCKS than the fragment does, the swap
    silently changes the list length -- so both go through the one
    DEADLINE_ROWS_LIMIT default, which now caps CONCERTS.

    Fifteen concerts, one round each: the cap makes ten blocks, six of them
    above the page-level fold and the rest inside it. Both surfaces must
    render all ten either way -- a folded block is in the DOM, just closed."""
    async def build(seed):
        rounds = []
        for i in range(DEADLINE_ROWS_LIMIT + 5):
            c = await seed.concert(f"c-{i:02d}", title=f"Concert {i:02d}", day_offset=None)
            rounds.append(await seed.open_round(c, "R1", days=1 + i))
        return rounds[0].id

    round_id = await seeded(client.db, build)
    login(client)

    home = client.get("/").text
    home_blocks = home.count('class="cblock"')
    assert home_blocks == DEADLINE_ROWS_LIMIT
    # One concert per block, and one member row inside each -- the cap counts
    # concerts, not the rows they contain.
    assert home.count('class="row"') == DEADLINE_ROWS_LIMIT

    fragment = client.post(
        f"/rounds/{round_id}/outcome", data={"outcome": "applied"}, headers=HX,
    )
    assert fragment.status_code == 200
    assert fragment.text.count('class="cblock"') == home_blocks


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
    assert "Home shows" in html


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


# ── Task 1: Coming up, grouped into per-concert blocks ───────────────────


@pytest_asyncio.fixture()
async def blocks_seed(db):
    """The service-level counterpart to `seeded`: the same subscribed-ARTIST-tag
    world, but yielding the live `Seed` (and with it `seed.s`, the session) so a
    test can call `my_deadline_blocks` directly instead of reading the grouping
    back out of rendered HTML.

    Times here are relative to the real clock rather than a frozen NOW, because
    `Seed` builds its concert days from `datetime.now(UTC)` and a test mixing
    the two would compare against a moment nothing was seeded around.
    """
    async with db() as s:
        s.add(User(discord_id=USER, username="reiji"))
        await s.flush()
        tag = Tag(name="Aqours", kind=TagKind.ARTIST)
        s.add(tag)
        await s.flush()
        s.add(TagSubscription(user_id=USER, tag_id=tag.id))
        await s.flush()
        yield Seed(s, tag)


async def test_blocks_collapse_a_round_to_its_soonest_anchor(blocks_seed):
    """One round carrying a close, a result announcement and a payment is ONE
    thing to watch, not three rows. The block keeps the soonest anchor and
    folds nothing behind it, because there is nothing else here."""
    now = datetime.now(UTC)
    c = await blocks_seed.concert("aqours-live", day_offset=None)
    await blocks_seed.round(
        c, "FC lottery",
        opens=now - timedelta(days=1), closes=now + timedelta(days=1),
        results=now + timedelta(days=8), payment=now + timedelta(days=15),
    )

    blocks = await my_deadline_blocks(blocks_seed.s, USER, now=now)
    assert len(blocks) == 1
    assert blocks[0].others == ()
    assert blocks[0].lead.deadline.anchor is Anchor.CLOSES
    assert blocks[0].concert_title == "aqours-live"


async def test_standing_beats_time_for_the_lead(blocks_seed):
    """A payment you owe in three weeks outranks a round that has not even
    opened tomorrow: the lead is what wants you, not what is soonest."""
    now = datetime.now(UTC)
    c = await blocks_seed.concert("aqours-live", day_offset=None)
    round_a = await blocks_seed.round(
        c, "FC lottery",
        opens=now - timedelta(days=30), closes=now - timedelta(days=10),
        payment=now + timedelta(days=20),
    )
    round_b = await blocks_seed.round(
        c, "General sale",
        opens=now + timedelta(days=1), closes=now + timedelta(days=10),
    )
    await record_round_outcome(blocks_seed.s, USER, round_a.id, LotteryOutcome.WON, now)

    blocks = await my_deadline_blocks(blocks_seed.s, USER, now=now)
    assert blocks[0].lead.deadline.round_id == round_a.id
    assert [r.deadline.round_id for r in blocks[0].others] == [round_b.id]


async def test_a_settled_round_never_leads(blocks_seed):
    """LOST is over. Its results row is still worth showing (the block folds
    it in), but leading with it would head the block with a dead campaign."""
    now = datetime.now(UTC)
    c = await blocks_seed.concert("aqours-live", day_offset=None)
    settled = await blocks_seed.round(
        c, "FC lottery",
        opens=now - timedelta(days=10), closes=now - timedelta(days=1),
        results=now + timedelta(days=1),
    )
    live = await blocks_seed.round(
        c, "General sale",
        opens=now - timedelta(days=1), closes=now + timedelta(days=5),
    )
    await record_round_outcome(blocks_seed.s, USER, settled.id, LotteryOutcome.LOST, now)

    blocks = await my_deadline_blocks(blocks_seed.s, USER, now=now)
    assert blocks[0].lead.deadline.round_id == live.id
    assert [r.deadline.round_id for r in blocks[0].others] == [settled.id]


async def test_a_round_that_closed_without_you_never_leads(blocks_seed):
    """The other half of "settled", and the one that needs `closes_at_utc`:
    a round you never entered whose close has PASSED is a chance already gone,
    even though it still has a results announcement ahead of it and no
    outcome recorded. Standing alone cannot tell it apart from a live round --
    both carry `outcome is None` and `can_capture` True (it opened) -- so the
    lead rule reads the close off the row, and the row only has one because
    `my_deadline_rows` copies it there.

    Drop `DeadlineRow.closes_at_utc` and this concert leads with a dead
    round's results row purely because it is sooner."""
    now = datetime.now(UTC)
    c = await blocks_seed.concert("aqours-live", day_offset=None)
    gone = await blocks_seed.round(
        c, "FC lottery",
        opens=now - timedelta(days=10), closes=now - timedelta(days=1),
        results=now + timedelta(days=1),
    )
    live = await blocks_seed.round(
        c, "General sale",
        opens=now - timedelta(days=1), closes=now + timedelta(days=5),
    )

    blocks = await my_deadline_blocks(blocks_seed.s, USER, now=now)
    # The dead round's results row is SOONER, and would lead on time alone.
    assert blocks[0].lead.deadline.round_id == live.id
    assert [r.deadline.round_id for r in blocks[0].others] == [gone.id]
    # The field the rule reads is really on the row, not merely implied by it.
    assert blocks[0].lead.closes_at_utc is not None


async def test_others_stay_chronological(blocks_seed):
    """Only the LEAD is chosen by standing. Everything folded behind it reads
    as a diary again, soonest first -- so the second open round does not jump
    ahead of a round that opens before it."""
    now = datetime.now(UTC)
    c = await blocks_seed.concert("aqours-live", day_offset=None)
    lead = await blocks_seed.round(
        c, "Open now", opens=now - timedelta(days=1), closes=now + timedelta(days=2),
    )
    also_open = await blocks_seed.round(
        c, "Also open", opens=now - timedelta(days=1), closes=now + timedelta(days=3),
    )
    unopened = await blocks_seed.round(
        c, "Not open yet", opens=now + timedelta(days=1), closes=now + timedelta(days=9),
    )

    blocks = await my_deadline_blocks(blocks_seed.s, USER, now=now)
    assert blocks[0].lead.deadline.round_id == lead.id
    # `also_open` wants you and `unopened` does not, so the lead rule would
    # have ordered these two the other way round.
    assert [r.deadline.round_id for r in blocks[0].others] == [unopened.id, also_open.id]


async def test_blocks_sort_by_their_lead_moment(blocks_seed):
    """Between concerts it is the LEAD that competes, not the earliest row a
    concert happens to carry -- otherwise a block would sort on a line folded
    out of sight."""
    now = datetime.now(UTC)
    noisy = await blocks_seed.concert("aqours-live", day_offset=None)
    await blocks_seed.round(
        noisy, "Not open yet",
        opens=now + timedelta(days=1), closes=now + timedelta(days=20),
    )
    await blocks_seed.round(
        noisy, "Open now", opens=now - timedelta(days=1), closes=now + timedelta(days=5),
    )
    other = await blocks_seed.concert("muse-live", day_offset=None)
    await blocks_seed.round(
        other, "Open now", opens=now - timedelta(days=1), closes=now + timedelta(days=2),
    )

    blocks = await my_deadline_blocks(blocks_seed.s, USER, now=now)
    assert [b.event_id for b in blocks] == ["muse-live", "aqours-live"]


async def test_block_cap_counts_concerts_not_rows(blocks_seed):
    """Twelve concerts of three anchors each. The cap is ten CONCERTS, and the
    internal fetch is wide enough to actually find them -- grouping a
    limit-sized row fetch would have under-filled the list."""
    now = datetime.now(UTC)
    for i in range(12):
        c = await blocks_seed.concert(f"concert-{i:02d}", day_offset=None)
        await blocks_seed.round(
            c, "FC lottery",
            opens=now - timedelta(days=1),
            closes=now + timedelta(days=i + 1),
            results=now + timedelta(days=i + 1, hours=1),
            payment=now + timedelta(days=i + 1, hours=2),
        )

    # The regression this guards: a limit-sized ROW fetch reaches barely a
    # third of these concerts.
    rows = await my_deadline_rows(blocks_seed.s, USER, now=now, limit=DEADLINE_ROWS_LIMIT)
    assert len({r.deadline.event_id for r in rows}) < DEADLINE_ROWS_LIMIT

    blocks = await my_deadline_blocks(blocks_seed.s, USER, now=now)
    assert len(blocks) == 10
    assert [b.event_id for b in blocks] == [f"concert-{i:02d}" for i in range(10)]
    assert all(b.others == () for b in blocks)


async def test_event_start_only_concert_leads_with_the_show(blocks_seed):
    """A concert with no rounds ahead still has a block: the show itself."""
    now = datetime.now(UTC)
    await blocks_seed.concert("aqours-live", day_offset=30)

    blocks = await my_deadline_blocks(blocks_seed.s, USER, now=now)
    assert len(blocks) == 1
    assert blocks[0].lead.deadline.round_id is None
    assert blocks[0].lead.deadline.anchor is Anchor.EVENT_START
    assert blocks[0].others == ()
    assert blocks[0].starts_at_utc is not None


async def test_every_leg_keeps_its_own_show_row(blocks_seed):
    """Rows with no round id have nothing to collapse onto -- a two-night
    stand is two shows, and folding them together would lose a date."""
    now = datetime.now(UTC)
    c = await blocks_seed.concert("aqours-live", day_offset=30)
    blocks_seed.s.add(ConcertDay(
        concert_id=c.id, label="Day 2", starts_at_utc=now + timedelta(days=31),
    ))
    await blocks_seed.s.flush()

    blocks = await my_deadline_blocks(blocks_seed.s, USER, now=now)
    assert len(blocks) == 1
    assert len(blocks[0].others) == 1
    assert blocks[0].others[0].deadline.round_id is None


# ── Task 2: Coming up renders those blocks ───────────────────────────────


async def test_home_renders_one_block_header_per_concert(client):
    """The concert's name left the rows and became ONE header per block. Two
    concerts, three rounds between them: two headers, each naming its concert
    exactly once."""
    async def build(seed):
        c = await seed.concert("aqours-live", title="Aqours Live", day_offset=None)
        await seed.open_round(c, "Open now", days=2)
        await seed.open_round(c, "Also open", days=9)
        other = await seed.concert("muse-live", title="Muse Live", day_offset=None)
        await seed.open_round(other, "FC lottery", days=5)

    await seeded(client.db, build)
    login(client)

    # Scoped to the Coming up fragment: the board above names concerts too.
    rows = client.get("/").text.split('id="deadline-rows"', 1)[1]
    assert rows.count('class="cblock"') == 2
    assert rows.count('class="blockhead"') == 2
    assert rows.count(">Aqours Live</a>") == 1
    assert rows.count(">Muse Live</a>") == 1
    # Three rounds, three member lines -- grouping folds them, never drops them.
    assert rows.count('class="row"') == 3


async def test_the_block_header_carries_the_venue_and_the_performance_date(client):
    """`ConcertBlock.venue` and `.starts_at_utc` are rendered in exactly ONE
    place -- the block header's <small> -- so nothing else would catch them
    going missing. The date is a performance date, so it renders day-month
    with no zone (fmt_day_month), not the dual deadline shape."""
    async def build(seed):
        c = await seed.concert("aqours-live", title="Aqours Live", day_offset=None)
        venue = Tag(name="Zepp Haneda", kind=TagKind.VENUE, region="Kanto")
        seed.s.add(venue)
        await seed.s.flush()
        seed.s.add(ConcertTag(concert_id=c.id, tag_id=venue.id))
        seed.s.add(ConcertDay(
            concert_id=c.id, label="Day 1",
            starts_at_utc=datetime(2026, 10, 12, 10, 0, tzinfo=UTC),
        ))
        await seed.s.flush()
        await seed.open_round(c, "FC lottery")

    await seeded(client.db, build)
    login(client)

    rows = client.get("/").text.split('id="deadline-rows"', 1)[1]
    head = rows.split('class="blockhead"', 1)[1].split("</div>", 1)[0]
    # Venue, separator, day-month -- all three, in the header, in that order.
    assert "<small>📍 Zepp Haneda · 12 Oct</small>" in head


_DETAILS_TAG = re.compile(r"<details\b([^>]*)>")
# One attribute: a name, optionally followed by a quoted value. The value is
# CONSUMED by the match, so a word inside one (class="a open") is never
# rescanned as an attribute of its own.
_ATTR = re.compile(r'([\w-]+)(?:="([^"]*)")?')


def open_fold_keys(html: str) -> set[str]:
    """Every fold rendered OPEN, read by the same `data-fold` key the
    client-side restore listener (base.html) keys on. Asserting through the
    key rather than the literal tag keeps these tests honest: an attribute
    added between the class and the `open` would make a bare
    `'morerounds" open' not in html` pass vacuously.

    Parsed attribute-by-attribute rather than as one `data-fold="..." open`
    pattern, because that pattern has the very fault it exists to fix, one
    level up: it is order-coupled, so an attribute inserted BETWEEN the two
    returns an empty set -- and an empty set makes every NEGATIVE assertion
    here pass without testing anything."""
    keys = set()
    for attrs in _DETAILS_TAG.findall(html):
        parsed = dict(_ATTR.findall(attrs))
        if "open" in parsed and parsed.get("data-fold"):
            keys.add(parsed["data-fold"])
    return keys


async def test_a_folded_round_is_present_but_collapsed(client):
    """A fold is presentation, not filtering: the second round's capture form
    is IN the DOM and posts to the same target, it is merely behind a closed
    <details>. Rendering it only on expand would need a round trip and would
    make the fold a second, silent limit."""
    async def build(seed):
        c = await seed.concert("aqours-live", title="Aqours Live", day_offset=None)
        await seed.open_round(c, "Open now", days=2)
        return (await seed.open_round(c, "Also open", days=9)).id

    folded_id = await seeded(client.db, build)
    login(client)

    rows = client.get("/").text.split('id="deadline-rows"', 1)[1]
    assert '<details class="morerounds" data-fold="block-aqours-live">' in rows
    # Closed: no `open` attribute anywhere on that element.
    assert open_fold_keys(rows) == set()
    assert "+1 more round" in rows

    fold = rows.split('<details class="morerounds" data-fold="block-aqours-live">', 1)[1]
    assert "Also open" in fold
    assert f'/rounds/{folded_id}/outcome' in fold
    assert 'hx-target="#deadline-rows"' in fold


async def test_capturing_a_folded_round_swaps_the_block_back_with_its_standing(client):
    """The other half of the fold being real DOM: pressing a button INSIDE it
    must swap the whole fragment back with that round's new standing, and the
    block must come back with the same shape. The fold it lives in comes back
    OPEN (see the reopening tests at the end of this file); this test is about
    the CONTENT of the swap, so it splits on the class rather than the whole
    tag."""
    async def build(seed):
        c = await seed.concert("aqours-live", title="Aqours Live", day_offset=None)
        await seed.open_round(c, "Open now", days=2)
        return (await seed.open_round(c, "Also open", days=9)).id

    folded_id = await seeded(client.db, build)
    login(client)

    r = client.post(f"/rounds/{folded_id}/outcome", data={"outcome": "applied"}, headers=HX)
    assert r.status_code == 200
    rows = r.text.split('id="deadline-rows"', 1)[1]
    # One concert, one block, both rounds still rendered -- the fold neither
    # dropped the round it holds nor promoted it out.
    assert rows.count('class="cblock"') == 1
    assert rows.count('class="row"') == 2
    assert "+1 more round" in rows
    fold = rows.split('class="morerounds"', 1)[1]
    assert "Also open" in fold
    assert "Applied" in fold.split("</details>", 1)[0]


async def test_data_happens_carries_only_the_anchor_verb(client):
    """The tablet band (701-1040px) drops the what-happens COLUMN and folds it
    back into the title cell via `content: " · " attr(data-happens)`. That cell
    used to hold the CONCERT TITLE, so the attribute carried "<round> <verb>"
    to make "Aqours Live · FC lottery closes".

    The block header owns the concert title now and the cell names the ROUND,
    so carrying the label again would print it twice in one line -- "FC lottery
    · FC lottery closes". The attribute keeps only what the dropped column
    actually adds: the anchor verb."""
    async def build(seed):
        c = await seed.concert("aqours-live", title="Aqours Live", day_offset=None)
        await seed.open_round(c, "FC lottery")

    await seeded(client.db, build)
    login(client)

    rows = client.get("/").text.split('id="deadline-rows"', 1)[1]
    assert 'data-happens="closes"' in rows
    assert 'data-happens="FC lottery closes"' not in rows
    # The visible cell still names the round; only the folded copy shrank.
    assert "FC lottery" in rows


async def test_no_fold_link_for_a_single_round_concert(client):
    """Nothing to fold means no affordance at all -- an empty "+0 more" line
    would be noise on the common single-round block."""
    async def build(seed):
        c = await seed.concert("aqours-live", title="Aqours Live", day_offset=None)
        await seed.open_round(c, "FC lottery")

    await seeded(client.db, build)
    login(client)

    body = client.get("/").text
    assert '<div class="cblock">' in body
    assert "morerounds" not in body


async def test_the_page_level_fold_appears_only_past_six_blocks(client):
    """VISIBLE_BLOCKS concerts fit on the page; the rest go behind the second,
    page-level fold -- and are still rendered, so the swap and the fold agree
    on how much exists."""
    async def build(seed, n):
        for i in range(n):
            c = await seed.concert(f"c-{i:02d}", title=f"Concert {i:02d}", day_offset=None)
            await seed.open_round(c, "R1", days=1 + i)

    await seeded(client.db, lambda seed: build(seed, VISIBLE_BLOCKS))
    login(client)
    rows = client.get("/").text.split('id="deadline-rows"', 1)[1]
    assert rows.count('class="cblock"') == VISIBLE_BLOCKS
    assert "moreconcerts" not in rows

    async with client.db() as s:
        seed = Seed(s, (await s.execute(select(Tag))).scalars().first())
        for i in range(VISIBLE_BLOCKS, VISIBLE_BLOCKS + 2):
            c = await seed.concert(f"c-{i:02d}", title=f"Concert {i:02d}", day_offset=None)
            await seed.open_round(c, "R1", days=1 + i)
        await s.commit()

    rows = client.get("/").text.split('id="deadline-rows"', 1)[1]
    assert '<details class="moreconcerts" data-fold="more-concerts">' in rows
    assert "+2 more events" in rows
    assert rows.count('class="cblock"') == VISIBLE_BLOCKS + 2


async def test_home_block_folds_carry_stable_keys(client):
    """The MARKUP half of the client-side fold restore (spec §D): every
    <details> in this swappable region carries a `data-fold` key, so
    base.html's listener can remember which were open across an outerHTML
    swap without any per-caller plumbing. A headless test cannot fire the
    browser event, so what it pins is the contract the script reads: a
    per-block key (`block-{event_id}`, distinct per block, and stable across
    the swap because the event_id is) and the page-level `more-concerts`.

    Stable is the whole point -- a key derived from position would name a
    different block after a re-sort and reopen the wrong fold."""
    async def build(seed):
        for i in range(VISIBLE_BLOCKS + 2):
            c = await seed.concert(f"c-{i:02d}", title=f"Concert {i:02d}", day_offset=None)
            await seed.open_round(c, "R1", days=1 + i)
            await seed.open_round(c, "R2", days=20 + i)

    await seeded(client.db, build)
    login(client)

    rows = client.get("/").text.split('id="deadline-rows"', 1)[1]
    # One key per block, visible slice and overflow alike -- the overflow
    # blocks are the ones a page-level collapse takes off the screen.
    for i in range(VISIBLE_BLOCKS + 2):
        assert f'<details class="morerounds" data-fold="block-c-{i:02d}">' in rows
    assert '<details class="moreconcerts" data-fold="more-concerts">' in rows


# ── the swap reopens the fold the reader was acting in ───────────────────


async def test_capturing_a_folded_round_reopens_its_per_round_fold(client):
    """An outerHTML swap replaces the <details> element, so its open state is
    gone unless the server re-renders it. Mild here (the write landed, the
    board updated) but it still collapses the very fold the reader was acting
    in, so the response marks the fold that owns the round it just wrote."""
    async def build(seed):
        c = await seed.concert("aqours-live", title="Aqours Live", day_offset=None)
        await seed.open_round(c, "Open now", days=2)
        return (await seed.open_round(c, "Also open", days=9)).id

    folded_id = await seeded(client.db, build)
    login(client)

    r = client.post(f"/rounds/{folded_id}/outcome", data={"outcome": "applied"}, headers=HX)
    rows = r.text.split('id="deadline-rows"', 1)[1]
    assert open_fold_keys(rows) == {"block-aqours-live"}
    # The round it wrote really is the one behind that fold.
    fold = rows.split('class="morerounds"', 1)[1]
    assert "Also open" in fold.split("</details>", 1)[0]


async def test_capturing_a_round_in_an_overflow_block_reopens_the_page_fold(client):
    """The sharp case: a reader with more than VISIBLE_BLOCKS concerts expands
    "+N more events", presses a button on block 8, and the swap folds every
    overflow block away under them. The response reopens the page-level fold
    when the block that owns the written round sits in the overflow slice."""
    async def build(seed):
        overflow_round_id = None
        for i in range(VISIBLE_BLOCKS + 2):
            c = await seed.concert(f"c-{i:02d}", title=f"Concert {i:02d}", day_offset=None)
            r = await seed.open_round(c, "R1", days=1 + i)
            if i == VISIBLE_BLOCKS + 1:
                overflow_round_id = r.id
        return overflow_round_id

    overflow_id = await seeded(client.db, build)
    login(client)

    r = client.post(f"/rounds/{overflow_id}/outcome", data={"outcome": "applied"}, headers=HX)
    rows = r.text.split('id="deadline-rows"', 1)[1]
    assert "more-concerts" in open_fold_keys(rows)


async def test_get_home_renders_neither_fold_open(client):
    """The default path is untouched: a plain GET has no round to reopen
    around, so both folds render closed exactly as they did before."""
    async def build(seed):
        for i in range(VISIBLE_BLOCKS + 2):
            c = await seed.concert(f"c-{i:02d}", title=f"Concert {i:02d}", day_offset=None)
            await seed.open_round(c, "R1", days=1 + i)
            await seed.open_round(c, "R2", days=20 + i)

    await seeded(client.db, build)
    login(client)

    rows = client.get("/").text.split('id="deadline-rows"', 1)[1]
    assert '<details class="morerounds" data-fold="block-c-00">' in rows
    assert '<details class="moreconcerts" data-fold="more-concerts">' in rows
    assert open_fold_keys(rows) == set()


async def test_capturing_a_visible_blocks_lead_opens_no_per_round_fold(client):
    """The written round can still be the block's LEAD after the write, and a
    lead is not behind the per-round fold -- so nothing there needs reopening.
    The block's other member here is the show itself (no round id), which can
    never be the round that was written."""
    async def build(seed):
        c = await seed.concert("aqours-live", title="Aqours Live", day_offset=60)
        return (await seed.open_round(c, "Open now", days=2)).id

    lead_id = await seeded(client.db, build)
    login(client)

    r = client.post(f"/rounds/{lead_id}/outcome", data={"outcome": "applied"}, headers=HX)
    rows = r.text.split('id="deadline-rows"', 1)[1]
    # The show row is folded, and nothing reopens it.
    assert '<details class="morerounds" data-fold="block-aqours-live">' in rows
    assert open_fold_keys(rows) == set()
