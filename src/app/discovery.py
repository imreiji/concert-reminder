"""Eventernote discovery: the fetch, and the daily sweep.

Sits ABOVE db/ like app/ops.py: it imports domain/ and db.service, and nothing
in db/ imports it. The parser is pure and lives in domain/eventernote.py; the
host-pinned fetch is shared with the ramen.events importer and lives in
app/fetching.py -- ONE copy of that guard, deliberately, so a weakness found in
it cannot be fixed in one caller and missed in the other.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import DiscoveredEvent, Notification, Tag, User
from app.db.service import (
    ensure_user,
    leads_matching_existing_legs,
    mark_leads_announced,
    record_discovered,
    stamp_discovery_run,
)
from app.domain.discovery_message import DM_LIST_LIMIT, Lead, build_discovery_dm
from app.domain.eventernote import (
    HOST,
    ActorEvent,
    actor_events_url,
    actor_id_from_url,
    future_events,
    parse_actor_events,
)
from app.domain.timezones import utc_to_jst
from app.fetching import FetchError, fetch_html

log = logging.getLogger(__name__)

ALLOWED_HOST = HOST
USER_AGENT = "dekimasen.app/1.0 (event discovery)"
# Sequential with a pause: 86 parallel requests at a third party is rude and is
# how an IP gets blocked.
SWEEP_DELAY_SECONDS = 1.0


class DiscoveryFetchError(Exception):
    """A page could not be fetched. One artist failing must not abort a sweep."""


async def fetch_actor_events(
    url: str, transport: httpx.AsyncBaseTransport | None = None
) -> str:
    """Fetch one actor-events page. `transport` is test-only.

    Catches FetchError -- the BASE class, so both HostNotAllowed and FetchFailed
    become the one error a sweep knows how to skip past.
    """
    try:
        return await fetch_html(
            url, allowed_host=ALLOWED_HOST, user_agent=USER_AGENT, transport=transport
        )
    except FetchError as exc:
        raise DiscoveryFetchError(str(exc)) from exc


@dataclass
class SweepReport:
    """What one sweep did, for the scheduler's log line."""

    fetched: int = 0
    failed: int = 0
    new_leads: int = 0
    announced: int = 0


async def run_sweep(
    session: AsyncSession,
    now: datetime,
    *,
    fetcher: Callable[[str], Awaitable[str]] = fetch_actor_events,
) -> SweepReport:
    """Walk every artist page, record what the catalogue is missing, queue ONE DM.

    `fetcher` is injected so tests never touch the network.

    NEVER sends a DM itself (invariant 4): it adds a `Notification` row and the
    scheduler's existing drain delivers it. `kind="discovery"` with
    `concert_id=None` falls through `scheduler.loop._notification_context` to
    the plain-text path, exactly as `ops_alert` does, so the send code needs no
    changes. It is deliberately NOT in UNREPORTED_NOTE_KINDS -- that set is for
    notices that report ON deliveries, and this one does not.

    The transaction stays the caller's: this flushes, never commits.
    """
    report = SweepReport()
    tags = list((await session.execute(
        select(Tag).where(Tag.eventernote_url.is_not(None)).order_by(Tag.id)
    )).scalars())

    today_jst = utc_to_jst(now).date()
    # Accumulated across ALL artists and handed to record_discovered in ONE
    # call: it batches its queries, and its event-id key deduplicates an event
    # that nine artist tags all list.
    seen: list[tuple[ActorEvent, int | None]] = []
    artist_by_tag_id: dict[int, str] = {}
    fetched_any = False

    for tag in tags:
        actor_id = actor_id_from_url(tag.eventernote_url or "")
        if actor_id is None:
            log.warning(
                "discovery: tag %s has an eventernote_url that is not an actor page: %s",
                tag.id, tag.eventernote_url,
            )
            continue
        # Between fetches, never after the last one: a pause the sweep pays
        # after its final page is a second of nothing, on a path that already
        # holds the scheduler's session.
        if fetched_any:
            await asyncio.sleep(SWEEP_DELAY_SECONDS)
        fetched_any = True

        url = actor_events_url(actor_id, tag.name)
        try:
            html = await fetcher(url)
        except DiscoveryFetchError as exc:
            # One artist's page being unreachable must not cost the other 85.
            log.warning("discovery: fetch failed for %s: %s", url, exc)
            report.failed += 1
            continue

        report.fetched += 1
        page = parse_actor_events(html)
        if page.skipped:
            log.info("discovery: %d unreadable row(s) on %s", page.skipped, url)
        artist_by_tag_id[tag.id] = tag.name
        for actor_event in future_events(page.events, today_jst):
            seen.append((actor_event, tag.id))

    fresh = await record_discovered(session, seen, now)
    report.new_leads = len(fresh)

    if fresh:
        hinted = await leads_matching_existing_legs(session, fresh)
        leads = [_lead(row, artist_by_tag_id, maybe_held=row.id in hinted) for row in fresh]
        body = build_discovery_dm(leads[:DM_LIST_LIMIT], total=len(fresh))
        # An empty body is a quiet day, and silence is the right output: a
        # daily "nothing found" trains the reader to ignore the channel.
        if body:
            for admin_id in sorted(settings.admin_ids):
                # An admin who has never logged into the web app has no users
                # row, and Notification.user_id is a FK to it. Guarded on
                # absence rather than calling ensure_user unconditionally:
                # that refreshes the username, which would overwrite a real
                # admin's name with this placeholder on every sweep.
                if await session.get(User, admin_id) is None:
                    await ensure_user(session, admin_id, str(admin_id))
                session.add(Notification(user_id=admin_id, body=body, kind="discovery"))
            # EVERY fresh lead, not only the ten the DM names. On the first
            # sweep every future event of every tag is new at once, mostly
            # duplicating concerts already held; marking only the listed ones
            # would trickle that backlog out at ten a day for weeks, burying
            # the real leads. The "+N more" line plus /admin/discoveries is
            # what carries the rest, and that surface is built for bulk triage.
            await mark_leads_announced(session, [row.id for row in fresh], now)
            report.announced = len(fresh)

    # OUTSIDE the `if`, deliberately: the clock starts on every sweep, quiet
    # ones included. Skipping it on a quiet day would leave discovery_due true
    # and re-sweep on the next tick, a minute later.
    await stamp_discovery_run(session, now)
    return report


def _lead(row: DiscoveredEvent, artists: dict[int, str], *, maybe_held: bool) -> Lead:
    """A stored row adapted to the pure message layer's plain dataclass."""
    return Lead(
        event_id=row.eventernote_event_id,
        title=row.title,
        date=row.event_date,
        venue=row.venue,
        # The tag that surfaced it, this sweep. A fresh row always came from a
        # tag read in this pass, so the lookup only misses if the tag was
        # deleted mid-sweep; an unnamed artist beats a KeyError in a DM path.
        artist=artists.get(row.first_seen_via_tag_id or 0, ""),
        maybe_held=maybe_held,
    )
