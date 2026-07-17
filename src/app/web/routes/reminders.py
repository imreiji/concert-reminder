"""Per-user reminder-rule routes, split out of concerts.py.

  POST /concerts/{event_id}/rules    add a rule (htmx fragment swap)
  POST /rules/{rule_id}/delete       remove one of the caller's own rules

require_user guards both: adding/removing your own reminders isn't "editing
the concert" (that's require_editor territory in concerts.py). Both routes
render the shared `_rules.html` fragment via concerts.render_rules_fragment,
the same helper concerts.py's own concert-detail page and
preferences.apply_preset_to_concert use, so the fragment always reflects the
DB state right after the mutation.
"""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ReminderRule
from app.db.service import ensure_user, sync_rule
from app.db.session import get_session
from app.domain.types import Anchor, Channel
from app.web.auth import SessionUser, require_user
from app.web.routes.concerts import get_concert, get_concert_by_event_id, render_rules_fragment

router = APIRouter()


@router.post("/concerts/{event_id}/rules", response_class=HTMLResponse)
async def add_rule(
    request: Request,
    event_id: str,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    anchor: Anchor = Form(...),
    days_before: int = Form(..., ge=0, le=60),
):
    concert = await get_concert_by_event_id(session, event_id)
    await ensure_user(session, user.id, user.username)
    rule = ReminderRule(
        user_id=user.id, concert_id=concert.id, anchor=anchor,
        offset_days=-days_before, channel=Channel.DM,
    )
    session.add(rule)
    await session.flush()
    await sync_rule(session, rule)
    await session.commit()
    return await render_rules_fragment(request, concert, user, session)


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
    return await render_rules_fragment(request, concert, user, session)
