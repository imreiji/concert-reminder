"""Recording lottery progress from the web.

  POST /rounds/{round_id}/outcome    record one outcome, re-render Home's
                                     three affected fragments

The web counterpart to the DM buttons in `bot/views.py`
(`_handle_outcome_click`). Both funnel into the SAME `record_round_outcome`,
which owns the whole sequence rule AND re-syncs the user's rules for the
round's concert -- a second write path would silently desync the reminder
queue (invariant 2). So this module deliberately holds no business logic: it
resolves the caller, hands off, commits, and re-renders.

Two things the service intentionally does NOT do, which the route therefore
must:
  - a missing round makes `record_round_outcome` return silently, so the route
    checks existence itself rather than reporting a success that never wrote.
  - it takes `user_id` as an argument, so the route must supply it from the
    SESSION. There is no user field on this form; two users acting on the same
    round keep entirely separate RoundOutcome rows.

Rendering: recording an outcome changes THREE things on Home -- the Coming up
row, the board card's column, and the tally above the board. The row is the
declared hx-target; the other two ride along as out-of-band swaps of
`#board` and `#board-summary`, re-rendering the very partials Home includes
so there is one source for each. Returning the row alone (which is what
shipped) is the worst outcome available: the press looks like it worked while
the card sits in its old column until a reload.

TWO surfaces now press these buttons: Home's rows and the concert page's
per-leg round rows, which share the `_capture_actions.html` macro. They need
different answers -- sending Home's fragments back to the concert page would
splice Home's content into it, and the `#board` out-of-band swap would hit
nothing there, which htmx treats as a silent no-op.

Which surface asked is read from `HX-Current-URL` (htmx sends it on every
request) rather than from a hidden field, so the shared macro stays free of
per-surface plumbing and a future third surface needs no change to it. The
JS-less path has no such header and falls back to `Referer`; when even that
is missing it redirects to Home exactly as it always did -- the write is
already committed either way, so the worst case is landing on the wrong page,
never a lost press.
"""

import re
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Round, User
from app.db.service import (
    board_cards,
    ensure_user,
    my_deadline_rows,
    record_round_outcome,
    tracked_concert_ids,
)
from app.db.session import get_session
from app.domain.types import LotteryOutcome
from app.web.auth import SessionUser, require_user

router = APIRouter()

templates = None  # injected by web/app.py, same as the other route modules

# The concert page's own URL. event_id is restricted to this charset at
# creation (EVENT_ID_RE in routes/concerts.py), so anything that matches here
# is a plausible handle. A handle that matches but names no concert makes the
# lookup below raise 404 -- AFTER record_round_outcome has committed, so the
# press is recorded and only the re-render is lost. Left as-is deliberately:
# reaching it means the header named a concert page that does not exist, which
# a reader cannot do (that page 404s), so a quiet fallback to Home would only
# hide a forged or stale header behind a screen that looks like it worked.
_CONCERT_PATH = re.compile(r"^/concerts/([A-Za-z0-9_-]{1,100})/?$")


def _concert_event_id(request: Request) -> str | None:
    """Which concert page this press came from, or None for anywhere else.

    HX-Current-URL first because htmx always sends it and it is the only one
    of the two that survives a referrer policy; Referer second so the JS-less
    form post still returns the reader to where they were."""
    for raw in (request.headers.get("HX-Current-URL"), request.headers.get("Referer")):
        if not raw:
            continue
        path = urlsplit(raw).path
        m = _CONCERT_PATH.match(path)
        if m:
            return m.group(1)
    return None


@router.post("/rounds/{round_id}/outcome", response_class=HTMLResponse)
async def record_outcome(
    request: Request,
    round_id: int,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    # Typing the form field as the enum gets the 422 for a bad value for free.
    outcome: LotteryOutcome = Form(...),
):
    round_ = await session.get(Round, round_id)
    if round_ is None:
        raise HTTPException(status_code=404)
    # RoundOutcome.user_id is an FK to users.discord_id; login creates the row,
    # but ensure_user keeps a stale session from turning into a 500.
    await ensure_user(session, user.id, user.username)
    await record_round_outcome(session, user.id, round_id, outcome)
    await session.commit()

    event_id = _concert_event_id(request)

    # No JS: the forms carry a real method/action, so the browser navigates
    # here and would render a bare fragment as the whole document. Send it
    # back where it came from instead -- the write is already committed, so
    # nothing is lost. 303 so a reload does not re-POST.
    if request.headers.get("HX-Request") != "true":
        return RedirectResponse(f"/concerts/{event_id}" if event_id else "/", status_code=303)

    # Pressed on a concert page: answer with THAT page's rounds region and
    # nothing else. There is no board and no deadline-rows list there for the
    # out-of-band swaps below to land on.
    if event_id is not None:
        from app.web.routes.concerts import concert_rounds_context, get_concert_by_event_id

        # No relationship refresh: `concert_rounds_context` queries the days
        # and rounds it needs directly, and the fragment touches no attribute
        # of `concert` itself -- so there is nothing here to lazy-load into a
        # MissingGreenlet during async rendering.
        concert = await get_concert_by_event_id(session, event_id)
        db_user = await session.get(User, user.id)
        return HTMLResponse(templates.get_template("_round_rows.html").render(
            request=request,
            user=user,
            tz=db_user.timezone if db_user else settings.default_timezone,
            **await concert_rounds_context(session, user.id, concert),
        ))

    # No explicit limit here, and none on Home either: both take
    # my_deadline_rows' DEADLINE_ROWS_LIMIT default, which is the only thing
    # keeping this swap from silently changing how many rows the page shows.
    #
    # tracked_concert_ids is resolved ONCE and shared by both queries below,
    # exactly as GET / does -- the rows and the board are two views of the
    # same tracked set.
    tracked = await tracked_concert_ids(session, user.id)
    rows = await my_deadline_rows(session, user.id, concert_ids=tracked)
    db_user = await session.get(User, user.id)
    tz = db_user.timezone if db_user else settings.default_timezone

    # Column is a StrEnum, and an Enum member does not hash equal to its
    # value, so a template doing columns["open"] would silently miss. Re-key
    # at the boundary, exactly as GET / does.
    columns, open_total = await board_cards(session, user.id, concert_ids=tracked)
    ctx = {
        "user": user,
        "rows": rows,
        "tz": tz,
        "columns": {col.value: cards for col, cards in columns.items()},
        "open_total": open_total,
        "oob": True,
    }
    fragments = [
        templates.get_template(name).render(request=request, **ctx)
        for name in ("_deadline_rows.html", "_board.html", "_board_summary.html")
    ]
    # One response, three top-level fragments: the first is the hx-target
    # swap, the other two carry hx-swap-oob. htmx only honours OOB elements at
    # the top level of the response body, so do not wrap this.
    return HTMLResponse("\n".join(fragments))
