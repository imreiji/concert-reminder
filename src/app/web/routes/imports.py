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
import io
import logging
import zipfile
from collections import namedtuple
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.service import (
    handle_newly_tagged,
    match_tag_ids_by_name,
    match_tag_ids_by_slug,
    match_venue_tag_id,
    match_venue_tag_id_by_slug,
    record_round_label_phrase,
    round_label_phrases,
    sync_concert,
    sync_concert_venue_tags,
    tag_picker_context,
)
from app.db.session import get_session
from app.domain.ingest import IngestError, parse_ramen_event
from app.domain.types import ConcertKind, RoundKind
from app.domain.yaml_import import DraftError, parse_draft
from app.fetching import FetchFailed, HostNotAllowed, check_host, fetch_html
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
            # One chip target per parsed day, keyed by day_key -- the round
            # cards render their leg chips from this via _round_leg_chips.html.
            "legs": _preview_legs(parsed),
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

    def form_error(message: str):
        return templates.TemplateResponse(
            request,
            "import_form.html",
            # lang_next_url: POST-only render, same reason as import_preview.
            {"user": user, "error": message, "lang_next_url": "/concerts/import"},
        )

    if len(draft) > MAX_DRAFT_CHARS:
        return form_error("draft too large -- pastes are capped at 200k characters")
    try:
        parsed = parse_draft(draft)
    except DraftError as e:
        return form_error(str(e))

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
    # fold as per-name "create this tag" chips rather than vanishing -- each
    # carries its kind (franchise/group/artist) so the quick-create dialog can
    # pre-select the right kind. Structured (name, kind) pairs, not a flat name
    # list: the kind is what the chip and the dialog both need.
    initial_selected: dict[str, list[str]] = {}
    unmatched_tags: list[dict] = []
    for kind_name, names, handles in (
        ("franchise", parsed.franchise_names, parsed.franchise_handles),
        ("group", parsed.group_names, parsed.group_handles),
        ("artist", parsed.artist_names, parsed.artist_handles),
    ):
        pool = picker["by_kind"].get(kind_name, [])
        # THE RULE, in one sentence: if series_handles names this kind it is
        # AUTHORITATIVE and the name list is ignored outright; otherwise names
        # resolve exactly as they always have.
        #
        # No per-entry fallback, deliberately. A handle identifies exactly one
        # tag, while a name is documented first-tag-wins and -- now that names
        # may repeat -- a guess. Falling back to the name for a handle that is
        # not here yet would quietly reintroduce that guess, which is the
        # failure this whole arc removed. A missing handle means "import
        # tags.yaml first", so it surfaces as unmatched and the editor decides.
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
        },
    )


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
    await session.commit()
    return RedirectResponse(f"/concerts/{concert.event_id}", status_code=303)
