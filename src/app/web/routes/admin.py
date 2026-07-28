"""Admin-only operational reader for the delivery log.

  GET /admin/deliveries               recent failures + the batch list
  GET /admin/deliveries/{batch_iso}   one batch expanded to its recipients

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

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.service import (
    DELIVERY_LOG_RETENTION_DAYS,
    delivery_batch_rows,
    delivery_batches,
    delivery_failures,
)
from app.db.session import get_session
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
