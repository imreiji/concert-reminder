"""Concert CRUD + reminder rules — the web UI's working core.

Route conventions:
  * Mutations are POSTs. Page-level actions redirect (PRG pattern);
    list-level actions (windows/days/rules) are htmx fragment swaps —
    the server renders the updated list and htmx swaps it in place.
  * require_editor guards everything that changes concerts/days/windows.
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

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Concert, ConcertDay, ReminderRule, User, Window
from app.db.service import ensure_user, sync_concert, sync_rule
from app.db.session import get_session
from app.domain.timezones import jst_to_utc
from app.domain.types import Anchor, Channel, WindowKind
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


# ── Fragments (htmx swap targets) ────────────────────────────────────────


async def user_tz(session: AsyncSession, user_id: int) -> str:
    db_user = await session.get(User, user_id)
    return db_user.timezone if db_user else "America/Moncton"


async def render_fragment(request: Request, name: str, concert: Concert, user: SessionUser,
                          session: AsyncSession) -> HTMLResponse:
    await session.refresh(concert, ["days", "windows"])
    rules = await user_rules(session, user.id, concert.id)
    tz = await user_tz(session, user.id)
    return templates.TemplateResponse(
        request,
        name,
        {"concert": concert, "user": user, "rules": rules, "tz": tz,
         "kinds": list(WindowKind), "anchors": list(Anchor)},
    )


async def user_rules(session: AsyncSession, user_id: int, concert_id: int) -> list[ReminderRule]:
    res = await session.execute(
        select(ReminderRule).where(
            ReminderRule.user_id == user_id, ReminderRule.concert_id == concert_id
        )
    )
    return list(res.scalars())


# ── Concerts ─────────────────────────────────────────────────────────────


@router.post("/concerts")
async def create_concert(
    request: Request,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
    title: str = Form(..., min_length=1, max_length=200),
    franchise: str = Form(""),
    venue: str = Form(""),
):
    await ensure_user(session, user.id, user.username)
    concert = Concert(
        title=title.strip(), franchise=franchise.strip() or None,
        venue=venue.strip() or None, created_by=user.id,
    )
    session.add(concert)
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
    from app.domain.types import TagKind

    concert = await get_concert(session, concert_id)
    await session.refresh(concert, ["days", "windows", "tags"])
    rules = await user_rules(session, user.id, concert_id)
    tz = await user_tz(session, user.id)
    all_tags = list((await session.execute(select(Tag).order_by(Tag.kind, Tag.name))).scalars())
    return templates.TemplateResponse(
        request,
        "concert_detail.html",
        {"concert": concert, "user": user, "rules": rules, "tz": tz,
         "kinds": list(WindowKind), "anchors": list(Anchor),
         "all_tags": all_tags, "tag_kinds": list(TagKind)},
    )


@router.post("/concerts/{concert_id}/edit")
async def edit_concert(
    concert_id: int,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
    title: str = Form(..., min_length=1, max_length=200),
    franchise: str = Form(""),
    venue: str = Form(""),
    notes: str = Form(""),
):
    concert = await get_concert(session, concert_id)
    concert.title = title.strip()
    concert.franchise = franchise.strip() or None
    concert.venue = venue.strip() or None
    concert.notes = notes.strip() or None
    await session.commit()
    return RedirectResponse(f"/concerts/{concert_id}", status_code=303)


@router.post("/concerts/{concert_id}/delete")
async def delete_concert(
    concert_id: int,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
):
    concert = await get_concert(session, concert_id)
    await session.delete(concert)  # cascades: days, windows, rules, queue
    await session.commit()
    return RedirectResponse("/", status_code=303)


# ── Windows ──────────────────────────────────────────────────────────────


@router.post("/concerts/{concert_id}/windows", response_class=HTMLResponse)
async def add_window(
    request: Request,
    concert_id: int,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
    label: str = Form(..., min_length=1, max_length=200),
    kind: WindowKind = Form(WindowKind.OTHER),
    opens_at: str = Form(""),
    closes_at: str = Form(""),
    url: str = Form(""),
):
    concert = await get_concert(session, concert_id)
    opens, closes = parse_jst(opens_at), parse_jst(closes_at)
    if opens is None and closes is None:
        raise HTTPException(status_code=422, detail="a window needs at least one of opens/closes")
    session.add(Window(
        concert_id=concert.id, kind=kind, label=label.strip(),
        opens_at_utc=opens, closes_at_utc=closes, url=url.strip() or None,
    ))
    await session.flush()
    await sync_concert(session, concert.id)
    await session.commit()
    return await render_fragment(request, "_windows.html", concert, user, session)


@router.post("/windows/{window_id}/edit", response_class=HTMLResponse)
async def edit_window(
    request: Request,
    window_id: int,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
    label: str = Form(..., min_length=1, max_length=200),
    kind: WindowKind = Form(...),
    opens_at: str = Form(""),
    closes_at: str = Form(""),
    url: str = Form(""),
):
    window = await session.get(Window, window_id)
    if window is None:
        raise HTTPException(status_code=404)
    window.label = label.strip()
    window.kind = kind
    window.opens_at_utc = parse_jst(opens_at)
    window.closes_at_utc = parse_jst(closes_at)
    window.url = url.strip() or None
    concert = await get_concert(session, window.concert_id)
    await sync_concert(session, concert.id)
    await session.commit()
    return await render_fragment(request, "_windows.html", concert, user, session)


@router.post("/windows/{window_id}/delete", response_class=HTMLResponse)
async def delete_window(
    request: Request,
    window_id: int,
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
):
    window = await session.get(Window, window_id)
    if window is None:
        raise HTTPException(status_code=404)
    concert = await get_concert(session, window.concert_id)
    await session.delete(window)
    await session.flush()
    await sync_concert(session, concert.id)
    await session.commit()
    return await render_fragment(request, "_windows.html", concert, user, session)


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
    starts = parse_jst(starts_at)
    if starts is None:
        raise HTTPException(status_code=422, detail="a day needs a start time")
    session.add(ConcertDay(concert_id=concert.id, label=label.strip(), starts_at_utc=starts))
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
    return RedirectResponse("/", status_code=303)


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
    return RedirectResponse("/", status_code=303)
