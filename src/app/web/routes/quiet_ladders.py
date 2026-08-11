"""Round watch: which concerts in the catalogue have a ladder that has gone quiet.

  GET  /admin/quiet-ladders                    the worklist, plus one paste block
  POST /admin/quiet-ladders/{event_id}/checked stamp "I re-checked this"

Its own module and its own page rather than a section of /admin/discoveries.
That surface answers "what exists that you are not tracking"; this answers
"what changed about what you already track". discoveries.py's own docstring
argues for splitting on exactly this line, and a router registers whole.

This surface WRITES ONE THING: ladder_rechecked_at_utc. It never edits a
concert -- there is no update path back in (import answers 409 for a concert
that exists, invariant 6), so a re-check ends at the concert's edit page or at
an agent, which is what the copy block is for.

Copy is English-only and NOT wrapped in _(), like /admin/deliveries and
/admin/discoveries: an operational page only admins see should not cost msgids
in three languages. Only the Preferences link to it is translated.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.service import quiet_entry_from_row, quiet_ladder_rows, record_ladder_checked
from app.db.session import get_session
from app.domain.quiet_ladder_message import build_quiet_ladder_block
from app.web.auth import SessionUser, require_admin

router = APIRouter()

templates = None  # set by web.app at startup


@router.get("/admin/quiet-ladders", response_class=HTMLResponse)
async def quiet_ladders(
    request: Request,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    rows = await quiet_ladder_rows(session)
    block = build_quiet_ladder_block([quiet_entry_from_row(row) for row in rows])
    return templates.TemplateResponse(
        request,
        "admin_quiet_ladders.html",
        {"user": user, "rows": rows, "copy_text": block},
    )


@router.post("/admin/quiet-ladders/{event_id}/checked")
async def mark_checked(
    event_id: str,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    if not await record_ladder_checked(session, event_id):
        raise HTTPException(status_code=404, detail="no such concert")
    await session.commit()
    return RedirectResponse("/admin/quiet-ladders", status_code=303)
