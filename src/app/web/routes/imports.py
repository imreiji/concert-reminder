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
import logging
from collections import namedtuple
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.service import (
    handle_newly_tagged,
    match_venue_tag_id,
    record_round_label_phrase,
    round_label_phrases,
    sync_concert,
    sync_concert_venue_tags,
    tag_picker_context,
)
from app.db.session import get_session
from app.domain.ingest import IngestError, parse_ramen_event
from app.domain.types import ConcertKind, RoundKind
from app.web.auth import SessionUser, require_editor
from app.web.forms import form_url, require_variants
from app.web.routes.concerts import (
    all_venue_tags,
    build_day,
    build_round,
    create_concert_row,
    generate_event_id,
    parse_round_legs,
    resolve_day_venue_tags,
)

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


# A parsed day has no id yet, so each round's leg chips reference it by a
# stable `day_key` ("d0", "d1", ...) the preview template also stamps on the
# matching `.eleg` card. This shim gives `_round_leg_chips.html` the same
# attribute surface a real ConcertDay exposes (id -> the key, plus label);
# the round chips render unselected (all-legs), so venue_tag/starts are never
# read. `venue_tag` is carried anyway, and left None: the leg card's venue
# picker is a client-side selection this server-side shim never sees (the
# chip script reads the live <select> instead), and an explicit None is what
# keeps the chip's label chain from tripping over a missing attribute.
_PreviewLeg = namedtuple("_PreviewLeg", "id label venue_tag starts_at_utc")


def _preview_legs(parsed) -> list[_PreviewLeg]:
    return [
        _PreviewLeg(id=f"d{i}", label=d.label, venue_tag=None, starts_at_utc=None)
        for i, d in enumerate(parsed.days)
    ]


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
            request,
            "import_form.html",
            # lang_next_url: this render is served from POST-only /preview,
            # so the header language chip must send its 303 somewhere
            # GET-able -- the import form we're re-rendering anyway.
            {"user": user, "error": detail, "lang_next_url": "/concerts/import"},
        )

    picker = await tag_picker_context(session)
    # The option list behind each leg row's venue picker -- the same helper
    # (and so the same ordering) both editor pages use. The scraped venue name
    # is matched against it here, at the route boundary, so the template only
    # ever compares ids.
    venue_tags = await all_venue_tags(session)
    return templates.TemplateResponse(
        request,
        "import_preview.html",
        {
            "user": user, "parsed": parsed, "source_url": url,
            # Served from POST-only /preview: aim the language chip's `next`
            # at the GET-able import form (its own path would 405).
            "lang_next_url": "/concerts/import",
            "fmt": _fmt, "kinds": list(RoundKind),
            # Concert-level Kind selector in the .ebar (the round-kind `kinds`
            # above is a different list -- per-round, not per-concert).
            "concert_kinds": list(ConcertKind),
            "by_kind": picker["by_kind"],
            # Raw dicts, never json.dumps -- the template applies `| tojson`.
            "groups": picker["groups"],
            "tag_names": picker["tag_names"],
            "initial_selected": {},
            # Every VENUE tag, for the per-leg <select>.
            "venue_tags": venue_tags,
            # Trilingual round labels already typed on earlier concerts, for
            # the picker each round row's "Remembered" chip opens. The scrape
            # fills Japanese only, so this is where they pay off most.
            "round_phrases": await round_label_phrases(session),
            # The one free-text venue the ramen.events parse scrapes for the
            # whole event. It fills each parsed leg's free-text `day_venue`
            # (the importer's find must not be thrown away when no tag matches)
            # and, when it DOES match a VENUE tag by trimmed case-insensitive
            # name, pre-selects that tag below.
            "venue_hint": parsed.venue_name,
            "matched_venue_tag_id": match_venue_tag_id(parsed.venue_name, venue_tags),
            # One chip target per parsed day, keyed by day_key -- the round
            # cards render their leg chips from this via _round_leg_chips.html.
            "legs": _preview_legs(parsed),
        },
    )


@router.post("/commit")
async def import_commit(
    request: Request,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
    title: str = Form(..., min_length=1, max_length=200),
    title_en: str = Form(default=""),
    title_zh: str = Form(default=""),
    organizer: str = Form(default=""),
    categories: str = Form(default=""),
    kind: str = Form(default=""),
    notes: str = Form(default=""),
    source_url: str = Form(default=""),
    franchise_tags: list[int] = Form(default=[]),
    group_tags: list[int] = Form(default=[]),
    artist_tags: list[int] = Form(default=[]),
    venue_tags: list[int] = Form(default=[]),
    day_key: list[str] = Form(default=[]),
    day_label: list[str] = Form(default=[]),
    day_label_en: list[str] = Form(default=[]),
    day_label_zh: list[str] = Form(default=[]),
    day_starts_at: list[str] = Form(default=[]),
    day_city: list[str] = Form(default=[]),
    day_venue: list[str] = Form(default=[]),
    day_venue_address: list[str] = Form(default=[]),
    day_venue_tag_id: list[str] = Form(default=[]),
    day_doors_at: list[str] = Form(default=[]),
    day_cancelled: list[str] = Form(default=[]),
    round_label: list[str] = Form(default=[]),
    round_label_en: list[str] = Form(default=[]),
    round_label_zh: list[str] = Form(default=[]),
    round_kind: list[RoundKind] = Form(default=[]),
    round_opens_at: list[str] = Form(default=[]),
    round_closes_at: list[str] = Form(default=[]),
    round_results_at: list[str] = Form(default=[]),
    round_payment_at: list[str] = Form(default=[]),
    round_url: list[str] = Form(default=[]),
    round_notes: list[str] = Form(default=[]),
    round_legs: list[str] = Form(default=[]),
):
    """Same validation as manual entry (build_day/build_round), just looped
    -- create_concert_row + add_day + add_round combined into one commit.
    event_id isn't a field the import form collects, so it's auto-suggested
    from the title (slugified, de-duplicated) -- editable afterward via the
    edit page.

    Rounds bind to legs by chip exactly as create_concert does: every leg is
    brand-new here (no id until the flush), so its card carries a client-side
    `day_key` that the round's `round_legs` value references, resolved to real
    ids through key_to_day_id after the days flush. Upgrade qualifiers are not
    part of import -- the parser never produces an UPGRADE round -- so this
    route does not collect round_qualifiers."""
    # _check_host pinned the *fetch* to ramen.events, but this value reached
    # the browser as a hidden field on the preview form and came back on this
    # request, so it is client-supplied like any other field. Validated before
    # anything is written, so a tampered value persists nothing.
    checked_source_url = form_url(source_url)
    # An import IS a create, so the title is held to the same all-three rule
    # create_concert applies -- the leg and round labels below already are, and
    # a route that rejects "Leg 1 label needs 中文" while quietly writing a
    # title_zh-less concert would be enforcing half a rule. Ordered AFTER the
    # source-URL check so a tampered URL still fails for the URL's reason.
    require_variants("Title", title, title_en, title_zh, mandatory=True)

    event_id = await generate_event_id(session, title)
    concert = await create_concert_row(
        session, user, title, event_id, franchise_tags, group_tags, artist_tags, venue_tags,
        kind=ConcertKind(kind) if kind else None,
        source_url=checked_source_url,
    )
    # The Details-fold scalars, set exactly as create_concert does after
    # create_concert_row returns (title_en/title_zh/organizer/categories/notes).
    # The
    # ramen.events parse supplies none of these, so they arrive blank unless
    # the editor filled them in.
    concert.title_en = title_en.strip() or None
    concert.title_zh = title_zh.strip() or None
    concert.organizer = organizer.strip() or None
    concert.categories = categories.strip() or None
    concert.notes = notes.strip() or None

    # The optional day_* fields (venue, city, doors, cancelled) round-trip in
    # full from the preview form, but a minimal client -- the older import
    # contract, and its tests -- posts only day_label/day_starts_at. End-pad
    # those secondary text arrays to the label count so their omission is read
    # as "blank for every row" (their own default) rather than tripping the
    # strict zip below. Safe because they are non-binding display text: a
    # trailing row losing empty text is harmless.
    n_days = len(day_label)
    # day_label_en/day_label_zh join that same end-padded group -- non-binding
    # display text, and the minimal client above supplies neither. This is the
    # rule round_label_en already follows in THIS route (and only here: the
    # manual create/edit routes leave every label variant unpadded, because
    # they have no minimal-client contract to honour).
    day_label_en = day_label_en + [""] * (n_days - len(day_label_en))
    day_label_zh = day_label_zh + [""] * (n_days - len(day_label_zh))
    day_city = day_city + [""] * (n_days - len(day_city))
    day_venue = day_venue + [""] * (n_days - len(day_venue))
    day_venue_address = day_venue_address + [""] * (n_days - len(day_venue_address))
    day_doors_at = day_doors_at + [""] * (n_days - len(day_doors_at))
    day_cancelled = day_cancelled + ["false"] * (n_days - len(day_cancelled))
    # day_key is the leg-binding key, so it is NOT end-padded: a partial array
    # is left alone so the strict zip raises instead of sliding a key one row
    # and assigning a round to the wrong leg, silently -- worse than a 500.
    # Only a WHOLLY-omitted array (an older client with no chips) is padded to
    # empty keys. Same rule create_concert applies.
    if not day_key:
        day_key = [""] * n_days
    # day_venue_tag_id is the leg's structured venue, not display text, so it
    # follows day_key's rule rather than the end-padding above: a partial array
    # is left alone (the strict zip raises) so one leg's venue can never slide
    # onto another; only a WHOLLY-omitted array -- every submitter predating
    # the field, including this route's older tests -- is padded to blanks.
    if not day_venue_tag_id:
        day_venue_tag_id = [""] * n_days
    # Resolved (and kind-checked) after the padding, so the strict zip below
    # still sees one entry per row. Same route-boundary check the manual
    # create/edit paths run -- see resolve_day_venue_tags.
    day_venue_tags = await resolve_day_venue_tags(session, day_venue_tag_id)
    # key -> the ConcertDay its row produced, built INSIDE the loop from the
    # same tuple so a key can never be paired with another row's day; ids are
    # filled in after the flush below.
    days: list = []
    key_rows: list[tuple[str, object]] = []
    for row_no, (
        key, label, label_en, label_zh, starts_at, city, venue, venue_address,
        doors_at, cancelled, v_tag
    ) in enumerate(zip(
        day_key, day_label, day_label_en, day_label_zh, day_starts_at, day_city, day_venue,
        day_venue_address, day_doors_at, day_cancelled, day_venue_tags, strict=True,
    ), start=1):
        # v_tag is in the guard because the next phase drops the free-text
        # city/venue inputs: without it, a row where the editor picked ONLY a
        # venue would read as blank and be silently dropped.
        if not any([label.strip(), starts_at.strip(), city.strip(), venue.strip(), v_tag]):
            continue  # a blank trailing row from the repeatable UI -- key and all
        # Same create-boundary rule create_concert applies, with the same row
        # numbering: an import is a create, so its labels are held to it too.
        require_variants(f"Leg {row_no} label", label, label_en, label_zh)
        day = build_day(
            concert.id, label, starts_at, city, venue, venue_address, doors_at, cancelled,
            v_tag, label_en, label_zh,
        )
        session.add(day)
        days.append(day)
        if key.strip():
            key_rows.append((key.strip(), day))
    await session.flush()  # real ids, needed to resolve the leg chips below
    valid_day_ids = {d.id for d in days}
    # Resolved only now, because a row had no id until the flush above. A
    # duplicate key would be the one way two rows could collide, so the first
    # row claiming a key keeps it rather than a later one silently stealing it.
    key_to_day_id: dict[str, int] = {}
    for key, day in key_rows:
        key_to_day_id.setdefault(key, day.id)

    # round_legs is the newer chip field; a submitter that omits it ENTIRELY
    # means "nothing to say about legs" -- every round gets no legs, matching
    # build_round's own default. A partial array is left alone so the strict
    # zip raises rather than sliding a row's selection.
    if not round_legs:
        round_legs = [""] * len(round_label)
    round_label_en = round_label_en + [""] * (len(round_label) - len(round_label_en))
    # Same end-pad round_label_en gets, for the same reason: the minimal import
    # contract posts only round_label plus its times.
    round_label_zh = round_label_zh + [""] * (len(round_label) - len(round_label_zh))
    round_notes = round_notes + [""] * (len(round_label) - len(round_notes))
    for row_no, (
        label, label_en, label_zh, kind, opens_at, closes_at, results_at, payment_at,
        r_url, notes_, legs
    ) in enumerate(zip(
        round_label, round_label_en, round_label_zh, round_kind, round_opens_at,
        round_closes_at, round_results_at, round_payment_at, round_url, round_notes,
        round_legs, strict=True,
    ), start=1):
        if not any([label.strip(), opens_at.strip(), closes_at.strip(),
                    results_at.strip(), payment_at.strip()]):
            continue  # a blank trailing row from the repeatable UI
        require_variants(f"Round {row_no} label", label, label_en, label_zh)
        session.add(build_round(
            concert.id, label, kind, opens_at, closes_at, results_at, payment_at, r_url,
            applies_to=parse_round_legs(legs, valid_day_ids, key_to_day_id),
            label_en=label_en, notes=notes_, label_zh=label_zh,
        ))
        await record_round_label_phrase(session, label, label_en, label_zh)

    await session.flush()
    # Same rollup the manual create/edit routes run: the concert's VENUE tags
    # are derived from its legs, so an import must not leave them unset -- and
    # the newly attached ones go through the same notify-and-apply pipeline,
    # since VENUE tags are subscribable (invariant 4).
    newly_venues = await sync_concert_venue_tags(session, concert.id)
    await handle_newly_tagged(session, concert, newly_venues)
    await sync_concert(session, concert.id)
    await session.commit()
    return RedirectResponse(f"/concerts/{concert.event_id}", status_code=303)
