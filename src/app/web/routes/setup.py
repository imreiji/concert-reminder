"""First-run capture flow (/setup), run AFTER the /welcome wizard.

Three plain GET screens, each rendering PURELY from DB truth, plus two batch
POSTs:

  GET  /setup               screen 1 -- prune the concerts your tags imply
  POST /setup/prune         write the prune selection, 303 -> applications
  GET  /setup/applications  screen 2 -- record rounds you already applied to
  POST /setup/applications  record APPLIED, 303 -> ready
  GET  /setup/ready         screen 3 -- the board-ready reveal

There is NO capture-flow step state anywhere -- that is what makes the flow
tamper-safe (nothing reads a step from user input) and re-runnable (every
screen renders current truth, every write is an idempotent diff). `GET /setup`
is also the target of Preferences' "Run first-time setup again" link. All the
logic lives in db/service.py; this module is a thin shell that resolves the
caller from the SESSION, delegates, commits, and renders.
"""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.service import (
    apply_prune_selection,
    ensure_user,
    record_setup_applications,
    setup_application_rows,
    setup_prune_tiles,
    setup_tallies,
)
from app.db.session import get_session
from app.web.auth import SessionUser, require_user

router = APIRouter()

templates = None  # injected by web/app.py, same as the other route modules


@router.get("/setup", response_class=HTMLResponse)
async def setup_prune(
    request: Request,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    db_user = await ensure_user(session, user.id, user.username)
    tiles = await setup_prune_tiles(session, user.id)
    because = sorted({name for t in tiles for name in t.because})
    return templates.TemplateResponse(request, "setup.html", {
        "user": user, "screen": "prune", "tiles": tiles,
        "because": because, "tz": db_user.timezone,
    })


@router.post("/setup/prune")
async def setup_prune_submit(
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    keep: list[int] = Form([]),
    shown: list[int] = Form([]),
):
    await ensure_user(session, user.id, user.username)
    await apply_prune_selection(session, user.id, set(shown), set(keep))
    await session.commit()
    return RedirectResponse("/setup/applications", status_code=303)


@router.get("/setup/applications", response_class=HTMLResponse)
async def setup_applications(
    request: Request,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    db_user = await ensure_user(session, user.id, user.username)
    rows = await setup_application_rows(session, user.id)
    return templates.TemplateResponse(request, "setup.html", {
        "user": user, "screen": "applications", "rows": rows, "tz": db_user.timezone,
    })


@router.post("/setup/applications")
async def setup_applications_submit(
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    applied: list[int] = Form([]),
):
    await ensure_user(session, user.id, user.username)
    await record_setup_applications(session, user.id, set(applied))
    await session.commit()
    return RedirectResponse("/setup/ready", status_code=303)


@router.get("/setup/ready", response_class=HTMLResponse)
async def setup_ready(
    request: Request,
    user: SessionUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    db_user = await ensure_user(session, user.id, user.username)
    tallies = await setup_tallies(session, user.id)
    return templates.TemplateResponse(request, "setup.html", {
        "user": user, "screen": "ready", "tallies": tallies, "tz": db_user.timezone,
    })
