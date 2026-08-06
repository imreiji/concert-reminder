"""Which websites the AI draft-completion pass may read.

  GET  /admin/fetch-domains                    every host it has wanted, and its verdict
  POST /admin/fetch-domains/{id}/approve       yes, read pages from this host
  POST /admin/fetch-domains/{id}/decline       no, and stop asking

Its own module rather than a section of `admin.py`, for the reason
`discoveries.py` is its own: a router registers whole, and this is a fifth
unrelated operational concern beside the delivery log, the broadcast, the
catalogue round-trip and the discovery queue.

WHY A HUMAN IS IN THIS LOOP. Every other fetch this app makes is pinned to one
host named in code. A draft's `official_url` cannot be -- an official page is
by definition somebody else's domain -- so the pin is replaced by a person: a
host is fetched only after an admin has approved it by name, and a redirect
off an approved host onto an unapproved one is refused on the hop
(`fetching.ApprovedPublicHosts`). A declined host is never proposed again,
because an approval queue that keeps re-asking becomes one nobody reads.

English-only and NOT wrapped in `_()`, exactly like /admin/deliveries and
/admin/discoveries; only the Preferences LINK is translated.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.service import decide_fetch_domain, fetch_domain_rows
from app.db.session import get_session
from app.web.auth import SessionUser, require_admin

router = APIRouter()

templates = None  # set by web/app.py, as every other router here does it


@router.get("/admin/fetch-domains", response_class=HTMLResponse)
async def fetch_domains_page(
    request: Request,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    return templates.TemplateResponse(
        request,
        "admin_fetch_domains.html",
        {"user": user, "rows": await fetch_domain_rows(session)},
    )


async def _decide(
    session: AsyncSession, domain_id: int, approve: bool, user: SessionUser
) -> RedirectResponse:
    """One verdict, or a 404. Shared by both POSTs so they cannot drift on
    what an already-decided host does -- `decide_fetch_domain` returns False
    for unknown AND already-decided, and both mean "there is nothing here to
    decide", which is a 404 either way (the same rule `dismiss_lead`'s caller
    in routes/discoveries.py follows for the identical reason: reporting a
    write that did not happen as a cheerful 303 is how a double-submit looks
    like it worked)."""
    if not await decide_fetch_domain(session, domain_id, approve, datetime.now(UTC), user.id):
        raise HTTPException(status_code=404, detail="no such host")
    await session.commit()
    return RedirectResponse("/admin/fetch-domains", status_code=303)


@router.post("/admin/fetch-domains/{domain_id}/approve")
async def approve_domain(
    domain_id: int,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    return await _decide(session, domain_id, True, user)


@router.post("/admin/fetch-domains/{domain_id}/decline")
async def decline_domain(
    domain_id: int,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    return await _decide(session, domain_id, False, user)
