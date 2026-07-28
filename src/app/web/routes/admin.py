"""Admin-only operational surfaces: the delivery log, and the broadcast.

  GET  /admin/deliveries               recent failures + the batch list
  GET  /admin/deliveries/{batch_iso}   one batch expanded to its recipients
  GET  /admin/broadcast                compose a broadcast
  POST /admin/broadcast/preview        resolve and render it -- writes NOTHING

This is the ONLY surface that names delivery recipients. The digest DM
deliberately reports counts, because a name in Discord history is a permanent
record of who follows which artists that POST /me/delete cannot reach; here it
sits behind require_admin, inside the app's own deletion story, on the 30-day
retention window.

Copy is English-only and NOT wrapped in _(), following /me/test-dm: an
operational page only admins see should not cost msgids in three languages
(tests/test_i18n_catalogues.py would enforce them).
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.service import (
    BROADCAST_BODY_MAX,
    DELIVERY_LOG_RETENTION_DAYS,
    TYPED_CONFIRM_THRESHOLD,
    delivery_batch_rows,
    delivery_batches,
    delivery_failures,
    duplicate_body_recently,
    recent_broadcasts,
    resolve_recipients,
)
from app.db.session import get_session
from app.domain.types import BroadcastMode
from app.web.auth import SessionUser, require_admin

router = APIRouter()

templates = None  # set by web.app at startup


async def _context(session: AsyncSession, user: SessionUser, batch: datetime | None) -> dict:
    """The whole page in one dict. Both routes render the same template: the
    failure list and the batch list are the page, and opening a batch only
    appends a third section rather than navigating away from them."""
    return {
        "user": user,
        "failures": await delivery_failures(session),
        "batches": await delivery_batches(session),
        "rows": await delivery_batch_rows(session, batch) if batch else None,
        "batch": batch,
        "retention_days": DELIVERY_LOG_RETENTION_DAYS,
    }


@router.get("/admin/deliveries", response_class=HTMLResponse)
async def deliveries(
    request: Request,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    return templates.TemplateResponse(
        request, "admin_deliveries.html", await _context(session, user, None)
    )


@router.get("/admin/deliveries/{batch_iso}", response_class=HTMLResponse)
async def delivery_batch(
    request: Request,
    batch_iso: str,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    try:
        batch = datetime.fromisoformat(batch_iso)
    except ValueError:
        # A malformed timestamp is a bad link, not a server fault.
        raise HTTPException(status_code=404, detail="no such batch") from None
    return templates.TemplateResponse(
        request, "admin_deliveries.html", await _context(session, user, batch)
    )


@router.get("/admin/broadcast", response_class=HTMLResponse)
async def broadcast_compose(
    request: Request,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    return templates.TemplateResponse(
        request,
        "admin_broadcast.html",
        {
            "user": user,
            "past": await recent_broadcasts(session),
            "preview": None,
            "status": None,
            "body_max": BROADCAST_BODY_MAX,
            "bot_enabled": settings.bot_enabled,
        },
    )


@router.post("/admin/broadcast/preview", response_class=HTMLResponse)
async def broadcast_preview(
    request: Request,
    mode: str = Form(...),
    mode_param: str = Form(""),
    body: str = Form(...),
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Resolves and renders. Writes NOTHING -- the outbox is untouched until
    the admin confirms from this screen.

    No try/except around resolve_recipients: it is total by construction (a
    malformed batch timestamp comes back as zero recipients with the offending
    text in `unmatched`), so there is no failure mode here to translate.
    """
    try:
        chosen = BroadcastMode(mode)
    except ValueError:
        raise HTTPException(status_code=422, detail="unknown broadcast mode") from None
    text = body.strip()
    if not text:
        raise HTTPException(status_code=422, detail="broadcast body is empty")
    if len(text) > BROADCAST_BODY_MAX:
        raise HTTPException(
            status_code=422,
            detail=f"body exceeds {BROADCAST_BODY_MAX} characters",
        )

    recipients = await resolve_recipients(session, chosen, mode_param or None)
    return templates.TemplateResponse(
        request,
        "admin_broadcast.html",
        {
            "user": user,
            "past": await recent_broadcasts(session),
            "status": None,
            "body_max": BROADCAST_BODY_MAX,
            "bot_enabled": settings.bot_enabled,
            "preview": {
                "mode": chosen.value,
                "mode_param": mode_param,
                "body": text,
                "count": len(recipients.ids),
                "unmatched": recipients.unmatched,
                "needs_typed_confirm": len(recipients.ids) > TYPED_CONFIRM_THRESHOLD,
                "duplicate": await duplicate_body_recently(session, text),
            },
        },
    )
