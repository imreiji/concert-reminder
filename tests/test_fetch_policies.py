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
    # The message text is pinned deliberately -- the brief called out that
    # PinnedHost.check must stay byte-identical to the old check_host, and an
    # unpinned pytest.raises would not have caught a reworded message.
    with pytest.raises(
        HostNotAllowed,
        match=r"^only https://www\.eventernote\.com/\.\.\. URLs are supported$",
    ):
        policy.check("https://evil.example/events/1")
    with pytest.raises(
        HostNotAllowed,
        match=r"^only https://www\.eventernote\.com/\.\.\. URLs are supported$",
    ):
        policy.check("http://www.eventernote.com/events/1")


async def test_approved_public_hosts_refuses_an_unapproved_host():
    policy = ApprovedPublicHosts(lambda host: host == "eplus.jp")
    with pytest.raises(HostNotAllowed):
        await policy.check_async("https://not-approved.example/x")


async def test_approved_public_hosts_refuses_plain_http():
    policy = ApprovedPublicHosts(lambda host: True)
    with pytest.raises(HostNotAllowed):
        await policy.check_async("http://eplus.jp/x")


async def test_approved_public_hosts_refuses_when_any_address_is_private(monkeypatch):
    # A host resolving to one public and one private address is a rebinding
    # setup, not a mixed deployment worth accommodating. This is a DIFFERENT
    # property from per-address classification (see the table-driven test
    # below): it is the ALL-must-pass rule across a multi-address answer.
    monkeypatch.setattr(
        "app.fetching._resolve", lambda host: ["93.184.216.34", "127.0.0.1"]
    )
    policy = ApprovedPublicHosts(lambda host: True)
    with pytest.raises(HostNotAllowed):
        await policy.check_async("https://mixed.example/x")


async def test_approved_public_hosts_accepts_an_approved_public_host(monkeypatch):
    monkeypatch.setattr("app.fetching._resolve", lambda host: ["93.184.216.34"])
    await ApprovedPublicHosts(lambda host: host == "eplus.jp").check_async(
        "https://eplus.jp/x"
    )


async def test_approved_public_hosts_normalizes_host_before_approval(monkeypatch):
    # A trailing DNS root dot and a mixed-case host must reach is_approved in
    # exactly the same normalized form, or the approval store and the guard
    # can silently disagree about what "eplus.jp" means.
    monkeypatch.setattr("app.fetching._resolve", lambda host: ["93.184.216.34"])
    seen = []

    def is_approved(host: str) -> bool:
        seen.append(host)
        return host == "eplus.jp"

    await ApprovedPublicHosts(is_approved).check_async("https://EPlus.JP./x")
    assert seen == ["eplus.jp"]


async def test_approved_public_hosts_refuses_an_unencodable_host():
    # A host that cannot even be IDNA-encoded (an empty label) must fail
    # CLOSED as HostNotAllowed, not escape as a bare UnicodeEncodeError.
    policy = ApprovedPublicHosts(lambda host: True)
    with pytest.raises(HostNotAllowed):
        await policy.check_async("https://a..b/x")


# ── the classification table ──────────────────────────────────────────────
# ONE address per family this app's resolver could plausibly hand back, from
# both encodings a plain IPv4/IPv6 address takes and every IPv4-in-IPv6
# encoding this module knows about. Two are accepted; every other row must be
# refused. This is what pins `_is_actually_global`'s actual property (public
# UNICAST) rather than the handful of one-off addresses earlier revisions
# tested -- exactly the gap that let the ::/96, ::ffff:0:0:0/96 and
# 64:ff9b::/96 encodings of the Lightsail metadata address slip through
# despite `169.254.169.254` and `127.0.0.1` themselves being covered.
_ADDRESS_FAMILY_CASES: list[tuple[str, str, bool]] = [
    ("public IPv4", "93.184.216.34", True),
    ("public IPv6", "2606:2800:220:1:248:1893:25c8:1946", True),
    ("RFC1918 10/8", "10.0.0.1", False),
    ("RFC1918 172.16/12", "172.16.0.1", False),
    ("RFC1918 192.168/16", "192.168.1.1", False),
    ("loopback IPv4", "127.0.0.1", False),
    ("loopback IPv6", "::1", False),
    ("link-local IPv4 (the Lightsail metadata address)", "169.254.169.254", False),
    ("link-local IPv6", "fe80::1", False),
    ("CGNAT 100.64.0.0/10", "100.64.0.1", False),
    ("unique-local IPv6 fc00::/7", "fc00::1", False),
    ("6to4 2002::/16 (deprecated automatic tunneling)", "2002:5db8:d822::1", False),
    ("Teredo 2001::/32", "2001:0:4136:e378:8000:63bf:3fff:fdd2", False),
    ("IPv4-mapped, loopback", "::ffff:127.0.0.1", False),
    ("IPv4-mapped, the metadata address", "::ffff:169.254.169.254", False),
    ("IPv4-compatible (deprecated ::/96), the metadata address", "::169.254.169.254", False),
    ("IPv4-translated (::ffff:0:0:0/96), the metadata address", "::ffff:0:a9fe:a9fe", False),
    ("NAT64 well-known prefix, the metadata address", "64:ff9b::a9fe:a9fe", False),
    ("NAT64 local-use prefix (already non-global natively)", "64:ff9b:1::a9fe:a9fe", False),
    ("unspecified IPv4", "0.0.0.0", False),
    ("unspecified IPv6", "::", False),
    ("documentation 192.0.2.0/24", "192.0.2.1", False),
    ("documentation 198.51.100.0/24", "198.51.100.1", False),
    ("documentation 203.0.113.0/24", "203.0.113.1", False),
    ("documentation 2001:db8::/32", "2001:db8::1", False),
    ("reserved 240.0.0.0/4", "240.0.0.1", False),
    ("benchmarking 198.18.0.0/15", "198.18.0.1", False),
    ("multicast IPv4", "224.0.0.1", False),
    ("multicast IPv6", "ff02::1", False),
]


@pytest.mark.parametrize(
    "label, address, accepted",
    _ADDRESS_FAMILY_CASES,
    ids=[c[0] for c in _ADDRESS_FAMILY_CASES],
)
async def test_address_family_classification(label, address, accepted, monkeypatch):
    monkeypatch.setattr("app.fetching._resolve", lambda host: [address])
    policy = ApprovedPublicHosts(lambda host: True)
    if accepted:
        await policy.check_async("https://example.test/x")
    else:
        with pytest.raises(HostNotAllowed):
            await policy.check_async("https://example.test/x")


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
