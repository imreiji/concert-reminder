"""The public terms of service.

  GET /terms

The counterpart to /privacy: Discord's Developer Portal takes a Terms of
Service URL alongside the Privacy Policy URL. Same reachability rule --
deliberately NO require_user, because terms nobody can read without an
account are useless, and Discord's app reviewers read them signed out.

Contact details come from app.web.contact, shared with the privacy page, so
the owner's real handle and address never enter the repository.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.web.auth import SessionUser, current_user
from app.web.contact import contact_context

router = APIRouter()

templates = None  # set by web.app at startup

# The date this text last changed. Bump it whenever the wording in
# terms.html changes in a way that affects what users are promised.
LAST_UPDATED = "2026-07-19"


@router.get("/terms", response_class=HTMLResponse)
async def terms(
    request: Request,
    # current_user, not require_user -- an anonymous visitor resolves to
    # None and base.html renders its logged-out header.
    user: SessionUser | None = Depends(current_user),
):
    return templates.TemplateResponse(
        request,
        "terms.html",
        {"user": user, "last_updated": LAST_UPDATED, **contact_context()},
    )
