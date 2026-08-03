"""The diff: what counts as already present, and what is merely a hint."""

import datetime as dt
from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Concert, ConcertDay, DiscoveredEvent, Tag
from app.db.service import (
    DiscoveredInput,
    dismiss_lead,
    leads_matching_existing_legs,
    mark_leads_announced,
    open_leads,
    record_discovered,
)
from app.domain.eventernote import ActorEvent
from app.domain.types import DismissReason, TagKind

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


def _di(
    *,
    event_id="1",
    day=15,
    title="show",
    venue="Zepp Haneda",
    tag_id=None,
    source="eventernote",
    date_is_deadline=False,
):
    return DiscoveredInput(
        event=_ev(event_id=event_id, day=day, title=title, venue=venue),
        tag_id=tag_id,
        source=source,
        date_is_deadline=date_is_deadline,
    )


async def test_a_new_event_becomes_an_open_lead(db):
    async with db() as s:
        fresh = await record_discovered(s, [_di()], NOW)
        await s.commit()
        assert [r.source_event_id for r in fresh] == ["1"]
        assert len(await open_leads(s)) == 1


async def test_one_event_seen_via_several_tags_is_one_lead(db):
    """The anniversary concert lists nine catalogue tags. Without an id key the
    maintainer hears about it nine times."""
    async with db() as s:
        s.add_all([
            Tag(name=f"a{i}", kind=TagKind.ARTIST, slug=f"a{i}") for i in range(3)
        ])
        await s.flush()
        await record_discovered(
            s, [_di(tag_id=1), _di(tag_id=2), _di(tag_id=3)], NOW
        )
        await s.commit()
        assert len(await open_leads(s)) == 1


async def test_a_second_sweep_returns_nothing_new(db):
    async with db() as s:
        await record_discovered(s, [_di()], NOW)
        await s.commit()
    async with db() as s:
        again = await record_discovered(s, [_di()], NOW)
        await s.commit()
        assert again == [], "an already-recorded event is not fresh"


async def test_an_announced_lead_is_not_re_announced(db):
    """Announced means the DM has said it once -- record_discovered stops
    returning it, so the next sweep cannot repeat it."""
    async with db() as s:
        fresh = await record_discovered(s, [_di()], NOW)
        await mark_leads_announced(s, [r.id for r in fresh], NOW)
        await s.commit()
        assert await record_discovered(s, [_di()], NOW) == []


async def test_an_announced_lead_is_still_open_for_triage(db):
    """Announced is not triaged (owner ruling, 2026-07-31). The sweep announces
    every fresh lead, including the ones the DM only COUNTS -- so if announcing
    closed the lead, the "+N more -- /admin/discoveries" line would point at an
    empty page and those leads would be reachable from nowhere."""
    async with db() as s:
        fresh = await record_discovered(s, [_di()], NOW)
        await mark_leads_announced(s, [r.id for r in fresh], NOW)
        await s.commit()
        assert [row.id for row in await open_leads(s)] == [fresh[0].id]


async def test_a_dismissed_lead_stays_gone(db):
    async with db() as s:
        fresh = await record_discovered(s, [_di()], NOW)
        await s.commit()
        assert await dismiss_lead(s, fresh[0].id, NOW, DismissReason.OTHER) is True
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
        fresh = await record_discovered(s, [_di()], NOW)
        await s.commit()
        assert fresh == []
        assert await open_leads(s) == []


async def test_a_lead_that_became_a_concert_leaves_the_queue(db):
    """The maintainer's happy path, end to end: a lead is announced, an agent
    turns it into a draft, the import stamps the Eventernote id onto the leg --
    and the NEXT sweep must close the lead against that concert. Without a
    writer for `concert_id` the review page only ever grew, and the sole exit
    was pressing Dismiss on a row the catalogue had already resolved."""
    async with db() as s:
        fresh = await record_discovered(s, [_di()], NOW)
        await s.commit()
        lead_id = fresh[0].id
        assert [row.id for row in await open_leads(s)] == [lead_id]

        # The catalogue gains the concert, carrying the id the import stamped.
        s.add(Concert(title="t", event_id="c1"))
        await s.flush()
        s.add(ConcertDay(
            concert_id=1, label="Day 1",
            starts_at_utc=datetime(2026, 11, 15, 9, 0, tzinfo=UTC),
            eventernote_event_id="1",
        ))
        await s.commit()

        assert await record_discovered(s, [_di()], NOW) == []
        await s.commit()
        assert await open_leads(s) == [], "the lead closed itself"
        row = await s.get(DiscoveredEvent, lead_id)
        assert row.concert_id == 1, "bound to the concert, not deleted"
        assert row.dismissed_at is None, "closed by being catalogued, not waved off"


async def test_binding_a_lead_leaves_an_unrelated_one_open(db):
    """The control for the test above: closing one lead must not empty the
    queue, so that assertion is about the binding and not about `open_leads`
    going quiet for some other reason."""
    async with db() as s:
        fresh = await record_discovered(s, [_di(), _di(event_id="2")], NOW)
        await s.commit()
        other_id = [row.id for row in fresh if row.source_event_id == "2"][0]

        s.add(Concert(title="t", event_id="c1"))
        await s.flush()
        s.add(ConcertDay(
            concert_id=1, label="Day 1",
            starts_at_utc=datetime(2026, 11, 15, 9, 0, tzinfo=UTC),
            eventernote_event_id="1",
        ))
        await s.commit()

        await record_discovered(s, [_di(), _di(event_id="2")], NOW)
        await s.commit()
        assert [row.id for row in await open_leads(s)] == [other_id]


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

        fresh = await record_discovered(s, [_di(event_id="99", title="夜公演")], NOW)
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
            s, [_di(event_id="15", day=15), _di(event_id="16", day=16)],
            NOW,
        )
        await s.commit()
        hinted = await leads_matching_existing_legs(s, fresh)
        by_id = {r.source_event_id: r.id for r in fresh}
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

        fresh = await record_discovered(s, [_di(venue="Zepp Haneda")], NOW)
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

        fresh = await record_discovered(s, [_di(venue="nippon budokan")], NOW)
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
        events = [_di(event_id=str(i), day=1 + i % 20) for i in range(50)]
        await record_discovered(s, events, NOW)
        selects = [q for q in statements if q.lstrip().upper().startswith("SELECT")]
        assert len(selects) == 2, selects


async def test_binding_leads_to_concerts_is_batched_too(db):
    """The lead-binding branch is skipped entirely when no leg holds an incoming
    id, so the test above never reaches it -- a per-event `_bind_leads_to_concerts`
    would sail through it. This fixture holds ten of the fifty, so the binding
    query really runs: THREE selects, not fifty-two."""
    statements: list[str] = []

    async with db() as s:
        s.add(Concert(title="t", event_id="c1"))
        await s.flush()
        for i in range(10):
            s.add(ConcertDay(
                concert_id=1, label=f"Day {i}",
                starts_at_utc=datetime(2026, 11, 15, 9, 0, tzinfo=UTC),
                eventernote_event_id=str(i),
            ))
        await s.commit()

        conn = await s.connection()
        event.listen(
            conn.sync_connection.engine, "before_cursor_execute",
            lambda c, cur, stmt, *a: statements.append(stmt),
        )
        events = [_di(event_id=str(i), day=1 + i % 20) for i in range(50)]
        fresh = await record_discovered(s, events, NOW)
        assert len(fresh) == 40, "the ten held ones really were held"
        selects = [q for q in statements if q.lstrip().upper().startswith("SELECT")]
        assert len(selects) == 3, selects


async def test_a_calendar_sourced_input_persists_source_and_deadline_flag(db):
    """A DiscoveredInput from the imas ticket calendar carries a non-default
    source and date_is_deadline=True -- both must land on the fresh row, not
    just the Eventernote defaults."""
    async with db() as s:
        fresh = await record_discovered(
            s,
            [_di(
                event_id="imas-tix:abc@google.com",
                source="imas-tix",
                date_is_deadline=True,
            )],
            NOW,
        )
        await s.commit()
        assert len(fresh) == 1
        row = fresh[0]
        assert row.source_event_id == "imas-tix:abc@google.com"
        assert row.source == "imas-tix"
        assert row.date_is_deadline is True


async def test_an_eventernote_shaped_input_defaults_source_and_deadline_flag(db):
    """The daily sweep's inputs never set source/date_is_deadline explicitly --
    the dataclass defaults must be what lands on the row."""
    async with db() as s:
        fresh = await record_discovered(s, [_di()], NOW)
        await s.commit()
        assert len(fresh) == 1
        row = fresh[0]
        assert row.source == "eventernote"
        assert row.date_is_deadline is False


async def test_a_re_sighted_calendar_uid_only_refreshes_last_seen_at(db):
    """Same shape as test_a_second_sweep_returns_nothing_new, but for a
    calendar-sourced id: a re-sighting must not duplicate the row or touch
    anything besides last_seen_at."""
    later = NOW + dt.timedelta(days=1)
    async with db() as s:
        first = await record_discovered(
            s,
            [_di(event_id="imas-tix:abc@google.com", source="imas-tix", date_is_deadline=True)],
            NOW,
        )
        await s.commit()
        lead_id = first[0].id
    async with db() as s:
        again = await record_discovered(
            s,
            [_di(event_id="imas-tix:abc@google.com", source="imas-tix", date_is_deadline=True)],
            later,
        )
        await s.commit()
        assert again == [], "an already-recorded event is not fresh"
        row = await s.get(DiscoveredEvent, lead_id)
        assert row.last_seen_at == later
        assert row.first_seen_at == NOW, "only last_seen_at moves on a re-sighting"


async def test_a_calendar_id_never_collides_with_a_bare_numeric_id(db):
    """The calendar id is namespaced ("<feed key>:<uid>"), so an Eventernote
    event whose bare numeric id happens to match the tail of a calendar UID
    must still land as a second, distinct row."""
    async with db() as s:
        fresh = await record_discovered(
            s,
            [
                _di(event_id="123"),
                _di(event_id="imas-tix:123", source="imas-tix", date_is_deadline=True),
            ],
            NOW,
        )
        await s.commit()
        assert len(fresh) == 2
        assert {row.source_event_id for row in fresh} == {"123", "imas-tix:123"}
