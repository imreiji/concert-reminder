"""Recording lottery progress from the web.

  POST /rounds/{round_id}/outcome    record one outcome, re-render the rows

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
"""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Round, User
from app.db.service import ensure_user, my_deadline_rows, record_round_outcome
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

    # No explicit limit here, and none on Home either: both take
    # my_deadline_rows' DEADLINE_ROWS_LIMIT default, which is the only thing
    # keeping this swap from silently changing how many rows the page shows.
    rows = await my_deadline_rows(session, user.id)
    db_user = await session.get(User, user.id)
    tz = db_user.timezone if db_user else settings.default_timezone
    return templates.TemplateResponse(
        request, "_deadline_rows.html", {"user": user, "rows": rows, "tz": tz},
    )
