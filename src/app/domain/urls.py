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
    URL with C0 controls trimmed from both ends and deleted from the
    interior -- so the value returned can differ from the input by more
    than whitespace trimming (see the module comment above).
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


# A return path lands in a Location header after login, so the whole job here
# is refusing anything that could point off-origin. Long values are dropped
# rather than truncated -- the value rides in the session cookie, and half a
# path is not a better destination than the default.
_MAX_NEXT_LENGTH = 512


def safe_next(raw: str | None) -> str | None:
    """Reduce a post-login return target to a same-origin path, or None.

    Returns None (caller falls back to "/") for anything that isn't a plain
    absolute-from-root path: an absolute URL, a scheme-relative one, a bare
    relative segment. Unlike clean_url this never raises -- a bad `next` is a
    stale or hostile link, not an editor mistake worth a 422.
    """
    if not raw:
        return None
    candidate = raw.strip(_EDGE_TRIM).translate(_INTERIOR_DELETE)
    if len(candidate) > _MAX_NEXT_LENGTH or not candidate.startswith("/"):
        return None
    # Browsers fold backslashes to forward slashes before resolving, so
    # "/\evil.com" is sent as scheme-relative "//evil.com" -- a redirect
    # straight off-origin that a naive startswith("/") check waves through.
    if candidate[:2].replace("\\", "/") == "//":
        return None
    parsed = urlsplit(candidate)
    if parsed.scheme or parsed.netloc:
        return None
    # Fragments never reach the server, so there is nothing to preserve.
    return parsed.path + (f"?{parsed.query}" if parsed.query else "")
