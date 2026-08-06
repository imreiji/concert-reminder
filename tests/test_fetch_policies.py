"""One guard, two policies -- and the widened one must not become a loophole."""

import httpx
import pytest

from app.fetching import (
    ApprovedPublicHosts,
    FetchFailed,
    HostNotAllowed,
    PinnedHost,
    fetch_html,
)


def test_pinned_host_is_unchanged_behaviour():
    policy = PinnedHost("www.eventernote.com")
    policy.check("https://www.eventernote.com/events/1")
    with pytest.raises(HostNotAllowed):
        policy.check("https://evil.example/events/1")
    with pytest.raises(HostNotAllowed):
        policy.check("http://www.eventernote.com/events/1")


def test_approved_public_hosts_refuses_an_unapproved_host():
    policy = ApprovedPublicHosts(lambda host: host == "eplus.jp")
    with pytest.raises(HostNotAllowed):
        policy.check("https://not-approved.example/x")


def test_approved_public_hosts_refuses_plain_http():
    policy = ApprovedPublicHosts(lambda host: True)
    with pytest.raises(HostNotAllowed):
        policy.check("http://eplus.jp/x")


def test_approved_public_hosts_refuses_a_private_address(monkeypatch):
    # The failure this exists to stop: a draft naming the instance metadata
    # endpoint, or any host whose DNS points inside the VPC.
    monkeypatch.setattr(
        "app.fetching._resolve", lambda host: ["169.254.169.254"]
    )
    policy = ApprovedPublicHosts(lambda host: True)
    with pytest.raises(HostNotAllowed):
        policy.check("https://metadata.example/latest/meta-data/")


def test_approved_public_hosts_refuses_when_any_address_is_private(monkeypatch):
    # A host resolving to one public and one private address is a rebinding
    # setup, not a mixed deployment worth accommodating.
    monkeypatch.setattr(
        "app.fetching._resolve", lambda host: ["93.184.216.34", "127.0.0.1"]
    )
    policy = ApprovedPublicHosts(lambda host: True)
    with pytest.raises(HostNotAllowed):
        policy.check("https://mixed.example/x")


def test_approved_public_hosts_accepts_an_approved_public_host(monkeypatch):
    monkeypatch.setattr("app.fetching._resolve", lambda host: ["93.184.216.34"])
    ApprovedPublicHosts(lambda host: host == "eplus.jp").check("https://eplus.jp/x")


@pytest.mark.asyncio
async def test_a_redirect_off_an_approved_host_to_an_unapproved_one_is_refused(monkeypatch):
    monkeypatch.setattr("app.fetching._resolve", lambda host: ["93.184.216.34"])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "good.example":
            return httpx.Response(302, headers={"location": "https://evil.example/x"})
        return httpx.Response(200, text="<html>should never be reached</html>")

    with pytest.raises(HostNotAllowed):
        await fetch_html(
            "https://good.example/x",
            policy=ApprovedPublicHosts(lambda host: host == "good.example"),
            user_agent="test",
            transport=httpx.MockTransport(handler),
        )


@pytest.mark.asyncio
async def test_an_approved_page_comes_back(monkeypatch):
    monkeypatch.setattr("app.fetching._resolve", lambda host: ["93.184.216.34"])
    transport = httpx.MockTransport(lambda r: httpx.Response(200, text="<p>hi</p>"))
    body = await fetch_html(
        "https://good.example/x",
        policy=ApprovedPublicHosts(lambda host: True),
        user_agent="test",
        transport=transport,
    )
    assert body == "<p>hi</p>"


@pytest.mark.asyncio
async def test_the_byte_cap_still_applies(monkeypatch):
    monkeypatch.setattr("app.fetching._resolve", lambda host: ["93.184.216.34"])
    transport = httpx.MockTransport(lambda r: httpx.Response(200, text="x" * 5000))
    with pytest.raises(FetchFailed):
        await fetch_html(
            "https://good.example/x",
            policy=ApprovedPublicHosts(lambda host: True),
            user_agent="test",
            max_bytes=100,
            transport=transport,
        )
