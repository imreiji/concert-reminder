"""The sweep: one DM, no network in tests, and silence on a quiet day."""

import datetime as dt
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base, DiscoveredEvent, DiscoveryState, Notification, Tag, User
from app.db.service import discovery_due, stamp_discovery_run
from app.discovery import DiscoveryFetchError, run_sweep
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
            row.eventernote_event_id
            for row in (await s.execute(select(DiscoveredEvent))).scalars()
        }
        assert ids == {"8000"}, "only the artist inside the budget was recorded"


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
