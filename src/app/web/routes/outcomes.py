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
"""

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

    # No JS: the forms carry a real method/action, so the browser navigates
    # here and would render a bare fragment as the whole document. Send it
    # back to Home instead -- the write is already committed, so nothing is
    # lost. 303 so a reload does not re-POST.
    if request.headers.get("HX-Request") != "true":
        return RedirectResponse("/", status_code=303)

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
