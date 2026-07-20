"""Per-concert following and per-leg opt-out from the web.

  POST /concerts/{event_id}/subscription           follow / unfollow a concert
  POST /concerts/{event_id}/legs/{day_id}/opt-out  skip / un-skip one leg

Both are thin shells over Task 2's writers -- `set_concert_subscription`,
`clear_concert_subscription`, `set_leg_opt_out` -- which own the whole
override model AND the invariant-2 reminder-queue resync (the subscription
writers re-plan this user's rules; the leg writer feeds a read-side planner
pass, so it needs no resync of its own). This module holds no business logic:
it resolves the caller from the SESSION, checks the concert/day exists, hands
off, commits, and re-renders. There is no user field on either form -- two
users acting on the same concert keep entirely separate override rows.

Rendering mirrors the outcome route's surface split. An htmx press gets the
re-rendered fragment (the Following toggle for a subscription change, the whole
rounds region for a leg opt-out, since dimming a leg is a region-wide change).
A JS-less post carries a real method/action, so the browser navigates here and
would render a bare fragment as the whole document -- send it back where it
came from instead (Referer path, falling back to the concert page). The write
is already committed either way, so the worst case is landing on the wrong
page, never a lost press.

The heavy WON/PAID confirmation lives entirely in the template as a client-side
<dialog> gate: this route performs the opt-out unconditionally and never
deletes the RoundOutcome (spec decision 3). Requiring the outcome to be gone
first would make forfeiting a won ticket a two-step chore instead of a
one-press, confirmed decision.
"""

from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import ConcertDay, User
from app.db.service import (
    clear_concert_subscription,
    ensure_user,
    set_concert_subscription,
    set_leg_opt_out,
)
from app.db.session import get_session
from app.domain.types import SubscriptionState
from app.web.auth import SessionUser, require_user

router = APIRouter()

templates = None  # injected by web/app.py, same as the other route modules


def _redirect_target(request: Request, event_id: str) -> str:
    """Where a JS-less post returns to: the page it came from (Referer path
    only, never the full URL -- an off-site or scheme-bearing value could
    become an open redirect), falling back to this concert's page. From Home's
    "Skip this concert entirely" that is Home; from the concert page toggle it
    is the concert page."""
    ref = request.headers.get("Referer")
    if ref:
        path = urlsplit(ref).path
        if path.startswith("/"):
            return path
    return f"/concerts/{event_id}"


@router.post("/concerts/{event_id}/subscription", response_class=HTMLResponse)
async def set_subscription(
    request: Request,
    event_id: str,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    # subscribed / opted_out = an explicit override; default = clear it (back
    # to the tag default). Not typed as SubscriptionState because "default" is
    # the absence of a row, not a member.
    state: str = Form(...),
):
    from app.web.routes.concerts import (
        following_toggle_context,
        get_concert_by_event_id,
    )

    concert = await get_concert_by_event_id(session, event_id)  # 404 on a bad handle
    # ConcertSubscription.user_id is an FK to users.discord_id; login creates
    # the row, but ensure_user keeps a stale session from turning into a 500.
    await ensure_user(session, user.id, user.username)
    if state == "opted_out":
        await set_concert_subscription(session, user.id, concert.id, SubscriptionState.OPTED_OUT)
    elif state == "subscribed":
        await set_concert_subscription(session, user.id, concert.id, SubscriptionState.SUBSCRIBED)
    elif state == "default":
        await clear_concert_subscription(session, user.id, concert.id)
    else:
        raise HTTPException(status_code=422, detail=f"bad state: {state!r}")
    await session.commit()

    if request.headers.get("HX-Request") != "true":
        return RedirectResponse(_redirect_target(request, event_id), status_code=303)

    db_user = await session.get(User, user.id)
    return HTMLResponse(templates.get_template("_following_toggle.html").render(
        request=request,
        user=user,
        tz=db_user.timezone if db_user else settings.default_timezone,
        **await following_toggle_context(session, user.id, concert),
    ))


@router.post("/concerts/{event_id}/legs/{day_id}/opt-out", response_class=HTMLResponse)
async def set_leg_subscription(
    request: Request,
    event_id: str,
    day_id: int,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    opted_out: str = Form(...),
):
    from app.web.routes.concerts import (
        concert_rounds_context,
        get_concert_by_event_id,
    )

    concert = await get_concert_by_event_id(session, event_id)  # 404 on a bad handle
    day = await session.get(ConcertDay, day_id)
    # A day that belongs to a DIFFERENT concert 404s as surely as a missing
    # one: the url names this concert, and a leg it does not own is not a leg.
    if day is None or day.concert_id != concert.id:
        raise HTTPException(status_code=404, detail="leg not found")
    await ensure_user(session, user.id, user.username)
    await set_leg_opt_out(session, user.id, day_id, opted_out == "true")
    await session.commit()

    if request.headers.get("HX-Request") != "true":
        return RedirectResponse(_redirect_target(request, event_id), status_code=303)

    db_user = await session.get(User, user.id)
    return HTMLResponse(templates.get_template("_round_rows.html").render(
        request=request,
        user=user,
        tz=db_user.timezone if db_user else settings.default_timezone,
        **await concert_rounds_context(session, user.id, concert),
    ))
