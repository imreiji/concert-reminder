"""Import a concert draft from a ramen.events URL.

  GET  /concerts/import           paste-a-URL form
  POST /concerts/import/preview   fetch + parse only -- nothing touches the DB
  POST /concerts/import/commit    the only route that writes; same field
                                   shape and validation as manual creation
                                   (create_concert_row / build_day / build_round
                                   in concerts.py), just called in a loop.

Nothing is ever auto-saved: preview always renders an editable draft, and
only the editor's final submit on that draft writes anything.
"""

import asyncio
import json
import logging
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.service import sync_concert, tag_picker_context
from app.db.session import get_session
from app.domain.ingest import IngestError, parse_ramen_event
from app.domain.types import RoundKind
from app.web.auth import SessionUser, require_editor
from app.web.routes.concerts import build_day, build_round, create_concert_row, generate_event_id

log = logging.getLogger(__name__)
router = APIRouter(prefix="/concerts/import")

templates = None  # set by web.app at startup

ALLOWED_HOST = "ramen.events"
FETCH_TIMEOUT = 10.0
MAX_RESPONSE_BYTES = 2_000_000
MAX_REDIRECTS = 5


def _check_host(url: str) -> None:
    """SSRF guard: v1 only fetches ramen.events, an allowlist, not a blocklist."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != ALLOWED_HOST:
        raise HTTPException(
            status_code=400,
            detail=f"only https://{ALLOWED_HOST}/... URLs are supported",
        )


async def _check_redirect_host(response: httpx.Response) -> None:
    """httpx response event hook, called for every hop including redirects.

    follow_redirects=True alone would chase a redirect issued by ramen.events
    (a compromised host, or an open-redirect endpoint there) to an arbitrary
    address, silently defeating _check_host's allowlist. Re-running the same
    check against the Location header on every hop closes that gap.
    """
    if response.is_redirect:
        location = response.headers.get("location", "")
        _check_host(urljoin(str(response.url), location))


async def fetch_ramen_html(url: str, transport: httpx.AsyncBaseTransport | None = None) -> str:
    """Fetch an already-host-checked URL.

    The body is read in capped chunks so an oversized response is aborted
    mid-download, instead of being fully buffered into memory first (as a
    plain `client.get()` + `len(resp.content)` check would do).

    `transport` is test-only (httpx.MockTransport); production always uses
    httpx's default.
    """
    async with httpx.AsyncClient(
        timeout=FETCH_TIMEOUT,
        follow_redirects=True,
        max_redirects=MAX_REDIRECTS,
        event_hooks={"response": [_check_redirect_host]},
        transport=transport,
    ) as client:
        async with client.stream(
            "GET", url, headers={"User-Agent": "dekimasen.app/1.0 (event import)"}
        ) as resp:
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=502, detail=f"fetch failed: HTTP {resp.status_code}"
                )
            body = bytearray()
            async for chunk in resp.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise HTTPException(status_code=502, detail="page too large")
            content_type = resp.headers.get("content-type", "")
            charset = "utf-8"
            if "charset=" in content_type:
                charset = content_type.split("charset=", 1)[1].split(";", 1)[0].strip()
            return bytes(body).decode(charset, errors="replace")


def _fmt(dt) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M") if dt else ""


@router.get("", response_class=HTMLResponse)
async def import_form(
    request: Request,
    user: SessionUser = Depends(require_editor),
    error: str = "",
):
    return templates.TemplateResponse(
        request, "import_form.html", {"user": user, "error": error}
    )


@router.post("/preview", response_class=HTMLResponse)
async def import_preview(
    request: Request,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
    url: str = Form(...),
):
    _check_host(url)
    try:
        html = await fetch_ramen_html(url)
        # CPU-bound HTML parsing (BeautifulSoup) -- this process's one event
        # loop also drives the Discord gateway and the reminder scheduler,
        # so this must never run inline on it.
        parsed = await asyncio.to_thread(parse_ramen_event, html, url)
    except (IngestError, HTTPException) as e:
        detail = e.detail if isinstance(e, HTTPException) else str(e)
        return templates.TemplateResponse(
            request, "import_form.html", {"user": user, "error": detail}
        )

    picker = await tag_picker_context(session)
    return templates.TemplateResponse(
        request,
        "import_preview.html",
        {
            "user": user, "parsed": parsed, "source_url": url,
            "fmt": _fmt, "kinds": list(RoundKind),
            "by_kind": picker["by_kind"],
            "groups_json": json.dumps(picker["groups_json"]),
            "tag_names_json": json.dumps(picker["tag_names_json"]),
            "initial_selected_json": "{}",
        },
    )


@router.post("/commit")
async def import_commit(
    request: Request,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
    title: str = Form(..., min_length=1, max_length=200),
    franchise_tags: list[int] = Form(default=[]),
    group_tags: list[int] = Form(default=[]),
    artist_tags: list[int] = Form(default=[]),
    venue_tags: list[int] = Form(default=[]),
    day_label: list[str] = Form(default=[]),
    day_starts_at: list[str] = Form(default=[]),
    round_label: list[str] = Form(default=[]),
    round_kind: list[RoundKind] = Form(default=[]),
    round_opens_at: list[str] = Form(default=[]),
    round_closes_at: list[str] = Form(default=[]),
    round_results_at: list[str] = Form(default=[]),
    round_payment_at: list[str] = Form(default=[]),
    round_url: list[str] = Form(default=[]),
):
    """Same validation as manual entry (build_day/build_round), just looped
    -- create_concert_row + add_day + add_round combined into one commit.
    event_id isn't a field the import form collects, so it's auto-suggested
    from the title (slugified, de-duplicated) -- editable afterward via the
    edit page."""
    event_id = await generate_event_id(session, title)
    concert = await create_concert_row(
        session, user, title, event_id, franchise_tags, group_tags, artist_tags, venue_tags
    )

    for label, starts_at in zip(day_label, day_starts_at, strict=True):
        if not label.strip() and not starts_at.strip():
            continue  # a blank trailing row from the repeatable UI
        session.add(build_day(concert.id, label, starts_at))

    for label, kind, opens_at, closes_at, results_at, payment_at, r_url in zip(
        round_label, round_kind, round_opens_at, round_closes_at,
        round_results_at, round_payment_at, round_url, strict=True
    ):
        if not any([label.strip(), opens_at.strip(), closes_at.strip(),
                    results_at.strip(), payment_at.strip()]):
            continue  # a blank trailing row from the repeatable UI
        session.add(build_round(
            concert.id, label, kind, opens_at, closes_at, results_at, payment_at, r_url
        ))

    await session.flush()
    await sync_concert(session, concert.id)
    await session.commit()
    return RedirectResponse(f"/concerts/{concert.event_id}", status_code=303)
