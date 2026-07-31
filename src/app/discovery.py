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
from time import monotonic

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
from app.domain.discovery_message import Lead, build_discovery_dm
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
from app.scheduler import heartbeat

log = logging.getLogger(__name__)

ALLOWED_HOST = HOST
USER_AGENT = "dekimasen.app/1.0 (event discovery)"
# Sequential with a pause: 86 parallel requests at a third party is rude and is
# how an IP gets blocked.
SWEEP_DELAY_SECONDS = 1.0
# The sweep runs INLINE in the reminder tick, and reminder_loop is strictly
# serial (beat, tick, sleep 60) -- so a sweep occupying the tick for T seconds
# delays the next reminder pass by T. A live sweep measured 442s; the structural
# worst case at these constants is 86 x (10s timeout + 1s pause) ~= 946s. And
# nothing above bounds it: httpx's timeout is per READ, not per request, so a
# server dripping bytes under the 2 MB cap holds a connection open with no
# deadline at all. Since heartbeat.beat() now fires per artist (the right fix
# for the false /healthz alarm), that reminder blackout produces no signal
# whatever, on an app whose worst failure is a missed deadline.
#
# So the sweep gets a wall clock. Artists past the budget simply wait for
# tomorrow: leads are deferred, never lost -- the next sweep re-reads the same
# pages and record_discovered's id key means a lead surfaced late is still
# fresh. Moving the sweep off the tick entirely is the right end state and is
# logged as such; this bounds the tick absolutely in the meantime.
SWEEP_BUDGET_SECONDS = 240.0


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
    # True when SWEEP_BUDGET_SECONDS ran out and artists were left for
    # tomorrow. Recorded rather than merely logged: a truncation that only the
    # journal knows about is the same silent-degradation shape this branch keeps
    # producing.
    budget_exhausted: bool = False


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

    deadline = monotonic() + SWEEP_BUDGET_SECONDS

    for index, tag in enumerate(tags):
        # Checked at the TOP, before anything is fetched: the budget is a cap on
        # how long the reminder tick is held, so the answer has to be "stop"
        # before the next page is asked for, not after.
        if monotonic() >= deadline:
            report.budget_exhausted = True
            log.warning(
                "discovery: %.0fs budget spent after %d artist(s); %d left for tomorrow",
                SWEEP_BUDGET_SECONDS, index, len(tags) - index,
            )
            break

        # The sweep occupies one tick for minutes (86 pages, each with its own
        # deliberate pause), and heartbeat.beat() fires BEFORE tick() -- so
        # without this, a long sweep ages the heartbeat past MAX_AGE_SECONDS
        # and /healthz reports a perfectly healthy app as down. Beating per
        # artist is honest, not a workaround: the loop genuinely is alive.
        # Imported directly rather than passed in as a callback, following
        # app/ops.py, which sits at this same layer and does exactly this.
        heartbeat.beat()

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
        # READING the page is inside the try, not just fetching it. `Exception`
        # rather than DiscoveryFetchError, deliberately: parse_actor_events
        # builds date(y, m, d) out of a regex match, so a page carrying
        # `2026年2月30` -- a typo, or a date-shaped string in a title -- raises
        # ValueError. Escaping here would mean run_sweep never stamps its
        # clock, so the next tick re-fetches every page up to the poisoned one,
        # a minute later, forever. One artist's page must cost the other 85
        # nothing whatever is wrong with it, which is also what
        # domain/eventernote.py's docstring already promises.
        try:
            html = await fetcher(url)
            page = parse_actor_events(html)
            events = future_events(page.events, today_jst)
        except Exception:
            log.exception("discovery: could not read %s", url)
            report.failed += 1
            continue

        # Counted after the parse: `fetched` means "pages actually read".
        report.fetched += 1
        if page.skipped:
            log.info("discovery: %d unreadable row(s) on %s", page.skipped, url)
        artist_by_tag_id[tag.id] = tag.name
        for actor_event in events:
            seen.append((actor_event, tag.id))

    try:
        await _record_and_announce(session, seen, artist_by_tag_id, now, report)
    finally:
        # In a `finally`, and OUTSIDE the "did we find anything" question: the
        # clock starts on every sweep -- quiet ones, and ones that died partway.
        # Any exit that leaves last_run_at unset re-arms discovery_due, and the
        # next tick re-runs the same sweep a minute later, forever.
        #
        # This covers run_sweep's own transaction. A caller that rolls back on
        # the exception (scheduler.loop does, correctly -- the session may be
        # poisoned) takes this stamp with it, which is why loop.py re-stamps in
        # its handler after the rollback. Both halves are needed.
        try:
            await stamp_discovery_run(session, now)
        except Exception:
            # Never mask the real failure with a bookkeeping one.
            log.exception("discovery: could not stamp the sweep timestamp")
    return report


async def _record_and_announce(
    session: AsyncSession,
    seen: list[tuple[ActorEvent, int | None]],
    artist_by_tag_id: dict[int, str],
    now: datetime,
    report: SweepReport,
) -> None:
    """The write half: diff, compose, queue, mark. Split out only so run_sweep's
    stamp can wrap all of it in one `finally` without a wall of indentation."""
    fresh = await record_discovered(session, seen, now)
    report.new_leads = len(fresh)

    if fresh:
        hinted = await leads_matching_existing_legs(session, fresh)
        leads = [_lead(row, artist_by_tag_id, maybe_held=row.id in hinted) for row in fresh]
        # EVERY fresh lead is offered; build_discovery_dm decides how many the
        # message can name. It caps at DM_LIST_LIMIT and then shrinks until the
        # whole thing fits, so the prose and the copy block always name the same
        # ones -- slicing here is what let the caller pick a number neither half
        # could honour.
        body = build_discovery_dm(leads, total=len(fresh))
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
