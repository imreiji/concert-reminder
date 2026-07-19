"""Scheme validation for editor-supplied URLs.

Every URL an editor stores (a concert's official/eventernote/source link, a
venue tag's map link, a round's ticketing link) is rendered into an `href`.
`<input type="url">` accepts `javascript:alert(1)` -- it is a syntactically
valid absolute URL -- so a stored one would execute in-origin for whoever
clicks it. Only http/https with a real host get through here.

Pure string logic, like the rest of domain/: raises its own exception
(mirroring ingest.IngestError) and lets web/forms.py turn that into a 422,
so the bot layer can reuse it without dragging FastAPI in.
"""

from urllib.parse import urlsplit

ALLOWED_SCHEMES = frozenset({"http", "https"})

# The WHATWG URL parser trims C0 controls AND space from both ends, but
# only *deletes* tab/newline from the interior. We delete every C0 control
# from the interior (an attacker hides them mid-scheme -- "java\tscript:" --
# to slip past a naive prefix check) but deliberately NOT space: browsers
# percent-encode "https://ex.com/a b", they don't collapse it to ".../ab",
# and silently rewriting a valid URL into a different one is its own bug.
_EDGE_TRIM = "".join(chr(c) for c in range(0x00, 0x21))
_INTERIOR_DELETE = dict.fromkeys(range(0x00, 0x20))


class UnsafeURLError(Exception):
    """The value isn't an http(s) URL we're willing to put in an href."""


def clean_url(raw: str | None) -> str | None:
    """Normalize an editor-supplied URL, or raise UnsafeURLError.

    Returns None for empty/blank input (callers store NULL), otherwise the
    trimmed URL unchanged.
    """
    if raw is None:
        return None
    candidate = raw.strip(_EDGE_TRIM).translate(_INTERIOR_DELETE)
    if not candidate:
        return None
    parsed = urlsplit(candidate)
    # netloc guards scheme-relative "//evil.com" (empty scheme) and
    # "http:\\evil.com" (backslashes aren't a netloc separator here).
    if parsed.scheme.lower() not in ALLOWED_SCHEMES or not parsed.netloc:
        raise UnsafeURLError("links must be http:// or https:// URLs")
    return candidate
