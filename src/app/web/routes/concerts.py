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

import json
import re
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Concert, ConcertDay, ReminderRule, Round, Tag, User
from app.db.service import (
    attach_tag,
    concert_audit_log,
    detach_tag,
    ensure_user,
    group_members,
    handle_newly_tagged,
    notify_newly_cancelled_legs,
    record_concert_edit,
    snapshot_concert,
    sync_concert,
    tag_picker_context,
)
from app.db.session import get_session
from app.domain.timezones import jst_to_utc
from app.domain.types import Anchor, ConcertKind, RoundKind, TagKind
from app.web.auth import SessionUser, require_editor, require_user
from app.web.forms import form_url

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


async def generate_event_id(session: AsyncSession, title: str) -> str:
    """Auto-suggest an event_id for flows with no dedicated input for one
    (the URL-import commit route) -- slugified title, de-duplicated with a
    numeric suffix. Editable afterward via the edit page."""
    from app.domain.yaml_export import slugify

    base = slugify(title)
    candidate = base
    suffix = 2
    while (await session.execute(
        select(Concert.id).where(Concert.event_id == candidate)
    )).scalar_one_or_none() is not None:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def apply_day_fields(
    day: ConcertDay,
    label: str,
    starts_at: str,
    city: str = "",
    venue: str = "",
    venue_address: str = "",
    doors_at: str = "",
    cancelled: str = "false",
) -> ConcertDay:
    """The JST->UTC parse + assignment shared by build_day (new rows) and
    the edit page's in-place update of existing rows."""
    starts = parse_jst(starts_at)
    if starts is None:
        raise HTTPException(status_code=422, detail="a day needs a start time")
    day.label = label.strip()
    day.starts_at_utc = starts
    day.city = city.strip() or None
    day.venue = venue.strip() or None
    day.venue_address = venue_address.strip() or None
    day.doors_at_utc = parse_jst(doors_at)
    day.cancelled = cancelled == "true"
    return day


def build_day(
    concert_id: int,
    label: str,
    starts_at: str,
    city: str = "",
    venue: str = "",
    venue_address: str = "",
    doors_at: str = "",
    cancelled: str = "false",
) -> ConcertDay:
    """New-row constructor: the rich creation form, the edit page's new
    rows, and the URL-import commit route."""
    return apply_day_fields(
        ConcertDay(concert_id=concert_id), label, starts_at, city, venue, venue_address,
        doors_at, cancelled,
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
) -> Round:
    """New-row constructor: the rich creation form, the edit page's new
    rows, and the URL-import commit route."""
    return apply_round_fields(
        Round(concert_id=concert_id), label, kind, opens_at, closes_at, results_at,
        payment_at, url, applies_to, label_en, notes,
    )


def resolve_round_leg(days: list[ConcertDay], leg: str) -> list[int] | None:
    """Free-text leg matching for the creation/edit forms, where repeatable
    performance rows are matched to rounds by typed text rather than a
    picker. Matches a day's city or label, case-insensitively, exact match
    (not substring -- avoids "Day 1" matching "Day 10"). Blank or no match
    -> None (round shown under "General")."""
    leg = leg.strip().lower()
    if not leg:
        return None
    matches = [d.id for d in days if leg in ((d.city or "").lower(), (d.label or "").lower())]
    return matches or None


def round_leg_display(round_: Round, days_by_id: dict[int, ConcertDay]) -> str:
    """Inverse of resolve_round_leg, for pre-filling the edit page's leg
    <select> from a round's real applies_to day ids. Label-first, falling
    back to city -- must match the same preference the leg dropdown's
    options use client-side (_leg_picker_script.html's legOptionFor), or
    this value won't match any of that dropdown's option values and the
    round's current leg would silently fail to pre-select."""
    if not round_.applies_to:
        return ""
    day = days_by_id.get(round_.applies_to[0])
    if day is None:
        return ""
    return day.label or day.city


def group_rounds_by_day(concert: Concert) -> tuple[dict[int, list[Round]], list[Round]]:
    """Rounds grouped by the ConcertDay(s) they apply to ("legs"), plus a
    'general' bucket for rounds with no day association."""
    by_day: dict[int, list[Round]] = {d.id: [] for d in concert.days}
    general: list[Round] = []
    for r in concert.rounds:
        if r.applies_to:
            for day_id in r.applies_to:
                if day_id in by_day:
                    by_day[day_id].append(r)
        else:
            general.append(r)
    return by_day, general


def find_venue_tag(venue_tags: list[Tag], name: str | None) -> Tag | None:
    """Resolve a day's free-text venue against real VENUE tags by exact,
    case-insensitive name match -- same free-text-to-structured pattern as
    resolve_round_leg above. No match just means no link is shown (a nudge
    to go create that tag, not a hard requirement)."""
    if not name:
        return None
    name = name.strip().lower()
    for t in venue_tags:
        if t.name.strip().lower() == name:
            return t
    return None


def is_round_past(round_: Round, now: datetime) -> bool:
    """A round is past once every timestamp it has set has already
    happened; a round with no timestamps set at all can't be "past"."""
    timestamps = [
        t for t in (
            round_.opens_at_utc, round_.closes_at_utc,
            round_.results_at_utc, round_.payment_deadline_at_utc,
        ) if t is not None
    ]
    return bool(timestamps) and all(t < now for t in timestamps)


def is_day_past(day: ConcertDay, now: datetime) -> bool:
    return day.starts_at_utc < now


def concert_date_range(days: list[ConcertDay]) -> tuple[datetime, datetime] | None:
    """Earliest and latest day.starts_at_utc among LIVE (non-cancelled)
    legs, for the detail page header's date-range summary. None when there
    are no days yet, or every existing day is cancelled."""
    live_days = [d for d in days if not d.cancelled]
    if not live_days:
        return None
    starts = [d.starts_at_utc for d in live_days]
    return min(starts), max(starts)


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
    return templates.TemplateResponse(
        request,
        "_rules.html",
        {"concert": concert, "user": user, "rules": rules, "presets": presets,
         "anchors": list(Anchor)},
    )


# ── Concerts ─────────────────────────────────────────────────────────────


async def resolve_tags(session: AsyncSession, tag_ids: list[int], kind) -> list[Tag]:
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


async def create_concert_row(
    session: AsyncSession,
    user: SessionUser,
    title: str,
    event_id: str,
    franchise_tags: list[int],
    group_tags: list[int],
    artist_tags: list[int],
    venue_tags: list[int],
    kind: ConcertKind | None = None,
    source_url: str | None = None,
) -> Concert:
    """Tag-driven creation supporting collab events: MULTIPLE franchises,
    MULTIPLE groups, explicit artist list (auto-populated client-side from
    the selected groups, editor-pruned). Groups attach WITHOUT expansion —
    the submitted artist list is authoritative. The notify-and-apply
    pipeline fires on everything attached here. Shared by create_concert
    and the URL-import commit route."""
    await ensure_user(session, user.id, user.username)
    event_id = await validate_event_id(session, event_id)

    f_tags = await resolve_tags(session, franchise_tags, TagKind.FRANCHISE)
    g_tags = await resolve_tags(session, group_tags, TagKind.GROUP)
    a_tags = await resolve_tags(session, artist_tags, TagKind.ARTIST)
    v_tags = await resolve_tags(session, venue_tags, TagKind.VENUE)

    concert = Concert(
        title=title.strip(),
        event_id=event_id,
        kind=kind,
        franchise=", ".join(t.name for t in f_tags) or None,  # denormalized display
        venue=", ".join(t.name for t in v_tags) or None,
        # Optional so create_concert, which assigns its own richer field set
        # (including source_url) right after this returns, is unaffected.
        # Callers pass an already-form_url-validated value.
        source_url=source_url,
        created_by=user.id,
    )
    session.add(concert)
    await session.flush()

    newly: list[Tag] = []
    for tag in [*f_tags, *g_tags, *v_tags]:
        newly += await attach_tag(session, concert.id, tag, expand=False)
    for artist in a_tags:
        newly += await attach_tag(session, concert.id, artist)
    await handle_newly_tagged(session, concert, newly)
    return concert


@router.get("/concerts/new", response_class=HTMLResponse)
async def new_concert_form(
    request: Request,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
):
    """The rich, all-in-one creation page: matches mting314/event-tracker's
    add.html field set (see CLAUDE.md / the plan this shipped from) --
    event fields, performers/notes, then repeatable performance and round
    rows, one atomic submit to POST /concerts below."""
    picker = await tag_picker_context(session)
    return templates.TemplateResponse(
        request,
        "concert_new.html",
        {
            "user": user, "kinds": list(RoundKind), "concert_kinds": list(ConcertKind),
            "by_kind": picker["by_kind"],
            "groups_json": json.dumps(picker["groups_json"]),
            "tag_names_json": json.dumps(picker["tag_names_json"]),
            "initial_selected_json": "{}",
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
    kind: str = Form(""),
    organizer: str = Form(""),
    categories: str = Form(""),
    eventernote_url: str = Form(""),
    official_url: str = Form(""),
    source_url: str = Form(""),
    performers_text: str = Form(""),
    notes: str = Form(""),
    franchise_tags: list[int] = Form(default=[]),
    group_tags: list[int] = Form(default=[]),
    artist_tags: list[int] = Form(default=[]),
    venue_tags: list[int] = Form(default=[]),
    day_label: list[str] = Form(default=[]),
    day_starts_at: list[str] = Form(default=[]),
    day_city: list[str] = Form(default=[]),
    day_venue: list[str] = Form(default=[]),
    day_venue_address: list[str] = Form(default=[]),
    day_doors_at: list[str] = Form(default=[]),
    day_cancelled: list[str] = Form(default=[]),
    round_label: list[str] = Form(default=[]),
    round_label_en: list[str] = Form(default=[]),
    round_kind: list[RoundKind] = Form(default=[]),
    round_opens_at: list[str] = Form(default=[]),
    round_closes_at: list[str] = Form(default=[]),
    round_results_at: list[str] = Form(default=[]),
    round_payment_at: list[str] = Form(default=[]),
    round_url: list[str] = Form(default=[]),
    round_notes: list[str] = Form(default=[]),
    round_leg: list[str] = Form(default=[]),
):
    """The rich all-in-one submit: concert + tags, then every performance
    and round row, created together in one transaction -- same
    compose-and-loop shape imports.py's import_commit uses, just with the
    fuller add.html-matched field set."""
    concert = await create_concert_row(
        session, user, title, event_id, franchise_tags, group_tags, artist_tags, venue_tags,
        kind=ConcertKind(kind) if kind else None,
    )
    concert.title_en = title_en.strip() or None
    concert.organizer = organizer.strip() or None
    concert.categories = categories.strip() or None
    concert.eventernote_url = form_url(eventernote_url)
    concert.official_url = form_url(official_url)
    concert.source_url = form_url(source_url)
    concert.performers_text = performers_text.strip() or None
    concert.notes = notes.strip() or None

    # day_cancelled is newer than the other day_* fields; a submitter that
    # omits it entirely (rather than one row per day) means "not cancelled"
    # for every row, matching apply_day_fields'/build_day's own default --
    # pad rather than let a whole-array omission trip the strict zip below.
    day_cancelled = day_cancelled + ["false"] * (len(day_label) - len(day_cancelled))
    days: list[ConcertDay] = []
    for label, starts_at, city, venue, venue_address, doors_at, cancelled in zip(
        day_label, day_starts_at, day_city, day_venue, day_venue_address, day_doors_at,
        day_cancelled, strict=True,
    ):
        if not any([label.strip(), starts_at.strip(), city.strip(), venue.strip()]):
            continue  # blank trailing row from the repeatable UI
        day = build_day(
            concert.id, label, starts_at, city, venue, venue_address, doors_at, cancelled
        )
        session.add(day)
        days.append(day)
    await session.flush()  # real ids, needed for leg-matching below

    for (
        label, label_en, kind_, opens_at, closes_at, results_at, payment_at, url, notes_, leg
    ) in zip(
        round_label, round_label_en, round_kind, round_opens_at, round_closes_at,
        round_results_at, round_payment_at, round_url, round_notes, round_leg,
        strict=True,
    ):
        if not any([label.strip(), opens_at.strip(), closes_at.strip(),
                    results_at.strip(), payment_at.strip()]):
            continue
        session.add(build_round(
            concert.id, label, kind_, opens_at, closes_at, results_at, payment_at, url,
            applies_to=resolve_round_leg(days, leg), label_en=label_en, notes=notes_,
        ))

    await session.flush()
    await sync_concert(session, concert.id)
    await session.commit()
    return RedirectResponse(f"/concerts/{concert.event_id}", status_code=303)


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
    venue_tags = list(
        (await session.execute(select(Tag).where(Tag.kind == TagKind.VENUE))).scalars()
    )
    by_day, general = group_rounds_by_day(concert)
    now = datetime.now(UTC)
    day_venue_tags = {d.id: find_venue_tag(venue_tags, d.venue) for d in concert.days}
    past_round_ids = {r.id for r in concert.rounds if is_round_past(r, now)}
    past_day_ids = {d.id for d in concert.days if is_day_past(d, now)}
    date_range = concert_date_range(concert.days)
    concert_past = bool(date_range) and date_range[1] < now
    # Editor-only, and only fetched for editors -- viewers have no use for
    # who-changed-what, and it's one extra query worth skipping for them.
    audit_log = await concert_audit_log(session, concert.id) if user.is_editor else []
    return templates.TemplateResponse(
        request,
        "concert_detail.html",
        {"concert": concert, "user": user, "rules": rules, "tz": tz, "presets": presets,
         "anchors": list(Anchor),
         "rounds_by_day": by_day, "general_rounds": general,
         "day_venue_tags": day_venue_tags, "past_round_ids": past_round_ids,
         "past_day_ids": past_day_ids, "now": now,
         "date_range": date_range, "concert_past": concert_past,
         "audit_log": audit_log},
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
    days_by_id = {d.id: d for d in concert.days}

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
    initial_selected = {
        "franchise": [str(t.id) for t in concert.tags if t.kind is TagKind.FRANCHISE],
        "group": [str(t.id) for t in concert.tags if t.kind is TagKind.GROUP],
        "artist": [str(i) for i in attached_artist_ids],
        "artist_excluded": [str(i) for i in excluded_ids],
        "venue": [str(t.id) for t in concert.tags if t.kind is TagKind.VENUE],
    }
    rounds_with_leg = [(r, round_leg_display(r, days_by_id)) for r in concert.rounds]
    return templates.TemplateResponse(
        request,
        "concert_edit.html",
        {
            "user": user, "concert": concert, "kinds": list(RoundKind),
            "concert_kinds": list(ConcertKind),
            "by_kind": picker["by_kind"],
            "groups_json": json.dumps(picker["groups_json"]),
            "tag_names_json": json.dumps(picker["tag_names_json"]),
            "initial_selected_json": json.dumps(initial_selected),
            "rounds_with_leg": rounds_with_leg,
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
    kind: str = Form(""),
    organizer: str = Form(""),
    categories: str = Form(""),
    eventernote_url: str = Form(""),
    official_url: str = Form(""),
    source_url: str = Form(""),
    performers_text: str = Form(""),
    notes: str = Form(""),
    franchise_tags: list[int] = Form(default=[]),
    group_tags: list[int] = Form(default=[]),
    artist_tags: list[int] = Form(default=[]),
    venue_tags: list[int] = Form(default=[]),
    day_id: list[str] = Form(default=[]),
    day_label: list[str] = Form(default=[]),
    day_starts_at: list[str] = Form(default=[]),
    day_city: list[str] = Form(default=[]),
    day_venue: list[str] = Form(default=[]),
    day_venue_address: list[str] = Form(default=[]),
    day_doors_at: list[str] = Form(default=[]),
    day_cancelled: list[str] = Form(default=[]),
    round_id: list[str] = Form(default=[]),
    round_label: list[str] = Form(default=[]),
    round_label_en: list[str] = Form(default=[]),
    round_kind: list[RoundKind] = Form(default=[]),
    round_opens_at: list[str] = Form(default=[]),
    round_closes_at: list[str] = Form(default=[]),
    round_results_at: list[str] = Form(default=[]),
    round_payment_at: list[str] = Form(default=[]),
    round_url: list[str] = Form(default=[]),
    round_notes: list[str] = Form(default=[]),
    round_leg: list[str] = Form(default=[]),
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
    concert.kind = ConcertKind(kind) if kind else None
    concert.organizer = organizer.strip() or None
    concert.categories = categories.strip() or None
    concert.eventernote_url = form_url(eventernote_url)
    concert.official_url = form_url(official_url)
    concert.source_url = form_url(source_url)
    concert.performers_text = performers_text.strip() or None
    concert.notes = notes.strip() or None

    # -- Tags: diff before/after, detach dropped ids, attach new ones only.
    # An unchanged, already-attached group is never re-touched, so its
    # previously-pruned members stay pruned (invariant: group expansion is
    # a one-time, at-attach-time event).
    f_tags = await resolve_tags(session, franchise_tags, TagKind.FRANCHISE)
    g_tags = await resolve_tags(session, group_tags, TagKind.GROUP)
    a_tags = await resolve_tags(session, artist_tags, TagKind.ARTIST)
    v_tags = await resolve_tags(session, venue_tags, TagKind.VENUE)
    desired_tags = {t.id: t for t in [*f_tags, *g_tags, *a_tags, *v_tags]}

    await session.refresh(concert, ["tags"])
    before_ids = {t.id for t in concert.tags}
    after_ids = set(desired_tags)
    for tid in before_ids - after_ids:
        await detach_tag(session, concert.id, tid)
    newly: list[Tag] = []
    for tid in after_ids - before_ids:
        newly += await attach_tag(session, concert.id, desired_tags[tid], expand=False)
    await handle_newly_tagged(session, concert, newly)

    # -- Days: update existing rows in place by id, insert blank-id rows,
    # delete rows that were dropped.
    await session.refresh(concert, ["days"])
    existing_days = {d.id: d for d in concert.days}
    before_cancelled_day_ids = {d.id for d in concert.days if d.cancelled}
    # See create_concert's identical comment: a submitter that omits
    # day_cancelled entirely means "not cancelled" for every row.
    day_cancelled = day_cancelled + ["false"] * (len(day_label) - len(day_cancelled))
    kept_day_ids: set[int] = set()
    days_for_leg_matching: list[ConcertDay] = []
    for did, label, starts_at, city, venue, venue_address, doors_at, cancelled in zip(
        day_id, day_label, day_starts_at, day_city, day_venue, day_venue_address, day_doors_at,
        day_cancelled, strict=True,
    ):
        if not any([label.strip(), starts_at.strip(), city.strip(), venue.strip()]):
            continue  # blank trailing row from the repeatable UI
        did = did.strip()
        if did.isdigit() and int(did) in existing_days:
            day = apply_day_fields(
                existing_days[int(did)], label, starts_at, city, venue, venue_address,
                doors_at, cancelled,
            )
            kept_day_ids.add(day.id)
        else:
            day = build_day(
                concert.id, label, starts_at, city, venue, venue_address, doors_at, cancelled
            )
            session.add(day)
        days_for_leg_matching.append(day)
    for did, day in existing_days.items():
        if did not in kept_day_ids:
            await session.delete(day)
    await session.flush()  # new/kept days have real ids, needed for leg-matching below
    newly_cancelled_day_ids = {
        d.id for d in days_for_leg_matching if d.cancelled
    } - before_cancelled_day_ids

    # -- Rounds: same id-preserving reconciliation.
    await session.refresh(concert, ["rounds"])
    existing_rounds = {r.id: r for r in concert.rounds}
    kept_round_ids: set[int] = set()
    for (
        rid, label, label_en, kind_, opens_at, closes_at, results_at, payment_at, url, notes_, leg
    ) in zip(
        round_id, round_label, round_label_en, round_kind, round_opens_at, round_closes_at,
        round_results_at, round_payment_at, round_url, round_notes, round_leg,
        strict=True,
    ):
        if not any([label.strip(), opens_at.strip(), closes_at.strip(),
                    results_at.strip(), payment_at.strip()]):
            continue
        applies_to = resolve_round_leg(days_for_leg_matching, leg)
        rid = rid.strip()
        if rid.isdigit() and int(rid) in existing_rounds:
            round_ = apply_round_fields(
                existing_rounds[int(rid)], label, kind_, opens_at, closes_at, results_at,
                payment_at, url, applies_to, label_en, notes_,
            )
            kept_round_ids.add(round_.id)
        else:
            session.add(build_round(
                concert.id, label, kind_, opens_at, closes_at, results_at, payment_at, url,
                applies_to, label_en, notes_,
            ))
    for rid, round_ in existing_rounds.items():
        if rid not in kept_round_ids:
            await session.delete(round_)

    await record_concert_edit(session, concert, user.id, before)
    await session.flush()
    if newly_cancelled_day_ids:
        await notify_newly_cancelled_legs(session, concert.id, newly_cancelled_day_ids)
    await sync_concert(session, concert.id)
    await session.commit()
    return RedirectResponse(f"/concerts/{concert.event_id}", status_code=303)


@router.get("/concerts/{event_id}/export.yaml")
async def export_concert_yaml(
    event_id: str,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    """Read-only sharing format, shaped like mting314/event-tracker's YAML --
    export only, never an import path. SQLite via the web UI stays the only
    way to create/edit data."""
    from app.domain.yaml_export import YamlDay, YamlRound, concert_to_yaml, slugify

    concert = await get_concert_by_event_id(session, event_id)
    await session.refresh(concert, ["days", "rounds", "tags"])

    days_by_id = {d.id: d.label for d in concert.days}
    yaml_days = [
        YamlDay(
            label=d.label, starts_at_utc=d.starts_at_utc,
            city=d.city, venue=d.venue, venue_address=d.venue_address,
            doors_at_utc=d.doors_at_utc,
        )
        for d in concert.days
    ]
    yaml_rounds = [
        YamlRound(
            label=r.label, label_en=r.label_en, kind=r.kind.value,
            applies_to_labels=[days_by_id[d] for d in (r.applies_to or []) if d in days_by_id],
            opens_at_utc=r.opens_at_utc, closes_at_utc=r.closes_at_utc,
            results_at_utc=r.results_at_utc, payment_deadline_at_utc=r.payment_deadline_at_utc,
            url=r.url, notes=r.notes,
        )
        for r in concert.rounds
    ]

    text = concert_to_yaml(
        title=concert.title,
        kind=concert.kind.value if concert.kind else None,
        franchises=[t.name for t in concert.tags if t.kind is TagKind.FRANCHISE],
        groups=[t.name for t in concert.tags if t.kind is TagKind.GROUP],
        artists=[t.name for t in concert.tags if t.kind is TagKind.ARTIST],
        venues=[t.name for t in concert.tags if t.kind is TagKind.VENUE],
        days=yaml_days, rounds=yaml_rounds, notes=concert.notes,
        title_en=concert.title_en, organizer=concert.organizer, categories=concert.categories,
        eventernote_url=concert.eventernote_url, official_url=concert.official_url,
        source_url=concert.source_url,
        performers=(
            [line.strip() for line in concert.performers_text.splitlines() if line.strip()]
            if concert.performers_text else []
        ),
    )
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
    concert's edit page to fill those in."""
    source = await get_concert_by_event_id(session, event_id)
    await session.refresh(source, ["tags"])
    await ensure_user(session, user.id, user.username)

    new_event_id = await generate_event_id(session, f"{source.title} copy")
    f_names = [t.name for t in source.tags if t.kind is TagKind.FRANCHISE]
    v_names = [t.name for t in source.tags if t.kind is TagKind.VENUE]
    clone = Concert(
        title=f"{source.title} (copy)",
        event_id=new_event_id,
        kind=source.kind,
        organizer=source.organizer,
        categories=source.categories,
        franchise=", ".join(f_names) or None,
        venue=", ".join(v_names) or None,
        created_by=user.id,
    )
    session.add(clone)
    await session.flush()

    newly: list[Tag] = []
    for tag in source.tags:
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
