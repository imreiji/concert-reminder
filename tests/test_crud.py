"""Web CRUD tests: authorization, JST parsing, and the edit->re-sync contract.

Test DB isolation: get_session is dependency-overridden with an in-memory
async SQLite, so these tests never touch app.db. Login is simulated by
monkeypatching the auth module's Discord calls (same trick as test_auth).
"""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base, Concert, ReminderQueue, Window
from app.db.session import get_session
from app.domain.timezones import jst_to_utc
from app.web import auth
from app.web.app import create_app

EDITOR_ID, VIEWER_ID = 42, 777


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
    monkeypatch.setattr(settings, "editor_whitelist", str(EDITOR_ID))
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
    async def fake_identity(token):
        return {"id": str(discord_id), "username": name, "global_name": name, "avatar": None}

    client.monkeypatch.setattr(auth, "fetch_identity", fake_identity)
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    client.get(f"/auth/callback?code=x&state={state}")


# ── Authorization boundaries ─────────────────────────────────────────────


def test_anonymous_cannot_view_concert_pages(client):
    assert client.post("/concerts", data={"title": "X"}).status_code == 401
    assert client.get("/concerts/1").status_code == 401


def test_viewer_cannot_create_concert(client):
    login_as(client, VIEWER_ID, "viewer")
    r = client.post("/concerts", data={"title": "Nope"})
    assert r.status_code == 403


def test_editor_creates_concert_and_it_lists(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/concerts", data={"title": "Hasunosora 5th", "franchise": "Hasunosora"})
    assert r.status_code == 303
    r = client.get("/")
    assert "Hasunosora 5th" in r.text


# ── JST datetime contract ────────────────────────────────────────────────


@pytest.mark.anyio
async def anyio_noop():  # keeps pytest-asyncio quiet about the async helper below
    pass


def test_window_datetime_is_parsed_as_jst(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "C"})
    r = client.post(
        "/concerts/1/windows",
        data={"label": "最速先行", "kind": "lottery_round", "closes_at": "2026-08-01T19:00"},
    )
    assert r.status_code == 200

    import asyncio

    async def check():
        async with client.db() as s:
            w = (await s.execute(select(Window))).scalar_one()
            assert w.closes_at_utc == jst_to_utc(datetime(2026, 8, 1, 19, 0))
            assert w.closes_at_utc == datetime(2026, 8, 1, 10, 0, tzinfo=UTC)  # JST-9

    asyncio.get_event_loop().run_until_complete(check())


def test_window_needs_at_least_one_bound(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "C"})
    r = client.post("/concerts/1/windows", data={"label": "empty", "kind": "other"})
    assert r.status_code == 422


# ── The core contract: edits re-sync the queue ───────────────────────────


def test_editing_window_over_http_reschedules_queue(client):
    """User story: staff extends a lottery; every affected reminder moves."""
    import asyncio

    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "C"})
    client.post(
        "/concerts/1/windows",
        data={"label": "R1", "kind": "lottery_round", "closes_at": "2099-06-25T23:59"},
    )
    client.post("/concerts/1/rules", data={"anchor": "closes", "days_before": 3})

    async def fire_at():
        async with client.db() as s:
            return (await s.execute(select(ReminderQueue))).scalar_one().fire_at_utc

    loop = asyncio.get_event_loop()
    before = loop.run_until_complete(fire_at())
    assert before == jst_to_utc(datetime(2099, 6, 22, 23, 59))

    client.post(
        "/windows/1/edit",
        data={"label": "R1", "kind": "lottery_round", "closes_at": "2099-06-28T23:59"},
    )
    after = loop.run_until_complete(fire_at())
    assert after == jst_to_utc(datetime(2099, 6, 25, 23, 59))  # moved with the deadline


def test_deleting_rule_removes_queue_rows(client):
    import asyncio

    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "C"})
    client.post(
        "/concerts/1/windows",
        data={"label": "R1", "kind": "lottery_round", "closes_at": "2099-06-25T23:59"},
    )
    client.post("/concerts/1/rules", data={"anchor": "closes", "days_before": 3})
    client.post("/rules/1/delete")

    async def count():
        async with client.db() as s:
            return len((await s.execute(select(ReminderQueue))).scalars().all())

    assert asyncio.get_event_loop().run_until_complete(count()) == 0


def test_cannot_delete_someone_elses_rule(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "C"})
    client.post(
        "/concerts/1/windows",
        data={"label": "R1", "kind": "lottery_round", "closes_at": "2099-06-25T23:59"},
    )
    client.post("/concerts/1/rules", data={"anchor": "closes", "days_before": 3})

    login_as(client, VIEWER_ID, "viewer")  # switch identity in the same client
    r = client.post("/rules/1/delete")
    assert r.status_code == 404  # not yours -> as if it doesn't exist


def test_timezone_setting_validates(client):
    login_as(client, EDITOR_ID, "reiji")
    assert client.post("/me/timezone", data={"timezone": "Asia/Tokyo"}).status_code == 303
    assert client.post("/me/timezone", data={"timezone": "Mars/Olympus"}).status_code == 422


def test_delete_concert_cascades_everything(client):
    import asyncio

    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "C"})
    client.post(
        "/concerts/1/windows",
        data={"label": "R1", "kind": "lottery_round", "closes_at": "2099-06-25T23:59"},
    )
    client.post("/concerts/1/rules", data={"anchor": "closes", "days_before": 3})
    client.post("/concerts/1/delete")

    async def counts():
        async with client.db() as s:
            c = len((await s.execute(select(Concert))).scalars().all())
            w = len((await s.execute(select(Window))).scalars().all())
            q = len((await s.execute(select(ReminderQueue))).scalars().all())
            return c, w, q

    assert asyncio.get_event_loop().run_until_complete(counts()) == (0, 0, 0)


def test_concert_detail_page_renders_for_logged_in_users(client):
    """Regression: the detail page must render with full context (tags fragment
    included) — this exact page 500'd in production because no test loaded it."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "Render Me"})
    client.post(
        "/concerts/1/windows",
        data={"label": "R1", "kind": "lottery_round", "closes_at": "2099-06-25T23:59"},
    )
    r = client.get("/concerts/1")
    assert r.status_code == 200
    assert "Render Me" in r.text
    assert "Franchises" in r.text  # tags fragment rendered

    login_as(client, VIEWER_ID, "viewer")
    r = client.get("/concerts/1")
    assert r.status_code == 200  # viewers render too (read-only chips)
