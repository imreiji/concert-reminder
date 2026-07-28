"""/admin/broadcast: compose and preview. The preview writes nothing."""

import asyncio

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base, Broadcast, Notification
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


def test_compose_renders_for_an_admin(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")
    r = client.get("/admin/broadcast")
    assert r.status_code == 200
    assert "Broadcast" in r.text


def test_a_signed_in_non_admin_gets_403(client):
    login_as(client, PLAIN_ID, "someone")
    assert client.get("/admin/broadcast").status_code == 403


def test_signed_out_is_redirected(client):
    r = client.get("/admin/broadcast")
    assert r.status_code == 303


def test_preview_writes_nothing(client, monkeypatch):
    """The whole point of a preview: nothing reaches the outbox until the
    admin confirms from the preview screen."""
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")
    r = client.post(
        "/admin/broadcast/preview",
        data={"mode": "all", "mode_param": "", "body": "hello everyone"},
    )
    assert r.status_code == 200
    assert "hello everyone" in r.text

    async def counts():
        async with client.db() as s:
            n = len((await s.execute(select(Notification))).scalars().all())
            b = len((await s.execute(select(Broadcast))).scalars().all())
            return n, b

    assert asyncio.get_event_loop().run_until_complete(counts()) == (0, 0)


def test_preview_shows_the_recipient_count(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")
    r = client.post(
        "/admin/broadcast/preview",
        data={"mode": "all", "mode_param": "", "body": "hi"},
    )
    assert "1 recipient" in r.text  # just the logged-in admin exists


def test_preview_lists_unmatched_explicit_ids(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")
    r = client.post(
        "/admin/broadcast/preview",
        data={"mode": "explicit", "mode_param": "999 oops", "body": "hi"},
    )
    assert "999" in r.text
    assert "oops" in r.text


def test_preview_rejects_an_over_long_body(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")
    r = client.post(
        "/admin/broadcast/preview",
        data={"mode": "all", "mode_param": "", "body": "x" * 5000},
    )
    assert r.status_code == 422
