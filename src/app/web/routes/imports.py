"""Import a concert draft from a ramen.events URL, or from a pasted YAML draft.

  GET  /concerts/import                  paste-a-URL / paste-a-draft form
  POST /concerts/import/preview          fetch + parse a URL -- nothing touches the DB
  POST /concerts/import/draft            parse ONE pasted document -- same, no DB write
  POST /concerts/import/batch            parse MANY '---'-separated documents, persist
                                          each as a PendingDraft work-batch row
  GET  /concerts/import/pending          the triage list of that editor's own rows
  GET  /concerts/import/pending/{id}     re-parse one row, render the SAME preview
  POST /concerts/import/pending/{id}/discard   an editor's "not this one"
  POST /concerts/import/commit           the only route that writes; same field
                                          shape and validation as manual creation
                                          (create_concert_row / build_day / build_round
                                          in concerts.py), just called in a loop.

Nothing is ever auto-saved: preview always renders an editable draft, and
only the editor's final submit on that draft writes anything -- true for a
single pasted draft and for one pulled off the pending list alike, since both
paths end at the same `import_commit`.
"""

import asyncio
import io
import logging
import zipfile
from collections import namedtuple
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PendingDraft, Round
from app.db.service import (
    create_pending_drafts,
    discard_pending_draft,
    handle_newly_tagged,
    mark_pending_committed,
    match_tag_ids_by_name,
    match_tag_ids_by_slug,
    match_venue_tag_id,
    match_venue_tag_id_by_slug,
    pending_drafts,
    record_round_label_phrase,
    round_label_phrases,
    sync_concert,
    sync_concert_venue_tags,
    tag_picker_context,
)
from app.db.session import get_session
from app.domain.draft import ParsedConcert
from app.domain.ingest import IngestError, parse_ramen_event
from app.domain.types import ITEM_SALE_KINDS, ConcertKind, RoundKind
from app.domain.yaml_import import DraftError, parse_draft, parse_drafts
from app.fetching import FetchFailed, HostNotAllowed, check_host, fetch_html
from app.web.auth import SessionUser, require_editor
from app.web.forms import form_url, require_variants
from app.web.routes.concerts import (
    RoundRequiresError,
    all_venue_tags,
    build_day,
    build_round,
    create_concert_row,
    generate_event_id,
    parse_round_legs,
    resolve_day_venue_tags,
    resolve_round_requires,
    validate_event_id,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/concerts/import")

templates = None  # set by web.app at startup

ALLOWED_HOST = "ramen.events"
FETCH_TIMEOUT = 10.0
MAX_RESPONSE_BYTES = 2_000_000
MAX_REDIRECTS = 5
MAX_DRAFT_CHARS = 200_000
# A separate, larger ceiling for /batch: a realistic agent-authored draft
# runs roughly 1,900 characters, and the owner has explicitly said a
# research sweep "could be 100 events" -- a hundred of those is already
# ~190,000 characters, right up against MAX_DRAFT_CHARS. Worse than being
# merely tight, exceeding either cap rejects the WHOLE paste with no
# partial credit, which is the exact opposite of this feature's governing
# rule (one bad document must cost nothing but itself). This constant is
# deliberately its own name, not a multiplied MAX_DRAFT_CHARS, so the
# single-document path's cap can never drift by editing this one.
#
# NOT the 2,000,000 that would otherwise match MAX_RESPONSE_BYTES's
# precedent above -- measured directly (never reasoned about): FastAPI's
# `Form(...)` calls Starlette's `Request.form()` with no arguments, which
# hard-caps EVERY form field at `max_part_size` = 1,048,576 BYTES (1MB),
# with no override reachable through the public `Form()` API. A paste past
# that wall never even reaches this route -- Starlette itself answers a bare
# `{"detail": "Field exceeded maximum size of 1024KB."}` 400, which is a
# worse version of exactly the failure this constant exists to prevent
# (a whole paste lost with no partial credit and no per-document detail).
# This app's drafts are trilingual, so CJK content -- 3 bytes per character
# in UTF-8 -- is the realistic worst case, not the exception: 300,000
# characters is only 900,000 bytes even if EVERY character were CJK, safely
# under the 1,048,576-byte wall, while still covering ~157 drafts at the
# realistic 1,900-character size -- past the owner's "100" with real margin.
MAX_BATCH_CHARS = 300_000


def _check_host(url: str) -> None:
    """SSRF guard: v1 only fetches ramen.events, an allowlist, not a blocklist.

    The guard itself lives in `app/fetching.py`, shared with the Eventernote
    discovery sweep; this wrapper only turns its error into the 400 this route
    has always answered.
    """
    try:
        check_host(url, ALLOWED_HOST)
    except HostNotAllowed as exc:
        raise HTTPException(
            status_code=400,
            detail=f"only https://{ALLOWED_HOST}/... URLs are supported",
        ) from exc


async def fetch_ramen_html(url: str, transport: httpx.AsyncBaseTransport | None = None) -> str:
    """Fetch a ramen.events page, or raise this route's HTTP errors.

    `fetch_html` re-checks the host on EVERY redirect hop (a bare
    follow_redirects=True would chase a redirect issued by ramen.events to an
    arbitrary address, silently defeating the allowlist) and reads the body in
    capped chunks so an oversized response is aborted mid-download.

    The module constants are passed explicitly rather than left to the shared
    defaults, so this route's limits stay this route's to tune.

    `transport` is test-only (httpx.MockTransport); production always uses
    httpx's default.
    """
    try:
        return await fetch_html(
            url,
            allowed_host=ALLOWED_HOST,
            user_agent="dekimasen.app/1.0 (event import)",
            timeout=FETCH_TIMEOUT,
            max_bytes=MAX_RESPONSE_BYTES,
            max_redirects=MAX_REDIRECTS,
            transport=transport,
        )
    except HostNotAllowed as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FetchFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _fmt(dt) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M") if dt else ""


def _import_form_context(user: SessionUser, **extra) -> dict:
    """The context every render of `import_form.html` needs -- a plain GET
    and several error/partial-failure re-renders alike -- built by ONE
    function so a future error path can't forget `max_batch_chars_display`
    (the persistent hint under the batch paste box) and show a blank number
    where the cap belongs."""
    return {
        "user": user,
        "max_batch_chars_display": f"{MAX_BATCH_CHARS:,}",
        **extra,
    }


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


def _resolve_series_tags(
    parsed: ParsedConcert, picker: dict
) -> tuple[dict[str, list[str]], list[dict]]:
    """Franchise/group/character/artist NAMES (or HANDLES, when the draft
    carries `series_handles`) -> picker pre-selection, plus the names that
    matched nothing.

    Shared by `_draft_preview_response` (which needs the pre-selection to
    render the picker) and the pending list (which only needs the count of
    the second return value) -- one place deciding what "unmatched" means, so
    the two surfaces can never quietly disagree on it. A missing-handle
    warning is appended onto `parsed.warnings` in place, exactly as it was
    before this was split out.
    """
    initial_selected: dict[str, list[str]] = {}
    unmatched_tags: list[dict] = []
    for kind_name, names, handles in (
        ("franchise", parsed.franchise_names, parsed.franchise_handles),
        ("group", parsed.group_names, parsed.group_handles),
        ("character", parsed.character_names, parsed.character_handles),
        ("artist", parsed.artist_names, parsed.artist_handles),
    ):
        pool = picker["by_kind"].get(kind_name, [])
        # THE RULE, in one sentence: if series_handles names this kind it is
        # AUTHORITATIVE and the name list is ignored outright; otherwise names
        # resolve exactly as they always have. See parse_draft's own docstring
        # for why there is no per-entry fallback.
        if handles:
            ids, missing = match_tag_ids_by_slug(handles, pool)
            if missing:
                parsed.warnings.append(
                    f"series_handles.{kind_name}s: {', '.join(missing)} not in the "
                    f"catalogue -- import tags.yaml first, or pick them by hand. "
                    f"The name list was NOT used as a fallback."
                )
        else:
            ids, missing = match_tag_ids_by_name(names, pool)
        if ids:
            initial_selected[kind_name] = [str(i) for i in ids]
        unmatched_tags.extend({"name": name, "kind": kind_name} for name in missing)
    return initial_selected, unmatched_tags


@router.get("", response_class=HTMLResponse)
async def import_form(
    request: Request,
    user: SessionUser = Depends(require_editor),
    error: str = "",
):
    return templates.TemplateResponse(
        request, "import_form.html", _import_form_context(user, error=error)
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
            _import_form_context(user, error=detail, lang_next_url="/concerts/import"),
        )

    picker = await tag_picker_context(session)
    # The option list behind each leg row's venue picker -- the same helper
    # (and so the same ordering) both editor pages use. The scraped venue name
    # is matched against it here, at the route boundary, so the template only
    # ever compares ids.
    venue_tags = await all_venue_tags(session)
    # One scraped venue for the whole event: stamp the match onto every day so
    # the template reads a single per-day attribute for both paths.
    matched = match_venue_tag_id(parsed.venue_name, venue_tags)
    for d in parsed.days:
        d.matched_venue_tag_id = matched
    return templates.TemplateResponse(
        request,
        "import_preview.html",
        {
            "user": user, "parsed": parsed, "source_url": url,
            # A scrape has no event_id to preserve; the field renders blank
            # and import_commit generates one. Passed explicitly rather than
            # left to Jinja Undefined being falsy by luck.
            "event_id": "",
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
            # {character id: seiyuu id} -- keeps a derived seiyuu out of the
            # artist row while her character is selected.
            "character_seiyuu": picker["character_seiyuu"],
            "tag_names": picker["tag_names"],
            # Handles for the tags whose (name, kind) collides -- the picker
            # shows one beneath the chip so two identical chips are
            # distinguishable. Empty for almost every tag.
            "tag_disambiguators": picker["tag_disambiguators"],
            "initial_selected": {},
            # Every VENUE tag, for the per-leg <select>.
            "venue_tags": venue_tags,
            # Trilingual round labels already typed on earlier concerts, for
            # the picker each round row's "Remembered" chip opens. The scrape
            # fills Japanese only, so this is where they pay off most.
            "round_phrases": await round_label_phrases(session),
            # The one free-text venue the ramen.events parse scrapes for the
            # whole event. It is shown as a hint beside each leg's venue picker
            # (the importer's find must not be thrown away when no tag matches)
            # and, when it DOES match a VENUE tag by trimmed case-insensitive
            # name, pre-selects that tag below.
            "venue_hint": parsed.venue_name,
            "matched_venue_tag_id": matched,
            # No name->tag resolution on the URL path, so nothing unmatched to
            # offer a create chip for -- the draft path below is the producer.
            "unmatched_tags": [],
            # The ramen.events parse never produces a requires-link -- see
            # ParsedRound.requires_label, parser-filled only by yaml_import --
            # so there is nothing to offer in the requires <select>. Passed
            # explicitly (never left to Jinja Undefined) so the template never
            # sees an undefined name.
            "requires_options": [],
            # One chip target per parsed day, keyed by day_key -- the round
            # cards render their leg chips from this via _round_leg_chips.html.
            "legs": _preview_legs(parsed),
            # The URL-scrape path has no pending-draft workflow -- only a
            # pasted draft can be part of a multi-draft batch.
            "pending_id": None,
        },
    )


async def _draft_preview_response(
    request: Request,
    user: SessionUser,
    session: AsyncSession,
    parsed: ParsedConcert,
    pending_id: int | None = None,
) -> HTMLResponse:
    """Build the `import_preview.html` render for an already-parsed draft.

    Shared by `import_draft` (a fresh single-document paste) and
    `import_pending_detail` (re-triaging a stored `PendingDraft` row) so the
    two can never drift on how a parsed draft becomes a preview -- there used
    to be only one caller, and this is that caller's body, unchanged, minus
    the parsing itself (each caller gets `parsed` its own way) and plus
    `pending_id`, which rides through as a hidden field on the preview form so
    `import_commit` knows which row to stamp on a successful commit.
    """
    picker = await tag_picker_context(session)
    venue_tags = await all_venue_tags(session)

    # Per-leg venue resolution: a draft names a venue on each performance.
    for d in parsed.days:
        # Same rule per leg: a handle is authoritative, so the name is only
        # consulted when there is no handle at all.
        d.matched_venue_tag_id = (
            match_venue_tag_id_by_slug(d.venue_handle, venue_tags)
            if d.venue_handle
            else match_venue_tag_id(d.venue_name, venue_tags)
        )

    # Tag names -> picker pre-selection. Unmatched names surface in the Tags
    # fold as per-name "create this tag" chips rather than vanishing -- see
    # _resolve_series_tags.
    initial_selected, unmatched_tags = _resolve_series_tags(parsed, picker)

    # applies_to leg labels -> the preview's day_key scheme ("d0", "d1", ...),
    # first row claiming a duplicate label keeps it (same rule as
    # import_commit's key_to_day_id).
    label_to_key: dict[str, str] = {}
    for i, d in enumerate(parsed.days):
        label_to_key.setdefault(d.label.strip(), f"d{i}")
    for r in parsed.rounds:
        keys = []
        for lbl in r.applies_to_labels:
            key = label_to_key.get(lbl.strip())
            if key is None:
                parsed.warnings.append(
                    f"round {r.label!r}: no performance labelled {lbl!r} -- "
                    "that leg reference was dropped, tick it by hand"
                )
            else:
                keys.append(key)
        r.leg_keys = " ".join(keys)
        r.leg_keys_selected = set(keys)

    # requires -> the preview's round_key scheme ("r0", "r1", ...), the same
    # label-claiming rule as legs: first round with a duplicate label keeps it.
    round_label_to_key: dict[str, str] = {}
    for i, r in enumerate(parsed.rounds):
        r.round_key = f"r{i}"
        round_label_to_key.setdefault(r.label.strip(), f"r{i}")
    for r in parsed.rounds:
        if not r.requires_label:
            continue
        key = round_label_to_key.get(r.requires_label.strip())
        if key is None or key == r.round_key:
            parsed.warnings.append(
                f"round {r.label!r}: no other round labelled "
                f"{r.requires_label!r} -- that item link was dropped, "
                "pick it by hand"
            )
        else:
            r.requires_key = key

    return templates.TemplateResponse(
        request,
        "import_preview.html",
        {
            "user": user, "parsed": parsed,
            "event_id": parsed.event_id or "",
            "source_url": parsed.source_url or "",
            "lang_next_url": "/concerts/import",
            "fmt": _fmt, "kinds": list(RoundKind),
            "concert_kinds": list(ConcertKind),
            "by_kind": picker["by_kind"],
            "groups": picker["groups"],
            "character_seiyuu": picker["character_seiyuu"],
            "tag_names": picker["tag_names"],
            # Handles for the tags whose (name, kind) collides -- the picker
            # shows one beneath the chip so two identical chips are
            # distinguishable. Empty for almost every tag.
            "tag_disambiguators": picker["tag_disambiguators"],
            "initial_selected": initial_selected,
            "venue_tags": venue_tags,
            "round_phrases": await round_label_phrases(session),
            # Event-level venue hint is the URL path's concept; drafts carry
            # venues per leg and hint per leg instead.
            "venue_hint": None,
            "matched_venue_tag_id": None,
            "legs": _preview_legs(parsed),
            "unmatched_tags": unmatched_tags,
            # Options for the "Requires item from" <select> (Task 6/7): every
            # item/goods-sale round IN THIS DRAFT, keyed by the preview's own
            # round_key scheme -- there is no saved round yet to key by id.
            "requires_options": [
                (r.round_key, r.label)
                for r in parsed.rounds
                if r.kind in ITEM_SALE_KINDS
            ],
            # None for a fresh paste (import_draft never sets it): the commit
            # form then carries no pending_id field at all, and import_commit
            # falls through to its unset-pending_id behaviour exactly as
            # before this feature existed.
            "pending_id": pending_id,
        },
    )


@router.post("/draft", response_class=HTMLResponse)
async def import_draft(
    request: Request,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
    draft: str = Form(...),
):
    """Paste an agent-authored YAML draft, get the SAME preview the URL path
    renders -- fully prefilled. Renders only; import_commit stays the one
    write path, and every commit-boundary gate (variants rule, form_url,
    venue rollup) applies to this data exactly as to typed data.

    Tag and venue NAMES resolve to ids here, at the route boundary, the same
    place the URL path matches its scraped venue name -- the draft never
    carries database ids.
    """
    if len(draft) > MAX_DRAFT_CHARS:
        return templates.TemplateResponse(
            request,
            "import_form.html",
            # lang_next_url: POST-only render, same reason as import_preview.
            _import_form_context(
                user,
                error="draft too large -- pastes are capped at 200k characters",
                lang_next_url="/concerts/import",
            ),
        )
    try:
        parsed = parse_draft(draft)
    except DraftError as e:
        return templates.TemplateResponse(
            request,
            "import_form.html",
            _import_form_context(user, error=str(e), lang_next_url="/concerts/import"),
        )
    return await _draft_preview_response(request, user, session, parsed)


@dataclass(frozen=True)
class PendingRow:
    """One row of the pending-drafts list: the stored row, plus the
    triage-worthy summary a fresh re-parse of its own `draft_text` yields."""

    row: PendingDraft
    leg_count: int
    round_count: int
    unmatched_count: int
    # True only if draft_text no longer parses at all -- can't happen from a
    # row this feature created (create_pending_drafts only ever stores a
    # document that already parsed once), but a future parser change must not
    # 500 the whole list over one stale row.
    unreadable: bool = False


@router.post("/batch", response_class=HTMLResponse)
async def import_batch(
    request: Request,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
    draft: str = Form(...),
):
    """Paste MANY '---'-separated YAML documents at once. One `PendingDraft`
    row is persisted per document that parses; `/draft` above stays the
    single-document path everything else (the preview, the skill zip, its own
    tests) already builds on.

    Deliberately NOT all-or-nothing. `parse_drafts` (domain/yaml_import.py)
    already isolates one bad document from the rest of the same paste, and
    `create_pending_drafts` (db/service.py) already persists every document
    that DID parse and ignores the ones that didn't -- Task 1 and Task 2's
    whole point was that a typo in document 37 of 50 must not cost the other
    49. Re-imposing an all-or-nothing gate HERE, by refusing to create any row
    unless every document parsed, would undo that one layer up, which is
    exactly the gap Task 2's report flagged: a caller that silently drops
    `batch.errors` reintroduces the "one bad document loses everything"
    failure by omission, just one route higher than where Task 1 fixed it. So
    this route always creates rows for what parsed, and ALWAYS reports what
    didn't, by document number, on the very form the editor pasted into --
    nothing here is allowed to vanish silently.
    """
    # Its own cap, not MAX_DRAFT_CHARS -- see MAX_BATCH_CHARS's own comment.
    # A hundred realistic drafts already presses against the single-document
    # limit, and exceeding either cap rejects the WHOLE paste with no partial
    # credit, the exact opposite of this route's governing rule.
    if len(draft) > MAX_BATCH_CHARS:
        return templates.TemplateResponse(
            request,
            "import_form.html",
            _import_form_context(
                user,
                error=(
                    f"draft too large -- batch pastes are capped at "
                    f"{MAX_BATCH_CHARS:,} characters, split it into two pastes"
                ),
                lang_next_url="/concerts/import",
            ),
        )
    batch = parse_drafts(draft)
    rows = await create_pending_drafts(session, batch, user.id)
    await session.commit()
    if batch.errors:
        return templates.TemplateResponse(
            request,
            "import_form.html",
            _import_form_context(
                user,
                batch_errors=batch.errors,
                batch_created=len(rows),
                lang_next_url="/concerts/import",
            ),
        )
    return RedirectResponse("/concerts/import/pending", status_code=303)


@router.get("/pending", response_class=HTMLResponse)
async def import_pending_list(
    request: Request,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
):
    """The still-open rows from the caller's OWN batch pastes -- scoped by
    `pending_drafts` itself (never committed, never discarded, `created_by`
    filtered), so two editors triaging their own batches at once naturally
    see only their own.

    Each row is re-parsed from its own stored `draft_text` purely to show a
    triage-worthy summary (legs / rounds / unmatched series tags) -- re-parsing
    per row rather than caching counts at paste time means the counts always
    reflect the CURRENT parser and the CURRENT catalogue (a tag created since
    the paste now counts as matched), at the cost of one parse and one tag
    comparison per row, which is cheap next to the query already paid to list
    them.
    """
    rows = await pending_drafts(session, user.id)
    picker = await tag_picker_context(session)
    pending_rows: list[PendingRow] = []
    for row in rows:
        try:
            parsed = parse_draft(row.draft_text)
        except DraftError:
            pending_rows.append(PendingRow(
                row=row, leg_count=0, round_count=0, unmatched_count=0, unreadable=True,
            ))
            continue
        _selected, unmatched = _resolve_series_tags(parsed, picker)
        pending_rows.append(PendingRow(
            row=row,
            leg_count=len(parsed.days),
            round_count=len(parsed.rounds),
            unmatched_count=len(unmatched),
        ))
    return templates.TemplateResponse(
        request, "import_pending.html", {"user": user, "rows": pending_rows}
    )


async def _own_open_pending(
    session: AsyncSession, pending_id: int, user: SessionUser
) -> PendingDraft | None:
    """The one row `pending_id` names, or None when it doesn't exist, belongs
    to somebody else, or has already been resolved (committed/discarded) --
    three reasons to answer the same way. Ownership 404s rather than 403s
    (invariant 5: don't confirm to a caller that another editor's id exists).
    """
    row = await session.get(PendingDraft, pending_id)
    if row is None or row.created_by != user.id:
        return None
    if row.committed_at is not None or row.discarded_at is not None:
        return None
    return row


@router.get("/pending/{pending_id}", response_class=HTMLResponse)
async def import_pending_detail(
    request: Request,
    pending_id: int,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
):
    """Re-parse one pending row's `draft_text` and render the SAME preview a
    fresh paste renders, through the SAME `_draft_preview_response` -- the
    whole point of a pending row is that triaging it later is indistinguishable
    from having just pasted it, except the preview now carries `pending_id` so
    the commit at the bottom stamps this row and returns to the list instead
    of the concert page.
    """
    row = await _own_open_pending(session, pending_id, user)
    if row is None:
        raise HTTPException(status_code=404)
    try:
        parsed = parse_draft(row.draft_text)
    except DraftError as e:
        # Can't currently happen (this row's text already parsed once, at
        # paste time) -- but a future parser change must render the ordinary
        # form-error path rather than 500 on a row nobody can now fix.
        return templates.TemplateResponse(
            request,
            "import_form.html",
            _import_form_context(user, error=str(e), lang_next_url="/concerts/import"),
        )
    return await _draft_preview_response(request, user, session, parsed, pending_id=row.id)


@router.post("/pending/{pending_id}/discard")
async def import_pending_discard(
    pending_id: int,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
):
    """An editor's "not this one" on a triage row: stamp `discarded_at`, back
    to the list. 404s exactly like the GET above rather than silently no-op'ing
    on an id that belongs to someone else."""
    row = await _own_open_pending(session, pending_id, user)
    if row is None:
        raise HTTPException(status_code=404)
    await discard_pending_draft(session, pending_id, datetime.now(UTC))
    await session.commit()
    return RedirectResponse("/concerts/import/pending", status_code=303)


SKILL_DIST_DIR = Path(__file__).resolve().parents[1] / "skill_dist" / "add-concert"


@router.get("/skill.zip")
async def download_skill_zip(user: SessionUser = Depends(require_editor)):
    """The add-concert agent skill, zipped for distribution to other
    editors. Built from src/app/web/skill_dist/ at request time -- a
    committed binary zip would go stale the moment the skill changed,
    and this way the only drift risk (the schema example) is pinned by
    test to the repo skill's copy. Editor-gated like the import page
    that links it. The zip is a few KB, so building it inline on the
    event loop is fine -- unlike the BS4 parse above, there is nothing
    here worth a thread hop.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(SKILL_DIST_DIR.rglob("*")):
            if path.is_file():
                # as_posix(): zip entries use forward slashes; str(Path)
                # would bake backslashes in on Windows dev machines.
                zf.write(path, arcname="add-concert/" + path.relative_to(SKILL_DIST_DIR).as_posix())
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="add-concert-skill.zip"'},
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
    notes_en: str = Form(default=""),
    notes_zh: str = Form(default=""),
    event_id: str = Form(default=""),
    source_url: str = Form(default=""),
    eventernote_url: str = Form(default=""),
    official_url: str = Form(default=""),
    performers_text: str = Form(default=""),
    franchise_tags: list[int] = Form(default=[]),
    group_tags: list[int] = Form(default=[]),
    artist_tags: list[int] = Form(default=[]),
    # The preview renders the SHARED tag picker, so its character row submits
    # here whether or not a draft can name one yet. Collected and passed
    # through rather than left to FastAPI's silent drop of unknown fields: an
    # editor who ticks 如月千早 on the preview would otherwise watch the chip
    # vanish on commit with nothing said.
    character_tags: list[int] = Form(default=[]),
    venue_tags: list[int] = Form(default=[]),
    day_key: list[str] = Form(default=[]),
    day_label: list[str] = Form(default=[]),
    day_label_en: list[str] = Form(default=[]),
    day_label_zh: list[str] = Form(default=[]),
    day_starts_at: list[str] = Form(default=[]),
    day_venue_tag_id: list[str] = Form(default=[]),
    day_doors_at: list[str] = Form(default=[]),
    day_cancelled: list[str] = Form(default=[]),
    day_eventernote_event_id: list[str] = Form(default=[]),
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
    round_key: list[str] = Form(default=[]),
    round_requires: list[str] = Form(default=[]),
    # Set only when this commit is triaging a PendingDraft row (the preview
    # renders it as a hidden field -- see _draft_preview_response). Absent for
    # every other caller: the URL-scrape path, a single fresh /draft paste,
    # and every existing test of this route, all of which must behave exactly
    # as they did before this feature existed.
    pending_id: int | None = Form(None),
):
    """Same validation as manual entry (build_day/build_round), just looped
    -- create_concert_row + add_day + add_round combined into one commit.
    event_id is OPTIONAL here: an exported draft carries the concert's own,
    so a restore lands on the original URL, and validate_event_id checks it
    exactly as the edit page would (format, reserved words, uniqueness -- so a
    re-import of a concert that still exists answers 409). Absent, it is
    auto-suggested from the title (slugified, de-duplicated) and editable
    afterward via the edit page.

    Rounds bind to legs by chip exactly as create_concert does: every leg is
    brand-new here (no id until the flush), so its card carries a client-side
    `day_key` that the round's `round_legs` value references, resolved to real
    ids through key_to_day_id after the days flush. Upgrade qualifiers are not
    part of import -- the parser never produces an UPGRADE round -- so this
    route does not collect round_qualifiers.

    `pending_id` set means this commit is triaging a stored PendingDraft: on
    success the row is stamped committed (mark_pending_committed) and the
    redirect goes to the pending LIST rather than the new concert, so the next
    draft in the batch is one click away instead of a trip back through
    /concerts/import. Unset, this route behaves exactly as it always has."""
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
    # Notes, same all-or-nothing rule create_concert applies -- but NOT
    # mandatory: an imported concert with no notes at all is the normal case,
    # and only a half-filled trio is a violation. Without this the import was
    # the one surviving way to create a rule-breaking row, and the edit page's
    # variant-gaps notice would then nag about a gap this form gave the editor
    # no field to fill.
    require_variants("Notes", notes, notes_en, notes_zh)

    # A draft may carry its own event_id, so a restore lands on the ORIGINAL
    # URL rather than minting a new one and breaking every link people hold.
    # validate_event_id is the SAME check the edit page runs -- format,
    # reserved words (invariant 6), uniqueness -- rather than a second copy of
    # the rule, so re-importing a file whose concert still exists answers 409
    # instead of quietly creating a duplicate.
    submitted_event_id = event_id.strip()
    event_id = (
        await validate_event_id(session, submitted_event_id)
        if submitted_event_id
        else await generate_event_id(session, title, title_en)
    )
    concert, newly = await create_concert_row(
        session, user, title, event_id, franchise_tags, group_tags, artist_tags, venue_tags,
        character_tags=character_tags,
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
    concert.eventernote_url = form_url(eventernote_url)
    concert.official_url = form_url(official_url)
    concert.performers_text = performers_text.strip() or None
    concert.notes = notes.strip() or None
    concert.notes_en = notes_en.strip() or None
    concert.notes_zh = notes_zh.strip() or None

    # The optional day_* fields (doors, cancelled) round-trip in full from the
    # preview form, but a minimal client -- the older import contract, and its
    # tests -- posts only day_label/day_starts_at. End-pad those secondary
    # arrays to the label count so their omission is read as "blank for every
    # row" (their own default) rather than tripping the strict zip below. Safe
    # because they are non-binding display text: a trailing row losing empty
    # text is harmless.
    n_days = len(day_label)
    # day_label_en/day_label_zh join that same end-padded group -- non-binding
    # display text, and the minimal client above supplies neither. This is the
    # rule round_label_en already follows in THIS route (and only here: the
    # manual create/edit routes leave every label variant unpadded, because
    # they have no minimal-client contract to honour).
    day_label_en = day_label_en + [""] * (n_days - len(day_label_en))
    day_label_zh = day_label_zh + [""] * (n_days - len(day_label_zh))
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
    # The Eventernote id follows day_venue_tag_id's rule, not the end-padding
    # above: it is a per-leg FACT, not display text, so a partial array is left
    # alone and the strict zip raises rather than stamping one leg's id onto
    # another -- a wrong id here is worse than a missing one, because the
    # discovery diff would then treat a performance we do NOT have as held and
    # never mention it again. Only a WHOLLY-omitted array (the minimal import
    # contract, and every client predating this field) is padded to blanks.
    if not day_eventernote_event_id:
        day_eventernote_event_id = [""] * n_days
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
        key, label, label_en, label_zh, starts_at,
        doors_at, cancelled, v_tag, en_event_id
    ) in enumerate(zip(
        day_key, day_label, day_label_en, day_label_zh, day_starts_at,
        day_doors_at, day_cancelled, day_venue_tags, day_eventernote_event_id, strict=True,
    ), start=1):
        # v_tag is in the guard so a row where the editor picked ONLY a venue
        # (no label, no start time yet) is not read as blank and dropped.
        if not any([label.strip(), starts_at.strip(), v_tag]):
            continue  # a blank trailing row from the repeatable UI -- key and all
        # Same create-boundary rule create_concert applies, with the same row
        # numbering: an import is a create, so its labels are held to it too.
        require_variants(f"Leg {row_no} label", label, label_en, label_zh)
        day = build_day(
            concert.id, label, starts_at, doors_at, cancelled,
            v_tag, label_en, label_zh,
        )
        # Set here rather than through build_day: the import is the ONLY path
        # that carries it (the manual create/edit forms have no such field), and
        # build_day is shared with them. This is what lets discovery answer "do
        # I already have this performance?" by id later -- see
        # ConcertDay.eventernote_event_id and service.record_discovered's
        # branch 1.
        day.eventernote_event_id = en_event_id.strip() or None
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
    # round_key/round_requires are newer still, and NOT end-padded like the
    # display-text arrays just above: they follow round_legs' rule instead
    # (padded only on whole-array omission -- an older client with no
    # required-item picker -- while a partial array is left alone so the
    # strict zip below raises rather than sliding a token onto the wrong row).
    if not round_key:
        round_key = [""] * len(round_label)
    if not round_requires:
        round_requires = [""] * len(round_label)
    # (round object, its submitted kind, its client key, its raw requires
    # token) for every created round -- resolved into required_item_round_id
    # AFTER the flush below hands the new rounds their ids, same post-flush
    # ordering create_concert uses. Every round here is new (import_commit
    # never edits an existing concert), so unlike create_concert's edit-aware
    # sibling this needs no id-token branch -- every round_requires value is
    # necessarily a round_key.
    round_jobs: list[tuple[Round, RoundKind, str, str]] = []
    for row_no, (
        label, label_en, label_zh, kind, opens_at, closes_at, results_at, payment_at,
        r_url, notes_, legs, key, req
    ) in enumerate(zip(
        round_label, round_label_en, round_label_zh, round_kind, round_opens_at,
        round_closes_at, round_results_at, round_payment_at, round_url, round_notes,
        round_legs, round_key, round_requires, strict=True,
    ), start=1):
        if not any([label.strip(), opens_at.strip(), closes_at.strip(),
                    results_at.strip(), payment_at.strip()]):
            continue  # a blank trailing row from the repeatable UI
        require_variants(f"Round {row_no} label", label, label_en, label_zh)
        round_ = build_round(
            concert.id, label, kind, opens_at, closes_at, results_at, payment_at, r_url,
            applies_to=parse_round_legs(legs, valid_day_ids, key_to_day_id),
            label_en=label_en, notes=notes_, label_zh=label_zh,
        )
        session.add(round_)
        await record_round_label_phrase(session, label, label_en, label_zh)
        round_jobs.append((round_, kind, key, req))

    await session.flush()  # new rounds now have ids, needed to resolve requires
    key_to_round_id: dict[str, int] = {}
    for round_, _k, key, _r in round_jobs:
        if key.strip():
            key_to_round_id.setdefault(key.strip(), round_.id)
    kinds_by_id = {round_.id: kind for round_, kind, _k, _r in round_jobs}
    for round_, _kind, _key, req in round_jobs:
        try:
            round_.required_item_round_id = resolve_round_requires(
                req, key_to_round_id, kinds_by_id, round_.id
            )
        except RoundRequiresError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Round {round_.label!r}: required-item link {exc}",
            ) from exc

    await session.flush()
    # The concert-level tag attach's notify-and-apply pipeline, run HERE and not
    # inside create_concert_row where the attach happens -- it asks
    # all_legs_cancelled, and this import's legs do not exist yet at the attach
    # site, so an import whose only leg arrives cancelled (the preview form
    # posts day_cancelled) would announce a show that is off. Same placement
    # and reasoning as create_concert and edit_concert.
    await handle_newly_tagged(session, concert, newly)
    # Same rollup the manual create/edit routes run: the concert's VENUE tags
    # are derived from its legs, so an import must not leave them unset -- and
    # the newly attached ones go through the same notify-and-apply pipeline,
    # since VENUE tags are subscribable (invariant 4).
    newly_venues = await sync_concert_venue_tags(session, concert.id)
    await handle_newly_tagged(session, concert, newly_venues)
    await sync_concert(session, concert.id)
    # Stamp the pending row (if any) in the SAME transaction as the concert it
    # produced, so the two can never disagree after a crash between them.
    # Ownership-checked here rather than trusted from the hidden field: the
    # only legitimate source of a pending_id is this editor's own
    # _draft_preview_response render, but the field still rode through the
    # browser like any other, and a foreign or already-resolved id must
    # refuse the commit outright (see the 409 branch below) rather than
    # silently fall through to an ordinary create -- that fallthrough used
    # to be this route's behavior, and it is exactly what let a resubmit of
    # an already-committed preview mint a silent second concert.
    if pending_id is not None:
        pending_row = await _own_open_pending(session, pending_id, user)
        if pending_row is None:
            # Already committed, already discarded, or not this editor's row
            # -- refuse the WHOLE commit rather than silently falling through
            # to an ordinary create. Falling through is exactly what let a
            # back-button resubmit of an already-reviewed, already-committed
            # preview mint a silent SECOND concert: an agent draft carries no
            # event_id (see the add-concert skill's own example), so the
            # resubmit never collides on event_id -- generate_event_id just
            # de-dupes ("alpha", "alpha-2") -- and nothing else was watching.
            # The concert above is only FLUSHED, never committed, so raising
            # here (before session.commit()) discards it: FastAPI's
            # get_session dependency closes this AsyncSession on an
            # unhandled exception, and closing an uncommitted session rolls
            # back everything flushed within it.
            raise HTTPException(status_code=409)
        await mark_pending_committed(session, pending_row.id, concert.id, datetime.now(UTC))
        await session.commit()
        return RedirectResponse("/concerts/import/pending", status_code=303)
    await session.commit()
    return RedirectResponse(f"/concerts/{concert.event_id}", status_code=303)
