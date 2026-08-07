"""Every script this app serves must come from this app.

htmx used to load from unpkg.com. That is one third-party origin with total
authority over the page: an outage broke every interactive surface (the whole
UI is htmx), and a compromised CDN would have run attacker script IN-ORIGIN,
where the session cookie lives and where SameSite=Lax -- the only CSRF defence
this app has by design (invariant 5) -- offers nothing at all.

Vendoring fixed both, and it is also what lets deploy/Caddyfile name 'self' as
the only script origin in its Content-Security-Policy. So the sweep below is
not style policing: re-adding a CDN <script> tag would silently re-open the
hole AND be blocked by the shipped CSP, which is a confusing way to find out.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TPL_DIR = ROOT / "src" / "app" / "web" / "templates"
STATIC = ROOT / "src" / "app" / "web" / "static"
CADDYFILE = ROOT / "deploy" / "Caddyfile"

# `src` on any element (script/img/iframe/...) pointing at an absolute URL.
# Scheme-relative (//host/x) counts too -- it is a remote fetch wearing a
# shorter spelling, and grepping only for "https://" would miss it.
_REMOTE_SRC = re.compile(r"""\bsrc\s*=\s*["'](?:https?:)?//([^/"']+)""", re.I)

# The one third-party origin the app genuinely needs: Discord avatars, via
# SessionUser.avatar_url (web/auth.py). It is an IMAGE origin, never a script
# one, which is exactly the distinction the CSP draws.
ALLOWED_REMOTE_HOSTS = {"cdn.discordapp.com"}


def _templates() -> list[Path]:
    return sorted(TPL_DIR.glob("*.html"))


def test_no_template_loads_a_remote_asset() -> None:
    offenders: list[str] = []
    for path in _templates():
        for host in _REMOTE_SRC.findall(path.read_text(encoding="utf-8")):
            # Jinja expressions ({{ user.avatar_url }}) are resolved at render
            # time and cannot be judged here; the avatar case is covered by
            # the CSP's img-src instead.
            if host.startswith("{{") or host.startswith("{%"):
                continue
            if host not in ALLOWED_REMOTE_HOSTS:
                offenders.append(f"{path.name} -> {host}")
    assert offenders == [], (
        f"remote asset(s) reintroduced: {offenders}. Vendor the file into "
        "src/app/web/static/ and link it with static_url() instead -- the "
        "shipped Content-Security-Policy names 'self' as the only script "
        "origin, so a CDN tag is both a security regression and broken."
    )


def test_htmx_is_vendored_and_linked_through_static_url() -> None:
    """The vendored file must exist AND be the one base.html actually links.

    Asserting only that the file is present would pass with base.html still
    pointing at a CDN -- the property under test is that the page loads OUR
    copy, not that a copy happens to sit on disk.
    """
    vendored = STATIC / "htmx.min.js"
    assert vendored.is_file(), "src/app/web/static/htmx.min.js is missing"

    body = vendored.read_bytes()
    assert len(body) > 30_000, (
        f"vendored htmx is only {len(body)} bytes -- a truncated download or "
        "an error page, not the library"
    )
    assert body.lstrip().startswith(b"var htmx="), "vendored file is not htmx"

    base = (TPL_DIR / "base.html").read_text(encoding="utf-8")
    assert "static_url('htmx.min.js')" in base, (
        "base.html must link htmx via static_url() so the content-hash "
        "cache-bust applies to it like every other static asset"
    )


@pytest.mark.parametrize(
    "directive",
    [
        # 'self' with NO third-party host beside it is the property vendoring
        # bought; if a CDN ever comes back, this is where it would show up.
        "script-src 'self' 'unsafe-inline';",
        "default-src 'self';",
        "frame-ancestors 'none';",
        "object-src 'none'",
        "base-uri 'self';",
        # The avatar origin, and deliberately only under img-src.
        "img-src 'self' data: https://cdn.discordapp.com;",
    ],
)
def test_caddyfile_ships_the_expected_csp_directive(directive: str) -> None:
    """The CSP lives in the Caddyfile, so no app test can observe it on a
    response -- TestClient talks to FastAPI, not to Caddy. Pinning the text is
    the only check available, and it is worth having: the policy and the
    vendoring are one change, and reverting either alone breaks the site.
    """
    assert directive in CADDYFILE.read_text(encoding="utf-8"), (
        f"Content-Security-Policy is missing `{directive}` -- see the header "
        "block in deploy/Caddyfile"
    )
