"""Scheme validation for every editor-supplied link.

`<input type="url">` does NOT reject `javascript:alert(1)` -- it is a
syntactically valid absolute URL, so it sails through both the browser and
any server-side "is this a URL" check. These values land in `href`
attributes on pages other signed-in users load, so a stored `javascript:`
(or `data:`) URI executes script in dekimasen.app's own origin. The scheme
is therefore a security boundary, not a formatting preference.

Pure string logic, no I/O: the web layer catches UnsafeUrlError and turns
it into an HTTP 422, the same way it already surfaces bad tag ids.
"""

from urllib.parse import urlparse

ALLOWED_SCHEMES = frozenset({"http", "https"})

# Browsers ignore ASCII whitespace and control characters when resolving a
# URL's scheme, so `java\tscript:`, `\njavascript:` and ` javascript:` all
# navigate to the same script URI a bare `javascript:` would. Stripping the
# whole \x00-\x20 range before parsing means we validate exactly the string
# the browser will act on -- a plain .strip() would not.
_CONTROL_CHARS = {c: None for c in range(0x21)}


class UnsafeUrlError(ValueError):
    """A submitted URL uses a scheme that must never reach an href."""


def clean_url(raw: str | None) -> str | None:
    """Normalize a submitted URL, or raise if it isn't http(s).

    Returns None for empty/blank input (the columns are all nullable), and
    otherwise the normalized string -- which is byte-identical to the input
    for any real URL, since valid URLs contain no raw control characters.
    """
    if raw is None:
        return None
    value = raw.translate(_CONTROL_CHARS)
    if not value:
        return None
    parsed = urlparse(value)
    # urlparse lowercases the scheme, and leaves it empty for relative and
    # scheme-relative ("//evil.com") inputs -- both correctly fail this.
    if parsed.scheme not in ALLOWED_SCHEMES or not parsed.netloc:
        raise UnsafeUrlError(f"{raw.strip()!r} is not a valid http:// or https:// URL")
    return value
