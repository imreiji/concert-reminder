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
    #   380 (nested, inside the 700 block), 760 (.fsheet bottom sheet) = 6.
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


def test_header_emits_theme_toggle_and_pill_nav(client):
    html = client.get("/").text
    assert "data-theme-toggle" in html, "theme toggle control must be in the header"
    assert 'nav class="main"' in html, "primary nav must render as nav.main for pill styling"
    # The no-flash guard: a synchronous read-and-stamp snippet in <head>.
    assert "localStorage" in html and "data-theme" in html
