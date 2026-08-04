"""Personal calendar-feed subscription: every tracked concert's show dates
plus the deadlines that still need the user, selected by their standing
(see `user_calendar_events`), as one subscribable .ics -- not a one-off
download, a URL calendar apps poll on their own schedule.

  POST /me/calendar-feed        (re)generate the feed token
  GET  /calendar/{token}.ics    the feed itself -- token-authenticated, no
                                 session/cookie (calendar apps fetch this
                                 directly, not through a browser)
"""

from fastapi import APIRouter, Depends, Form, HTTPException, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.service import (
    generate_calendar_token,
    get_user_by_calendar_token,
    user_calendar_events,
)
from app.db.session import get_session
from app.domain.ics_export import CANONICAL_ANCHOR_QUALIFIERS, build_calendar
from app.domain.urls import safe_next
from app.web.auth import SessionUser, require_user

router = APIRouter()

_ALLOWED_NEXT = {"/preferences", "/welcome"}


def _allowed_next(raw: str) -> str:
    """Where the mint may bounce back to. safe_next FIRST (the standing
    open-redirect guard: same-origin path or None), then an allowlist of
    shapes rather than of literal paths -- the concert page is the third
    surface that mints, and hardcoding every concert is not a list anyone
    maintains. Anything else falls back to /preferences, as always.

    The mint route appends `?feed_token=` to whatever this returns, so a
    `next` carrying its own query would produce an unparseable
    double-query URL and the one-time reveal would never render -- the
    token still regenerates, silently wasting it. A `..` segment could
    likewise normalize away from the page that renders the reveal. Both
    are stripped/rejected here, after safe_next, so every caller gets a
    clean single-query destination."""
    path = safe_next(raw)
    if path is None:
        return "/preferences"
    path = path.split("?", 1)[0]
    if ".." in path:
        return "/preferences"
    if path in _ALLOWED_NEXT or path.startswith("/concerts/"):
        return path
    return "/preferences"


@router.post("/me/calendar-feed")
async def create_calendar_feed(
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    next_url: str = Form("/preferences", alias="next"),
):
    """Generating a new token invalidates any previously-issued feed URL
    (only the hash is stored, so the old raw token stops matching)."""
    token = await generate_calendar_token(session, user.id)
    await session.commit()
    destination = _allowed_next(next_url)
    return RedirectResponse(f"{destination}?feed_token={token}", status_code=303)


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

    def _summary(e):
        qual = CANONICAL_ANCHOR_QUALIFIERS.get(e.anchor)
        base = f"{e.concert_title} — {e.label}"
        return f"{base} · {qual}" if qual else base

    text = build_calendar([
        (_summary(e), e.at_utc, e.url, e.notes) for e in events
    ])
    return Response(content=text, media_type="text/calendar")
