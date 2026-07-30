"""Task 1 guards: the global token layer, both theme directions, the 3px
radius pass, and the header theme toggle.

The CSS assertions read style.css as text on purpose -- they are parity
guards ("the demo's token is present"), the CSS equivalent of the project's
"every page a logged-in GET render test" discipline. The render test proves
the toggle control and the pill nav actually reach the page.
"""

import re
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base
from app.db.session import get_session
from app.web.app import create_app

STYLE = Path(__file__).resolve().parents[1] / "src/app/web/static/style.css"


def css():
    return STYLE.read_text(encoding="utf-8")


def test_style_defines_previously_missing_tokens():
    text = css()
    for token in (
        "--raise",
        "--chip",
        "--shadow",
        "--ok-wash",
        "--off-wash",
        "--danger-wash",
        "--accent-wash",
    ):
        assert token in text, f"missing token {token} (fallback-to-white regression guard)"


def test_style_defines_both_theme_directions():
    text = css()
    assert "@media (prefers-color-scheme: dark)" in text
    assert ':root[data-theme="dark"]' in text
    assert ':root[data-theme="light"]' in text


def test_style_uses_3px_radius_not_6or8():
    text = css()
    assert "border-radius: 6px" not in text
    assert "border-radius: 8px" not in text
    assert "border-radius: 3px" in text


def test_style_hidden_attribute_wins():
    # Any element with an author display rule (.upgradebox, .feedbox, ...)
    # renders despite hidden="" unless a global [hidden] override exists --
    # the UA's display:none loses to any author display. This shipped once:
    # every non-upgrade round on the edit page showed the upgrade qualifier
    # box because .upgradebox { display: grid } beat the attribute.
    assert "[hidden] { display: none !important; }" in css()


def test_dupe_banner_hidden_default_outspecifics_banner():
    # The duplicate-name warning in the new-tag dialog is JS-gated by a .show
    # class. `.banner { display: flex }` is defined LATER in this file, so a
    # bare `.dupe { display: none }` ties on specificity and loses on source
    # order -- which shipped, leaving the banner permanently visible and the
    # .show toggle a no-op. Both rules must therefore carry `.banner`.
    #
    # Sibling of test_style_hidden_attribute_wins above: same failure mode (an
    # author display rule quietly winning), second occurrence in this file.
    style = css()
    assert ".banner.dupe {" in style, "the hidden default must be qualified with .banner"
    assert ".banner.dupe.show { display: flex; }" in style
    # A bare `.dupe {` block would reintroduce the bug.
    assert not re.search(r"(?m)^\.dupe\s*\{", style), "bare .dupe rule loses to .banner"


def test_style_ports_the_demos_dark_paper_hex():
    # The dark palette is the demo's, not a naive invert -- pin one hex so a
    # future "simplify" can't quietly swap it.
    assert "#17161a" in css()


# ── Tablet band (701–1040px): one bounded section, no scattered breakpoints ──


def test_tablet_band_section_exists():
    # The band's rules live in ONE banner-commented section, the same
    # single-bounded-section discipline as the mobile block. Pin the banner
    # and the exact media query so a future edit can't quietly scatter band
    # rules back into ad-hoc breakpoints.
    text = css()
    assert "Tablet band (701" in text, "the tablet-band banner comment must exist"
    assert "@media (min-width: 701px) and (max-width: 1040px) {" in text


def test_tablet_band_keeps_the_coming_up_fold_target_alive():
    """Coming up's `.title-c small` is EMPTY -- the venue/date it used to hold
    moved to the block header -- so the main body hides it. The band is the one
    width where it has anything to say: the what-happens COLUMN is dropped
    there and folded back onto this line via `::after attr(data-happens)`.
    A `display: none` element renders no ::after, so the band must switch it
    back on, and the failure mode is silent -- the row simply stops saying
    what the moment is. Both halves pinned here."""
    band = css().split("@media (min-width: 701px) and (max-width: 1040px) {")[1].split("\n}")[0]
    assert ".deadline-rows .row .title-c small { display: block; }" in band
    assert "content: attr(data-happens)" in band, "the fold must carry the verb"
    # No leading separator: the small it used to be appended to is gone, so
    # " · " would render as a dangling "· closes".
    assert "content: \" \\00b7" not in band


def test_no_scattered_max_width_breakpoints_reappear():
    # Count actual `@media (max-width: Npx) {` rule blocks (not comment
    # mentions, and not the tablet band, which is written min-width-first so
    # it is deliberately excluded from this max-width tally). The tablet work
    # absorbed the old 1024 and 960 breakpoints; what remains is:
    #   1040 (.layout collapse), 900 (.rnd2), 860 (.plyt), 700 (mobile),
    #   380 (nested, inside the 700 block), and a SECOND 700 (the .fsheet
    #   bottom sheet, which was the old 760 query until .layout's collapse
    #   moved to 1040 and took the sheet boundary with it) = 6.
    # A new scattered breakpoint bumps this and fails the guard, which is the
    # point -- band rules belong in the one section above, not a fresh query.
    blocks = re.findall(r"@media \(max-width: \d+px\) \{", css())
    assert len(blocks) == 6, f"expected 6 max-width media blocks, found {len(blocks)}: {blocks}"


# ── header render ──────────────────────────────────────────────────────────


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture()
def client(db):
    app = create_app()

    async def override_session():
        async with db() as s:
            yield s

    app.dependency_overrides[get_session] = override_session
    return TestClient(app, follow_redirects=False)


def test_base_restores_open_folds_across_an_htmx_swap(client):
    """Spec §D's client half, beside the existing htmx listeners: an
    outerHTML swap of a whole region replaces every <details> in it, so the
    fold the reader expanded comes back closed. The listener collects the
    open `data-fold` keys inside the request target on `htmx:beforeRequest`
    and reopens the matching folds on `htmx:afterSettle`.

    Asserted as script TEXT for the same reason the CSS parity guards above
    read style.css: no headless client can fire a browser event, and the
    listener silently ceasing to exist is exactly the regression that would
    otherwise ship unnoticed."""
    html = client.get("/").text
    # `htmx:beforeRequest` alone proves nothing -- #hxbar's progress bar has
    # listened for it since long before this feature, so the bare `in` check
    # passed before the listener existed. The collector is a SECOND one.
    assert html.count("htmx:beforeRequest") >= 2, (
        "the fold collector is its own beforeRequest listener, beside #hxbar's"
    )
    assert "htmx:afterSettle" in html, "the reopen must run after the swap has settled"
    assert "details[data-fold]" in html, "the restore keys off data-fold"
    assert "dataset.fold" in html, "the keys are the data-fold values themselves"


def test_backdrop_close_requires_the_press_to_land_there_too(client):
    """A click's target is the nearest common ancestor of press and release, so
    selecting text in a dialog field and overshooting the card -- press inside,
    release on the backdrop -- reports the DIALOG and closed it, throwing away
    what was being typed. Confirmed in a browser before the fix: pointerdown hit
    a DIV inside the card while the click reported DIALOG#new-tag-dialog.

    The guard is that the press must have landed on the same element. Asserted
    as script TEXT for the same reason the fold-restore test above is: no
    headless client fires mouse events, and the guard quietly disappearing is
    precisely the regression that would ship unnoticed.
    """
    html = client.get("/").text
    assert "pointerdown" in html, (
        "the backdrop-close guard needs the press target, so it must listen for pointerdown"
    )
    assert "HTMLDialogElement" in html, "the close still keys off the target being a dialog"
    # The comparison IS the fix: closing on the click target alone is the bug.
    assert "pressedOn === e.target" in html, (
        "close only when press and release agree, or a drag out of a field closes the dialog"
    )
    # Backdrop-click-to-close and Esc remain the documented conventions.
    assert "e.target.close()" in html


def test_header_emits_theme_toggle_and_pill_nav(client):
    html = client.get("/").text
    assert "data-theme-toggle" in html, "theme toggle control must be in the header"
    assert 'nav class="main"' in html, "primary nav must render as nav.main for pill styling"
    # The no-flash guard: a synchronous read-and-stamp snippet in <head>.
    assert "localStorage" in html and "data-theme" in html
