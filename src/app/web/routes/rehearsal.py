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

  GET  /admin/rehearsal              the walk, the actions, and the state table
  POST /admin/rehearsal/start        seed (or reseed) the canonical scenario
  POST /admin/rehearsal/next         pull the soonest unsent reminder forward
  POST /admin/rehearsal/cancel-show  cancel every live leg -> leg_cancelled DM
  POST /admin/rehearsal/end          delete the concert; cascades take the rest

Every action is a plain form POST answering 303 back to the page: each one
rewrites the state table wholesale, and there is nothing here worth an htmx
fragment on a page only its own author ever loads.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.service import (
    cancel_rehearsal_show,
    get_rehearsal_concert,
    pull_rehearsal_forward,
    rehearsal_rows,
    seed_rehearsal,
    teardown_rehearsal,
)
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
        request,
        "rehearsal.html",
        {
            "user": user,
            "concert": await get_rehearsal_concert(session),
            "rows": await rehearsal_rows(session, user.id),
        },
    )


@router.post("/admin/rehearsal/start")
async def start(
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Idempotent: seed_rehearsal tears down any previous rehearsal first, so
    pressing this twice leaves one concert rather than two."""
    await seed_rehearsal(session, user.id)
    await session.commit()
    return RedirectResponse("/admin/rehearsal", status_code=303)


@router.post("/admin/rehearsal/next")
async def next_reminder(
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """No queue id crosses this boundary -- the row is resolved through the
    rehearsal concert, so no other concert's reminder is reachable from here."""
    await pull_rehearsal_forward(session)
    await session.commit()
    return RedirectResponse("/admin/rehearsal", status_code=303)


@router.post("/admin/rehearsal/cancel-show")
async def cancel_show(
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Terminal, and the template says so: this kills every remaining queue
    row, the upgrade round's included."""
    await cancel_rehearsal_show(session)
    await session.commit()
    return RedirectResponse("/admin/rehearsal", status_code=303)


@router.post("/admin/rehearsal/end")
async def end(
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    await teardown_rehearsal(session)
    await session.commit()
    return RedirectResponse("/admin/rehearsal", status_code=303)
