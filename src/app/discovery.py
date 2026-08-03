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

from app.calendars import CALENDAR_FEEDS, feed_leads, fetch_feed
from app.config import settings
from app.db.models import DiscoveredEvent, Notification, Tag, User
from app.db.service import (
    DiscoveredInput,
    discovery_status,
    ensure_user,
    leads_matching_existing_legs,
    mark_leads_announced,
    record_discovered,
    set_sweep_cursor,
    stamp_discovery_run,
)
from app.domain.discovery_message import Lead, build_discovery_dm
from app.domain.eventernote import (
    HOST,
    actor_events_url,
    actor_id_from_url,
    future_events,
    parse_actor_events,
)
from app.domain.timezones import utc_to_jst
from app.domain.types import TagKind
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
# So the sweep gets a wall clock. Artists past the budget wait for the NEXT
# sweep, which resumes where this one stopped (see the cursor in run_sweep):
# leads are deferred, never lost. A fixed start point would have been worse than
# no budget -- ~374s of real work against a 240s budget reads roughly the first
# half of the list, every day, forever, and since a Tag id is creation order
# that starves exactly the artists added most recently.
#
# THE BOUND IS NOT THE BUDGET ALONE, and since the calendar pass (Task 5) the
# bound is TWO-PHASE, not one number. The check happens between artists, so
# the actor phase's worst case is one full budget plus the pause and fetch
# already in flight: SWEEP_BUDGET_SECONDS + SWEEP_DELAY_SECONDS +
# FETCH_DEADLINE_SECONDS, ~271s. That last term is why FETCH_DEADLINE_SECONDS
# exists at all -- without a TOTAL per-fetch deadline the final fetch is
# unbounded, and "the sweep is bounded" would be a comment rather than a fact.
#
# The calendar pass in run_sweep runs BEFORE `deadline` below is even
# computed -- deliberately OUTSIDE this budget, not a slice of it, because a
# feed roster that grows must never starve the actor budget it sits in front
# of (a starved actor phase reads fewer artists every day; a starved feed
# phase just delays that day's feeds, which are cheap and few). Its own
# structural worst case is the same shape, over CALENDAR_FEEDS instead of
# tags: len(CALENDAR_FEEDS) x (FETCH_DEADLINE_SECONDS + SWEEP_DELAY_SECONDS),
# ~279s at 9 feeds. So the sweep's real total worst case is the SUM of both
# phases, ~550s, not the ~271s of the actor phase alone.
#
# Moving the sweep off the tick entirely is still the right end state and is
# logged as such.
SWEEP_BUDGET_SECONDS = 240.0
# A TOTAL deadline per page. httpx's own timeout is per READ operation, so a
# server dripping bytes can hold a connection open indefinitely up to the 2 MB
# cap without ever tripping it. Generous next to the 10s read timeout, because
# its job is to catch the pathological case, not to tighten the normal one: a
# fetch that takes 30 wall-clock seconds has already failed at being a fetch.
FETCH_DEADLINE_SECONDS = 30.0


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
    feed_fetcher: Callable[[str], Awaitable[str]] = fetch_feed,
) -> SweepReport:
    """Read every calendar feed, then walk every artist page, record what the
    catalogue is missing, queue ONE DM.

    `fetcher` and `feed_fetcher` are injected so tests never touch the
    network -- one per pipeline, since they read different hosts under
    different fetch functions (see app/calendars.py).

    Two pipelines, one digest: calendar leads and actor-page leads both land
    in the same `seen` list and pass through `_record_and_announce` together,
    so a maintainer gets one DM whichever source found something.

    The actor query excludes CHARACTER tags (owner ruling, 2026-08-02; see
    docs/superpowers/specs/2026-08-02-calendar-discovery-design.md Sec 5): a
    character's `eventernote_url` is her seiyuu's own actor page, and that
    page is exactly the one the calendar feeds above cannot replace -- but
    reading it DAILY for every character in an expanding catalogue is the load
    this reversal exists to avoid. The URL stays storable and rendered, and
    the manual per-tag button (`sweep_one_tag`) stays unfiltered -- one
    deliberate fetch is not a daily cost.

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
        select(Tag)
        .where(Tag.eventernote_url.is_not(None))
        # See the docstring above: a CHARACTER's page belongs to her seiyuu,
        # and the daily sweep must not read it. `sweep_one_tag` (the manual
        # per-tag button) deliberately does NOT carry this filter.
        .where(Tag.kind != TagKind.CHARACTER)
        .order_by(Tag.id)
    )).scalars())

    today_jst = utc_to_jst(now).date()
    # Accumulated across ALL artists AND every calendar feed, handed to
    # record_discovered in ONE call: it batches its queries, and its event-id
    # key deduplicates an event that nine artist tags all list.
    seen: list[DiscoveredInput] = []
    artist_by_tag_id: dict[int, str] = {}
    fetched_any = False

    # Calendar feeds first: cheap (a handful of bounded fetches), and their
    # leads ride the same record-and-announce as the actor pages below --
    # one sweep, one digest. A failing feed is counted and skipped; no
    # cursor/budget machinery at this size (spec §4).
    for feed in CALENDAR_FEEDS:
        heartbeat.beat()
        if fetched_any:
            await asyncio.sleep(SWEEP_DELAY_SECONDS)
        fetched_any = True
        try:
            async with asyncio.timeout(FETCH_DEADLINE_SECONDS):
                body = await feed_fetcher(feed.url)
            leads, skipped = feed_leads(feed, body, today_jst)
        except Exception:
            log.exception("discovery: could not read feed %s", feed.key)
            report.failed += 1
            continue
        report.fetched += 1
        if skipped:
            log.info("discovery: %d unreadable VEVENT(s) on %s", skipped, feed.key)
        seen.extend(leads)

    # Where the last sweep stopped. The budget cannot reach the whole list at
    # the shipped constants, so a sweep that always started at tags[0] would
    # read the same head every day and NEVER touch the tail -- and since a Tag
    # id is creation order, the tail is the artists added most recently.
    #
    # Resume at the first id AT OR AFTER the cursor (the stored tag may have
    # been deleted, or may have lost its eventernote_url), then wrap: the visit
    # order is tags[start:] + tags[:start], which is every tag exactly once, so
    # a wrap can never re-read what this run already read.
    state = await discovery_status(session)
    cursor = state.sweep_cursor_tag_id if state else None
    start = 0
    if cursor is not None:
        start = next((i for i, tag in enumerate(tags) if tag.id >= cursor), 0)
    order = tags[start:] + tags[:start]
    # Unchanged unless this sweep gets somewhere: a run that dies before the
    # loop must not silently rewind the list to the head.
    next_cursor = cursor

    deadline = monotonic() + SWEEP_BUDGET_SECONDS

    for index, tag in enumerate(order):
        # Checked at the TOP, before anything is fetched: the budget is a cap on
        # how long the reminder tick is held, so the answer has to be "stop"
        # before the next page is asked for, not after.
        if monotonic() >= deadline:
            report.budget_exhausted = True
            # This tag was NOT read, so it is where the next sweep starts.
            next_cursor = tag.id
            log.warning(
                "discovery: %.0fs budget spent after %d artist(s); "
                "%d resume from tag %s next sweep",
                SWEEP_BUDGET_SECONDS, index, len(order) - index, tag.id,
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
        #
        # The fetch carries its own TOTAL deadline, which httpx's per-read
        # timeout is not: without it the last fetch of a sweep is unbounded and
        # the budget above bounds nothing. TimeoutError is an Exception, so a
        # page that runs long is counted failed and skipped like any other.
        # Only the fetch is wrapped -- parse_actor_events never awaits, so a
        # timeout could not fire inside it anyway.
        try:
            async with asyncio.timeout(FETCH_DEADLINE_SECONDS):
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
            seen.append(DiscoveredInput(event=actor_event, tag_id=tag.id))

    if not report.budget_exhausted:
        # The loop ran to the end, so every artist was visited and there is no
        # partial sweep in progress: the next one starts at the head again.
        # Written as an explicit check rather than a `for ... else`, which is
        # easy to misread as belonging to the nested loop just above.
        next_cursor = None

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
        # its handler after the rollback. Both halves are needed. That re-stamp
        # passes no counts, and that is right: a sweep that raised has no report
        # to give, and NULL means UNKNOWN rather than zero -- displaying
        # yesterday's numbers beside today's timestamp would read as a healthy
        # sweep on the day the sweep died. The CURSOR is the opposite case and
        # has its own writer for that reason -- it is progress, not a report,
        # so loop.py's re-stamp must leave it alone (see set_sweep_cursor).
        try:
            await stamp_discovery_run(
                session, now,
                fetched=report.fetched,
                failed=report.failed,
                truncated=report.budget_exhausted,
            )
            await set_sweep_cursor(session, next_cursor)
        except Exception:
            # Never mask the real failure with a bookkeeping one.
            log.exception("discovery: could not stamp the sweep timestamp")
    return report


@dataclass(frozen=True)
class TagSweepReport:
    """What a one-artist sweep did, for the operator who is watching.

    `status` rather than a bare count because "nothing new" and "could not
    reach the page" are the two answers a manual check exists to tell apart,
    and a count alone renders them identically as zero. Eventernote timed out
    on 12 of 86 fetches in a live run, so the failing case is ordinary.
    """

    status: str  # "ok" | "no_actor" | "failed"
    new_leads: int = 0


async def sweep_one_tag(
    session: AsyncSession,
    tag: Tag,
    now: datetime,
    *,
    fetcher: Callable[[str], Awaitable[str]] = fetch_actor_events,
) -> TagSweepReport:
    """Read ONE artist's page and record what the catalogue is missing.

    Runs INLINE in the request that asks for it, unlike `run_sweep`: one page
    is 1-10 seconds and is already bounded by FETCH_DEADLINE_SECONDS, which is
    an ordinary length for an HTTP request. Nothing is queued and the scheduler
    is not involved.

    It reuses `run_sweep`'s pieces exactly -- `actor_id_from_url`,
    `actor_events_url`, the injected fetcher, `parse_actor_events`,
    `future_events` and `record_discovered` -- so dedup, the branch-1 exact
    event-id match and the date-and-venue hint all behave identically to the
    daily sweep. It must never grow its own diff.

    THREE THINGS IT DELIBERATELY DOES NOT DO, each of which would be a silent
    regression in the daily sweep:

    1. It does NOT call `stamp_discovery_run`. That sets `last_run_at`, which
       is the 24h clock -- checking one artist would suppress that day's
       automatic sweep of all 86 and count as it on the status line.
    2. It does NOT call `set_sweep_cursor`. The cursor is accumulated progress
       through the full tag list; one artist read out of order is not progress
       through it, and moving the cursor there would make the next full sweep
       resume past artists it never read.
    3. It does NOT queue a `Notification`. The operator pressed a button and is
       looking at the result page; a Discord DM describing what they just
       clicked is noise, and the leads land on /admin/discoveries anyway.
       (`announced_at` is therefore left unset, which is correct: the next
       daily sweep will not re-report a lead that is still open, because
       `record_discovered` only ever returns rows it created.)

    The transaction stays the caller's: this flushes, never commits.
    """
    actor_id = actor_id_from_url(tag.eventernote_url or "")
    if actor_id is None:
        # Not a fetch failure: the tag's URL is missing or is not an actor
        # page. Distinct because the remedy is different -- fix the URL, not
        # try again later.
        return TagSweepReport(status="no_actor")

    url = actor_events_url(actor_id, tag.name)
    # Same wrapping as run_sweep's loop, and for the same reasons: `Exception`
    # rather than DiscoveryFetchError, because parse_actor_events builds a
    # date() out of a regex match and a page carrying `2026年2月30` raises
    # ValueError; and the TOTAL deadline, because httpx's timeout is per READ
    # and a server dripping bytes would otherwise hold this request open with
    # no bound at all.
    try:
        async with asyncio.timeout(FETCH_DEADLINE_SECONDS):
            html = await fetcher(url)
        page = parse_actor_events(html)
        events = future_events(page.events, utc_to_jst(now).date())
    except Exception:
        log.exception("discovery: could not read %s", url)
        return TagSweepReport(status="failed")

    if page.skipped:
        log.info("discovery: %d unreadable row(s) on %s", page.skipped, url)
    fresh = await record_discovered(
        session,
        [DiscoveredInput(event=event, tag_id=tag.id) for event in events],
        now,
    )
    return TagSweepReport(status="ok", new_leads=len(fresh))


async def _record_and_announce(
    session: AsyncSession,
    seen: list[DiscoveredInput],
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


def _feed_label(source: str) -> str:
    """A calendar feed's own label, for the digest's grouping slot -- falling
    back to the raw key for a feed later removed from config (2026-08-02
    design doc Sec 1). Looked up against the LIVE `CALENDAR_FEEDS` name
    (never a dict frozen at import) so tests can still monkeypatch it."""
    return next((f.label for f in CALENDAR_FEEDS if f.key == source), source)


def _lead(row: DiscoveredEvent, artists: dict[int, str], *, maybe_held: bool) -> Lead:
    """A stored row adapted to the pure message layer's plain dataclass."""
    if row.source == "eventernote":
        # The tag that surfaced it, this sweep. A fresh row always came from a
        # tag read in this pass, so the lookup only misses if the tag was
        # deleted mid-sweep; an unnamed artist beats a KeyError in a DM path.
        artist = artists.get(row.first_seen_via_tag_id or 0, "")
    else:
        # A calendar lead names no tag at all (a feed is not a subscription,
        # app/calendars.py's feed_leads sets tag_id=None) -- it groups under
        # its FEED's own label instead, in the exact same slot.
        artist = _feed_label(row.source)
    return Lead(
        event_id=row.source_event_id,
        title=row.title,
        date=row.event_date,
        venue=row.venue,
        artist=artist,
        maybe_held=maybe_held,
        deadline=row.date_is_deadline,
        source=row.source,
    )
