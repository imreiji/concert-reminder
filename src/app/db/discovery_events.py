"""`DiscoveredEvent` rows: leads found by the discovery sweep.

Named `discovery_events`, NOT `discovery`: `app/discovery.py` is the sweep
RUNNER and imports this layer, so two modules a namespace apart sharing one
name would make every import site ambiguous to a reader. This module stores
leads; that one goes and finds them.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ConcertDay,
    DiscoveredEvent,
)
from app.domain.eventernote import ActorEvent
from app.domain.types import (
    DismissReason,
)

# ── Eventernote discovery ────────────────────────────────────────────────
#
# The diff: given performances scraped off an artist's Eventernote page,
# which ones does the catalogue not already have? A lead is reported unless
# one of these holds, checked in that order:
#
#   1. an existing ConcertDay already carries that eventernote_event_id --
#      we have it, say nothing (and if a lead for it is still open, bind it to
#      that concert, which is how a lead leaves the review queue by being
#      catalogued rather than by being dismissed);
#   2. it was dismissed -- never mention it again;
#   3. it was already announced -- do not re-announce;
#   4. otherwise it is a new lead.
#
# What is deliberately NOT on that list is a same-date-same-venue collision
# with an existing leg. 昼公演 and 夜公演 are two separate Eventernote events
# on one date at one venue, and two legs of one concert; suppressing on date
# plus venue would hide exactly the second show. That comparison is a HINT,
# computed separately by leads_matching_existing_legs so a surface can mark a
# lead "you may already have this" -- it never removes anything.


async def _bind_leads_to_concerts(
    session: AsyncSession, held: Mapping[str, int]
) -> None:
    """THE writer for `DiscoveredEvent.concert_id`, and the loop's other end.

    `open_leads` reads that column and nothing wrote it, so the maintainer's
    happy path -- lead, agent, draft, import (which stamps the Eventernote id
    onto the leg) -- left the review page dirtier rather than cleaner, and the
    only exit was pressing Dismiss by hand on a row the catalogue already
    resolved.

    Called from `record_discovered`'s branch 1 and BEFORE it drops the held ids:
    once an id is dropped it is never looked up in `discovered_events` at all,
    so a later sweep could not even see the row it needed to close. A lead that
    is not stored is simply absent here, which is the common case and costs one
    empty `IN ()`-free query.

    Only writes a row whose `concert_id` disagrees, so a lead already bound is
    not re-dirtied on every sweep. Nothing else on the row is touched: a bound
    lead is out of the queue and its `announced_at`/`dismissed_at` history is
    the record of how it got there.
    """
    if not held:
        return
    rows = (await session.execute(
        select(DiscoveredEvent)
        .where(DiscoveredEvent.source_event_id.in_(list(held)))
    )).scalars()
    changed = False
    for row in rows:
        concert_id = held[row.source_event_id]
        if row.concert_id != concert_id:
            row.concert_id = concert_id
            changed = True
    if changed:
        await session.flush()


@dataclass(frozen=True)
class DiscoveredInput:
    """One sighting of one event, whichever pipeline saw it.

    `record_discovered`'s input contract, the way `NoticeContext` is for
    notices -- kept beside the function it feeds rather than in `domain/`,
    since it names nothing domain-pure beyond `ActorEvent`.
    """

    event: ActorEvent
    # The surfacing tag, Eventernote only -- a calendar feed carries no tag.
    tag_id: int | None = None
    # Which pipeline produced it: "eventernote", or a CalendarFeed.key.
    source: str = "eventernote"
    # True when `event.date` is an application deadline, not a performance
    # date (the imas ticket calendar) -- see DiscoveredEvent.date_is_deadline.
    date_is_deadline: bool = False


async def record_discovered(
    session: AsyncSession,
    events: Sequence[DiscoveredInput],
    now: datetime,
) -> list[DiscoveredEvent]:
    """Upsert one row per source event id; return only the FRESH rows.

    A sweep passes ~86 artists' worth of events in one call, so this is two
    queries total regardless of size -- one to find the ids legs already
    hold, one to load the DiscoveredEvent rows for the rest -- never a query
    per event. A third runs only when a leg holds one of the incoming ids,
    to close those leads (`_bind_leads_to_concerts`); still batched, still
    independent of how many events arrived.
    """
    if not events:
        return []

    # One event surfaced by nine artist tags arrives here nine times; the
    # first sighting wins, which is what first_seen_via_tag_id means.
    incoming: dict[str, DiscoveredInput] = {}
    for di in events:
        incoming.setdefault(di.event.event_id, di)

    # Branch 1, one query. These are dropped entirely and never stored: a leg
    # carrying the id is the catalogue saying it already has this. The CONCERT
    # comes back with the id because branch 1 is also where a lead's life ends
    # -- see _bind_leads_to_concerts.
    held: dict[str, int] = {}
    for event_id, concert_id in (await session.execute(
        select(ConcertDay.eventernote_event_id, ConcertDay.concert_id)
        .where(ConcertDay.eventernote_event_id.in_(list(incoming)))
    )).all():
        held.setdefault(event_id, concert_id)
    await _bind_leads_to_concerts(session, held)

    remaining = [event_id for event_id in incoming if event_id not in held]
    if not remaining:
        return []

    # Branches 2 and 3, one query. Dismissed and announced leads are rows in
    # this same table, so the lookup a repeat sighting needs answers all three.
    existing = {
        row.source_event_id: row for row in (await session.execute(
            select(DiscoveredEvent)
            .where(DiscoveredEvent.source_event_id.in_(remaining))
        )).scalars()
    }

    fresh: list[DiscoveredEvent] = []
    for event_id in remaining:
        di = incoming[event_id]
        row = existing.get(event_id)
        if row is not None:
            # Seen again. Nothing else is touched: re-writing the title or the
            # surfacing tag would let a later sweep quietly rewrite a lead the
            # maintainer has already read, and re-writing dismissed_at would
            # resurrect something explicitly killed.
            row.last_seen_at = now
            continue
        row = DiscoveredEvent(
            source_event_id=di.event.event_id,
            title=di.event.title,
            event_date=di.event.date,
            venue=di.event.venue,
            first_seen_via_tag_id=di.tag_id,
            source=di.source,
            date_is_deadline=di.date_is_deadline,
            first_seen_at=now,
            last_seen_at=now,
        )
        session.add(row)
        fresh.append(row)

    # Flush, not commit: callers need the ids (to announce them), the
    # transaction stays theirs.
    await session.flush()
    return fresh


async def open_leads(session: AsyncSession) -> list[DiscoveredEvent]:
    """Leads still awaiting triage -- not dismissed, not bound to a concert.
    Newest performance date first.

    ANNOUNCED IS NOT TRIAGED, and `announced_at` is deliberately NOT a filter
    here (owner ruling, 2026-07-31). It exists for one job: stop the daily DM
    repeating a lead. The real exits from this queue are `dismissed_at` (waved
    off) and `concert_id` (it became a concert).

    Filtering on it shipped once and was a hole with no bottom: the sweep marks
    EVERY fresh lead announced, listed or merely counted (see run_sweep), so the
    first sweep's DM would name ten, say "+30 more -- /admin/discoveries", and
    send the maintainer to an empty page -- with those thirty reachable from
    nowhere and never announced again.
    """
    return list((await session.execute(
        select(DiscoveredEvent)
        .where(
            DiscoveredEvent.dismissed_at.is_(None),
            DiscoveredEvent.concert_id.is_(None),
        )
        .order_by(DiscoveredEvent.event_date.desc(), DiscoveredEvent.id.desc())
    )).scalars())


async def api_lead_rows(
    session: AsyncSession, *, limit: int = 200, offset: int = 0
) -> tuple[list[dict], int]:
    """Open leads as JSON rows, plus the pre-paging total.

    Built on `open_leads`, so the API and /admin/discoveries agree on what
    "open" means -- not dismissed, not bound to a concert, and deliberately NOT
    filtered on announced_at (announced is not triaged). Its sort is already
    (event_date DESC, id DESC), which is totally ordered and safe to page.

    `date_is_deadline` is not optional decoration: the imas feed's DTSTART is
    an application deadline, and an agent reading it as a performance date
    would file the wrong thing.

    Does NOT carry the same-date-same-venue collision hint
    `/admin/discoveries` computes (`leads_matching_existing_legs`,
    `db/drafts.py`): that helper runs a second query and a JST date
    conversion per row, and is worth adding here only when an agent
    consumer actually needs it -- a wrong hint would be worse than an
    absent one.
    """
    leads = await open_leads(session)
    total = len(leads)
    return [
        {
            "id": r.id,
            "source": r.source,
            "source_event_id": r.source_event_id,
            "title": r.title,
            "event_date": r.event_date.isoformat(),
            "date_is_deadline": r.date_is_deadline,
            "venue": r.venue,
            "first_seen_via_tag_id": r.first_seen_via_tag_id,
            "first_seen_at": r.first_seen_at.isoformat(),
            "announced_at": r.announced_at.isoformat() if r.announced_at else None,
        }
        for r in leads[offset : offset + limit]
    ], total


async def dismissed_reason_counts(session: AsyncSession) -> dict[str, int]:
    """How many leads were dismissed as each taxonomy class.

    Rows with a NULL reason are EXCLUDED rather than bucketed as `other`: they
    predate the column, and folding them in would invent a human judgment in
    the one place whose value is that every entry is a real one.
    """
    rows = await session.execute(
        select(DiscoveredEvent.dismiss_reason, func.count())
        .where(DiscoveredEvent.dismiss_reason.is_not(None))
        .group_by(DiscoveredEvent.dismiss_reason)
    )
    return {reason: n for reason, n in rows.all()}


async def dismiss_lead(
    session: AsyncSession, lead_id: int, now: datetime, reason: DismissReason
) -> bool:
    """Kill a lead for good, recording which taxonomy class it was.

    False when there was nothing to dismiss (an unknown id, or one already
    dismissed) so a caller can 404 rather than report a write that did not
    happen.

    `reason` is required rather than defaulted on purpose: a column added after
    the fact that quietly accepts a default is how the concert draft silently
    shipped without characters, and there is exactly one production caller.
    """
    row = await session.get(DiscoveredEvent, lead_id)
    if row is None or row.dismissed_at is not None:
        return False
    row.dismissed_at = now
    row.dismiss_reason = reason
    await session.flush()
    return True
