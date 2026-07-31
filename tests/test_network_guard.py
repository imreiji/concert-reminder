"""The suite's no-real-network guard (`_no_real_network` in conftest.py).

Its own file because a guard nobody tests stops guarding silently: it is an
autouse fixture, so if it ever stopped raising, every test would keep passing
-- on a connected machine -- and the suite would quietly go back to depending
on eventernote.com's uptime and sending it real traffic.

The rule it enforces is "no httpx client without a transport". These four cases
are the three ways to reach the network and the one legitimate pattern that
must keep working.
"""

import httpx
import pytest
from conftest import RealNetworkAttempt

from app.discovery import fetch_actor_events
from app.fetching import fetch_html

URL = "https://www.eventernote.com/actors/Liyuu/34637/events"


async def test_the_named_wrapper_is_blocked():
    """The exact hole this guard was added for: a route test that forgets to
    stub the fetch. Without the guard this reaches the real host and PASSES on
    any machine with a connection."""
    with pytest.raises(RealNetworkAttempt):
        await fetch_actor_events(URL)


async def test_fetch_html_direct_is_blocked():
    """`fetch_html` is re-exported by name into app.discovery and
    app.web.routes.imports, so patching app.fetching alone would miss both."""
    with pytest.raises(RealNetworkAttempt):
        await fetch_html(URL, allowed_host="www.eventernote.com", user_agent="x")


async def test_a_bare_async_client_is_blocked():
    """The chokepoint, for code that does not go through fetch_html at all --
    web/auth.py's two OAuth calls, and anything written later."""
    with pytest.raises(RealNetworkAttempt):
        httpx.AsyncClient()


async def test_a_mock_transport_still_works():
    """The control. A guard that also broke the legitimate pattern would just
    get switched off, so this is the half that keeps it survivable."""

    def handler(request):
        return httpx.Response(200, text="<html>ok</html>")

    body = await fetch_actor_events(URL, transport=httpx.MockTransport(handler))
    assert "ok" in body
