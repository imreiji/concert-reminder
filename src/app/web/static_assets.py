"""Content-hash cache-busting for static assets.

`base.html` links `/static/style.css` and `/static/favicon.ico`. Cloudflare
caches those at the edge, so after a deploy ships new CSS a browser keeps the
OLD stylesheet until the cache expires or someone manually purges -- the new
markup then renders unstyled (this broke three deploys in a row). Appending a
content-hash `?v=` makes every change to a file a new cache key, and the manual
purge step disappears.

Lives in `web/`, NOT `domain/`: it reads files, and `domain/` is pure with no
I/O. The hash is a CONTENT hash (not mtime, which is unreliable across
checkouts, and not a startup-time value, which would bust the cache on every
restart and defeat the point), computed at most ONCE per file per process and
memoized in a module-level dict. Per-FILE hashing -- changing `style.css`
changes only its URL and leaves `favicon.ico`'s stable.
"""

import hashlib
from pathlib import Path

_STATIC_DIR = Path(__file__).parent / "static"

# Process-local memo: filename -> short content hash. Read-mostly, and Python
# dict ops are atomic enough here; no lock needed (worst case under a race is a
# file hashed twice, which is harmless).
_hash_cache: dict[str, str] = {}


def _hash_bytes(data: bytes) -> str:
    """First 12 hex chars of the sha256 -- enough to make a changed file a new
    cache key, short enough to keep the URL tidy."""
    return hashlib.sha256(data).hexdigest()[:12]


def static_url(filename: str) -> str:
    """`/static/<filename>?v=<content-hash>`, hashing the file once per process.

    A missing file DEGRADES: it returns the bare `/static/<filename>` with no
    query rather than crashing template rendering -- a missing asset should not
    500 the whole page.
    """
    digest = _hash_cache.get(filename)
    if digest is None:
        try:
            digest = _hash_bytes((_STATIC_DIR / filename).read_bytes())
        except OSError:
            # Missing/unreadable: degrade to the unversioned URL. Not cached --
            # the miss is cheap and rare, and an asset added later then picks
            # up its hash on the next call.
            return f"/static/{filename}"
        _hash_cache[filename] = digest
    return f"/static/{filename}?v={digest}"
