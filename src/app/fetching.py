"""One host-pinned HTTP fetch, shared by every importer that leaves the box.

Top-level, beside `i18n.py` and `ops.py`: it does I/O so it cannot live in
`domain/`, and both a web route (`web/routes/imports.py`) and the Eventernote
discovery sweep (`app/discovery.py`) import it.

ONE copy of this guard, deliberately. It was private to the ramen.events
importer first; copying it for a second caller would have left two copies of a
security control, and a weakness found later would be fixed in one and missed
in the other. Callers differ only in the exception they want back and in which
POLICY they hand the guard (see `HostPolicy` below), so the guard raises its
own errors and each caller translates: the web route to its existing HTTP
status codes, the scheduler to a per-artist skip.

The guard is three-way, and all three parts matter regardless of policy:
  1. https + a host the policy accepts (an allowlist, never a blocklist),
  2. that same check re-run on EVERY redirect hop, and
  3. a byte-capped streamed body.
"""

import ipaddress
import logging
import socket
from collections.abc import Callable
from urllib.parse import urljoin, urlparse

import httpx

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10.0
DEFAULT_MAX_BYTES = 2_000_000
DEFAULT_MAX_REDIRECTS = 5


class FetchError(Exception):
    """Base: anything that stopped us returning a page."""


class HostNotAllowed(FetchError):
    """The scheme or host is not the one this fetch is pinned to."""


class FetchFailed(FetchError):
    """The request was allowed but did not produce a usable page."""


class HostPolicy:
    """Which hosts a fetch may reach. One method, called before the request
    and again on every redirect hop.

    A POLICY rather than a widened `allowed_host` string, because the two
    answers this app needs are different KINDS of answer -- "exactly this one
    host" and "any public host a human has approved" -- and expressing both
    through one loosened parameter is how a security control acquires a mode
    nobody remembers is there. Two policies, one guard, one redirect hook.
    """

    def check(self, url: str) -> None:
        raise NotImplementedError


class PinnedHost(HostPolicy):
    """https, and exactly one host. The original guard, unchanged: an
    allowlist of one, never a blocklist."""

    def __init__(self, host: str) -> None:
        self.host = host

    def check(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != self.host:
            raise HostNotAllowed(f"only https://{self.host}/... URLs are supported")


def _resolve(host: str) -> list[str]:
    """Every address `host` resolves to. Its own function so a test can
    replace it without a network, and so the policy below reads as policy."""
    return [info[4][0] for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)]


def _is_actually_global(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """`ip.is_global` alone is not quite enough, MEASURED rather than assumed:
    it correctly unwraps an IPv4-MAPPED IPv6 address (`::ffff:a.b.c.d`) and
    classifies the embedded IPv4 -- `::ffff:169.254.169.254` already comes
    back non-global. What it does NOT unwrap is the older, deprecated
    IPv4-COMPATIBLE form (`::a.b.c.d`, no `ffff`, RFC 4291 `::/96`): both
    glibc's and Windows' `inet_ntop` render ANY address in that block back
    into dotted-decimal text, so a crafted AAAA answer of `::169.254.169.254`
    round-trips through `_resolve` as that exact string, and `is_global` on
    the parsed result reports True -- confirmed empirically, not assumed.
    A host an admin approved, later re-pointed at that record, would sail
    through the guard. Unwrap that block ourselves and classify the embedded
    IPv4 instead of trusting the library's classification of the IPv6
    wrapper. `::` and `::1` are excluded from the unwrap (they collapse to
    0.0.0.0 and 0.0.0.1, an unrelated question) and native `is_global`
    already reports both of them correctly as non-global.
    """
    if (
        isinstance(ip, ipaddress.IPv6Address)
        and ip.ipv4_mapped is None
        and ip.packed[:12] == b"\x00" * 12
        and ip not in (ipaddress.IPv6Address("::"), ipaddress.IPv6Address("::1"))
    ):
        return ipaddress.IPv4Address(ip.packed[12:]).is_global
    return ip.is_global


class ApprovedPublicHosts(HostPolicy):
    """https, a host a human has approved, and only public addresses.

    The completion pass reads a draft's `official_url`, which is by nature an
    arbitrary host -- that is what an official page IS. Three things stand
    between that and an SSRF:

      1. https only, as everywhere else here;
      2. `is_approved(host)`, so nothing is fetched from a host an admin has
         not personally approved (see `FetchDomain`) -- and, because this same
         check runs on every redirect hop, a redirect off an approved host
         onto an unapproved one is refused rather than followed;
      3. every address the host resolves to must be GLOBAL, so a private,
         loopback, link-local or CGNAT target is refused. That covers the
         instance metadata endpoint at 169.254.169.254, which on this deploy
         is a real credential source. ALL addresses must pass, not any: a host
         answering with one public and one private address is a rebinding
         setup, not a deployment to accommodate.

    Accepted residual risk, recorded rather than ignored: DNS rebinding
    between this resolution and the connection httpx makes. Closing it means
    connecting to the resolved address with a Host override and re-doing TLS
    verification by name; the exposure is an attacker who both controls a host
    an admin explicitly approved and can flip its DNS inside the request
    window.
    """

    def __init__(self, is_approved: Callable[[str], bool]) -> None:
        self.is_approved = is_approved

    def check(self, url: str) -> None:
        parsed = urlparse(url)
        host = parsed.hostname
        if parsed.scheme != "https" or not host:
            raise HostNotAllowed("only https:// URLs can be read")
        if not self.is_approved(host):
            raise HostNotAllowed(f"{host} has not been approved for fetching")
        try:
            addresses = _resolve(host)
        except OSError as exc:
            raise HostNotAllowed(f"{host} does not resolve: {exc}") from exc
        if not addresses:
            raise HostNotAllowed(f"{host} does not resolve")
        for address in addresses:
            try:
                ip = ipaddress.ip_address(address)
            except ValueError as exc:
                raise HostNotAllowed(f"{host} resolved to something unreadable") from exc
            if not _is_actually_global(ip):
                raise HostNotAllowed(f"{host} resolves to a non-public address ({address})")


def _redirect_hook(policy: HostPolicy):
    """The httpx response event hook, called for every hop.

    follow_redirects=True alone would chase a redirect issued by an allowed
    host (a compromised host, or an open-redirect endpoint there) to an
    arbitrary address, silently defeating the policy. Built PER CALL so it
    closes over THIS caller's policy -- a module-level hook pinned to one
    policy is the obvious extraction bug and is exactly what a shared guard
    must not have.
    """

    async def _check_redirect(response: httpx.Response) -> None:
        if response.is_redirect:
            location = response.headers.get("location", "")
            policy.check(urljoin(str(response.url), location))

    return _check_redirect


async def fetch_html(
    url: str,
    *,
    policy: HostPolicy,
    user_agent: str,
    timeout: float = DEFAULT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """Fetch one page `policy` allows, or raise a FetchError.

    The host is checked BEFORE any request is made, and again on every redirect
    hop (see `_redirect_hook`). The body is read in capped chunks so an
    oversized response is aborted mid-download, instead of being fully buffered
    into memory first (as a plain `client.get()` + `len(resp.content)` check
    would do).

    `transport` is test-only (httpx.MockTransport); production always uses
    httpx's default.
    """
    policy.check(url)
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            max_redirects=max_redirects,
            event_hooks={"response": [_redirect_hook(policy)]},
            transport=transport,
        ) as client:
            async with client.stream("GET", url, headers={"User-Agent": user_agent}) as resp:
                if resp.status_code != 200:
                    raise FetchFailed(f"fetch failed: HTTP {resp.status_code}")
                body = bytearray()
                async for chunk in resp.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise FetchFailed("page too large")
                content_type = resp.headers.get("content-type", "")
                charset = "utf-8"
                if "charset=" in content_type:
                    charset = content_type.split("charset=", 1)[1].split(";", 1)[0].strip()
                return bytes(body).decode(charset, errors="replace")
    except httpx.HTTPError as exc:
        # Timeouts, connection failures, too many redirects: a transport-level
        # problem is a failed fetch, not a crash for the caller to work out.
        raise FetchFailed(f"fetch failed: {exc}") from exc
