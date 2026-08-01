"""Concert CRUD -- the web UI's working core.

Personal reminder-rule mutations (add/delete) live in web/routes/reminders.py;
this file keeps render_rules_fragment/user_rules since the concert-detail page
and reminders.py/preferences.py all share them. User-timezone preference
routes live in web/routes/preferences.py.

Route conventions:
  * Mutations are POSTs. Page-level actions redirect (PRG pattern); the
    reminder-rule fragment swap lives in reminders.py, not here.
  * require_editor guards everything that changes a concert's own data
    (creation, the rich edit page, deletion).
  * EVERY mutation that touches dates ends with a queue re-sync
    (sync_concert). That is the contract that makes the web UI and the
    scheduler agree with each other.
  * Concerts are looked up by `event_id` (a stable, editor-chosen URL
    handle) everywhere in this file's routes. `Concert.id` (the real PK)
    stays the FK target for everything internal -- reminder rules, the
    queue, bot button custom_ids -- and is never itself URL-facing.

Datetime contract: <input type="datetime-local"> values are interpreted as
JST — that is how Japanese ticketing announces times, so the form matches
the source material. Conversion to UTC happens here, at the boundary,
via domain.timezones.jst_to_utc. Nowhere else.
"""

import re
from collections.abc import Sequence
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    Concert,
    ConcertDay,
    ConcertTag,
    LegOptOut,
    ReminderRule,
    Round,
    RoundOutcome,
    RoundQualifier,
    Tag,
    TagSubscription,
    User,
)
from app.db.service import (
    RoundRow,
    _qualifiers_by_upgrade_round,
    all_legs_cancelled,
    attach_tag,
    concert_audit_log,
    concert_export_yaml,
    concert_next_moment,
    concert_round_rows,
    concert_subscription_states,
    concert_variant_gaps,
    detach_tag,
    ensure_user,
    forget_round_label_phrase,
    get_default_preset,
    group_members,
    handle_newly_tagged,
    notify_newly_cancelled_legs,
    performer_clusters,
    record_concert_edit,
    record_round_label_phrase,
    round_label_phrases,
    snapshot_concert,
    sync_concert,
    sync_concert_venue_tags,
    tag_picker_context,
    tracked_concert_ids,
)
from app.db.session import get_session
from app.domain.timezones import jst_to_utc
from app.domain.types import Anchor, ConcertKind, LotteryOutcome, RoundKind, TagKind
from app.i18n import get_locale, loc_field
from app.web.auth import SessionUser, require_editor, require_user
from app.web.forms import form_url, require_variants

router = APIRouter()

# set by web.app at startup to avoid a circular import
templates = None

# event_id becomes a URL path segment: restrict to a safe charset, and block
# the two literal segments FastAPI would otherwise treat as real routes
# (/concerts/new, /concerts/import) -- literal paths must stay unambiguous
# from the {event_id} catch-all (this project has hit that routing-order
# gotcha once already).
EVENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
RESERVED_EVENT_IDS = {"new", "import"}


def parse_jst(value: str | None) -> datetime | None:
    """'2026-08-01T19:00' from a datetime-local input, interpreted as JST -> UTC."""
    if not value:
        return None
    try:
        naive = datetime.strptime(value, "%Y-%m-%dT%H:%M")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"bad datetime: {value!r}") from e
    return jst_to_utc(naive)


async def get_concert(session: AsyncSession, concert_id: int) -> Concert:
    """Internal PK lookup -- for code that already has a real id (rounds,
    reminder rules) and isn't resolving a URL."""
    concert = await session.get(Concert, concert_id)
    if concert is None:
        raise HTTPException(status_code=404, detail="concert not found")
    return concert


async def get_concert_by_event_id(session: AsyncSession, event_id: str) -> Concert:
    """The URL-facing lookup every route in this file uses."""
    result = await session.execute(select(Concert).where(Concert.event_id == event_id))
    concert = result.scalar_one_or_none()
    if concert is None:
        raise HTTPException(status_code=404, detail="concert not found")
    return concert


async def validate_event_id(
    session: AsyncSession, event_id: str, exclude_concert_id: int | None = None
) -> str:
    """Format + reserved-word + uniqueness checks, shared by creation and
    the edit page. `exclude_concert_id` lets a concert keep its own
    (unchanged) event_id when re-submitting the edit form."""
    event_id = event_id.strip()
    if not EVENT_ID_RE.match(event_id):
        raise HTTPException(
            status_code=422,
            detail="event id may only contain letters, digits, hyphens, and underscores",
        )
    if event_id.lower() in RESERVED_EVENT_IDS:
        raise HTTPException(status_code=422, detail=f"event id {event_id!r} is reserved")
    stmt = select(Concert.id).where(Concert.event_id == event_id)
    if exclude_concert_id is not None:
        stmt = stmt.where(Concert.id != exclude_concert_id)
    if (await session.execute(stmt)).scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail=f"event id {event_id!r} is already in use")
    return event_id


async def generate_event_id(
    session: AsyncSession, title: str, title_en: str | None = None
) -> str:
    """Auto-suggest an event_id for flows with no dedicated input for one
    (the URL-import commit route) -- slugified title, de-duplicated with a
    numeric suffix. Editable afterward via the edit page.

    Slugs from `title_en` when it has one. slugify() strips everything outside
    [a-z0-9], so a Japanese-only title collapses to its "concert" fallback and
    successive imports mint concert-2, concert-3 -- unique, but empty in a URL
    whose whole job (invariant 6: URLs carry the editor-chosen event_id, never
    the PK) is to be the human-readable identity. The trilingual rule made
    title_en mandatory at every create boundary, so it is there to use; `title`
    remains the fallback for the duplicate route, which clones rows predating
    that rule.

    NO backfill of existing rows, deliberately: event_id is editor-owned once
    the concert exists, and rewriting a live one would break every link people
    already hold.

    A RESERVED id counts as taken, so a concert titled "Import" gets
    `import-2`. validate_event_id guards what an editor types; this is the
    app's other producer of ids and nothing validates what it returns, so
    without this the reserved word lands in the DB and the concert's own page
    is unreachable for good -- both owning routes are registered ahead of
    /concerts/{event_id} (invariant 6) -- while every list keeps linking to it.
    No .lower() needed, unlike validate_event_id: slugify lowercases, so
    `candidate` is always already lowercase.
    """
    from app.domain.yaml_export import slugify

    base = slugify((title_en or "").strip() or title)
    candidate = base
    suffix = 2
    while candidate in RESERVED_EVENT_IDS or (await session.execute(
        select(Concert.id).where(Concert.event_id == candidate)
    )).scalar_one_or_none() is not None:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def apply_day_fields(
    day: ConcertDay,
    label: str,
    starts_at: str,
    doors_at: str = "",
    cancelled: str = "false",
    venue_tag_id: int | None = None,
    label_en: str = "",
    label_zh: str = "",
) -> ConcertDay:
    """The JST->UTC parse + assignment shared by build_day (new rows) and
    the edit page's in-place update of existing rows.

    label_en/label_zh are the viewer-locale variants of `label`. An empty one
    means "the editor cleared it" and nulls the column -- both have real
    inputs on every row template.

    venue_tag_id arrives ALREADY resolved -- an int that is known to name a
    VENUE tag, or None -- from `resolve_day_venue_tags` at the route
    boundary. This function stays sync and I/O-free; see that function's
    docstring for why the check lives there rather than here.
    """
    starts = parse_jst(starts_at)
    if starts is None:
        raise HTTPException(status_code=422, detail="a day needs a start time")
    day.label = label.strip()
    day.starts_at_utc = starts
    day.doors_at_utc = parse_jst(doors_at)
    day.cancelled = cancelled == "true"
    day.venue_tag_id = venue_tag_id
    day.label_en = label_en.strip() or None
    day.label_zh = label_zh.strip() or None
    return day


def build_day(
    concert_id: int,
    label: str,
    starts_at: str,
    doors_at: str = "",
    cancelled: str = "false",
    venue_tag_id: int | None = None,
    label_en: str = "",
    label_zh: str = "",
) -> ConcertDay:
    """New-row constructor: the rich creation form, the edit page's new
    rows, and the URL-import commit route."""
    return apply_day_fields(
        ConcertDay(concert_id=concert_id), label, starts_at,
        doors_at, cancelled, venue_tag_id, label_en, label_zh,
    )


def apply_round_fields(
    round_: Round,
    label: str,
    kind: RoundKind,
    opens_at: str,
    closes_at: str,
    results_at: str,
    payment_at: str,
    url: str,
    applies_to: list[int] | None = None,
    label_en: str = "",
    notes: str = "",
    label_zh: str = "",
) -> Round:
    """The parse + assignment shared by build_round (new rows) and the edit
    page's in-place update of existing rows.

    applies_to: which concert_day ids ("legs") this round belongs to;
    empty/None means it's not tied to a specific day (shown under
    "General" on the detail page). Organizational only -- never read by
    the reminder planner."""
    opens, closes = parse_jst(opens_at), parse_jst(closes_at)
    results, payment = parse_jst(results_at), parse_jst(payment_at)
    if opens is None and closes is None and results is None and payment is None:
        raise HTTPException(
            status_code=422, detail="a round needs at least one of opens/closes/results/payment"
        )
    round_.kind = kind
    round_.label = label.strip()
    round_.opens_at_utc = opens
    round_.closes_at_utc = closes
    round_.results_at_utc = results
    round_.payment_deadline_at_utc = payment
    round_.url = form_url(url)
    round_.applies_to = applies_to or None
    round_.label_en = label_en.strip() or None
    round_.label_zh = label_zh.strip() or None
    round_.notes = notes.strip() or None
    return round_


def build_round(
    concert_id: int,
    label: str,
    kind: RoundKind,
    opens_at: str,
    closes_at: str,
    results_at: str,
    payment_at: str,
    url: str,
    applies_to: list[int] | None = None,
    label_en: str = "",
    notes: str = "",
    label_zh: str = "",
) -> Round:
    """New-row constructor: the rich creation form, the edit page's new
    rows, and the URL-import commit route."""
    return apply_round_fields(
        Round(concert_id=concert_id), label, kind, opens_at, closes_at, results_at,
        payment_at, url, applies_to, label_en, notes, label_zh,
    )


def parse_round_legs(
    value: str, valid_day_ids: set[int], key_to_day_id: dict[str, int] | None = None
) -> list[int] | None:
    """One round row's leg selection, as submitted by the edit page's chips:
    a space-separated list of ConcertDay ids and/or `day_key`s.

    ONE form field per round row, not one field per selected id. The round_*
    fields are parallel repeatable lists zipped positionally (see
    edit_concert), so a flat repeated field could not say which row an id
    belonged to -- ids would silently slide between rounds, which is a
    quieter version of the very data loss this encoding exists to fix.

    Ids are filtered against the days that actually survived this submit, so
    a leg the editor deleted in the same save leaves no dangling reference.
    Ids the chips never showed (a cancelled leg -- chips are live legs only)
    ride along in the hidden value untouched and come back through here
    intact: a cancelled ConcertDay is flagged, never deleted (invariant 2),
    so it is still in valid_day_ids.

    A leg the editor ADDED in this same submit has no id yet, so its chip
    carries the row's `day_key` instead -- a client-generated string, unique
    within the page. `key_to_day_id` maps those to the ids the flush just
    handed out. A saved leg's key is simply its own id, so the numeric path
    below is the whole story for every row that already existed; only a
    brand-new row depends on the key map at all.

    An unrecognised token -- a key for a row the editor removed again, an id
    for a deleted leg -- is dropped rather than guessed at. Resolving it to
    "some nearby leg" would be the silent mis-assignment this whole encoding
    exists to make impossible.

    Empty -> None, not [], matching apply_round_fields and what
    concert_round_rows reads to put a round in the all-legs group.
    """
    key_to_day_id = key_to_day_id or {}
    ids: list[int] = []
    for token in value.replace(",", " ").split():
        if token.isdigit() and int(token) in valid_day_ids:
            day_id = int(token)
        elif token in key_to_day_id:
            day_id = key_to_day_id[token]
        else:
            continue
        if day_id not in ids:
            ids.append(day_id)
    return ids or None


def parse_round_qualifiers(
    value: str, valid_round_ids: set[int], self_id: int
) -> list[int]:
    """One UPGRADE round's qualifier selection, as submitted by the editor's
    "Qualifies" chips: a space-separated list of Round ids naming the rounds
    whose ticket-holders may enter this upgrade round.

    Same encoding as parse_round_legs -- ONE form field per round row
    (`round_qualifiers`), not one field per id, so the parallel round_* lists
    stay positionally aligned when zipped in edit_concert. A flat repeated
    field could not say which row an id belonged to.

    Ids are filtered against the rounds that actually survived this submit
    (valid_round_ids), so a round the editor deleted in the same save leaves no
    dangling qualifier -- the DB's ON DELETE CASCADE would drop it too, but the
    filter means the stored set is correct even before the flush. `self_id`
    (the upgrade round's own id) is dropped: a round cannot qualify itself.
    Non-integers and unknown ids are dropped rather than guessed at.

    Chips only ever offer already-saved rounds, so unlike parse_round_legs
    there is no provisional-key path: a round created in the same submit has no
    id and cannot be a qualifier until the concert is saved once.

    Returns [] (not None) when empty. Zero qualifier rows is the
    empty-means-all convention: any secured ticket on this concert qualifies
    (see domain/upgrades.py).
    """
    ids: list[int] = []
    for token in value.replace(",", " ").split():
        if not token.isdigit():
            continue
        rid = int(token)
        if rid == self_id or rid not in valid_round_ids or rid in ids:
            continue
        ids.append(rid)
    return ids


# Separators an editor might put between a group name and the rest of a
# title. Trimmed after the group is removed so "Aqours 9th LoveLive!" and
# "Aqours - 9th LoveLive!" both leave "9th LoveLive!" rather than a stray dash.
_TITLE_SEPARATORS = " \t·・:：-–—~〜|/"


def title_without_lineage(title: str, group_names: list[str]) -> str:
    """The concert title with a leading group name removed, because the
    concert page's lineage line already carries it -- "Aqours · Aqours 9th
    LoveLive!" reads as a stutter, and the half a reader actually needs is
    the subtitle.

    Only a LEADING match is stripped, and only when something is left over: a
    concert whose whole title IS the group name has nothing else to show, and
    a group named mid-title is usually part of a real phrase rather than a
    prefix ("Aqours" in "...featuring Aqours").
    """
    for name in group_names:
        if not name:
            continue
        if len(title) > len(name) and title[: len(name)].casefold() == name.casefold():
            rest = title[len(name):].lstrip(_TITLE_SEPARATORS)
            if rest:
                return rest
    return title


# ── Reminder-rule fragment (htmx swap target) ────────────────────────────


async def user_tz(session: AsyncSession, user_id: int) -> str:
    db_user = await session.get(User, user_id)
    return db_user.timezone if db_user else "America/Moncton"


async def user_rules(session: AsyncSession, user_id: int, concert_id: int) -> list[ReminderRule]:
    res = await session.execute(
        select(ReminderRule).where(
            ReminderRule.user_id == user_id, ReminderRule.concert_id == concert_id
        )
    )
    return list(res.scalars())


async def render_rules_fragment(
    request: Request, concert: Concert, user: SessionUser, session: AsyncSession
) -> HTMLResponse:
    """Renders _rules.html after a user-level reminder-rule mutation -- the
    only htmx fragment swap left on the (now read-only) concert detail
    page; editing the concert itself always redirects (PRG), it never
    swaps a fragment."""
    from app.web.routes.preferences import my_presets

    rules = await user_rules(session, user.id, concert.id)
    presets = await my_presets(session, user.id)
    # Names the preset "My reminders" falls back to when this concert has no
    # per-concert rules of its own -- the demo's "From your default preset —
    # <name>" note. Presentation only; does not change which rules apply.
    default_preset = await get_default_preset(session, user.id)
    return templates.TemplateResponse(
        request,
        "_rules.html",
        {"concert": concert, "user": user, "rules": rules, "presets": presets,
         "anchors": list(Anchor), "default_preset": default_preset},
    )


# ── Concerts ─────────────────────────────────────────────────────────────


async def resolve_tags(session: AsyncSession, tag_ids: Sequence[int], kind) -> list[Tag]:
    """Shared by create_concert_row and the edit route: every submitted tag
    id must exist and match the expected kind."""
    out = []
    for tag_id in tag_ids:
        if not tag_id:
            continue
        tag = await session.get(Tag, tag_id)
        if tag is None or tag.kind is not kind:
            raise HTTPException(status_code=422, detail=f"invalid {kind.value} tag")
        out.append(tag)
    return out


async def all_venue_tags(session: AsyncSession) -> list[Tag]:
    """Every VENUE tag, name-ordered -- the option list behind each leg row's
    venue picker on both editor pages."""
    return list(
        (
            await session.execute(
                select(Tag).where(Tag.kind == TagKind.VENUE).order_by(Tag.name)
            )
        ).scalars()
    )


async def resolve_day_venue_tags(
    session: AsyncSession, values: list[str]
) -> list[int | None]:
    """Validate a save's per-leg venue tag ids, positionally: "" -> None,
    a digit naming a VENUE tag -> that int, anything else -> 422.

    day_venue_tag_id is the one editor-supplied tag id that does not pass
    through create_concert_row/edit_concert's `resolve_tags` calls, and it
    must not skip the same "exists and is of the expected kind" check.
    Unguarded, an id naming a non-VENUE tag lands in
    sync_concert_venue_tags' `desired` set but never in its VENUE-filtered
    `current` set, so every save re-adds it -- and the second trips
    ConcertTag's composite PK with a 500, leaving the concert permanently
    unsavable.

    WHY HERE: `apply_day_fields`/`build_day` are sync, I/O-free parse-and-
    assign helpers shared by four call sites across two modules; threading
    an AsyncSession through all of them (and making them async) for one
    field's sake would turn the editor's cheapest code into DB code. The
    route boundary is also where every other tag id in the app is already
    checked, so this keeps one rule in one place. The list comes back
    POSITIONALLY -- same length, same order -- so it drops straight into the
    strict zip in place of the raw array, leaving the padding rule
    (whole-array omission only, never end-padding) untouched.
    """
    out: list[int | None] = []
    resolved: dict[int, int] = {}  # one lookup per distinct id, not per row
    for raw in values:
        raw = raw.strip()
        if not raw:
            out.append(None)
            continue
        # `<= 0` is checked alongside the non-digit branch, not folded into it:
        # "0".isdigit() is True, but resolve_tags skips falsy ids, so "0" came
        # back as an empty list and the single-value unpack below raised
        # ValueError -- a 500 on an ordinary bad form value. A negative id is
        # already caught by isdigit(); the comparison states the real rule.
        if not raw.isdigit() or int(raw) <= 0:
            raise HTTPException(status_code=422, detail="invalid venue tag")
        tag_id = int(raw)
        if tag_id not in resolved:
            (tag,) = await resolve_tags(session, [tag_id], TagKind.VENUE)
            resolved[tag_id] = tag.id
        out.append(resolved[tag_id])
    return out


async def create_concert_row(
    session: AsyncSession,
    user: SessionUser,
    title: str,
    event_id: str,
    franchise_tags: list[int],
    group_tags: list[int],
    artist_tags: list[int],
    venue_tags: list[int],
    character_tags: Sequence[int] = (),
    kind: ConcertKind | None = None,
    source_url: str | None = None,
) -> tuple[Concert, list[Tag]]:
    """Tag-driven creation supporting collab events: MULTIPLE franchises,
    MULTIPLE groups, explicit artist list (auto-populated client-side from
    the selected groups, editor-pruned). Groups attach WITHOUT expansion —
    the submitted artist list is authoritative. Shared by create_concert
    and the URL-import commit route.

    Returns the concert AND the newly attached tags, which the CALLER must
    feed to handle_newly_tagged once its legs are flushed -- this function
    deliberately does not run that pipeline itself. It used to, right here,
    and that was a bug: handle_newly_tagged asks all_legs_cancelled, and at
    this point in the transaction no ConcertDay exists yet, so the predicate
    reads a dateless draft (correctly -- it must, or every create would
    silence itself) and notifies. A concert created with its only leg
    submitted cancelled therefore DM'd every franchise, group and artist
    follower a 🆕 "Apply here" for a show that is off, while every VENUE
    follower on the same request was correctly skipped by the rollup a
    hundred lines later. The DM has no un-send, and the correct notice, if
    the leg is later un-cancelled, never arrives. Same reasoning and same
    call ordering as edit_concert -- see the comment at its own
    handle_newly_tagged call."""
    await ensure_user(session, user.id, user.username)
    event_id = await validate_event_id(session, event_id)

    f_tags = await resolve_tags(session, franchise_tags, TagKind.FRANCHISE)
    g_tags = await resolve_tags(session, group_tags, TagKind.GROUP)
    a_tags = await resolve_tags(session, artist_tags, TagKind.ARTIST)
    # CHARACTER is a first-class picked kind, not a derived one: an im@s show is
    # credited to 如月千早, and attach_tag chains from her to 今井麻美. The reverse
    # never happens (a seiyuu implies no character), so this list has to come
    # from the editor.
    c_tags = await resolve_tags(session, character_tags, TagKind.CHARACTER)
    # VENUE ids are validated and then DROPPED. sync_concert_venue_tags, which
    # the caller runs once the legs exist, is the sole writer of this
    # concert's VENUE rows (the leg is where a venue is entered, so the
    # concert level is derived). Attaching one here would fire
    # handle_newly_tagged -- DM notices and preset auto-apply to that venue's
    # subscribers -- for a tag the rollup may delete before the transaction
    # ends. Still resolved, so a bad or wrong-kind id 422s exactly as before.
    await resolve_tags(session, venue_tags, TagKind.VENUE)

    concert = Concert(
        title=title.strip(),
        event_id=event_id,
        kind=kind,
        franchise=", ".join(t.name for t in f_tags) or None,  # denormalized display
        # Optional so create_concert, which assigns its own richer field set
        # (including source_url) right after this returns, is unaffected.
        # Callers pass an already-form_url-validated value.
        source_url=source_url,
        created_by=user.id,
    )
    session.add(concert)
    await session.flush()

    newly: list[Tag] = []
    for tag in [*f_tags, *g_tags]:
        newly += await attach_tag(session, concert.id, tag, expand=False)
    # A character pulls her seiyuu in behind her (attach_tag's chained step),
    # so an ARTIST id the editor also ticked explicitly is already attached
    # when the loop below reaches it -- `_is_attached` skips it and she lands
    # in `newly` exactly once, which is what keeps handle_newly_tagged from
    # DM-ing her followers twice for one create.
    for character in c_tags:
        newly += await attach_tag(session, concert.id, character)
    for artist in a_tags:
        newly += await attach_tag(session, concert.id, artist)
    return concert, newly


@router.get("/concerts/new", response_class=HTMLResponse)
async def new_concert_form(
    request: Request,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
):
    """The rich, all-in-one creation page, on the editor's card/chip/fold
    language: identity spine open (title, event id, the first performance),
    everything optional folded, and rounds bound to legs by the SAME chips as
    edit_concert -- one atomic submit to POST /concerts below."""
    picker = await tag_picker_context(session)
    return templates.TemplateResponse(
        request,
        "concert_new.html",
        {
            "user": user, "kinds": list(RoundKind), "concert_kinds": list(ConcertKind),
            "by_kind": picker["by_kind"],
            # Raw dicts, never json.dumps -- the template applies `| tojson`.
            "groups": picker["groups"],
            "tag_names": picker["tag_names"],
            # Handles for the tags whose (name, kind) collides -- the picker
            # shows one beneath the chip so two identical chips are
            # distinguishable. Empty for almost every tag.
            "tag_disambiguators": picker["tag_disambiguators"],
            "initial_selected": {},
            # Every VENUE tag, for each leg row's venue picker. A leg's venue
            # is a real tag now (ConcertDay.venue_tag_id), not typed text.
            "venue_tags": await all_venue_tags(session),
            # Trilingual round labels already typed on earlier concerts, for
            # the picker each round row's "Remembered" chip opens.
            "round_phrases": await round_label_phrases(session),
            # No leg or round exists yet on a create page, but the chip
            # partials the <template>s include still read these -- empty here,
            # so the client-side script builds every chip from the DOM rows.
            "legs": [],
            "saved_rounds": [],
        },
    )


@router.post("/concerts")
async def create_concert(
    request: Request,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
    event_id: str = Form(..., max_length=100),
    title: str = Form(..., min_length=1, max_length=200),
    title_en: str = Form(""),
    title_zh: str = Form(""),
    kind: str = Form(""),
    organizer: str = Form(""),
    categories: str = Form(""),
    eventernote_url: str = Form(""),
    official_url: str = Form(""),
    source_url: str = Form(""),
    performers_text: str = Form(""),
    notes: str = Form(""),
    notes_en: str = Form(""),
    notes_zh: str = Form(""),
    franchise_tags: list[int] = Form(default=[]),
    group_tags: list[int] = Form(default=[]),
    artist_tags: list[int] = Form(default=[]),
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
    round_qualifiers: list[str] = Form(default=[]),
):
    """The rich all-in-one submit: concert + tags, then every performance
    and round row, created together in one transaction -- same
    compose-and-loop shape imports.py's import_commit uses, just with the
    fuller add.html-matched field set.

    Rounds bind to legs by chip, exactly as edit_concert does: every leg is
    brand-new here (no id until the flush), so its row carries a client-side
    `day_key` that the round's `round_legs` value references, resolved to real
    ids through key_to_day_id after the days flush. The old single-value
    `round_leg` <select> could only ever store one leg of a multi-leg round.

    Translation variants are enforced HERE and never on the edit route: a new
    record has no legacy to be trapped by, while most existing rows predate
    the rule and their editor must stay able to save them."""
    # Checked before anything is written, so a rejected submit persists
    # nothing. Rows the loops below skip as blank are checked inside those
    # loops instead -- a trailing empty row is not a missing translation.
    require_variants("Title", title, title_en, title_zh, mandatory=True)
    require_variants("Notes", notes, notes_en, notes_zh)
    concert, newly = await create_concert_row(
        session, user, title, event_id, franchise_tags, group_tags, artist_tags, venue_tags,
        character_tags=character_tags,
        kind=ConcertKind(kind) if kind else None,
    )
    concert.title_en = title_en.strip() or None
    concert.title_zh = title_zh.strip() or None
    concert.organizer = organizer.strip() or None
    concert.categories = categories.strip() or None
    concert.eventernote_url = form_url(eventernote_url)
    concert.official_url = form_url(official_url)
    concert.source_url = form_url(source_url)
    concert.performers_text = performers_text.strip() or None
    concert.notes = notes.strip() or None
    concert.notes_en = notes_en.strip() or None
    concert.notes_zh = notes_zh.strip() or None

    # day_cancelled is newer than the other day_* fields; a submitter that
    # omits it entirely (rather than one row per day) means "not cancelled"
    # for every row, matching apply_day_fields'/build_day's own default --
    # pad rather than let a whole-array omission trip the strict zip below.
    day_cancelled = day_cancelled + ["false"] * (len(day_label) - len(day_cancelled))
    # day_key is padded only when omitted ENTIRELY (an older submitter that
    # predates the chips). A partially-supplied array is left alone so the
    # strict zip below raises instead of end-padding it into misalignment --
    # a key that slid one row would assign a round to the wrong leg, silently,
    # which is worse than a 500. Same rule edit_concert applies.
    if not day_key:
        day_key = [""] * len(day_label)
    n_days = len(day_label)
    # day_venue_tag_id is NOT free text -- it is the leg's structured venue, so
    # a value that slid one row would hand a leg another leg's venue, silently.
    # It therefore follows day_key's rule, not the free-text one: padded only
    # when omitted ENTIRELY (any submitter predating the field), while a
    # partial array is left alone so the strict zip raises instead.
    if not day_venue_tag_id:
        day_venue_tag_id = [""] * n_days
    # day_label_en/day_label_zh get NO padding at all -- the same rule day_label
    # itself follows, and the same one round_label_en already follows below.
    # Every leg-row template in this app emits all three inputs together, so the
    # arrays arrive 1:1 by construction; leaving them unpadded means a template
    # that ever stops emitting one trips the strict zip loudly instead of
    # sliding one leg's English name onto the next leg.
    # Resolved (and kind-checked) once the padding above has settled the
    # array's length, so the strict zip below still sees one entry per row.
    day_venue_tags = await resolve_day_venue_tags(session, day_venue_tag_id)
    days: list[ConcertDay] = []
    # key -> the ConcertDay its row produced, built INSIDE the loop from the
    # same tuple so a key can never be paired with another row's day; the ids
    # are filled in after the flush below.
    key_rows: list[tuple[str, ConcertDay]] = []
    for row_no, (
        key, label, label_en, label_zh, starts_at,
        doors_at, cancelled, v_tag
    ) in enumerate(zip(
        day_key, day_label, day_label_en, day_label_zh, day_starts_at,
        day_doors_at, day_cancelled, day_venue_tags, strict=True,
    ), start=1):
        # v_tag is in the guard so a row where the editor picked ONLY a venue
        # (no label, no start time yet) is not read as blank and dropped.
        if not any([label.strip(), starts_at.strip(), v_tag]):
            continue  # blank trailing row from the repeatable UI -- key and all
        # Numbered from the submitted row order (blank rows included), so an
        # editor with six legs is told WHICH one to fix rather than that
        # something, somewhere, is half-translated.
        require_variants(f"Leg {row_no} label", label, label_en, label_zh)
        day = build_day(
            concert.id, label, starts_at, doors_at, cancelled,
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

    # round_legs / round_qualifiers are the newer chip fields; a submitter
    # that omits either ENTIRELY means "nothing to say" -- every round gets no
    # legs / no qualifiers, matching build_round's own defaults. A partial
    # array is left alone so the strict zip raises rather than sliding a row.
    if not round_legs:
        round_legs = [""] * len(round_label)
    if not round_qualifiers:
        round_qualifiers = [""] * len(round_label)
    # (round object, its submitted kind, its raw qualifier value) for every
    # created round, reconciled into round_qualifiers rows AFTER the flush
    # below hands the new rounds their ids -- the same post-flush ordering
    # edit_concert uses so a same-submit qualifier reference resolves.
    qual_jobs: list[tuple[Round, RoundKind, str]] = []
    for row_no, (
        label, label_en, label_zh, kind_, opens_at, closes_at, results_at, payment_at, url,
        notes_, legs, quals
    ) in enumerate(zip(
        round_label, round_label_en, round_label_zh, round_kind, round_opens_at,
        round_closes_at, round_results_at, round_payment_at, round_url, round_notes,
        round_legs, round_qualifiers, strict=True,
    ), start=1):
        if not any([label.strip(), opens_at.strip(), closes_at.strip(),
                    results_at.strip(), payment_at.strip()]):
            continue
        require_variants(f"Round {row_no} label", label, label_en, label_zh)
        round_ = build_round(
            concert.id, label, kind_, opens_at, closes_at, results_at, payment_at, url,
            applies_to=parse_round_legs(legs, valid_day_ids, key_to_day_id),
            label_en=label_en, notes=notes_, label_zh=label_zh,
        )
        session.add(round_)
        await record_round_label_phrase(session, label, label_en, label_zh)
        qual_jobs.append((round_, kind_, quals))

    await session.flush()  # new rounds now have ids, needed by parse_round_qualifiers
    # valid_round_ids is every round created this submit, so an upgrade round
    # can qualify off another one made in the same save (its id exists after
    # the flush) while a reference to a non-existent round is dropped.
    valid_round_ids = {r.id for r, _, _ in qual_jobs}
    for round_, kind_, quals in qual_jobs:
        if kind_ is RoundKind.UPGRADE:
            for q_id in parse_round_qualifiers(quals, valid_round_ids, round_.id):
                session.add(
                    RoundQualifier(upgrade_round_id=round_.id, qualifying_round_id=q_id)
                )

    await session.flush()
    # The concert-level tag attach's notify-and-apply pipeline, run HERE and
    # not inside create_concert_row where the attach happens: it asks
    # all_legs_cancelled, and at the attach site this submit's legs do not
    # exist yet, so it would answer about a legless draft and announce a
    # concert created with its only leg already cancelled. Same placement and
    # same reasoning as edit_concert. Before sync_concert, which every rule a
    # preset creates here still needs.
    await handle_newly_tagged(session, concert, newly)
    # The legs are the only place a venue is entered, so the concert's VENUE
    # tags are rolled up from them here, once every leg exists. A venue that
    # newly lands on the concert goes through the same notify-and-apply
    # pipeline the concert-level tags above did -- VENUE tags are
    # subscribable, so its followers are owed the notice (invariant 4).
    newly_venues = await sync_concert_venue_tags(session, concert.id)
    await handle_newly_tagged(session, concert, newly_venues)
    await sync_concert(session, concert.id)
    await session.commit()
    return RedirectResponse(f"/concerts/{concert.event_id}", status_code=303)


async def concert_rounds_context(
    session: AsyncSession, user_id: int, concert: Concert, now: datetime | None = None
) -> dict:
    """Everything `_round_rows.html` needs, as one dict.

    Two callers render that fragment and they MUST agree: GET
    /concerts/{event_id} builds it inside the page, and POST
    /rounds/{id}/outcome swaps it back in after a capture action. Assembling
    it in two places is how the swapped-in copy quietly starts disagreeing
    with the one it replaced -- a leg missing its venue, or a round in the
    wrong group.

    Everything the fragment touches is loaded HERE, eagerly. A lazy load
    during async template rendering raises MissingGreenlet, which is a 500,
    and this project has already shipped that bug once (Concert.tags).
    """
    now = now or datetime.now(UTC)
    leg_groups, all_legs_rows = await concert_round_rows(session, user_id, concert, now=now)
    # The leg's venue is a real FK now, so this is a targeted load of exactly
    # the tags in play -- not every VENUE tag in the DB matched by name. That
    # name match was the bug: an editor who re-pointed a leg at a different
    # venue tag saved the new FK while this page kept rendering the OLD
    # free-text name, with no UI path to correct it.
    # Resolved in its own query rather than off ConcertDay.venue_tag because
    # that relationship is lazy="raise": touching it lazily during async
    # rendering would be a MissingGreenlet 500.
    venue_tag_ids = {g.day.venue_tag_id for g in leg_groups if g.day.venue_tag_id}
    venue_tags_by_id: dict[int, Tag] = {}
    if venue_tag_ids:
        venue_tags_by_id = {
            t.id: t for t in (await session.execute(
                select(Tag).where(Tag.id.in_(venue_tag_ids))
            )).scalars()
        }
    # This user's per-leg opt-outs over THIS concert's days, so `_round_rows`
    # can dim the legs they are not going to. Assembled here, the single place
    # that fragment's context is built, so the copy POST
    # /concerts/{event_id}/legs/{day_id}/opt-out swaps back in cannot drift
    # from the one GET /concerts/{event_id} rendered (same reasoning as the
    # outcome re-render).
    day_ids = [g.day.id for g in leg_groups]
    opted_out_day_ids = set((await session.execute(
        select(LegOptOut.concert_day_id).where(
            LegOptOut.user_id == user_id, LegOptOut.concert_day_id.in_(day_ids)
        )
    )).scalars()) if day_ids else set()
    return {
        "concert": concert,
        "now": now,
        # The show is off entirely -- what the page's cancelled banner renders
        # on. Asked of the legs already loaded above, through the one predicate
        # (`all_legs_cancelled`), never re-stated as its own rule here. It is a
        # PAGE-level fact rather than a row-level one on purpose: a concert
        # whose legs are all cancelled and which has no rounds at all still has
        # to say so, and there would be no row to carry it.
        "concert_cancelled": all_legs_cancelled([g.day for g in leg_groups]),
        "leg_groups": leg_groups,
        "all_legs_rows": all_legs_rows,
        "day_venue_tags": {
            g.day.id: venue_tags_by_id.get(g.day.venue_tag_id) for g in leg_groups
        },
        "leg_opted_out": opted_out_day_ids,
        "next_row": concert_next_moment(
            [row for g in leg_groups for row in g.rounds] + all_legs_rows, now=now
        ),
    }


def pending_capture_row(ctx: dict) -> RoundRow | None:
    """The one round the concert page's catch-up dialog asks about, or None.

    Read entirely off the rows `concert_rounds_context` already built -- no
    second query, and no second definition of "still owes an answer": a round
    qualifies when it has unresolved legs to ask about (`capture_days`), or
    when it is the single-leg case where the web's only exit from APPLIED is
    open (`can_report_result`). `covered` rounds are excluded because their
    answer is already known elsewhere; the gate resolvers shut both flags for
    them anyway, so the check is belt and braces.

    De-duplicated by round id, because a round covering several legs renders
    once per leg and would otherwise compete with itself -- exactly what
    `concert_next_moment` does, and for the same reason. Any of those copies
    will do here: `capture_days` is a fact about the ROUND (only `leg_result`
    differs between copies), and the dialog asks the whole-round question.

    ONE round, not all of them: two modal dialogs stacked over each other is
    not a design, and the soonest result moment is the one the reader is most
    overdue on. Answering it re-renders the page without it, so the next one
    surfaces on the following visit.
    """
    seen: set[int] = set()
    candidates: list[RoundRow] = []
    for row in [r for g in ctx["leg_groups"] for r in g.rounds] + ctx["all_legs_rows"]:
        # `upgrade_locked` never reaches the gates as a capture row anyway (an
        # ineligible viewer cannot have applied), but a modal asking how a
        # round went that this reader could not enter is the one failure worth
        # spending a clause to make impossible.
        if row.round_.id in seen or row.covered or row.upgrade_locked:
            continue
        seen.add(row.round_.id)
        if row.capture_days or row.can_report_result:
            candidates.append(row)
    if not candidates:
        return None
    # Soonest knowable result first; a round announcing neither results nor a
    # close sorts last rather than jumping the queue on a missing value.
    return min(
        candidates,
        key=lambda r: (
            r.round_.results_at_utc or r.round_.closes_at_utc or datetime.max.replace(tzinfo=UTC)
        ),
    )


async def following_toggle_context(
    session: AsyncSession, user_id: int, concert: Concert
) -> dict:
    """Everything `_following_toggle.html` needs, built in ONE place so GET
    /concerts/{event_id} and the POST /concerts/{event_id}/subscription
    re-render agree on which button to show.

    `following` drives whether the button offers to follow or unfollow;
    `sub_state` distinguishes an OPTED_OUT prune (unfollow-clears-it) from a
    no-row default (follow-subscribes). `won_payment_at_utc` is set when the
    user holds a WON/PAID outcome anywhere on this concert -- the concert-page
    opt-out button uses it to raise the heavy confirmation naming the ticket
    it would forfeit (a client-side gate; the write still goes through).
    `via_tags` names WHY the reader tracks this concert (C3): the viewer's
    followed tags attached to it -- the derivation `tracked_concert_ids`
    performs, surfaced. Empty for a manually subscribed concert.
    `concert_cancelled` is what the caption says instead of promising a
    reminder for every round below: a dead concert plans none of them, so the
    toggle states the fact. It is resolved HERE, not read off the page's
    context, precisely because the POST re-render swaps this partial in ALONE
    -- the drift this single context builder exists to prevent."""
    tracked = await tracked_concert_ids(session, user_id)
    states = await concert_subscription_states(session, user_id)
    followed_tag_ids = set((await session.execute(
        select(TagSubscription.tag_id).where(TagSubscription.user_id == user_id)
    )).scalars())
    via_tags: list[Tag] = []
    if followed_tag_ids:
        via_tags = list((await session.execute(
            select(Tag)
            .join(ConcertTag, ConcertTag.tag_id == Tag.id)
            .where(ConcertTag.concert_id == concert.id, Tag.id.in_(followed_tag_ids))
            .order_by(Tag.kind, Tag.name)
        )).scalars())
    # Its own query rather than `concert.days`: the POST re-render's concert
    # comes straight from get_concert_by_event_id with nothing refreshed, and
    # a lazy load mid-render would be a MissingGreenlet 500. Answered through
    # the one predicate the page's banner uses, never re-stated as a rule here.
    days = list((await session.execute(
        select(ConcertDay).where(ConcertDay.concert_id == concert.id)
    )).scalars())
    round_ids = list((await session.execute(
        select(Round.id).where(Round.concert_id == concert.id)
    )).scalars())
    won_payment_at_utc = None
    holds_ticket = False
    if round_ids:
        rows = list(await session.execute(
            select(RoundOutcome.round_id, RoundOutcome.outcome).where(
                RoundOutcome.user_id == user_id, RoundOutcome.round_id.in_(round_ids)
            )
        ))
        won_round_ids = {
            rid for rid, oc in rows if oc in (LotteryOutcome.WON, LotteryOutcome.PAID)
        }
        holds_ticket = bool(won_round_ids)
        if won_round_ids:
            payments = list((await session.execute(
                select(Round.payment_deadline_at_utc).where(
                    Round.id.in_(won_round_ids),
                    Round.payment_deadline_at_utc.is_not(None),
                )
            )).scalars())
            won_payment_at_utc = min(payments) if payments else None
    return {
        "concert": concert,
        "following": concert.id in tracked,
        "sub_state": states.get(concert.id),
        "holds_ticket": holds_ticket,
        "won_payment_at_utc": won_payment_at_utc,
        "via_tags": via_tags,
        "concert_cancelled": all_legs_cancelled(days),
    }


@router.get("/concerts/{event_id}", response_class=HTMLResponse)
async def concert_detail(
    request: Request,
    event_id: str,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    concert = await get_concert_by_event_id(session, event_id)
    await session.refresh(concert, ["days", "rounds", "tags"])
    rules = await user_rules(session, user.id, concert.id)
    tz = await user_tz(session, user.id)
    from app.web.routes.preferences import my_presets

    presets = await my_presets(session, user.id)
    # Same "From your default preset — <name>" note render_rules_fragment
    # builds after a rules mutation -- computed here too so the first GET
    # agrees with every later htmx swap.
    default_preset = await get_default_preset(session, user.id)
    # The lineage line above the title carries the group, so the title itself
    # does not repeat it (see title_without_lineage).
    display_title = title_without_lineage(
        loc_field(concert, "title", get_locale()),
        [loc_field(t, "name", get_locale()) for t in concert.tags if t.kind is TagKind.GROUP],
    )
    # Editor-only, and only fetched for editors -- viewers have no use for
    # who-changed-what, and it's one extra query worth skipping for them.
    audit_log = await concert_audit_log(session, concert.id) if user.is_editor else []
    # The Performing panel's per-group blocks. AWAITED HERE, never in the
    # template: group membership has to be read from the `tag_members`
    # association table, and a template doing that walk would have to go
    # through `Tag.members` -- a lazy self-referential m2m whose load during
    # async rendering is a MissingGreenlet 500. The service reads the
    # association table directly instead, so the query lands here, awaited,
    # rather than mid-render. The refresh above is what makes `concert.tags`
    # safe for it to walk.
    clusters = await performer_clusters(session, concert)
    rounds_ctx = await concert_rounds_context(session, user.id, concert)
    return templates.TemplateResponse(
        request,
        "concert_detail.html",
        {"concert": concert, "user": user, "rules": rules, "tz": tz, "presets": presets,
         "anchors": list(Anchor),
         "default_preset": default_preset,
         "display_title": display_title,
         "audit_log": audit_log,
         "performer_clusters": clusters,
         # The catch-up dialog's subject, or None for a page with nothing
         # outstanding. GET-only on purpose: the htmx re-render after a press
         # answers with the rounds region alone, so the dialog does not
         # re-open over the answer the reader just gave.
         "pending_capture": pending_capture_row(rounds_ctx),
         # Both of these carry `concert_cancelled` -- the same `all_legs_cancelled`
         # answer over the same legs, one for the banner and one for the toggle
         # partial that re-renders on its own. rounds_ctx wins the merge; there is
         # nothing to choose between them.
         **await following_toggle_context(session, user.id, concert),
         **rounds_ctx},
    )


@router.get("/concerts/{event_id}/edit", response_class=HTMLResponse)
async def edit_concert_form(
    request: Request,
    event_id: str,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
):
    """A near-clone of /concerts/new, pre-filled with this concert's
    current scalars, tags, days, and rounds -- the only place editors edit
    a concert's own data (the detail page is read-only)."""
    concert = await get_concert_by_event_id(session, event_id)
    await session.refresh(concert, ["days", "rounds", "tags"])
    picker = await tag_picker_context(session)

    # artist_excluded: members of an already-attached group that AREN'T
    # currently on the concert (previously pruned). Without this, the
    # picker's group->members auto-population would show them as selected
    # again just because their group is initially checked, and an editor
    # who doesn't notice would silently re-add a pruned non-performer.
    attached_artist_ids = {t.id for t in concert.tags if t.kind is TagKind.ARTIST}
    excluded_ids: list[int] = []
    for t in concert.tags:
        if t.kind is TagKind.GROUP:
            for m in await group_members(session, t.id):
                if m.id not in attached_artist_ids:
                    excluded_ids.append(m.id)
    # Owner ruling (2026-08-01): where a seiyuu represents a CHARACTER, only the
    # character is added and the artist is auto-correlated -- shown as `cv. xxx`,
    # never offered as a tick. An artist added by herself correlates with no
    # character, so "derived" needs no provenance column: she is derived exactly
    # when some ATTACHED character names her, which is the same derivation the
    # display rule and the prune rule already run.
    #
    # Pre-ticking her is what made the prune rule unreachable from this editor:
    # ticked means submitted means always in `after_ids` means never detached,
    # whatever happened to her character. Both halves of that are fixed together
    # -- see the desired-set expansion in edit_concert, without which dropping
    # her from this list would detach her on every save instead.
    derived_seiyuu_ids = {
        t.voiced_by_tag_id for t in concert.tags
        if t.kind is TagKind.CHARACTER and t.voiced_by_tag_id is not None
    }
    # `attached_artist_ids` above is deliberately NOT narrowed: artist_excluded
    # answers "which group members did the editor prune", a different question,
    # and a derived seiyuu is attached, not pruned.
    initial_selected = {
        "franchise": [str(t.id) for t in concert.tags if t.kind is TagKind.FRANCHISE],
        "group": [str(t.id) for t in concert.tags if t.kind is TagKind.GROUP],
        # Without this bucket the picker renders an empty character row over a
        # concert that HAS characters, the editor saves without noticing, and
        # the POST twin -- which now diffs characters like every other kind --
        # detaches them and cascades their seiyuu away. The round trip is not
        # cosmetic here; it is half the fix.
        "character": [str(t.id) for t in concert.tags if t.kind is TagKind.CHARACTER],
        "artist": [str(i) for i in attached_artist_ids - derived_seiyuu_ids],
        "artist_excluded": [str(i) for i in excluded_ids],
        "venue": [str(t.id) for t in concert.tags if t.kind is TagKind.VENUE],
    }
    # Each round row renders one toggle chip per LIVE leg, pre-selected from
    # the round's real applies_to -- no text matching in either direction.
    # Cancelled legs get no chip (there is nothing useful to newly assign a
    # round to), but an id already pointing at one still round-trips: the
    # hidden field below carries the round's WHOLE applies_to, and
    # parse_round_legs keeps every id whose day still exists.
    # Re-read this concert's days WITH their venue tags: each chip falls back
    # to the leg's venue name when the leg has no label, and
    # ConcertDay.venue_tag is lazy="raise", so a lazy touch during async
    # rendering would be a MissingGreenlet 500. Loading every day (not just
    # the live ones) populates the same identity-mapped objects `concert.days`
    # hands the leg-row loop below.
    days = list((await session.execute(
        select(ConcertDay)
        .where(ConcertDay.concert_id == concert.id)
        .options(selectinload(ConcertDay.venue_tag))
    )).scalars())
    legs = sorted(
        (d for d in days if not d.cancelled),
        key=lambda d: (d.starts_at_utc, d.id),
    )
    # Upgrade rounds carry a second chip row -- which OTHER rounds qualify a
    # user to enter (see _round_qualifier_chips.html). Loaded from the
    # association table directly, in ONE query, not the lazy Round.qualifiers
    # relationship (which would trip MissingGreenlet under async rendering).
    qualifiers_by_round = await _qualifiers_by_upgrade_round(
        session, [r.id for r in concert.rounds]
    )
    rounds_with_chips = [
        (
            r,
            " ".join(str(i) for i in (r.applies_to or [])), set(r.applies_to or []),
            " ".join(str(i) for i in qualifiers_by_round.get(r.id, [])),
            set(qualifiers_by_round.get(r.id, [])),
        )
        for r in concert.rounds
    ]
    # The folds each summarise their own contents, so a closed one still says
    # what is inside it. Assembled here rather than in the template because
    # the tag summary is a per-kind ordering the template has no business
    # re-deriving, and audit_log needs a query.
    tag_summary = [
        t.name
        for kind in (TagKind.FRANCHISE, TagKind.GROUP, TagKind.CHARACTER,
                     TagKind.ARTIST, TagKind.VENUE)
        for t in concert.tags
        if t.kind is kind
    ]
    return templates.TemplateResponse(
        request,
        "concert_edit.html",
        {
            "user": user, "concert": concert, "kinds": list(RoundKind),
            "concert_kinds": list(ConcertKind),
            "by_kind": picker["by_kind"],
            # Raw dicts, never json.dumps -- the template applies `| tojson`.
            "groups": picker["groups"],
            "tag_names": picker["tag_names"],
            # Handles for the tags whose (name, kind) collides -- the picker
            # shows one beneath the chip so two identical chips are
            # distinguishable. Empty for almost every tag.
            "tag_disambiguators": picker["tag_disambiguators"],
            "initial_selected": initial_selected,
            # Every VENUE tag, for each leg row's venue picker (the leg's own
            # pick is pre-selected in the template from d.venue_tag_id).
            "venue_tags": await all_venue_tags(session),
            # Trilingual round labels already typed on earlier concerts, for
            # the picker each round row's "Remembered" chip opens.
            "round_phrases": await round_label_phrases(session),
            "legs": legs,
            "rounds_with_chips": rounds_with_chips,
            # Chip options for the "Qualifies" row: every already-saved round
            # (each row excludes itself in the template).
            "saved_rounds": list(concert.rounds),
            "tag_summary": tag_summary,
            # What this record is still missing, in the viewer's language --
            # informational only (this route's POST twin never blocks on it).
            # Numbered off the same collections the leg/round loops below
            # render, so "Leg 2 label" points at the second row on screen.
            "concert_gaps": concert_variant_gaps(concert, concert.days, concert.rounds),
            "audit_log": await concert_audit_log(session, concert.id),
            "tz": await user_tz(session, user.id),
        },
    )


@router.post("/concerts/{event_id}/edit")
async def edit_concert(
    event_id: str,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
    new_event_id: str = Form(..., alias="event_id", max_length=100),
    title: str = Form(..., min_length=1, max_length=200),
    title_en: str = Form(""),
    title_zh: str = Form(""),
    kind: str = Form(""),
    organizer: str = Form(""),
    categories: str = Form(""),
    eventernote_url: str = Form(""),
    official_url: str = Form(""),
    source_url: str = Form(""),
    performers_text: str = Form(""),
    notes: str = Form(""),
    notes_en: str = Form(""),
    notes_zh: str = Form(""),
    franchise_tags: list[int] = Form(default=[]),
    group_tags: list[int] = Form(default=[]),
    artist_tags: list[int] = Form(default=[]),
    character_tags: list[int] = Form(default=[]),
    venue_tags: list[int] = Form(default=[]),
    day_id: list[str] = Form(default=[]),
    day_key: list[str] = Form(default=[]),
    day_label: list[str] = Form(default=[]),
    day_label_en: list[str] = Form(default=[]),
    day_label_zh: list[str] = Form(default=[]),
    day_starts_at: list[str] = Form(default=[]),
    day_venue_tag_id: list[str] = Form(default=[]),
    day_doors_at: list[str] = Form(default=[]),
    day_cancelled: list[str] = Form(default=[]),
    round_id: list[str] = Form(default=[]),
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
    round_qualifiers: list[str] = Form(default=[]),
):
    """Atomic update: scalars assigned directly; tags/days/rounds
    RECONCILED (not blindly recreated) against what's submitted, so rows
    that didn't conceptually change keep their id -- required by the
    queue-sync invariant (a Round/ConcertDay that keeps its id keeps its
    ReminderQueue.sent_at_utc history; delete-and-recreate would re-arm
    already-delivered reminders on an unrelated edit)."""
    concert = await get_concert_by_event_id(session, event_id)
    before = snapshot_concert(concert)
    concert.event_id = await validate_event_id(session, new_event_id, exclude_concert_id=concert.id)
    concert.title = title.strip()
    concert.title_en = title_en.strip() or None
    concert.title_zh = title_zh.strip() or None
    concert.kind = ConcertKind(kind) if kind else None
    concert.organizer = organizer.strip() or None
    concert.categories = categories.strip() or None
    concert.eventernote_url = form_url(eventernote_url)
    concert.official_url = form_url(official_url)
    concert.source_url = form_url(source_url)
    concert.performers_text = performers_text.strip() or None
    concert.notes = notes.strip() or None
    concert.notes_en = notes_en.strip() or None
    concert.notes_zh = notes_zh.strip() or None

    # -- Tags: diff before/after, detach dropped ids, attach new ones only.
    # An unchanged, already-attached group is never re-touched, so its
    # previously-pruned members stay pruned (invariant: group expansion is
    # a one-time, at-attach-time event).
    f_tags = await resolve_tags(session, franchise_tags, TagKind.FRANCHISE)
    g_tags = await resolve_tags(session, group_tags, TagKind.GROUP)
    a_tags = await resolve_tags(session, artist_tags, TagKind.ARTIST)
    # CHARACTER is resolved here for a reason that is not symmetry. It is a
    # non-VENUE kind, so an attached character has always been in `before_ids`;
    # leaving it out of `desired_tags` put every one of them in
    # `before_ids - after_ids` and detached it on a save that never mentioned
    # it -- and detach_tag then cascaded her seiyuu off too, so a routine edit
    # of an im@s concert stripped exactly the performer this feature exists to
    # reach. Omission means removal, as it does for every other kind: the
    # picker emits one hidden input per SELECTED id and none for an empty row,
    # so "no character_tags" is the only way the form can say "no characters".
    c_tags = await resolve_tags(session, character_tags, TagKind.CHARACTER)
    # VENUE is excluded from BOTH sides of the diff below, so the leg-driven
    # rollup (sync_concert_venue_tags, further down) stays the only writer of
    # this concert's VENUE rows. Leaving them in produced two real bugs: a
    # venue added here was attached, notified about, then deleted by the
    # rollup in the same request; and a venue dropped here while a leg still
    # carried it was detached and re-attached, landing in `newly` a second
    # time and DM-ing a second "New event" to any subscriber without a preset
    # (the "already has rules on this concert" guard never fires for them).
    # Still resolved, so a bad or wrong-kind id 422s exactly as before.
    await resolve_tags(session, venue_tags, TagKind.VENUE)
    desired_tags = {t.id: t for t in [*f_tags, *g_tags, *c_tags, *a_tags]}

    await session.refresh(concert, ["tags"])
    before_ids = {t.id for t in concert.tags if t.kind is not TagKind.VENUE}
    after_ids = set(desired_tags)
    # THE DESIRED SET EXPANDS EACH CHARACTER TO HER SEIYUU, mirroring on the
    # detach side what attach_tag's chained step already does on the attach
    # side. Owner ruling (2026-08-01): a derived seiyuu is not an editor choice,
    # so `initial_selected["artist"]` no longer pre-ticks her and this form no
    # longer submits her -- a plain diff would therefore read "she is gone" from
    # every save and detach the performer the whole feature exists to reach.
    # Keep the character, keep the seiyuu; drop the character, drop the seiyuu.
    #
    # KEPT SEPARATE FROM `after_ids` on purpose. This set governs the DETACH
    # diff only; the attach loop below still iterates what the editor actually
    # chose. A concert whose character is attached but whose seiyuu somehow is
    # not (a row predating this, or one an old save unticked her out of) is left
    # exactly as it is by an ordinary save, rather than silently attaching her
    # and DM-ing her followers a 🆕 "New event" for a concert that already
    # existed (invariant 4). Adding the character is what attaches her, and
    # attach_tag chains that itself -- the attach loop never needs her here.
    keep_ids = after_ids | {
        c.voiced_by_tag_id for c in c_tags if c.voiced_by_tag_id is not None
    }
    # `keep_tag_ids` covers the one case the expansion above cannot: an artist
    # ticked EXPLICITLY (she is no longer pre-ticked, so ticking her is a
    # deliberate "credit the performer, not the character") while her character
    # is unticked. She is then in BOTH before_ids and keep_ids -- in NEITHER
    # diff -- so nothing puts her back after the character's detach cascades her
    # off. The first save would silently lose her and a second identical save
    # restore her.
    #
    # Swapping the loops does NOT fix that (measured: the attach loop still
    # never sees her). Iterating `keep_ids` and letting attach_tag's
    # `_is_attached` deduplicate DOES fix it -- and puts the re-attached seiyuu
    # into `newly`, which handle_newly_tagged turns into a 🆕 "New event" DM to
    # every follower she has, for a concert that already existed and has no
    # un-send (invariant 4).
    #
    # Telling the cascade what this save is keeping is the shape that neither
    # loses her nor announces her: nothing is re-attached, so `newly` stays
    # exactly "tags this submit added", and the FIRST save is already correct.
    for tid in before_ids - keep_ids:
        await detach_tag(session, concert.id, tid, keep_tag_ids=keep_ids)
    newly: list[Tag] = []
    for tid in after_ids - before_ids:
        newly += await attach_tag(session, concert.id, desired_tags[tid], expand=False)
    # The ATTACH happens here; the notify-and-apply pipeline for it runs at the
    # foot of this function, once this submit's legs are in the DB -- see the
    # comment there.

    # -- Days: update existing rows in place by id, insert blank-id rows,
    # delete rows that were dropped.
    await session.refresh(concert, ["days"])
    existing_days = {d.id: d for d in concert.days}
    before_cancelled_day_ids = {d.id for d in concert.days if d.cancelled}
    # See create_concert's identical comment: a submitter that omits
    # day_cancelled entirely means "not cancelled" for every row.
    day_cancelled = day_cancelled + ["false"] * (len(day_label) - len(day_cancelled))
    # day_key is padded only when omitted ENTIRELY (an older submitter, or a
    # test that predates it). A partially-supplied array is left alone so the
    # strict zip below raises instead of end-padding it into misalignment --
    # a key that slid one row would assign a round to the wrong leg, silently,
    # which is worse than a 500.
    if not day_key:
        day_key = [""] * len(day_label)
    # day_venue_tag_id -- the leg's structured venue -- is padded only when
    # omitted ENTIRELY, so a partial array trips the strict zip below rather
    # than sliding one leg's venue onto another.
    n_days = len(day_label)
    if not day_venue_tag_id:
        day_venue_tag_id = [""] * n_days
    # day_label_en/day_label_zh are NOT padded, exactly as create_concert leaves
    # them and as round_label_en below already is: every leg-row template emits
    # all three together, so the arrays are 1:1 by construction and the strict
    # zip is the guard that keeps them that way.
    # Resolved (and kind-checked) after the padding, so the strict zip below
    # still sees one entry per row. See resolve_day_venue_tags for why the
    # check is here and not inside apply_day_fields.
    day_venue_tags = await resolve_day_venue_tags(session, day_venue_tag_id)
    kept_day_ids: set[int] = set()
    submitted_days: list[ConcertDay] = []
    # key -> the id that key's row actually got. Built INSIDE the loop below,
    # from the same tuple that produced the ConcertDay, so a key can never be
    # paired with another row's day; the ids are filled in after the flush.
    key_rows: list[tuple[str, ConcertDay]] = []
    for (
        key, did, label, label_en, label_zh, starts_at,
        doors_at, cancelled, v_tag
    ) in zip(
        day_key, day_id, day_label, day_label_en, day_label_zh, day_starts_at,
        day_doors_at, day_cancelled, day_venue_tags,
        strict=True,
    ):
        # v_tag is in the guard so a row where the editor picked ONLY a venue
        # (no label, no start time yet) is not read as blank and dropped.
        if not any([label.strip(), starts_at.strip(), v_tag]):
            continue  # blank trailing row from the repeatable UI -- key and all
        did = did.strip()
        if did.isdigit() and int(did) in existing_days:
            day = apply_day_fields(
                existing_days[int(did)], label, starts_at,
                doors_at, cancelled, v_tag, label_en, label_zh,
            )
            kept_day_ids.add(day.id)
        else:
            day = build_day(
                concert.id, label, starts_at, doors_at, cancelled,
                v_tag, label_en, label_zh,
            )
            session.add(day)
        submitted_days.append(day)
        if key.strip():
            key_rows.append((key.strip(), day))
    for did, day in existing_days.items():
        if did not in kept_day_ids:
            await session.delete(day)
    await session.flush()  # new/kept days have real ids, needed by parse_round_legs below
    newly_cancelled_day_ids = {
        d.id for d in submitted_days if d.cancelled
    } - before_cancelled_day_ids
    # The legs a round may legitimately point at after this submit. A day the
    # editor dropped is gone from here, so its id cannot survive in any
    # applies_to; a cancelled one is still here (invariant 2), so an id
    # pointing at it does.
    valid_day_ids = {d.id for d in submitted_days}
    # Resolved only now, because a row added in this submit had no id until
    # the flush above. A duplicate key would be the one way two rows could
    # collide, so the first row claiming a key keeps it rather than a later
    # one silently stealing the reference.
    key_to_day_id: dict[str, int] = {}
    for key, day in key_rows:
        key_to_day_id.setdefault(key, day.id)

    # -- Rounds: same id-preserving reconciliation.
    await session.refresh(concert, ["rounds"])
    existing_rounds = {r.id: r for r in concert.rounds}
    kept_round_ids: set[int] = set()
    # Each upgrade round's current qualifier set, loaded up front (one query,
    # not the lazy relationship) so a wholly-omitted round_qualifiers array can
    # preserve it -- the same absent-field rule as round_legs below.
    existing_qualifiers = await _qualifiers_by_upgrade_round(session, list(existing_rounds))
    # (round object, its submitted kind, its raw qualifier value) for every
    # surviving round, reconciled into round_qualifiers rows AFTER the flush
    # below hands new rounds their ids.
    qual_jobs: list[tuple[Round, RoundKind, str]] = []
    # round_qualifiers is padded/omitted exactly like round_legs (see below):
    # a whole-array omission preserves each round's existing qualifiers; a
    # partial array is left alone so the strict zip raises rather than sliding
    # every later row's selection by one.
    quals_omitted = not round_qualifiers
    if quals_omitted:
        round_qualifiers = [""] * len(round_label)
    # round_legs is newer than the other round_* fields, so a submitter can
    # legitimately omit it ENTIRELY: a browser still holding the pre-deploy
    # edit page posts the old free-text `round_leg`, which this signature no
    # longer accepts, and no `round_legs` at all. That omission means "this
    # submitter has nothing to say about legs" -- every round keeps the
    # applies_to it already had. Reading it as "no legs selected" instead
    # (which is what a blank-filled pad says) let one innocent Save wipe the
    # leg assignment off every round of the concert.
    #
    # A short-but-NONEMPTY array gets no padding, exactly as day_key above:
    # end-padding one slides every later row's selection back a row and
    # assigns rounds to the wrong legs, silently, which is worse than a 500.
    # The strict zip below raises on it instead.
    legs_omitted = not round_legs
    if legs_omitted:
        round_legs = [""] * len(round_label)
    for (
        rid, label, label_en, label_zh, kind_, opens_at, closes_at, results_at, payment_at,
        url, notes_, legs, quals
    ) in zip(
        round_id, round_label, round_label_en, round_label_zh, round_kind, round_opens_at,
        round_closes_at, round_results_at, round_payment_at, round_url, round_notes,
        round_legs, round_qualifiers, strict=True,
    ):
        if not any([label.strip(), opens_at.strip(), closes_at.strip(),
                    results_at.strip(), payment_at.strip()]):
            continue
        rid = rid.strip()
        existing = existing_rounds.get(int(rid)) if rid.isdigit() else None
        if legs_omitted and existing is not None:
            # Fed back through parse_round_legs rather than assigned straight
            # across, so an id whose leg THIS submit deleted still drops --
            # preserving the selection must not preserve a dangling reference.
            legs = " ".join(str(i) for i in existing.applies_to or [])
        if quals_omitted and existing is not None:
            # Same rationale as legs above: re-fed through parse_round_qualifiers
            # so a qualifier round THIS submit deleted still drops.
            quals = " ".join(str(i) for i in existing_qualifiers.get(existing.id, []))
        applies_to = parse_round_legs(legs, valid_day_ids, key_to_day_id)
        if existing is not None:
            round_ = apply_round_fields(
                existing, label, kind_, opens_at, closes_at, results_at,
                payment_at, url, applies_to, label_en, notes_, label_zh,
            )
            kept_round_ids.add(round_.id)
        else:
            round_ = build_round(
                concert.id, label, kind_, opens_at, closes_at, results_at, payment_at, url,
                applies_to, label_en, notes_, label_zh,
            )
            session.add(round_)
        await record_round_label_phrase(session, label, label_en, label_zh)
        qual_jobs.append((round_, kind_, quals))
    for rid, round_ in existing_rounds.items():
        if rid not in kept_round_ids:
            await session.delete(round_)

    await record_concert_edit(session, concert, user.id, before)
    await session.flush()  # new rounds now have ids; dropped rounds are gone

    # -- Qualifiers: reconcile each surviving round's round_qualifiers rows.
    # Done after the flush so new rounds have ids and deleted rounds (with
    # their qualifier links, via ON DELETE CASCADE) are already gone.
    # valid_round_ids is every round that survived, so a qualifier naming a
    # round dropped in this same submit is filtered out. A round created in
    # this submit gets its id here but is never itself a qualifier -- the chips
    # only ever offered already-saved rounds. Clean-slate delete-then-insert
    # keeps this idempotent and drops qualifiers when a kind flips off UPGRADE.
    valid_round_ids = {r.id for r, _, _ in qual_jobs}
    for round_, kind_, quals in qual_jobs:
        await session.execute(
            delete(RoundQualifier).where(RoundQualifier.upgrade_round_id == round_.id)
        )
        if kind_ is RoundKind.UPGRADE:
            for q_id in parse_round_qualifiers(quals, valid_round_ids, round_.id):
                session.add(
                    RoundQualifier(upgrade_round_id=round_.id, qualifying_round_id=q_id)
                )
    await session.flush()

    if newly_cancelled_day_ids:
        await notify_newly_cancelled_legs(session, concert.id, newly_cancelled_day_ids)
    # The concert-level tag attach's notify-and-apply pipeline, run HERE rather
    # than beside the attach itself: handle_newly_tagged asks all_legs_cancelled
    # (a dead concert notifies nobody and applies nothing), and at the attach
    # site this submit's legs are not written yet, so it would answer about the
    # concert as it ARRIVED. Both directions are wrong and one is unrecoverable:
    # un-cancelling a leg while adding a tag suppressed a notice that has no
    # re-announce path, and cancelling the last leg while adding one announced
    # a show that is not happening.
    #
    # After notify_newly_cancelled_legs on purpose, which is the same order the
    # venue rollup below already used: that function probes each user's UNSENT
    # queue rows to find who is about to lose everything, and rules a preset
    # applies here would be fresh rows it must not read as a loss. Before
    # sync_concert, which every rule created here still needs.
    await handle_newly_tagged(session, concert, newly)
    # Re-derived from the legs as they stand after this submit -- this is the
    # staleness fix: a changed leg venue now moves the concert's VENUE tags
    # with it, and a dropped leg takes its venue off the concert. A venue that
    # newly lands here is fed to handle_newly_tagged exactly as the
    # concert-level tag diff above does: VENUE tags are subscribable, so
    # their followers get the notice and preset auto-apply (invariant 4).
    newly_venues = await sync_concert_venue_tags(session, concert.id)
    await handle_newly_tagged(session, concert, newly_venues)
    await sync_concert(session, concert.id)
    await session.commit()
    return RedirectResponse(f"/concerts/{concert.event_id}", status_code=303)


@router.get("/concerts/{event_id}/export.yaml")
async def export_concert_yaml(
    event_id: str,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    """Read-only sharing format, shaped like mting314/event-tracker's YAML.

    The assembly lives in service.concert_export_yaml because the admin
    catalogue zip emits the SAME document, and a restore file that differed
    from the one an editor downloads would be a second format nobody agreed
    to.
    """
    from app.domain.yaml_export import slugify

    concert = await get_concert_by_event_id(session, event_id)
    text = await concert_export_yaml(session, concert)
    return Response(
        content=text,
        media_type="application/yaml",
        headers={"Content-Disposition": f'attachment; filename="{slugify(concert.title)}.yaml"'},
    )


@router.post("/concerts/{event_id}/duplicate")
async def duplicate_concert(
    event_id: str,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
):
    """Clone this concert's scalar fields + tags into a fresh draft, for
    recurring-franchise events where the tags/kind/organizer carry over but
    the dates don't. Days and rounds are deliberately NOT copied -- a new
    edition has its own performances, and copying them would just be dates
    the editor has to delete one by one. Redirects straight to the new
    concert's edit page to fill those in.

    VENUE tags are NOT copied, for the same reason days aren't: a venue is
    now derived from the legs (sync_concert_venue_tags), and this clone has
    none. Carrying them over would give the clone venue tags its empty leg
    set contradicts from birth -- and since duplicate redirects to /edit,
    the editor's very first save would strip them again anyway.

    DELIBERATELY NOT a require_variants boundary, despite creating a row. It
    writes no editor-supplied text: every field is COPIED from a concert that
    may predate the i18n layer, and half of the database does. Enforcing here
    would reject duplicating exactly the legacy records this phase already
    decided to leave editable (the same reasoning that keeps edit_concert
    open) -- and it would reject them with no form to fix them in, since this
    route has no fields at all. The clone lands on /edit, where the
    variant-gaps notice names the gap instead. Do not "fix" this by adding
    enforcement; the create boundaries are the four in
    tests/test_variant_enforcement.py's census."""
    source = await get_concert_by_event_id(session, event_id)
    await session.refresh(source, ["tags"])
    await ensure_user(session, user.id, user.username)

    # .strip(), not truthiness: generate_event_id decides "has an English
    # title" that way, and a whitespace-only title_en handed over as
    # "   copy" strips back to "copy" inside it -- minting /concerts/copy
    # instead of falling back to `title`. Both halves must ask the same
    # question of the same value.
    source_title_en = (source.title_en or "").strip()
    new_event_id = await generate_event_id(
        session,
        f"{source.title} copy",
        f"{source_title_en} copy" if source_title_en else None,
    )
    f_names = [t.name for t in source.tags if t.kind is TagKind.FRANCHISE]
    clone = Concert(
        title=f"{source.title} (copy)",
        event_id=new_event_id,
        kind=source.kind,
        organizer=source.organizer,
        categories=source.categories,
        franchise=", ".join(f_names) or None,
        created_by=user.id,
    )
    session.add(clone)
    await session.flush()

    newly: list[Tag] = []
    for tag in source.tags:
        if tag.kind is TagKind.VENUE:
            continue  # derived from legs; the legless clone has none
        # expand=False regardless of kind: source.tags already reflects
        # this concert's own pruned GROUP membership, so we carry that
        # exact set over rather than re-expanding to the group's current
        # (possibly different) membership.
        newly += await attach_tag(session, clone.id, tag, expand=False)
    await handle_newly_tagged(session, clone, newly)
    await session.commit()
    return RedirectResponse(f"/concerts/{clone.event_id}/edit", status_code=303)


@router.post("/concerts/{event_id}/delete")
async def delete_concert(
    event_id: str,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
):
    concert = await get_concert_by_event_id(session, event_id)
    await session.delete(concert)  # cascades: days, rounds, rules, queue
    await session.commit()
    return RedirectResponse("/", status_code=303)


# ── Rounds ───────────────────────────────────────────────────────────────


@router.get("/rounds/{round_id}/ics")
async def round_ics(
    round_id: int,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    """One .ics per round, keyed to whichever timestamp is most relevant:
    closes -> opens -> results -> payment, first one set. Matches the
    reference site's one-icon-per-row pattern rather than exporting all 4
    possible deadlines separately."""
    from app.domain.ics_export import build_ics
    from app.domain.yaml_export import slugify

    round_ = await session.get(Round, round_id)
    if round_ is None:
        raise HTTPException(status_code=404)
    at_utc = (
        round_.closes_at_utc or round_.opens_at_utc
        or round_.results_at_utc or round_.payment_deadline_at_utc
    )
    if at_utc is None:
        raise HTTPException(status_code=422, detail="round has no timestamps to export")
    concert = await get_concert(session, round_.concert_id)
    text = build_ics(
        f"{concert.title} — {round_.label}", at_utc,
        url=round_.url, description=round_.notes,
    )
    return Response(
        content=text,
        media_type="text/calendar",
        headers={
            "Content-Disposition": f'attachment; filename="{slugify(round_.label)}.ics"'
        },
    )


# ── Round-label phrases ────────────────────────────────────────────────────


@router.post("/round-phrases/{phrase_id}/forget", status_code=204)
async def forget_phrase(
    phrase_id: int,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Stop offering a remembered label. Never touches the rounds using it --
    a phrase is a suggestion, not a foreign key (see
    db.service.forget_round_label_phrase)."""
    if not await forget_round_label_phrase(session, phrase_id):
        raise HTTPException(status_code=404, detail="no such phrase")
    await session.commit()
    return Response(status_code=204)
