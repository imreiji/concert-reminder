"""Personal calendar-feed subscription: every deadline the user has an
active reminder for, as one subscribable .ics -- not a one-off download,
a URL calendar apps poll on their own schedule.

  POST /me/calendar-feed        (re)generate the feed token
  GET  /calendar/{token}.ics    the feed itself -- token-authenticated, no
                                 session/cookie (calendar apps fetch this
                                 directly, not through a browser)
"""

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.service import (
    generate_calendar_token,
    get_user_by_calendar_token,
    user_calendar_events,
)
from app.db.session import get_session
from app.domain.ics_export import build_calendar
from app.web.auth import SessionUser, require_user

router = APIRouter()


@router.post("/me/calendar-feed")
async def create_calendar_feed(
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    """Generating a new token invalidates any previously-issued feed URL
    (only the hash is stored, so the old raw token stops matching)."""
    token = await generate_calendar_token(session, user.id)
    await session.commit()
    return RedirectResponse(f"/preferences?feed_token={token}", status_code=303)


@router.get("/calendar/{token}.ics")
async def calendar_feed(
    token: str,
    session: AsyncSession = Depends(get_session),
):
    """No require_user here on purpose: calendar apps fetch this URL
    directly on their own polling schedule, with no cookies. The token
    itself is the credential -- same trust model as a session cookie,
    just carried in the URL instead."""
    user = await get_user_by_calendar_token(session, token)
    if user is None:
        raise HTTPException(status_code=404)
    events = await user_calendar_events(session, user.discord_id)
    text = build_calendar([
        (f"{e.concert_title} — {e.label}", e.at_utc, e.url, e.notes) for e in events
    ])
    return Response(content=text, media_type="text/calendar")
