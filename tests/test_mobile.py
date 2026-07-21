"""Mobile scaffold: tab bar, FAB, compact header markup (presence + gating).

Fixtures mirror tests/test_i18n_web.py's sync TestClient shape.
"""

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
