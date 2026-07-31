"""Parse an Eventernote actor's events page into rows.

Pure: takes an HTML string, returns rows. No httpx, exactly like
`domain/ingest.py` -- the fetch lives in `app/discovery.py` so this module
stays testable against a saved page with no network.

WARNINGS OVER FAILURES, following parse_draft and parse_tags: a row that
cannot be read is skipped and counted, never raised on. A site redesign must
degrade to "found nothing", which an operator can see on /admin/discoveries,
not to a scheduler tick that crashes every day.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import quote, urlparse

from bs4 import BeautifulSoup

HOST = "www.eventernote.com"
_EVENT_HREF = re.compile(r"/events/(\d+)")
_ACTOR_PATH = re.compile(r"^/actors/[^/]+/(\d+)(?:/|$)")
_DATE = re.compile(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})")


@dataclass(frozen=True)
class ActorEvent:
    event_id: str
    title: str
    date: date
    venue: str


@dataclass
class ParsedActorPage:
    events: list[ActorEvent] = field(default_factory=list)
    skipped: int = 0


def parse_actor_events(html: str) -> ParsedActorPage:
    """One actor-events page -> its rows, in the order the page lists them."""
    page = ParsedActorPage()
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()

    for link in soup.find_all("a", href=_EVENT_HREF):
        match = _EVENT_HREF.search(link.get("href", ""))
        if match is None:
            continue
        event_id = match.group(1)
        if event_id in seen:
            continue

        # MEASURED against the real fixture (tests/fixtures/eventernote_actor_events.html):
        # each row is a <li class="clearfix"> holding a sibling <div class="date">
        # (the date) beside <div class="event"> (the title link, inside <h4>, plus
        # the <div class="place"> venue link). The event link's NEAREST div
        # ancestor is div.event, which does not itself contain the date -- only
        # the enclosing <li> spans both. So the row must be the <li> ancestor
        # first; div/tr is kept as a fallback for a markup shape this fixture
        # didn't show.
        row = link.find_parent("li") or link.find_parent(["div", "tr"])
        text = row.get_text(" ", strip=True) if row is not None else ""
        stamp = _DATE.search(text)
        title = link.get_text(" ", strip=True)
        if stamp is None or not title:
            page.skipped += 1
            continue

        seen.add(event_id)
        page.events.append(ActorEvent(
            event_id=event_id,
            title=title,
            date=date(int(stamp.group(1)), int(stamp.group(2)), int(stamp.group(3))),
            venue=_venue(row),
        ))
    return page


def _venue(row) -> str:
    """The venue as displayed, or "" -- free text, never resolved to a tag here.

    Resolving a venue name to a VENUE tag is a NAME match, which invariant 3
    forbids as an identity test, and this module cannot reach the DB anyway.

    MEASURED against the fixture: the venue link is
    `<div class="place">会場: <a href="/places/8">...</a></div>`, so the
    "/places/" href prefix holds as assumed.
    """
    if row is None:
        return ""
    for link in row.find_all("a", href=True):
        if "/places/" in link["href"]:
            return link.get_text(" ", strip=True)
    return ""


def future_events(events: Sequence[ActorEvent], today_jst: date) -> list[ActorEvent]:
    """The future prefix of a newest-first page.

    TAKE-WHILE, not filter, and that is the whole economy of this feature: rows
    are strictly newest-first (pinned by a test), so stopping at the first past
    row means one fetch covers nearly every artist -- ~86 per sweep instead of
    ~1,548 if all 18 pages of every artist were read. An event dated TODAY
    counts as future: a same-day announcement is the most urgent lead there is.
    """
    out: list[ActorEvent] = []
    for event in events:
        if event.date < today_jst:
            break
        out.append(event)
    return out


def actor_id_from_url(url: str) -> str | None:
    """The numeric id out of a stored eventernote_url, or None if it is not one."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.hostname != HOST:
        return None
    match = _ACTOR_PATH.match(parsed.path or "")
    return match.group(1) if match else None


def actor_events_url(actor_id: str, name: str) -> str:
    """Build the events URL. The name segment is DECORATIVE -- /actors/x/5847
    resolves the same as the site's own path (verified against the live site) --
    so it is built from OUR name, percent-encoded, and only the id matters."""
    return f"https://{HOST}/actors/{quote(name, safe='')}/{actor_id}/events"
