"""Task 1 guards: the global token layer, both theme directions, the 3px
radius pass, and the header theme toggle.

The CSS assertions read style.css as text on purpose -- they are parity
guards ("the demo's token is present"), the CSS equivalent of the project's
"every page a logged-in GET render test" discipline. The render test proves
the toggle control and the pill nav actually reach the page.
"""

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


def test_style_ports_the_demos_dark_paper_hex():
    # The dark palette is the demo's, not a naive invert -- pin one hex so a
    # future "simplify" can't quietly swap it.
    assert "#17161a" in css()


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
