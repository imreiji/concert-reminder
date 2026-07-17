"""Concert CRUD + reminder rules — the web UI's working core.

Route conventions:
  * Mutations are POSTs. Page-level actions redirect (PRG pattern);
    list-level actions (rounds/days/rules) are htmx fragment swaps —
    the server renders the updated list and htmx swaps it in place.
  * require_editor guards everything that changes concerts/days/rounds.
    require_user guards personal reminder rules.
  * EVERY mutation that touches dates ends with a queue re-sync
    (sync_concert / sync_rule). That is the contract that makes the web UI
    and the scheduler agree with each other.

Datetime contract: <input type="datetime-local"> values are interpreted as
JST — that is how Japanese ticketing announces times, so the form matches
the source material. Conversion to UTC happens here, at the boundary,
via domain.timezones.jst_to_utc. Nowhere else.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Concert, ConcertDay, ReminderRule, Round, Tag, User
from app.db.service import attach_tag, ensure_user, handle_newly_tagged, sync_concert, sync_rule
from app.db.session import get_session
from app.domain.timezones import jst_to_utc
from app.domain.types import Anchor, Channel, ConcertKind, RoundKind
from app.web.auth import SessionUser, require_editor, require_user

router = APIRouter()

# set by web.app at startup to avoid a circular import
templates = None


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
    concert = await session.get(Concert, concert_id)
    if concert is None:
        raise HTTPException(status_code=404, detail="concert not found")
    return concert


def build_day(concert_id: int, label: str, starts_at: str) -> ConcertDay:
    """Shared by add_day and the URL-import commit route -- same validation
    ("a day needs a start time") either way, one JST -> UTC boundary."""
    starts = parse_jst(starts_at)
    if starts is None:
        raise HTTPException(status_code=422, detail="a day needs a start time")
    return ConcertDay(concert_id=concert_id, label=label.strip(), starts_at_utc=starts)


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
) -> Round:
    """Shared by add_round and the URL-import commit route.

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
    return Round(
        concert_id=concert_id, kind=kind, label=label.strip(),
        opens_at_utc=opens, closes_at_utc=closes,
        results_at_utc=results, payment_deadline_at_utc=payment,
        url=url.strip() or None, applies_to=applies_to or None,
    )


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


# ── Fragments (htmx swap targets) ────────────────────────────────────────


async def user_tz(session: AsyncSession, user_id: int) -> str:
    db_user = await session.get(User, user_id)
    return db_user.timezone if db_user else "America/Moncton"


async def render_fragment(request: Request, name: str, concert: Concert, user: SessionUser,
                          session: AsyncSession) -> HTMLResponse:
    from app.web.routes.preferences import my_presets

    await session.refresh(concert, ["days", "rounds"])
    rules = await user_rules(session, user.id, concert.id)
    tz = await user_tz(session, user.id)
    presets = await my_presets(session, user.id)
    by_day, general = group_rounds_by_day(concert)
    return templates.TemplateResponse(
        request,
        name,
        {"concert": concert, "user": user, "rules": rules, "tz": tz, "presets": presets,
         "kinds": list(RoundKind), "anchors": list(Anchor),
         "rounds_by_day": by_day, "general_rounds": general},
    )


async def user_rules(session: AsyncSession, user_id: int, concert_id: int) -> list[ReminderRule]:
    res = await session.execute(
        select(ReminderRule).where(
            ReminderRule.user_id == user_id, ReminderRule.concert_id == concert_id
        )
    )
    return list(res.scalars())


# ── Concerts ─────────────────────────────────────────────────────────────


async def resolve_tags(session: AsyncSession, tag_ids: list[int], kind) -> list[Tag]:
    """Shared by create_concert and the URL-import commit route: every
    submitted tag id must exist and match the expected kind."""
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
    franchise_tags: list[int],
    group_tags: list[int],
    artist_tags: list[int],
    venue_tags: list[int],
    kind: ConcertKind | None = None,
) -> Concert:
    """Tag-driven creation supporting collab events: MULTIPLE franchises,
    MULTIPLE groups, explicit artist list (auto-populated client-side from
    the selected groups, editor-pruned). Groups attach WITHOUT expansion —
    the submitted artist list is authoritative. The notify-and-apply
    pipeline fires on everything attached here. Shared by create_concert
    and the URL-import commit route."""
    from app.domain.types import TagKind

    await ensure_user(session, user.id, user.username)

    f_tags = await resolve_tags(session, franchise_tags, TagKind.FRANCHISE)
    g_tags = await resolve_tags(session, group_tags, TagKind.GROUP)
    a_tags = await resolve_tags(session, artist_tags, TagKind.ARTIST)
    v_tags = await resolve_tags(session, venue_tags, TagKind.VENUE)

    concert = Concert(
        title=title.strip(),
        kind=kind,
        franchise=", ".join(t.name for t in f_tags) or None,  # denormalized display
        venue=", ".join(t.name for t in v_tags) or None,
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


@router.post("/concerts")
async def create_concert(
    request: Request,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
    title: str = Form(..., min_length=1, max_length=200),
    kind: str = Form(""),
    franchise_tags: list[int] = Form(default=[]),
    group_tags: list[int] = Form(default=[]),
    artist_tags: list[int] = Form(default=[]),
    venue_tags: list[int] = Form(default=[]),
):
    concert = await create_concert_row(
        session, user, title, franchise_tags, group_tags, artist_tags, venue_tags,
        kind=ConcertKind(kind) if kind else None,
    )
    await session.commit()
    return RedirectResponse(f"/concerts/{concert.id}", status_code=303)


@router.get("/concerts/{concert_id}", response_class=HTMLResponse)
async def concert_detail(
    request: Request,
    concert_id: int,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    from app.db.models import Tag
    from app.web.routes.preferences import my_presets

    concert = await get_concert(session, concert_id)
    await session.refresh(concert, ["days", "rounds", "tags"])
    rules = await user_rules(session, user.id, concert_id)
    tz = await user_tz(session, user.id)
    presets = await my_presets(session, user.id)
    tags = list((await session.execute(select(Tag).order_by(Tag.kind, Tag.name))).scalars())
    by_kind: dict[str, list[Tag]] = {}
    for tg in tags:
        by_kind.setdefault(tg.kind.value, []).append(tg)
    by_day, general = group_rounds_by_day(concert)
    return templates.TemplateResponse(
        request,
        "concert_detail.html",
        {"concert": concert, "user": user, "rules": rules, "tz": tz, "presets": presets,
         "kinds": list(RoundKind), "anchors": list(Anchor), "concert_kinds": list(ConcertKind),
         "by_kind": by_kind, "attached": {tg.id for tg in concert.tags},
         "rounds_by_day": by_day, "general_rounds": general},
    )


@router.post("/concerts/{concert_id}/edit")
async def edit_concert(
    concert_id: int,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
    title: str = Form(..., min_length=1, max_length=200),
    kind: str = Form(""),
    notes: str = Form(""),
):
    """Title/notes/kind only — franchise/group/artists/venue are managed as tags."""
    concert = await get_concert(session, concert_id)
    concert.title = title.strip()
    concert.kind = ConcertKind(kind) if kind else None
    concert.notes = notes.strip() or None
    await session.commit()
    return RedirectResponse(f"/concerts/{concert_id}", status_code=303)


@router.get("/concerts/{concert_id}/export.yaml")
async def export_concert_yaml(
    concert_id: int,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    """Read-only sharing format, shaped like mting314/event-tracker's YAML --
    export only, never an import path. SQLite via the web UI stays the only
    way to create/edit data."""
    from app.domain.types import TagKind
    from app.domain.yaml_export import YamlDay, YamlRound, concert_to_yaml, slugify

    concert = await get_concert(session, concert_id)
    await session.refresh(concert, ["days", "rounds", "tags"])

    days_by_id = {d.id: d.label for d in concert.days}
    yaml_days = [YamlDay(label=d.label, starts_at_utc=d.starts_at_utc) for d in concert.days]
    yaml_rounds = [
        YamlRound(
            label=r.label, kind=r.kind.value,
            applies_to_labels=[days_by_id[d] for d in (r.applies_to or []) if d in days_by_id],
            opens_at_utc=r.opens_at_utc, closes_at_utc=r.closes_at_utc,
            results_at_utc=r.results_at_utc, payment_deadline_at_utc=r.payment_deadline_at_utc,
            url=r.url,
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
    )
    return Response(
        content=text,
        media_type="application/yaml",
        headers={"Content-Disposition": f'attachment; filename="{slugify(concert.title)}.yaml"'},
    )


@router.post("/concerts/{concert_id}/delete")
async def delete_concert(
    concert_id: int,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
):
    concert = await get_concert(session, concert_id)
    await session.delete(concert)  # cascades: days, rounds, rules, queue
    await session.commit()
    return RedirectResponse("/", status_code=303)


# ── Rounds ───────────────────────────────────────────────────────────────


@router.post("/concerts/{concert_id}/rounds", response_class=HTMLResponse)
async def add_round(
    request: Request,
    concert_id: int,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
    label: str = Form(..., min_length=1, max_length=200),
    kind: RoundKind = Form(RoundKind.OTHER),
    opens_at: str = Form(""),
    closes_at: str = Form(""),
    results_at: str = Form(""),
    payment_at: str = Form(""),
    url: str = Form(""),
    applies_to: list[int] = Form(default=[]),
):
    concert = await get_concert(session, concert_id)
    session.add(build_round(
        concert.id, label, kind, opens_at, closes_at, results_at, payment_at, url, applies_to
    ))
    await session.flush()
    await sync_concert(session, concert.id)
    await session.commit()
    return await render_fragment(request, "_rounds.html", concert, user, session)


@router.post("/rounds/{round_id}/edit", response_class=HTMLResponse)
async def edit_round(
    request: Request,
    round_id: int,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
    label: str = Form(..., min_length=1, max_length=200),
    kind: RoundKind = Form(...),
    opens_at: str = Form(""),
    closes_at: str = Form(""),
    results_at: str = Form(""),
    payment_at: str = Form(""),
    url: str = Form(""),
    applies_to: list[int] = Form(default=[]),
):
    round_ = await session.get(Round, round_id)
    if round_ is None:
        raise HTTPException(status_code=404)
    round_.label = label.strip()
    round_.kind = kind
    round_.opens_at_utc = parse_jst(opens_at)
    round_.closes_at_utc = parse_jst(closes_at)
    round_.results_at_utc = parse_jst(results_at)
    round_.payment_deadline_at_utc = parse_jst(payment_at)
    round_.url = url.strip() or None
    round_.applies_to = applies_to or None
    concert = await get_concert(session, round_.concert_id)
    await sync_concert(session, concert.id)
    await session.commit()
    return await render_fragment(request, "_rounds.html", concert, user, session)


@router.post("/rounds/{round_id}/delete", response_class=HTMLResponse)
async def delete_round(
    request: Request,
    round_id: int,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
):
    round_ = await session.get(Round, round_id)
    if round_ is None:
        raise HTTPException(status_code=404)
    concert = await get_concert(session, round_.concert_id)
    await session.delete(round_)
    await session.flush()
    await sync_concert(session, concert.id)
    await session.commit()
    return await render_fragment(request, "_rounds.html", concert, user, session)


# ── Days ─────────────────────────────────────────────────────────────────


@router.post("/concerts/{concert_id}/days", response_class=HTMLResponse)
async def add_day(
    request: Request,
    concert_id: int,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
    label: str = Form(..., min_length=1, max_length=100),
    starts_at: str = Form(...),
):
    concert = await get_concert(session, concert_id)
    session.add(build_day(concert.id, label, starts_at))
    await session.flush()
    await sync_concert(session, concert.id)
    await session.commit()
    return await render_fragment(request, "_days.html", concert, user, session)


@router.post("/days/{day_id}/delete", response_class=HTMLResponse)
async def delete_day(
    request: Request,
    day_id: int,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
):
    day = await session.get(ConcertDay, day_id)
    if day is None:
        raise HTTPException(status_code=404)
    concert = await get_concert(session, day.concert_id)
    await session.delete(day)
    await session.flush()
    await sync_concert(session, concert.id)
    await session.commit()
    return await render_fragment(request, "_days.html", concert, user, session)


# ── Reminder rules (any signed-in user) ──────────────────────────────────


@router.post("/concerts/{concert_id}/rules", response_class=HTMLResponse)
async def add_rule(
    request: Request,
    concert_id: int,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    anchor: Anchor = Form(...),
    days_before: int = Form(..., ge=0, le=60),
):
    concert = await get_concert(session, concert_id)
    await ensure_user(session, user.id, user.username)
    rule = ReminderRule(
        user_id=user.id, concert_id=concert.id, anchor=anchor,
        offset_days=-days_before, channel=Channel.DM,
    )
    session.add(rule)
    await session.flush()
    await sync_rule(session, rule)
    await session.commit()
    return await render_fragment(request, "_rules.html", concert, user, session)


@router.post("/rules/{rule_id}/delete", response_class=HTMLResponse)
async def delete_rule(
    request: Request,
    rule_id: int,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    rule = await session.get(ReminderRule, rule_id)
    if rule is None or rule.user_id != user.id:
        raise HTTPException(status_code=404)  # not yours -> pretend it doesn't exist
    concert = await get_concert(session, rule.concert_id)
    await session.delete(rule)  # cascade removes its queue rows
    await session.commit()
    return await render_fragment(request, "_rules.html", concert, user, session)


# ── User settings ────────────────────────────────────────────────────────


@router.post("/me/timezone")
async def set_timezone(
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    timezone: str = Form(...),
):
    """Manual choice: sticks, and turns browser auto-detection off."""
    try:
        ZoneInfo(timezone)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"unknown timezone: {timezone}") from e
    db_user = await ensure_user(session, user.id, user.username)
    db_user.timezone = timezone
    db_user.tz_auto = False
    await session.commit()
    return RedirectResponse("/preferences", status_code=303)


@router.post("/me/timezone/auto")
async def set_timezone_auto(
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    timezone: str = Form(...),
):
    """Browser-detected timezone. Respected only while the user hasn't overridden."""
    try:
        ZoneInfo(timezone)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"unknown timezone: {timezone}") from e
    db_user = await ensure_user(session, user.id, user.username)
    if db_user.tz_auto:
        db_user.timezone = timezone
        await session.commit()
    return HTMLResponse("", status_code=204)


@router.post("/me/timezone/reset")
async def reset_timezone_auto(
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    """Back to auto: next page load re-detects from the browser."""
    db_user = await ensure_user(session, user.id, user.username)
    db_user.tz_auto = True
    await session.commit()
    return RedirectResponse("/preferences", status_code=303)
