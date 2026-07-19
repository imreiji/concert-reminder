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
instead of 500ing on a page anonymous visitors can reach.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.config import settings
from app.web.auth import SessionUser, current_user

router = APIRouter()

templates = None  # set by web.app at startup

# The date this policy text last changed. Bump it whenever the wording below
# or in privacy.html changes in a way that affects what users are told.
LAST_UPDATED = "2026-07-19"


def _is_plain_email(value: str) -> bool:
    """Cheap sanity check before a config value becomes a `mailto:` href.

    Not RFC validation -- just enough that a misconfigured or hostile value
    ("javascript:alert(1)", a value with a space or a second scheme in it)
    renders as inert text instead of a link. Autoescaping still applies to
    the text either way; this only decides link vs. no link.
    """
    if value.count("@") != 1:
        return False
    local, _, domain = value.partition("@")
    if not local or not domain or "." not in domain:
        return False
    return not any(c in value for c in ' \t\n\r<>":/\\,;')


@router.get("/privacy", response_class=HTMLResponse)
async def privacy(
    request: Request,
    # current_user, not require_user -- an anonymous visitor resolves to
    # None and base.html renders its logged-out header.
    user: SessionUser | None = Depends(current_user),
):
    discord_handle = settings.privacy_contact_discord.strip()
    email = settings.privacy_contact_email.strip()
    return templates.TemplateResponse(
        request,
        "privacy.html",
        {
            "user": user,
            "contact_discord": discord_handle,
            "contact_email": email,
            "contact_email_is_linkable": _is_plain_email(email),
            "last_updated": LAST_UPDATED,
        },
    )
