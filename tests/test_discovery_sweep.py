"""The sweep: one DM, no network in tests, and silence on a quiet day."""

import datetime as dt
from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base, DiscoveredEvent, DiscoveryState, Notification, Tag, User
from app.db.service import discovery_due
from app.discovery import DiscoveryFetchError, run_sweep
from app.domain.discovery_message import DM_LIST_LIMIT
from app.domain.types import TagKind

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
    queue_ops_alerts' precedent."""
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


async def test_discovery_is_due_when_it_has_never_run_and_again_after_a_day(db):
    async with db() as s:
        assert await discovery_due(s, NOW)
        s.add(DiscoveryState(id=1, last_run_at=NOW))
        await s.commit()
        assert not await discovery_due(s, NOW + dt.timedelta(hours=23))
        assert await discovery_due(s, NOW + dt.timedelta(hours=25))
