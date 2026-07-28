"""The local rehearsal harness: seed a canonical concert, pull its reminders
forward, and send every DM shape on demand.

Registered ONLY when `settings.rehearsal_enabled` is true, which production
never sets -- see web/app.py. `require_admin` is a second layer for a
misconfigured deploy, not the primary guard.

Its own module rather than a section of admin.py: a router registers whole,
and admin.py serves /admin/deliveries and /admin/broadcast, which must exist
in production.

English-only and not wrapped in _(), following /me/test-dm and
/admin/deliveries -- this page only ever renders on a developer machine.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.web.auth import SessionUser, require_admin

router = APIRouter()

templates = None  # set by web.app at startup


@router.get("/admin/rehearsal", response_class=HTMLResponse)
async def rehearsal(
    request: Request,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    return templates.TemplateResponse(
        request, "rehearsal.html", {"user": user, "state": None}
    )
