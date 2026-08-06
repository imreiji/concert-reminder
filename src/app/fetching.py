"""One outbound HTTP fetch, gated by a host policy, shared by every importer
that leaves the box.

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

import asyncio
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
# A TOTAL deadline on address resolution, for the same reason `discovery.py`
# and `triage.py` each keep a FETCH_DEADLINE_SECONDS: a stalling nameserver
# has no other bound (glibc's own default is 5s x 2 attempts x however many
# nameservers are configured), and this runs off the event loop precisely so
# a slow answer costs a borrowed thread, not the whole process -- see
# `_resolve_async`.
DEFAULT_RESOLVE_TIMEOUT = 10.0


class FetchError(Exception):
    """Base: anything that stopped us returning a page."""


class HostNotAllowed(FetchError):
    """The scheme or host is not the one this fetch is pinned to."""


class FetchFailed(FetchError):
    """The request was allowed but did not produce a usable page."""


class HostPolicy:
    """Which hosts a fetch may reach. `check_async` is the one required
    method, called before the request and again on every redirect hop.

    A POLICY rather than a widened `allowed_host` string, because the two
    answers this app needs are different KINDS of answer -- "exactly this one
    host" and "any public host a human has approved" -- and expressing both
    through one loosened parameter is how a security control acquires a mode
    nobody remembers is there. Two policies, one guard, one redirect hook.

    ASYNC, not sync, because a policy that resolves DNS (`ApprovedPublicHosts`)
    must never do that on the shared event loop this process runs discord.py,
    FastAPI and the 60s scheduler tick on -- see `_resolve_async`'s docstring
    for the incident this prevents. `PinnedHost` does no I/O of its own and
    additionally exposes a genuine synchronous `check`, for its one
    synchronous caller (`web/routes/imports.py`'s `_check_host`); that is a
    property of `PinnedHost` specifically, not a second method every policy
    must grow.
    """

    async def check_async(self, url: str) -> None:
        raise NotImplementedError


class PinnedHost(HostPolicy):
    """https, and exactly one host. The original guard, unchanged: an
    allowlist of one, never a blocklist.

    `check` is a genuine SYNCHRONOUS method -- `web/routes/imports.py`'s
    `_check_host` calls it directly, off any event loop, since pinning to one
    literal host needs no I/O and never will. `check_async` just forwards to
    it so `fetch_html` and the redirect hook can call every policy the same
    (awaited) way regardless of which one actually does I/O.
    """

    def __init__(self, host: str) -> None:
        self.host = host

    def check(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != self.host:
            raise HostNotAllowed(f"only https://{self.host}/... URLs are supported")

    async def check_async(self, url: str) -> None:
        self.check(url)


def _resolve(host: str) -> list[str]:
    """Every address `host` resolves to. Its own function so a test can
    replace it without a network, and so the policy below reads as policy.

    Synchronous and BLOCKING (`socket.getaddrinfo` does real I/O) -- callers
    on the event loop must go through `_resolve_async`, never this directly.
    """
    return [info[4][0] for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)]


async def _resolve_async(host: str) -> list[str]:
    """`_resolve`, off the event loop, with a total deadline.

    This process runs discord.py, FastAPI and the 60s scheduler tick on ONE
    asyncio loop (CLAUDE.md). A plain synchronous `_resolve` call from
    `ApprovedPublicHosts.check_async` would block that ENTIRE loop for
    however long a stalling nameserver takes -- not one request stalling, the
    whole process: no Discord heartbeat, no web response, no scheduler tick,
    for as long as DNS takes to give up. `asyncio.to_thread` pays that cost
    with a borrowed thread instead of the loop, and `asyncio.wait_for` bounds
    it so one bad nameserver cannot hang a fetch (or, transitively, the
    process) indefinitely. Wraps the still-plain, still-monkeypatchable
    `_resolve` rather than switching to `loop.getaddrinfo` directly, so every
    existing test that patches `app.fetching._resolve` keeps intercepting it.
    """
    return await asyncio.wait_for(
        asyncio.to_thread(_resolve, host), timeout=DEFAULT_RESOLVE_TIMEOUT
    )


# Every IPv6 /96 (or narrower) block whose LOW 32 bits carry an embedded IPv4
# address, where the block's OWN allocation status is not what determines
# whether an address inside it is reachable -- the embedded IPv4 is. Measured
# empirically (not assumed) that native `ip.is_global` does NOT unwrap any of
# these the way it correctly unwraps IPv4-mapped (`::ffff:a.b.c.d`, handled by
# `ip.ipv4_mapped` and deliberately absent from this list -- it needs no help):
#   - `::/96`            IPv4-compatible, deprecated (RFC 4291 2.5.5.1)
#   - `::ffff:0:0:0/96`  IPv4-translated (historic SIIT / RFC 2765 -- NOT the
#                        same block as IPv4-mapped `::ffff:0:0/96` despite the
#                        similar prefix text; one extra zero group shifts it)
#   - `64:ff9b::/96`     the NAT64 well-known prefix (RFC 6052 2.1)
# `64:ff9b:1::/48` (RFC 6052's LOCAL-USE translation prefix) is deliberately
# NOT here: it already reads non-global natively for every embedded payload,
# confirmed empirically, so unwrapping it would be a no-op branch.
_EMBEDDED_IPV4_BLOCKS: tuple[ipaddress.IPv6Network, ...] = (
    ipaddress.IPv6Network("::/96"),
    ipaddress.IPv6Network("::ffff:0:0:0/96"),
    ipaddress.IPv6Network("64:ff9b::/96"),
)
# :: and ::1 both sit inside ::/96 too, but they mean "unspecified" and
# "loopback", not "IPv4 embedded as 0.0.0.0 / 0.0.0.1" -- an unrelated
# question -- and native `is_global` already reports both correctly, so they
# are carved out of the unwrap rather than reclassified through it.
_NOT_EMBEDDED_IPV4 = (ipaddress.IPv6Address("::"), ipaddress.IPv6Address("::1"))


def _is_actually_global(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Public unicast, not merely `is_global` -- both measured gaps in the
    standard library's own classification, closed explicitly rather than
    trusted:

    1. `ip.is_global` reports the IPv6 WRAPPER's classification, not the
       embedded IPv4's, for every block in `_EMBEDDED_IPV4_BLOCKS` --
       confirmed for each: `::169.254.169.254`, `::ffff:0:a9fe:a9fe` and
       `64:ff9b::a9fe:a9fe` (all three encode 169.254.169.254, the Lightsail
       metadata endpoint) each read back `is_global=True` unpatched. A host an
       admin approved, later re-pointed at any of these AAAA forms, would
       sail through the guard. Detect membership in one of those blocks and
       classify the embedded IPv4 (`ip.packed`'s low 4 bytes) instead.
    2. `ip.is_global` alone says nothing about multicast, IANA-reserved
       (`240.0.0.0/4`) or unspecified addresses -- none are on a private- or
       loopback-shaped allowlist, so `is_global` alone lets them through, and
       none are a route to a TCP-reachable credential source either, so this
       is defense-in-depth rather than a closed gap: excluded explicitly so
       the predicate reads as "public unicast", not "not on a list I thought
       of enumerating".
    """
    effective: ipaddress.IPv4Address | ipaddress.IPv6Address = ip
    if (
        isinstance(ip, ipaddress.IPv6Address)
        and ip.ipv4_mapped is None
        and ip not in _NOT_EMBEDDED_IPV4
        and any(ip in block for block in _EMBEDDED_IPV4_BLOCKS)
    ):
        effective = ipaddress.IPv4Address(ip.packed[12:])
    return (
        effective.is_global
        and not effective.is_multicast
        and not effective.is_reserved
        and not effective.is_unspecified
    )


def _normalize_host(host: str) -> str:
    """The exact host BOTH `is_approved` and `_resolve_async` see: a single
    trailing DNS root dot stripped, IDNA-encoded to ASCII, lowercased.

    Normalized ONCE, here, rather than left to whatever an approval STORE (a
    later task) happens to pick: `eplus.jp.` and `eplus.jp` must approve
    identically, and an IDN host must reach `is_approved` in the same ASCII
    form `_resolve_async` (and httpx, and TLS) actually use to reach the
    network, or a Unicode-vs-punycode split lets a host bypass approval by
    spelling -- with nothing today to catch it, since `.encode("idna")` on an
    already-ASCII host is close to a no-op and the gap would sit silent until
    the approval store picked its own, possibly different, convention.
    Raises `UnicodeError` (a `ValueError`) on an unencodable label; the
    caller turns that into `HostNotAllowed` same as any other malformed host.
    """
    if host.endswith("."):
        host = host[:-1]
    return host.encode("idna").decode("ascii").lower()


class ApprovedPublicHosts(HostPolicy):
    """https, a host a human has approved, and only public addresses.

    The completion pass reads a draft's `official_url`, which is by nature an
    arbitrary host -- that is what an official page IS. Three things stand
    between that and an SSRF:

      1. https only, as everywhere else here;
      2. `is_approved(host)`, so nothing is fetched from a host an admin has
         not personally approved (see `FetchDomain`) -- and, because this same
         check runs on every redirect hop, a redirect off an approved host
         onto an unapproved one is refused rather than followed. `is_approved`
         is always called with the NORMALIZED host (see `_normalize_host`):
         ASCII, lowercased, no trailing dot -- a contract the guard keeps, not
         one every future caller of `is_approved` has to remember.
      3. every address the host resolves to must be public UNICAST
         (`_is_actually_global`), so a private, loopback, link-local, CGNAT,
         multicast or reserved target is refused -- across every IPv4-in-IPv6
         encoding this module knows about, not just the obvious one. That
         covers the instance metadata endpoint at 169.254.169.254, which on
         this deploy is a real credential source. ALL addresses must pass, not
         any: a host answering with one public and one private address is a
         rebinding setup, not a deployment to accommodate.

    Resolution happens OFF the event loop (`_resolve_async`) -- this check
    runs from `fetch_html` and from the async redirect hook, both on the one
    loop this whole process shares, and a synchronous DNS call there would
    block Discord, the web app and the scheduler tick together, not just this
    fetch.

    Accepted residual risk, recorded rather than ignored: this check and
    httpx's own connection are TWO INDEPENDENT DNS lookups. "ALL addresses
    must pass" only covers the ONE answer set this check happens to see; a
    short-TTL or round-robin record can hand httpx's later lookup a
    completely different, disjoint answer set with NO attacker timing needed
    at all -- this is broader than the classic rebinding picture of an
    attacker flipping DNS inside a narrow window, and can happen to any
    approved host whose DNS legitimately varies between requests. Closing it
    means connecting to the resolved address directly with a Host override and
    re-doing TLS verification by name.
    """

    def __init__(self, is_approved: Callable[[str], bool]) -> None:
        self.is_approved = is_approved

    async def check_async(self, url: str) -> None:
        parsed = urlparse(url)
        raw_host = parsed.hostname
        if parsed.scheme != "https" or not raw_host:
            raise HostNotAllowed("only https:// URLs can be read")
        try:
            host = _normalize_host(raw_host)
        except ValueError as exc:
            raise HostNotAllowed(f"{raw_host} is not a usable hostname: {exc}") from exc
        if not self.is_approved(host):
            raise HostNotAllowed(f"{host} has not been approved for fetching")
        try:
            addresses = await _resolve_async(host)
        except (OSError, ValueError, TimeoutError) as exc:
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
            await policy.check_async(urljoin(str(response.url), location))

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
    await policy.check_async(url)
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
