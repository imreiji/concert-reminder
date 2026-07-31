"""One host-pinned HTTP fetch, shared by every importer that leaves the box.

Top-level, beside `i18n.py` and `ops.py`: it does I/O so it cannot live in
`domain/`, and both a web route (`web/routes/imports.py`) and the Eventernote
discovery sweep (`app/discovery.py`) import it.

ONE copy of this guard, deliberately. It was private to the ramen.events
importer first; copying it for a second caller would have left two copies of a
security control, and a weakness found later would be fixed in one and missed
in the other. The two callers genuinely differ only in the exception they want
back, so the guard raises its own errors and each caller translates: the web
route to its existing HTTP status codes, the scheduler to a per-artist skip.

The guard is three-way, and all three parts matter:
  1. https + an exact host match (an allowlist, never a blocklist),
  2. that same check re-run on EVERY redirect hop, and
  3. a byte-capped streamed body.
"""

import logging
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


def check_host(url: str, allowed_host: str) -> None:
    """SSRF guard: only https, and only the one host named by the caller."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != allowed_host:
        raise HostNotAllowed(f"only https://{allowed_host}/... URLs are supported")


def _redirect_host_hook(allowed_host: str):
    """Build the httpx response event hook, called for every hop.

    follow_redirects=True alone would chase a redirect issued by the allowed
    host (a compromised host, or an open-redirect endpoint there) to an
    arbitrary address, silently defeating check_host's allowlist. Re-running
    the same check against the Location header on every hop closes that gap.
    """

    async def _check_redirect_host(response: httpx.Response) -> None:
        if response.is_redirect:
            location = response.headers.get("location", "")
            check_host(urljoin(str(response.url), location), allowed_host)

    return _check_redirect_host


async def fetch_html(
    url: str,
    *,
    allowed_host: str,
    user_agent: str,
    timeout: float = DEFAULT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """Fetch one page from `allowed_host`, or raise a FetchError.

    The host is checked BEFORE any request is made, and again on every redirect
    hop (see `_redirect_host_hook`). The body is read in capped chunks so an
    oversized response is aborted mid-download, instead of being fully buffered
    into memory first (as a plain `client.get()` + `len(resp.content)` check
    would do).

    `transport` is test-only (httpx.MockTransport); production always uses
    httpx's default.
    """
    check_host(url, allowed_host)
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            max_redirects=max_redirects,
            event_hooks={"response": [_redirect_host_hook(allowed_host)]},
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
