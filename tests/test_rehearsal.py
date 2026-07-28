"""The local rehearsal harness. Gated off in production by config."""

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

ADMIN_ID, PLAIN_ID = 42, 777


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
    # Registration is decided AT create_app() time, so the flag must be on
    # BEFORE the app is built -- otherwise every route test in this file 404s.
    monkeypatch.setattr(settings, "rehearsal_enabled", True)
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


def login_as(client, discord_id: int, name: str):
    """Drives the real OAuth callback, which CREATES the user row -- so no test
    here seeds the admin itself (that is an IntegrityError, not a shortcut)."""

    async def fake_identity(token):
        return {"id": str(discord_id), "username": name, "global_name": name, "avatar": None}

    client.monkeypatch.setattr(auth, "fetch_identity", fake_identity)
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    client.get(f"/auth/callback?code=x&state={state}")


def route_paths(routes) -> set[str]:
    """Every path in an app's route table, flattened.

    `app.routes` is NOT flat: this FastAPI wraps each `include_router` call in
    an `_IncludedRouter` that carries no `.path` of its own and exposes the
    real routes through `.original_router`. Reading `.path` off the top level
    alone would therefore see none of the included routers -- and the
    flag-off assertion below would pass for the wrong reason, forever.
    """
    out: set[str] = set()
    for r in routes:
        inner = getattr(r, "original_router", None)
        if inner is not None:
            out |= route_paths(inner.routes)
        path = getattr(r, "path", None)
        if path:
            out.add(path)
    return out


def test_the_router_is_not_registered_when_the_flag_is_off(monkeypatch):
    """THE safety model, asserted directly. With the flag off the route must
    not exist at all -- not 403, not 404-from-a-guard, but absent from the
    application's route table. Production never sets the flag, so a
    'pull every reminder forward' button is unreachable by construction
    rather than by a permission check somebody could get wrong."""
    monkeypatch.setattr(settings, "rehearsal_enabled", False)
    paths = route_paths(create_app().routes)
    # A control: the flattening genuinely reaches included routers, so an
    # absent /admin/rehearsal means absent, not merely unreachable by this walk.
    assert "/admin/broadcast" in paths
    assert "/admin/rehearsal" not in paths


def test_the_router_is_registered_when_the_flag_is_on(monkeypatch):
    monkeypatch.setattr(settings, "rehearsal_enabled", True)
    paths = route_paths(create_app().routes)
    assert "/admin/rehearsal" in paths


def test_the_flag_defaults_to_off():
    """A developer opts in; nobody opts out."""
    assert settings.model_fields["rehearsal_enabled"].default is False


def test_page_renders_for_an_admin(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")
    r = client.get("/admin/rehearsal")
    assert r.status_code == 200
    assert "Rehearsal" in r.text


def test_a_signed_in_non_admin_gets_403(client):
    """require_admin stays on the routes as a second layer, in case a deploy
    is ever misconfigured with the flag on."""
    login_as(client, PLAIN_ID, "someone")
    assert client.get("/admin/rehearsal").status_code == 403
