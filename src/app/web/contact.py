"""Contact details for the two public legal pages (/privacy and /terms).

Both pages end with the same "how to reach the operator" block, and both
must render on a deploy that has configured neither contact -- they are the
only pages an anonymous visitor can reach, so a 500 here is a 500 in front
of Discord's app reviewers.

The values come from settings (PRIVACY_CONTACT_DISCORD /
PRIVACY_CONTACT_EMAIL), never from a template or a literal, so the owner's
real handle and address never enter the repository.

This lives here rather than in routes/privacy.py so routes/terms.py can use
it without importing its sibling page, and rather than in domain/ because
it reads settings.
"""

from app.config import settings


def is_plain_email(value: str) -> bool:
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


def contact_context() -> dict:
    """The three template keys the shared contact block needs. Either, both,
    or neither contact may be set; the templates branch on the empty case."""
    email = settings.privacy_contact_email.strip()
    return {
        "contact_discord": settings.privacy_contact_discord.strip(),
        "contact_email": email,
        "contact_email_is_linkable": is_plain_email(email),
    }
