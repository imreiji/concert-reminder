"""Eventernote discovery: the fetch, and the daily sweep.

Sits ABOVE db/ like app/ops.py: it imports domain/ and db.service, and nothing
in db/ imports it. The parser is pure and lives in domain/eventernote.py; the
host-pinned fetch is shared with the ramen.events importer and lives in
app/fetching.py -- ONE copy of that guard, deliberately, so a weakness found in
it cannot be fixed in one caller and missed in the other.
"""

import logging

import httpx

from app.domain.eventernote import HOST
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
