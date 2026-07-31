"""The diff: what counts as already present, and what is merely a hint."""

import datetime as dt
from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Concert, ConcertDay, Tag
from app.db.service import (
    dismiss_lead,
    leads_matching_existing_legs,
    mark_leads_announced,
    open_leads,
    record_discovered,
)
from app.domain.eventernote import ActorEvent
from app.domain.types import TagKind

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


def _ev(event_id="1", day=15, title="show", venue="Zepp Haneda"):
    return ActorEvent(
        event_id=event_id, title=title, date=dt.date(2026, 11, day), venue=venue
    )


async def test_a_new_event_becomes_an_open_lead(db):
    async with db() as s:
        fresh = await record_discovered(s, [(_ev(), None)], NOW)
        await s.commit()
        assert [r.eventernote_event_id for r in fresh] == ["1"]
        assert len(await open_leads(s)) == 1


async def test_one_event_seen_via_several_tags_is_one_lead(db):
    """The anniversary concert lists nine catalogue tags. Without an id key the
    maintainer hears about it nine times."""
    async with db() as s:
        s.add_all([
            Tag(name=f"a{i}", kind=TagKind.ARTIST, slug=f"a{i}") for i in range(3)
        ])
        await s.flush()
        await record_discovered(s, [(_ev(), 1), (_ev(), 2), (_ev(), 3)], NOW)
        await s.commit()
        assert len(await open_leads(s)) == 1


async def test_a_second_sweep_returns_nothing_new(db):
    async with db() as s:
        await record_discovered(s, [(_ev(), None)], NOW)
        await s.commit()
    async with db() as s:
        again = await record_discovered(s, [(_ev(), None)], NOW)
        await s.commit()
        assert again == [], "an already-recorded event is not fresh"


async def test_an_announced_lead_is_not_re_announced(db):
    """Announced means the DM has said it once -- record_discovered stops
    returning it, so the next sweep cannot repeat it."""
    async with db() as s:
        fresh = await record_discovered(s, [(_ev(), None)], NOW)
        await mark_leads_announced(s, [r.id for r in fresh], NOW)
        await s.commit()
        assert await record_discovered(s, [(_ev(), None)], NOW) == []


async def test_an_announced_lead_is_still_open_for_triage(db):
    """Announced is not triaged (owner ruling, 2026-07-31). The sweep announces
    every fresh lead, including the ones the DM only COUNTS -- so if announcing
    closed the lead, the "+N more -- /admin/discoveries" line would point at an
    empty page and those leads would be reachable from nowhere."""
    async with db() as s:
        fresh = await record_discovered(s, [(_ev(), None)], NOW)
        await mark_leads_announced(s, [r.id for r in fresh], NOW)
        await s.commit()
        assert [row.id for row in await open_leads(s)] == [fresh[0].id]


async def test_a_dismissed_lead_stays_gone(db):
    async with db() as s:
        fresh = await record_discovered(s, [(_ev(), None)], NOW)
        await s.commit()
        assert await dismiss_lead(s, fresh[0].id, NOW) is True
        await s.commit()
        assert await open_leads(s) == []


async def test_an_event_already_held_by_a_leg_is_never_a_lead(db):
    """The exact branch: a leg carrying this event id means we have it."""
    async with db() as s:
        s.add(Concert(title="t", event_id="c1"))
        await s.flush()
        s.add(ConcertDay(
            concert_id=1, label="Day 1",
            starts_at_utc=datetime(2026, 11, 15, 9, 0, tzinfo=UTC),
            eventernote_event_id="1",
        ))
        await s.commit()
        fresh = await record_discovered(s, [(_ev(), None)], NOW)
        await s.commit()
        assert fresh == []
        assert await open_leads(s) == []


async def test_same_date_same_venue_is_a_HINT_not_a_suppression(db):
    """The matinee and evening shows of one day are two Eventernote events at
    one venue on one date, and two legs. Auto-suppressing on date-and-venue
    would hide exactly the second one."""
    async with db() as s:
        venue = Tag(name="Zepp Haneda", kind=TagKind.VENUE, slug="zepp-haneda")
        s.add_all([Concert(title="t", event_id="c1"), venue])
        await s.flush()
        s.add(ConcertDay(
            concert_id=1, label="昼公演", venue_tag_id=venue.id,
            starts_at_utc=datetime(2026, 11, 15, 4, 0, tzinfo=UTC),
        ))
        await s.commit()

        fresh = await record_discovered(s, [(_ev(event_id="99", title="夜公演"), None)], NOW)
        await s.commit()
        assert len(fresh) == 1, "the evening show is still reported"
        assert {r.id for r in fresh} == await leads_matching_existing_legs(s, fresh)


async def test_a_leg_whose_utc_instant_is_a_different_jst_day_is_no_hint(db):
    """2026-11-14 16:00 UTC is 2026-11-15 01:00 JST -- a different calendar day
    from the UTC one, and the comparison is against the JST date."""
    async with db() as s:
        venue = Tag(name="Zepp Haneda", kind=TagKind.VENUE, slug="zepp-haneda")
        s.add_all([Concert(title="t", event_id="c1"), venue])
        await s.flush()
        s.add_all([
            # 2026-11-14 16:00 UTC -> 2026-11-15 JST: hits the 15th's lead.
            ConcertDay(
                concert_id=1, label="A", venue_tag_id=venue.id,
                starts_at_utc=datetime(2026, 11, 14, 16, 0, tzinfo=UTC),
            ),
            # 2026-11-16 16:00 UTC -> 2026-11-17 JST: misses the 16th's.
            ConcertDay(
                concert_id=1, label="B", venue_tag_id=venue.id,
                starts_at_utc=datetime(2026, 11, 16, 16, 0, tzinfo=UTC),
            ),
        ])
        await s.commit()

        fresh = await record_discovered(
            s, [(_ev(event_id="15", day=15), None), (_ev(event_id="16", day=16), None)],
            NOW,
        )
        await s.commit()
        hinted = await leads_matching_existing_legs(s, fresh)
        by_id = {r.eventernote_event_id: r.id for r in fresh}
        assert hinted == {by_id["15"]}


async def test_a_leg_at_a_different_venue_on_the_same_day_is_no_hint(db):
    """The hint is date AND venue. Without this the date half alone would pass
    every test in this file, and a lead would be flagged "you may already have
    this" for any leg anywhere in Japan that night."""
    async with db() as s:
        elsewhere = Tag(name="Nippon Budokan", kind=TagKind.VENUE, slug="budokan")
        s.add_all([Concert(title="t", event_id="c1"), elsewhere])
        await s.flush()
        s.add(ConcertDay(
            concert_id=1, label="Day 1", venue_tag_id=elsewhere.id,
            starts_at_utc=datetime(2026, 11, 15, 4, 0, tzinfo=UTC),
        ))
        await s.commit()

        fresh = await record_discovered(s, [(_ev(venue="Zepp Haneda"), None)], NOW)
        await s.commit()
        assert len(fresh) == 1
        assert await leads_matching_existing_legs(s, fresh) == set()


async def test_a_venue_matching_only_via_name_en_is_a_hint(db):
    """Eventernote writes a venue under whichever name it is commonly listed
    by, so the Latin string it carries may only match the tag's name_en."""
    async with db() as s:
        venue = Tag(
            name="日本武道館", name_en="Nippon Budokan",
            kind=TagKind.VENUE, slug="nippon-budokan",
        )
        s.add_all([Concert(title="t", event_id="c1"), venue])
        await s.flush()
        s.add(ConcertDay(
            concert_id=1, label="Day 1", venue_tag_id=venue.id,
            starts_at_utc=datetime(2026, 11, 15, 4, 0, tzinfo=UTC),
        ))
        await s.commit()

        fresh = await record_discovered(s, [(_ev(venue="nippon budokan"), None)], NOW)
        await s.commit()
        assert {r.id for r in fresh} == await leads_matching_existing_legs(s, fresh)


async def test_record_discovered_batches_its_queries(db):
    """A sweep passes ~86 artists' worth of events in one call. Two queries,
    whatever the size -- a per-event lookup here is what makes a sweep crawl."""
    statements: list[str] = []

    async with db() as s:
        conn = await s.connection()
        event.listen(
            conn.sync_connection.engine, "before_cursor_execute",
            lambda c, cur, stmt, *a: statements.append(stmt),
        )
        events = [(_ev(event_id=str(i), day=1 + i % 20), None) for i in range(50)]
        await record_discovered(s, events, NOW)
        selects = [q for q in statements if q.lstrip().upper().startswith("SELECT")]
        assert len(selects) == 2, selects
