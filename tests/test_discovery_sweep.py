"""The sweep: one DM, no network in tests, and silence on a quiet day."""

import asyncio
import datetime as dt
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import (
    Base,
    Concert,
    ConcertDay,
    DiscoveredEvent,
    DiscoveryState,
    Notification,
    Tag,
    User,
)
from app.db.service import (
    discovery_due,
    discovery_status,
    request_sweep,
    stamp_discovery_run,
    sweep_requested,
)
from app.discovery import DiscoveryFetchError, run_sweep, sweep_one_tag
from app.domain.discovery_message import DM_LIST_LIMIT
from app.domain.types import TagKind
from app.scheduler import heartbeat

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
PAGE = """
<html><body>
<li><a href="/events/464372">Anniversary Day 2</a>
    <span>2026-11-15</span><a href="/places/1">Zepp Haneda</a></li>
</body></html>
"""


def _page(count: int) -> str:
    """A page of `count` distinct future events, newest first."""
    rows = [
        f'<li><a href="/events/{9000 + n}">Show {n}</a>'
        f'<span>2026-11-{(n % 28) + 1:02d}</span>'
        f'<a href="/places/1">Zepp Haneda</a></li>'
        for n in range(count)
    ]
    return "<html><body>" + "".join(rows) + "</body></html>"


def _page_for(n: int) -> str:
    """One artist's page, carrying an event id nobody else's page carries."""
    return (
        f'<html><body><li><a href="/events/{8000 + n}">Show {n}</a>'
        f'<span>2026-11-15</span><a href="/places/1">Zepp Haneda</a>'
        "</li></body></html>"
    )


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


async def _seed(s, url="https://www.eventernote.com/actors/Liyuu/34637"):
    s.add(User(discord_id=42, username="reiji"))
    s.add(Tag(name="Liyuu", kind=TagKind.ARTIST, slug="liyuu", eventernote_url=url))
    await s.commit()


async def test_a_sweep_records_leads_and_queues_one_dm(db, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", "42")
    async with db() as s:
        await _seed(s)

        async def fake_fetch(url, transport=None):
            return PAGE

        report = await run_sweep(s, NOW, fetcher=fake_fetch)
        await s.commit()
        assert report.new_leads == 1
        notes = (await s.execute(select(Notification))).scalars().all()
        assert len(notes) == 1
        assert notes[0].kind == "discovery"
        assert notes[0].concert_id is None
        assert "add-concert" in notes[0].body


async def test_a_quiet_sweep_sends_nothing(db, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", "42")
    async with db() as s:
        await _seed(s)

        async def fake_fetch(url, transport=None):
            return "<html></html>"

        await run_sweep(s, NOW, fetcher=fake_fetch)
        await s.commit()
        assert (await s.execute(select(Notification))).scalars().all() == []


async def test_a_second_sweep_does_not_re_announce(db, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", "42")

    async def fake_fetch(url, transport=None):
        return PAGE

    async with db() as s:
        await _seed(s)
        await run_sweep(s, NOW, fetcher=fake_fetch)
        await s.commit()
    async with db() as s:
        await run_sweep(s, NOW + dt.timedelta(days=1), fetcher=fake_fetch)
        await s.commit()
        assert len((await s.execute(select(Notification))).scalars().all()) == 1


async def test_one_artist_failing_does_not_abort_the_sweep(db, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", "42")
    async with db() as s:
        await _seed(s)
        s.add(Tag(
            name="Other", kind=TagKind.ARTIST, slug="other",
            eventernote_url="https://www.eventernote.com/actors/o/2",
        ))
        await s.commit()

        async def flaky(url, transport=None):
            if "/2" in url:
                raise DiscoveryFetchError("boom")
            return PAGE

        report = await run_sweep(s, NOW, fetcher=flaky)
        await s.commit()
        assert report.failed == 1 and report.fetched == 1
        assert report.new_leads == 1


async def test_a_tag_without_a_url_is_skipped(db, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", "42")
    async with db() as s:
        s.add(User(discord_id=42, username="reiji"))
        s.add(Tag(name="Test", kind=TagKind.ARTIST, slug="test", eventernote_url=None))
        await s.commit()

        async def boom(url, transport=None):
            raise AssertionError("should not fetch")

        report = await run_sweep(s, NOW, fetcher=boom)
        assert report.fetched == 0


async def test_an_admin_who_never_signed_in_gets_a_user_row(db, monkeypatch):
    """Notification.user_id is an FK to users.discord_id -- queuing without a
    row raises IntegrityError at flush, far from the cause. Follows
    evaluate_and_alert's precedent."""
    monkeypatch.setattr(settings, "admin_whitelist", "99")
    async with db() as s:
        s.add(Tag(
            name="Liyuu", kind=TagKind.ARTIST, slug="liyuu",
            eventernote_url="https://www.eventernote.com/actors/Liyuu/34637",
        ))
        await s.commit()

        async def fake_fetch(url, transport=None):
            return PAGE

        await run_sweep(s, NOW, fetcher=fake_fetch)
        await s.commit()
        assert await s.get(User, 99) is not None


async def test_a_url_that_is_not_an_actor_page_is_skipped(db, monkeypatch):
    """A stored URL that is an eventernote page but not an actor's (or another
    site entirely) yields no actor id, so there is nothing to fetch."""
    monkeypatch.setattr(settings, "admin_whitelist", "42")
    async with db() as s:
        s.add(User(discord_id=42, username="reiji"))
        s.add(Tag(
            name="Test", kind=TagKind.ARTIST, slug="test",
            eventernote_url="https://www.eventernote.com/events/464372",
        ))
        await s.commit()

        async def boom(url, transport=None):
            raise AssertionError("should not fetch")

        report = await run_sweep(s, NOW, fetcher=boom)
        assert report.fetched == 0 and report.failed == 0


async def test_many_leads_still_queue_exactly_one_dm(db, monkeypatch):
    """One sweep, one message. A notification per LEAD would put fifteen DMs in
    the maintainer's inbox on an ordinary day and dozens on the first sweep,
    which is the failure the digest shape exists to prevent."""
    monkeypatch.setattr(settings, "admin_whitelist", "42")
    async with db() as s:
        await _seed(s)

        async def fake_fetch(url, transport=None):
            return _page(15)

        report = await run_sweep(s, NOW, fetcher=fake_fetch)
        await s.commit()
        assert report.new_leads == 15
        notes = (await s.execute(select(Notification))).scalars().all()
        assert len(notes) == 1
        # The body names ten and counts the rest -- one message, whole truth.
        assert "15 new leads" in notes[0].body
        assert f"+{15 - DM_LIST_LIMIT} more" in notes[0].body


async def test_every_fresh_lead_is_marked_announced_not_only_the_listed_ones(db, monkeypatch):
    """The DM names DM_LIST_LIMIT leads and counts the rest, but ALL of them are
    announced. Marking only the named ten would trickle the first sweep's
    backlog out at ten a day for weeks, burying the real leads behind
    duplicates of concerts already held."""
    monkeypatch.setattr(settings, "admin_whitelist", "42")
    async with db() as s:
        await _seed(s)

        async def fake_fetch(url, transport=None):
            return _page(15)

        report = await run_sweep(s, NOW, fetcher=fake_fetch)
        await s.commit()
        assert report.announced == 15
        rows = (await s.execute(select(DiscoveredEvent))).scalars().all()
        assert len(rows) == 15
        assert [r.announced_at for r in rows] == [NOW] * 15


async def test_a_page_that_cannot_be_parsed_does_not_abort_the_sweep(db, monkeypatch):
    """A malformed date on a third-party page raises ValueError out of
    parse_actor_events (date(2026, 2, 30) is not a date), which is NOT a
    DiscoveryFetchError. Letting it escape would leave the clock unstamped, so
    the next tick would re-fetch every page up to the poisoned one -- a
    permanent 86-fetch-a-minute loop nobody asked for."""
    monkeypatch.setattr(settings, "admin_whitelist", "42")
    async with db() as s:
        await _seed(s)
        s.add(Tag(
            name="Other", kind=TagKind.ARTIST, slug="other",
            eventernote_url="https://www.eventernote.com/actors/o/2",
        ))
        await s.commit()

        async def poisoned(url, transport=None):
            if "/2" in url:
                return (
                    '<html><body><li><a href="/events/999">Show</a>'
                    "<span>2026-02-30</span></li></body></html>"
                )
            return PAGE

        report = await run_sweep(s, NOW, fetcher=poisoned)
        await s.commit()
        assert report.failed == 1 and report.fetched == 1
        # The healthy artist's lead still landed, and the sweep still announced.
        assert report.new_leads == 1
        assert len((await s.execute(select(Notification))).scalars().all()) == 1
        assert not await discovery_due(s, NOW + dt.timedelta(hours=1))


async def test_a_write_failure_still_stamps_the_state(db, monkeypatch):
    """The `finally`. If the diff itself raises, the sweep must still record
    that it ran -- otherwise the failure re-arms itself every 60 seconds."""
    monkeypatch.setattr(settings, "admin_whitelist", "42")

    async def boom(*_a, **_kw):
        raise RuntimeError("diff exploded")

    monkeypatch.setattr("app.discovery.record_discovered", boom)
    async with db() as s:
        await _seed(s)

        async def fake_fetch(url, transport=None):
            return PAGE

        with pytest.raises(RuntimeError, match="diff exploded"):
            await run_sweep(s, NOW, fetcher=fake_fetch)
        # The caller's transaction is its own; here nothing rolled it back.
        assert not await discovery_due(s, NOW + dt.timedelta(hours=1))


async def test_a_quiet_sweep_still_stamps_the_state(db, monkeypatch):
    """Without this the 24h clock never starts on a quiet day, and the sweep
    re-runs every tick -- 86 third-party fetches a minute."""
    monkeypatch.setattr(settings, "admin_whitelist", "42")
    async with db() as s:
        await _seed(s)

        async def fake_fetch(url, transport=None):
            return "<html></html>"

        await run_sweep(s, NOW, fetcher=fake_fetch)
        await s.commit()
        assert not await discovery_due(s, NOW + dt.timedelta(hours=1))


async def test_the_sweep_beats_the_heartbeat_per_artist(db, monkeypatch):
    """heartbeat.beat() fires BEFORE tick(), and a real sweep occupies the tick
    for minutes -- long enough to age past MAX_AGE_SECONDS and have /healthz
    report a healthy app as down. The loop IS alive, so it says so."""
    monkeypatch.setattr(settings, "admin_whitelist", "42")
    beats = []
    monkeypatch.setattr(heartbeat, "beat", lambda: beats.append(1))
    async with db() as s:
        # A skipped tag beats too, and pays no inter-fetch pause.
        s.add(Tag(
            name="Bad", kind=TagKind.ARTIST, slug="bad",
            eventernote_url="https://www.eventernote.com/events/1",
        ))
        await _seed(s)

        async def fake_fetch(url, transport=None):
            return PAGE

        await run_sweep(s, NOW, fetcher=fake_fetch)
        assert len(beats) == 2


async def test_the_sweep_stops_when_its_wall_clock_budget_runs_out(db, monkeypatch):
    """The sweep runs INLINE in the reminder tick and reminder_loop is strictly
    serial, so every second it holds is a second the next reminder pass is late.
    Nothing else bounds it -- httpx's timeout is per read, not per request -- and
    the per-artist heartbeat means the blackout raises no alarm. So the sweep
    keeps a wall clock and leaves the rest of the artists for tomorrow.

    The clock is faked rather than slept through: a real 240s budget is not a
    test. `monotonic` is called once for the deadline and once per artist, so
    the second artist's check is the one that lands past it."""
    monkeypatch.setattr(settings, "admin_whitelist", "42")
    monkeypatch.setattr("app.discovery.SWEEP_DELAY_SECONDS", 0)
    clock = iter([0.0, 0.0, 1000.0, 1000.0, 1000.0])
    monkeypatch.setattr("app.discovery.monotonic", lambda: next(clock))

    async with db() as s:
        s.add(User(discord_id=42, username="reiji"))
        for n in range(3):
            s.add(Tag(
                name=f"A{n}", kind=TagKind.ARTIST, slug=f"a{n}",
                eventernote_url=f"https://www.eventernote.com/actors/a/{n}",
            ))
        await s.commit()

        pages = {}

        async def fake_fetch(url, transport=None):
            pages[url] = pages.get(url, 0) + 1
            n = int(url.split("/events")[0].rsplit("/", 1)[1])
            return _page_for(n)

        report = await run_sweep(s, NOW, fetcher=fake_fetch)
        await s.commit()

        assert report.budget_exhausted is True
        assert report.fetched == 1, "the second artist's check landed past the deadline"
        assert len(pages) == 1, "nothing past the budget was even asked for"
        ids = {
            row.source_event_id
            for row in (await s.execute(select(DiscoveredEvent))).scalars()
        }
        assert ids == {"8000"}, "only the artist inside the budget was recorded"


async def _three_tags(s):
    s.add(User(discord_id=42, username="reiji"))
    for n in range(3):
        s.add(Tag(
            name=f"A{n}", kind=TagKind.ARTIST, slug=f"a{n}",
            eventernote_url=f"https://www.eventernote.com/actors/a/{n}",
        ))
    await s.commit()
    return [
        row.id for row in (await s.execute(select(Tag).order_by(Tag.id))).scalars()
    ]


def _order_recorder():
    """A fetcher that records which artists it was asked for, in order."""
    asked: list[int] = []

    async def fetch(url, transport=None):
        n = int(url.split("/events")[0].rsplit("/", 1)[1])
        asked.append(n)
        return _page_for(n)

    return asked, fetch


async def test_a_truncated_sweep_resumes_where_it_stopped(db, monkeypatch):
    """The budget alone is not enough. Tags sweep in `id` order, the deadline is
    recomputed every sweep, and ~374s of real work against a 240s budget reads
    roughly the first half of the list -- so without a cursor the SAME artists
    are read every day and the tail is never swept at all. A Tag id is creation
    order, so that tail is the artists the owner added most recently."""
    monkeypatch.setattr(settings, "admin_whitelist", "42")
    monkeypatch.setattr("app.discovery.SWEEP_DELAY_SECONDS", 0)

    async with db() as s:
        tag_ids = await _three_tags(s)

        # Sweep one: budget runs out before the second artist.
        clock = iter([0.0, 0.0, 1000.0])
        monkeypatch.setattr("app.discovery.monotonic", lambda: next(clock))
        asked, fetch = _order_recorder()
        report = await run_sweep(s, NOW, fetcher=fetch)
        await s.commit()
        assert report.budget_exhausted is True and asked == [0]
        state = await s.get(DiscoveryState, 1)
        assert state.sweep_cursor_tag_id == tag_ids[1], "stopped before the second"

        # Sweep two, same budget shape: it must start at the SECOND artist, not
        # read the first one all over again.
        clock = iter([0.0, 0.0, 1000.0])
        monkeypatch.setattr("app.discovery.monotonic", lambda: next(clock))
        asked, fetch = _order_recorder()
        await run_sweep(s, NOW + dt.timedelta(days=1), fetcher=fetch)
        await s.commit()
        assert asked == [1], "resumed, rather than re-reading the head of the list"
        state = await s.get(DiscoveryState, 1)
        assert state.sweep_cursor_tag_id == tag_ids[2]


async def test_a_resumed_sweep_wraps_without_re_reading_anyone(db, monkeypatch):
    """The wrap. Starting mid-list and running to the end has to come back round
    to the artists before the cursor -- otherwise resuming would starve the HEAD
    of the list instead of the tail -- and it must visit each exactly once, or a
    third of every sweep is spent re-reading pages it already read this run."""
    monkeypatch.setattr(settings, "admin_whitelist", "42")
    monkeypatch.setattr("app.discovery.SWEEP_DELAY_SECONDS", 0)
    monkeypatch.setattr("app.discovery.monotonic", lambda: 0.0)

    async with db() as s:
        tag_ids = await _three_tags(s)
        s.add(DiscoveryState(id=1, sweep_cursor_tag_id=tag_ids[2]))
        await s.commit()

        asked, fetch = _order_recorder()
        report = await run_sweep(s, NOW, fetcher=fetch)
        await s.commit()
        assert asked == [2, 0, 1], "started at the cursor and wrapped"
        assert report.fetched == 3
        state = await s.get(DiscoveryState, 1)
        assert state.sweep_cursor_tag_id is None, "a whole pass clears the cursor"


async def test_a_cursor_pointing_at_a_deleted_tag_starts_over(db, monkeypatch):
    """The cursor is a POSITION, not a foreign key: it resumes at the first id
    at or after it, and an id past the end of the list wraps to the head rather
    than sweeping nobody. A tag deleted between sweeps must not stall discovery
    permanently."""
    monkeypatch.setattr(settings, "admin_whitelist", "42")
    monkeypatch.setattr("app.discovery.SWEEP_DELAY_SECONDS", 0)
    monkeypatch.setattr("app.discovery.monotonic", lambda: 0.0)

    async with db() as s:
        await _three_tags(s)
        s.add(DiscoveryState(id=1, sweep_cursor_tag_id=9999))
        await s.commit()

        asked, fetch = _order_recorder()
        await run_sweep(s, NOW, fetcher=fetch)
        await s.commit()
        assert asked == [0, 1, 2]


async def test_a_truncated_sweep_still_counts_as_todays_sweep(db, monkeypatch):
    """The budget break must not skip the clock. A truncated sweep that left
    last_run_at unset would re-arm discovery_due and re-run 60 seconds later,
    truncating identically, forever -- the same failure the `finally` exists to
    prevent, arriving through a new exit."""
    monkeypatch.setattr(settings, "admin_whitelist", "42")
    monkeypatch.setattr("app.discovery.SWEEP_DELAY_SECONDS", 0)
    clock = iter([0.0, 1000.0])
    monkeypatch.setattr("app.discovery.monotonic", lambda: next(clock))

    async with db() as s:
        await _three_tags(s)

        async def boom(url, transport=None):
            raise AssertionError("the budget was spent before any fetch")

        report = await run_sweep(s, NOW, fetcher=boom)
        await s.commit()
        assert report.budget_exhausted is True and report.fetched == 0
        assert not await discovery_due(s, NOW + dt.timedelta(hours=1))
        state = await s.get(DiscoveryState, 1)
        assert state.last_truncated is True


async def test_a_hanging_fetch_is_cut_off_and_counted_failed(db, monkeypatch):
    """httpx's timeout is per READ, not per request, so a server dripping bytes
    under the 2 MB cap holds a connection open with no deadline at all -- and
    the budget is checked BETWEEN artists, so the fetch already in flight when
    it expires is unbounded. Without a total per-fetch deadline "the sweep is
    bounded" is a comment rather than a fact.

    The slow fetch is bounded at 5s rather than left hanging: with the deadline
    removed this test must FAIL in five seconds, not block the suite forever. A
    test that hangs under mutation is not a test anyone can run."""
    monkeypatch.setattr(settings, "admin_whitelist", "42")
    monkeypatch.setattr("app.discovery.FETCH_DEADLINE_SECONDS", 0.01)

    async with db() as s:
        await _seed(s)

        async def far_too_slow(url, transport=None):
            await asyncio.sleep(5)
            return PAGE

        report = await run_sweep(s, NOW, fetcher=far_too_slow)
        await s.commit()
        assert report.failed == 1 and report.fetched == 0
        assert not await discovery_due(s, NOW + dt.timedelta(hours=1))


async def test_a_sweep_inside_its_budget_reads_every_artist(db, monkeypatch):
    """The control: the same three tags with a clock that never runs out. Without
    it the assertions above could be satisfied by a sweep that simply stopped
    after one artist for any reason at all."""
    monkeypatch.setattr(settings, "admin_whitelist", "42")
    monkeypatch.setattr("app.discovery.SWEEP_DELAY_SECONDS", 0)
    monkeypatch.setattr("app.discovery.monotonic", lambda: 0.0)

    async with db() as s:
        s.add(User(discord_id=42, username="reiji"))
        for n in range(3):
            s.add(Tag(
                name=f"A{n}", kind=TagKind.ARTIST, slug=f"a{n}",
                eventernote_url=f"https://www.eventernote.com/actors/a/{n}",
            ))
        await s.commit()

        async def fake_fetch(url, transport=None):
            n = int(url.split("/events")[0].rsplit("/", 1)[1])
            return _page_for(n)

        report = await run_sweep(s, NOW, fetcher=fake_fetch)
        await s.commit()
        assert report.budget_exhausted is False
        assert report.fetched == 3


async def test_the_sweep_records_what_it_read_and_what_failed(db, monkeypatch):
    """A broken sweep and a quiet sweep produce identical output -- nothing. A
    site redesign that breaks the parser, a blocked IP and a genuinely quiet day
    are indistinguishable to the maintainer, and the first real sweep failed 12
    of 86 fetches with nothing saying so. The counts land on DiscoveryState,
    which is the durable surface /admin/discoveries can read; the scheduler's
    log line is not a monitor."""
    monkeypatch.setattr(settings, "admin_whitelist", "42")
    async with db() as s:
        await _seed(s)
        s.add(Tag(
            name="Other", kind=TagKind.ARTIST, slug="other",
            eventernote_url="https://www.eventernote.com/actors/o/2",
        ))
        await s.commit()

        async def flaky(url, transport=None):
            if "/2/" in url:
                raise DiscoveryFetchError("boom")
            return PAGE

        report = await run_sweep(s, NOW, fetcher=flaky)
        await s.commit()
        assert (report.fetched, report.failed) == (1, 1), "the fixture really did fail one"

        state = await s.get(DiscoveryState, 1)
        assert state.last_fetched == 1
        assert state.last_failed == 1


async def test_a_sweep_with_no_report_clears_the_counts(db):
    """NULL means UNKNOWN, not zero. scheduler.loop re-stamps after a sweep
    RAISED, with no report to give -- and leaving yesterday's 74/0 beside
    today's timestamp would read as a healthy sweep on the day the sweep died."""
    async with db() as s:
        s.add(DiscoveryState(id=1, last_run_at=NOW, last_fetched=74, last_failed=0))
        await s.commit()
        await stamp_discovery_run(s, NOW + dt.timedelta(days=1))
        await s.commit()
        state = await s.get(DiscoveryState, 1)
        assert state.last_fetched is None and state.last_failed is None


async def test_discovery_is_due_when_it_has_never_run_and_again_after_a_day(db):
    async with db() as s:
        assert await discovery_due(s, NOW)
        s.add(DiscoveryState(id=1, last_run_at=NOW))
        await s.commit()
        assert not await discovery_due(s, NOW + dt.timedelta(hours=23))
        assert await discovery_due(s, NOW + dt.timedelta(hours=25))


# -- The manual sweep request -------------------------------------------------
#
# These are about the REQUEST, not about fetching, so the tag table is left
# empty and this fetcher stands as the assertion that nothing reached for a
# page.


async def _never_called(url, transport=None):
    raise AssertionError(f"no page should have been fetched, but {url} was")


async def test_a_request_is_recorded_before_any_state_row_exists(db):
    """The button can be pressed before the first sweep ever runs, when there is
    no DiscoveryState row at all -- the same create-if-absent branch
    stamp_discovery_run has, for the same reason."""
    async with db() as s:
        assert not await sweep_requested(s)
        await request_sweep(s, NOW)
        await s.commit()
    async with db() as s:
        assert await sweep_requested(s)
        assert (await s.get(DiscoveryState, 1)).sweep_requested_at == NOW


async def test_an_existing_state_row_with_no_request_is_not_pending(db):
    """The control for the test above: `sweep_requested` reads the COLUMN, not
    the presence of the row -- every sweep that has ever run leaves a row
    behind, and a row-shaped answer would make the button permanently pending."""
    async with db() as s:
        s.add(DiscoveryState(id=1, last_run_at=NOW, last_fetched=86))
        await s.commit()
        assert not await sweep_requested(s)


async def test_a_sweep_clears_the_request(db):
    """Cleared by the stamp, so every exit reaches it. A request that survived
    its sweep would re-run 60 seconds later, forever -- the 86-fetches-a-minute
    trap the 24h clock exists to prevent."""
    async with db() as s:
        await request_sweep(s, NOW)
        await s.commit()
        await run_sweep(s, NOW, fetcher=_never_called)
        await s.commit()
        assert not await sweep_requested(s)


async def test_the_request_survives_a_sweep_that_never_ran(db):
    """The control: nothing but a sweep clears it. Reading the state (which
    /admin/discoveries does on every render) must not consume the request."""
    async with db() as s:
        await request_sweep(s, NOW)
        await s.commit()
        await discovery_status(s)
        await discovery_due(s, NOW)
        assert await sweep_requested(s)
        # The column, not just the predicate: a reader that answered True and
        # consumed the row on its way out would satisfy the line above and
        # still lose the request before the tick could act on it.
        assert (await s.get(DiscoveryState, 1)).sweep_requested_at == NOW


async def test_a_stamp_with_no_report_still_clears_the_request(db):
    """scheduler.loop's re-stamp after a failed sweep passes no counts at all.
    It must still clear the request, or a sweep that dies is retried every tick
    forever -- which is why the clear lives in stamp_discovery_run rather than
    beside run_sweep's own success path."""
    async with db() as s:
        await request_sweep(s, NOW)
        await s.commit()
        await stamp_discovery_run(s, NOW)
        await s.commit()
        assert not await sweep_requested(s)


# ── One artist, inline ────────────────────────────────────────────────────
#
# The narrow sweep behind the Tags page's "Check eventernote now" button. What
# matters here is not that it records leads -- run_sweep's tests already cover
# the diff it shares -- but the THREE things it must not do. Each would be a
# silent regression in the DAILY sweep, visible nowhere near this button.


async def _one_tag(s, url="https://www.eventernote.com/actors/Liyuu/34637"):
    """One artist tag, returned, with no admin user and no state row."""
    tag = Tag(name="Liyuu", kind=TagKind.ARTIST, slug="liyuu", eventernote_url=url)
    s.add(tag)
    await s.commit()
    return tag


async def test_a_one_tag_sweep_records_leads(db):
    """The positive control for the three below: without it they would all pass
    against a function that read nothing at all."""
    async with db() as s:
        tag = await _one_tag(s)

        async def fake_fetch(url, transport=None):
            return PAGE

        report = await sweep_one_tag(s, tag, NOW, fetcher=fake_fetch)
        await s.commit()
        assert report.status == "ok" and report.new_leads == 1
        row = (await s.execute(select(DiscoveredEvent))).scalar_one()
        assert row.source_event_id == "464372"
        assert row.first_seen_via_tag_id == tag.id


async def test_a_one_tag_sweep_does_not_stamp_the_daily_clock(db):
    """`last_run_at` is the 24h clock. Stamping it here would let a check of one
    artist displace that day's automatic sweep of all 86 -- and the counts on
    /admin/discoveries would describe one page as though they described the
    whole run."""
    async with db() as s:
        tag = await _one_tag(s)
        s.add(DiscoveryState(
            id=1, last_run_at=NOW - dt.timedelta(hours=23),
            last_fetched=86, last_failed=0, last_truncated=False,
        ))
        await s.commit()

        async def fake_fetch(url, transport=None):
            return PAGE

        report = await sweep_one_tag(s, tag, NOW, fetcher=fake_fetch)
        await s.commit()
        assert report.new_leads == 1, "it really did sweep"
        state = await s.get(DiscoveryState, 1)
        assert state.last_run_at == NOW - dt.timedelta(hours=23)
        assert state.last_fetched == 86 and state.last_failed == 0
        # The daily sweep is still due at the hour it was already due.
        assert not await discovery_due(s, NOW)


async def test_a_one_tag_sweep_does_not_move_the_sweep_cursor(db):
    """The cursor is accumulated progress through the FULL tag list. One artist
    read out of order is not progress through it, and writing here would make
    the next full sweep resume past artists it never read -- or, with None,
    restart at the head and starve the tail the cursor exists to protect."""
    async with db() as s:
        tag = await _one_tag(s)
        s.add(DiscoveryState(id=1, sweep_cursor_tag_id=52))
        await s.commit()

        async def fake_fetch(url, transport=None):
            return PAGE

        report = await sweep_one_tag(s, tag, NOW, fetcher=fake_fetch)
        await s.commit()
        assert report.new_leads == 1, "it really did sweep"
        assert (await s.get(DiscoveryState, 1)).sweep_cursor_tag_id == 52


async def test_a_one_tag_sweep_queues_no_dm(db, monkeypatch):
    """The operator is looking at the page they will be redirected to. A Discord
    DM about the button they just pressed is noise -- and the admin whitelist is
    deliberately set here, so this fails for the absent notice specifically and
    not merely for there being no admin to notify."""
    monkeypatch.setattr(settings, "admin_whitelist", "42")
    async with db() as s:
        s.add(User(discord_id=42, username="reiji"))
        tag = await _one_tag(s)

        async def fake_fetch(url, transport=None):
            return PAGE

        report = await sweep_one_tag(s, tag, NOW, fetcher=fake_fetch)
        await s.commit()
        assert report.new_leads == 1, "it really did find something to announce"
        assert (await s.execute(select(Notification))).scalars().all() == []
        # Nothing was announced, so the lead is still un-stamped and the next
        # DAILY sweep is free to report it in the ordinary way.
        assert (await s.execute(select(DiscoveredEvent))).scalar_one().announced_at is None


async def test_a_one_tag_sweep_reports_a_fetch_failure_instead_of_raising(db):
    """Eventernote timed out on 12 of 86 fetches in a live run, so this is the
    ordinary case, not the exotic one. It must come back as a distinguishable
    status -- reported as zero leads it would read as a healthy quiet check."""
    async with db() as s:
        tag = await _one_tag(s)

        async def boom(url, transport=None):
            raise DiscoveryFetchError("timed out")

        report = await sweep_one_tag(s, tag, NOW, fetcher=boom)
        await s.commit()
        assert report.status == "failed" and report.new_leads == 0
        assert (await s.execute(select(DiscoveredEvent))).scalars().all() == []


async def test_a_one_tag_sweep_survives_an_unparseable_page(db):
    """`Exception`, not DiscoveryFetchError: parse_actor_events builds a date()
    out of a regex match, so `2026年2月30` raises ValueError -- from the parse,
    not from the fetch."""
    async with db() as s:
        tag = await _one_tag(s)

        async def impossible_date(url, transport=None):
            return (
                '<html><body><li><a href="/events/1">X</a>'
                "<span>2026-02-30</span></li></body></html>"
            )

        assert (await sweep_one_tag(s, tag, NOW, fetcher=impossible_date)).status == "failed"


async def test_a_one_tag_sweep_of_a_tag_with_no_url_fetches_nothing(db):
    """The button is hidden without a URL, but the route is reachable anyway --
    and `no_actor` is its own status because the remedy is to fix the URL, not
    to try again later."""
    async with db() as s:
        tag = await _one_tag(s, url=None)
        report = await sweep_one_tag(s, tag, NOW, fetcher=_never_called)
        assert report.status == "no_actor" and report.new_leads == 0


async def test_a_one_tag_sweep_of_a_non_actor_url_fetches_nothing(db):
    """An eventernote URL that is not an actor page yields no actor id."""
    async with db() as s:
        tag = await _one_tag(s, url="https://www.eventernote.com/events/464372")
        assert (await sweep_one_tag(s, tag, NOW, fetcher=_never_called)).status == "no_actor"


async def test_a_one_tag_sweep_shares_the_daily_sweep_s_diff(db):
    """It must never grow its own idea of what the catalogue already has: a leg
    carrying the event id is branch 1 of record_discovered, and the lead is
    dropped entirely rather than reported."""
    async with db() as s:
        tag = await _one_tag(s)
        s.add(Concert(title="Held", event_id="held"))
        await s.flush()
        s.add(ConcertDay(
            concert_id=1, label="Day 2", eventernote_event_id="464372",
            starts_at_utc=datetime(2026, 11, 14, 16, 0, tzinfo=UTC),
        ))
        await s.commit()

        async def fake_fetch(url, transport=None):
            return PAGE

        report = await sweep_one_tag(s, tag, NOW, fetcher=fake_fetch)
        await s.commit()
        assert report.status == "ok" and report.new_leads == 0
        assert (await s.execute(select(DiscoveredEvent))).scalars().all() == []
