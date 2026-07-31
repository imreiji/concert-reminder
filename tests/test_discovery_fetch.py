"""The discovery fetch: pinned to one host, on every hop."""

import httpx
import pytest

from app.discovery import DiscoveryFetchError, fetch_actor_events

OK = "https://www.eventernote.com/actors/x/1/events"


def _transport(handler):
    return httpx.MockTransport(handler)


async def test_it_fetches_an_allowed_url():
    async def handler(request):
        return httpx.Response(200, text="<html>hi</html>")

    assert "hi" in await fetch_actor_events(OK, transport=_transport(handler))


@pytest.mark.parametrize(
    "url",
    [
        "http://www.eventernote.com/actors/x/1/events",  # not https
        "https://evil.example.com/actors/x/1/events",  # wrong host
        "https://eventernote.com.evil.example/actors/x/1",  # suffix trick
    ],
)
async def test_a_disallowed_url_is_refused_before_any_request(url):
    async def handler(request):
        raise AssertionError("no request should have been made")

    with pytest.raises(DiscoveryFetchError):
        await fetch_actor_events(url, transport=_transport(handler))


async def test_a_redirect_off_host_is_refused():
    """NOT hypothetical: the site advertises its next-page link on an
    eventernote.s3.amazonaws.com host, so a fetcher that follows where the page
    points leaves the host it was pinned to."""

    async def handler(request):
        if request.url.host == "www.eventernote.com":
            return httpx.Response(
                302,
                headers={"location": "https://eventernote.s3.amazonaws.com/x"},
            )
        raise AssertionError("followed the redirect off-host")

    with pytest.raises(DiscoveryFetchError):
        await fetch_actor_events(OK, transport=_transport(handler))


async def test_an_oversized_body_is_aborted():
    async def handler(request):
        return httpx.Response(200, content=b"x" * 3_000_000)

    with pytest.raises(DiscoveryFetchError):
        await fetch_actor_events(OK, transport=_transport(handler))


async def test_a_non_200_raises():
    async def handler(request):
        return httpx.Response(503)

    with pytest.raises(DiscoveryFetchError):
        await fetch_actor_events(OK, transport=_transport(handler))
