"""The public privacy policy.

  GET /privacy

Deliberately NO require_user. Almost every route in this app is behind
auth, but a privacy policy that demands a login is useless: it exists so
that someone who has *not* signed up can decide whether to, and Discord's
app reviewers read it signed out. `/healthz` in web/app.py is the existing
precedent for a route with no session requirement.

The contact details come from settings (PRIVACY_CONTACT_DISCORD /
PRIVACY_CONTACT_EMAIL) rather than the template, so the owner's real handle
and address never enter the repository. Either, both, or neither may be
set: an unconfigured deploy still renders, with a neutral fallback line,
instead of 500ing on a page anonymous visitors can reach. That block is
shared with /terms and lives in app.web.contact.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.web.auth import SessionUser, current_user
from app.web.contact import contact_context

router = APIRouter()

templates = None  # set by web.app at startup

# The date this policy text last changed. Bump it whenever the wording below
# or in privacy.html changes in a way that affects what users are told.
LAST_UPDATED = "2026-07-19"


@router.get("/privacy", response_class=HTMLResponse)
async def privacy(
    request: Request,
    # current_user, not require_user -- an anonymous visitor resolves to
    # None and base.html renders its logged-out header.
    user: SessionUser | None = Depends(current_user),
):
    return templates.TemplateResponse(
        request,
        "privacy.html",
        {"user": user, "last_updated": LAST_UPDATED, **contact_context()},
    )
