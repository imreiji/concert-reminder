"""/admin/broadcast: compose, preview, send, status and cancel.

The preview writes nothing; send queues HELD notifications through the outbox.
"""

import asyncio

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base, Broadcast, Notification, User
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


def test_send_queues_held_notifications_and_redirects(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")
    r = client.post(
        "/admin/broadcast/send",
        data={"mode": "all", "mode_param": "", "body": "sorry about that"},
    )
    assert r.status_code == 303
    assert r.headers["location"].startswith("/admin/broadcast/")

    async def rows():
        async with client.db() as s:
            notes = (await s.execute(select(Notification))).scalars().all()
            b = (await s.execute(select(Broadcast))).scalar_one()
            return notes, b

    notes, b = asyncio.get_event_loop().run_until_complete(rows())
    assert len(notes) == 1
    assert notes[0].kind == "admin_broadcast"
    assert notes[0].send_after_utc is not None
    assert notes[0].broadcast_id == b.id
    assert b.recipient_count == 1


def test_send_above_the_threshold_requires_the_typed_count(client, monkeypatch):
    """Seeds past the real threshold rather than monkeypatching it.

    An earlier draft patched `service.TYPED_CONFIRM_THRESHOLD`, which would
    have passed VACUOUSLY: `admin.py` does `from app.db.service import
    TYPED_CONFIRM_THRESHOLD`, binding the value into its own namespace at
    import time, so patching the service module's attribute never reaches the
    route. Seeding real users tests the real constant and has no such trap.
    """
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")

    async def seed_many():
        async with client.db() as s:
            for i in range(2000, 2015):  # 15 + the admin = 16, over the 10 threshold
                s.add(User(discord_id=i, username=f"u{i}", language="en"))
            await s.commit()

    asyncio.get_event_loop().run_until_complete(seed_many())

    bad = client.post(
        "/admin/broadcast/send",
        data={"mode": "all", "mode_param": "", "body": "hi", "confirm_count": "99"},
    )
    assert bad.status_code == 422

    missing = client.post(
        "/admin/broadcast/send",
        data={"mode": "all", "mode_param": "", "body": "hi"},
    )
    assert missing.status_code == 422  # absent is as wrong as incorrect

    ok = client.post(
        "/admin/broadcast/send",
        data={"mode": "all", "mode_param": "", "body": "hi", "confirm_count": "16"},
    )
    assert ok.status_code == 303


def test_send_below_the_threshold_needs_no_typed_count(client, monkeypatch):
    """The gate is keyed on SIZE, so a small send must stay frictionless."""
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")
    r = client.post(
        "/admin/broadcast/send",
        data={"mode": "all", "mode_param": "", "body": "hi"},
    )
    assert r.status_code == 303


def test_status_page_and_cancel(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")
    sent = client.post(
        "/admin/broadcast/send",
        data={"mode": "all", "mode_param": "", "body": "oops"},
    )
    bid = sent.headers["location"].rsplit("/", 1)[1]

    page = client.get(f"/admin/broadcast/{bid}")
    assert page.status_code == 200
    assert "Cancel" in page.text

    cancelled = client.post(f"/admin/broadcast/{bid}/cancel")
    assert cancelled.status_code == 303

    async def remaining():
        async with client.db() as s:
            return len((await s.execute(select(Notification))).scalars().all())

    assert asyncio.get_event_loop().run_until_complete(remaining()) == 0


def test_the_body_is_framed_per_recipient_language(client, monkeypatch):
    """The frame is applied at QUEUE time, in each recipient's language, which
    is what keeps the scheduler's plain-text send path unchanged."""
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")

    async def make_ja():
        async with client.db() as s:
            s.add(User(discord_id=1234, username="jp", language="ja"))
            await s.commit()

    asyncio.get_event_loop().run_until_complete(make_ja())
    client.post(
        "/admin/broadcast/send",
        data={"mode": "explicit", "mode_param": "1234", "body": "test"},
    )

    async def body():
        async with client.db() as s:
            return (await s.execute(select(Notification))).scalar_one().body

    text = asyncio.get_event_loop().run_until_complete(body())
    assert "test" in text
    assert "dekimasen.app" in text
    assert "From dekimasen.app" not in text  # translated, not the English msgid
