"""Mobile scaffold: tab bar, FAB, compact header markup (presence + gating).

Fixtures mirror tests/test_i18n_web.py's sync TestClient shape.
"""

from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base
from app.db.session import get_session
from app.web import auth
from app.web.app import create_app

USER = 4242


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
def client(db, monkeypatch):
    app = create_app()

    async def override_session():
        async with db() as s:
            yield s

    app.dependency_overrides[get_session] = override_session

    async def fake_exchange(code):
        return "tok"

    monkeypatch.setattr(auth, "exchange_code", fake_exchange)
    c = TestClient(app, follow_redirects=False)
    c.db = db
    c.monkeypatch = monkeypatch
    return c


def login(client, discord_id: int = USER, name: str = "reiji"):
    async def fake_identity(token):
        return {"id": str(discord_id), "username": name, "global_name": name, "avatar": None}

    client.monkeypatch.setattr(auth, "fetch_identity", fake_identity)
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    client.get(f"/auth/callback?code=x&state={state}")


def test_tabbar_signed_out(client):
    r = client.get("/")
    assert 'class="tabbar"' in r.text
    tab_count = r.text.count('class="tab"') + r.text.count('class="tab" aria-current')
    assert tab_count >= 2  # Home, Discover
    assert "Sign in" in r.text            # third tab
    assert 'class="fab"' not in r.text    # FAB is editor-only


def test_tabbar_signed_in_marks_current(client):
    login(client)
    r = client.get("/discover")
    assert 'class="tabbar"' in r.text
    assert "Me" in r.text
    # active tab carries aria-current="page" like the desktop nav
    assert 'aria-current="page"' in r.text


def test_fab_editor_only(client):
    login(client)                          # plain user
    assert 'class="fab"' not in client.get("/").text

    client.monkeypatch.setattr(settings, "editor_whitelist", str(USER))
    assert 'class="fab"' in client.get("/").text


def test_tags_page_marks_current(client):
    login(client)
    r = client.get("/tags")
    assert 'aria-current="page"' in r.text


def test_discover_filter_sheet_contains_controls(client):
    r = client.get("/discover")
    assert 'class="fsheet"' in r.text
    # the sheet holds the relocated sidebar controls (sort + facet + tags)
    body = r.text
    sheet = body.split('class="fsheet"')[1]
    assert "Filters" in body
    for fragment in ("sort=", "status="):   # the existing GET filter links
        assert fragment in sheet


# ── Preferences section rail (.prail) at narrow widths ───────────────────
# Below 860px the rail flips from the sticky left column to a horizontal
# strip (`grid-auto-flow: column; overflow-x: auto`). Its implicit tracks
# default to `auto`, which is FLEXIBLE: when the labels don't fit, each
# track shrinks toward min-content instead of letting the strip scroll.
# In Latin that floors at the longest word, so the strip merely overflows;
# in Japanese min-content is a SINGLE CHARACTER (CJK breaks between
# characters), so every label squashed into a 2-3 character column and
# stacked vertically -- measured at 320px: five 3-line labels, the rail
# 80px tall instead of 35px. Pin the tracks to max-content so a label
# never breaks and the strip scrolls, which is what overflow-x already
# intended. Guarded as CSS text, the same parity-guard idiom as
# tests/test_theme_and_tokens.py.
STYLE = Path(__file__).resolve().parents[1] / "src/app/web/static/style.css"


def _prail_collapse_rule() -> str:
    """The `.prail` rule inside the 860px block, as text."""
    text = STYLE.read_text(encoding="utf-8")
    # the media block ends at the first close-brace in column 0
    block = text.split("@media (max-width: 860px) {")[1].split("\n}")[0]
    assert ".prail" in block, "the 860px block no longer holds the .prail collapse rule"
    return block


def test_prail_strip_keeps_labels_on_one_line():
    """Tracks sized to content, so a CJK label can't break per character."""
    assert "grid-auto-columns: max-content" in _prail_collapse_rule()


def test_prail_links_never_wrap():
    text = STYLE.read_text(encoding="utf-8")
    rule = text.split(".prail a {")[1].split("}")[0]
    assert "white-space: nowrap" in rule


# ── Preferences column overflow at phone widths ──────────────────────────
# Below 860px .plyt is a single 1fr track, and a grid track's automatic
# minimum is its content's min-content width -- so one unshrinkable child
# inflates the WHOLE track and drags every card in the column past the
# viewport with it, even cards that set no width of their own. Two children
# did this: the timezone <select> carries the full IANA zone list, whose
# widest option (`America/Argentina/ComodRivadavia`) floors the track at
# ~392px (where the timezone/detection/setup/editor/danger cards all broke
# together -- they were passengers, not causes); the "New preset" fold is
# pinned `flex: 0 0 auto`, so it never shrank below its `.stack`'s 26rem
# max-width, flooring the track at ~470px. Guarded as CSS text, same idiom
# as the .prail block above.


def _phone_block() -> str:
    """The whole `@media (max-width: 700px)` block, as text.

    That block contains a NESTED `@media (max-width: 380px)`, so splitting
    on the first column-0 `\n}` is still correct: the nested block's own
    closing brace is indented, not at column 0.
    """
    text = STYLE.read_text(encoding="utf-8")
    block = text.split("@media (max-width: 700px) {")[1].split("\n}")[0]
    assert ".plyt > *" in block, "the 700px block no longer holds the preferences overflow fix"
    return block


def test_plyt_children_get_min_width_zero():
    assert ".plyt > * { min-width: 0; }" in _phone_block()


def test_psec_select_and_input_capped_to_column_width():
    assert ".psec select, .psec input { max-width: 100%; min-width: 0; }" in _phone_block()


def test_new_preset_fold_can_shrink():
    assert ".bar details.fold { flex: 1 1 100%; min-width: 0; }" in _phone_block()


def test_new_preset_stack_drops_its_max_width():
    assert ".bar .stack { max-width: none; }" in _phone_block()
