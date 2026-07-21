"""Personal calendar feed: token generation, the .ics endpoint itself, and
the preferences-page states around it.
"""

import re

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


def generate_feed_token(client) -> str:
    r = client.post("/me/calendar-feed")
    assert r.status_code == 303
    match = re.search(r"feed_token=([\w-]+)", r.headers["location"])
    assert match
    return match.group(1)


def create_round_with_rule(client, closes_at: str, event_id: str = "c") -> None:
    client.post(
        "/concerts",
        data={
            "title": "C", "event_id": event_id,
            "round_label": ["R1"], "round_kind": ["lottery_round"],
            "round_opens_at": [""], "round_closes_at": [closes_at],
            "round_results_at": [""], "round_payment_at": [""],
            "round_label_en": [""], "round_url": [""], "round_notes": [""], "round_leg": [""],
        },
    )
    client.post(f"/concerts/{event_id}/rules", data={"anchor": "closes", "days_before": 3})


def test_generate_feed_requires_login(client):
    assert client.post("/me/calendar-feed").status_code == 303


def test_generate_feed_creates_token_and_redirects_with_it(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/me/calendar-feed")
    assert r.status_code == 303
    assert "feed_token=" in r.headers["location"]


def test_generate_feed_honors_next_param(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/me/calendar-feed", data={"next": "/welcome"})
    assert r.status_code == 303
    assert r.headers["location"].startswith("/welcome?feed_token=")


def test_calendar_feed_returns_ics_with_active_reminders(client):
    login_as(client, EDITOR_ID, "reiji")
    create_round_with_rule(client, "2099-06-25T23:59")
    token = generate_feed_token(client)

    r = client.get(f"/calendar/{token}.ics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/calendar")
    assert "BEGIN:VEVENT" in r.text
    assert "SUMMARY:C — R1" in r.text
    assert "DTSTART:20990625T145900Z" in r.text  # 2099-06-25 23:59 JST -> UTC


def test_calendar_feed_excludes_past_deadlines(client):
    login_as(client, EDITOR_ID, "reiji")
    create_round_with_rule(client, "2000-01-01T00:00")
    token = generate_feed_token(client)

    r = client.get(f"/calendar/{token}.ics")
    assert r.status_code == 200
    assert "BEGIN:VEVENT" not in r.text


def test_calendar_feed_unknown_token_404s(client):
    assert client.get("/calendar/not-a-real-token.ics").status_code == 404


def test_regenerating_feed_invalidates_old_token(client):
    login_as(client, EDITOR_ID, "reiji")
    old_token = generate_feed_token(client)
    new_token = generate_feed_token(client)
    assert old_token != new_token

    assert client.get(f"/calendar/{old_token}.ics").status_code == 404
    assert client.get(f"/calendar/{new_token}.ics").status_code == 200


def test_preferences_shows_generate_then_active_state(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.get("/preferences")
    assert "Generate feed link" in r.text
    assert "Calendar feed active" not in r.text

    generate_feed_token(client)
    r = client.get("/preferences")  # no feed_token this time -- one-time reveal only
    assert "Calendar feed active" in r.text  # the demo's status pill
    assert "Generate a new one" in r.text    # the regenerate control
    assert "won't be shown again" not in r.text


def test_preferences_shows_one_time_reveal_right_after_generating(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/me/calendar-feed")
    location = r.headers["location"]
    r = client.get(location)
    assert "won't be shown again" in r.text
    assert "/calendar/" in r.text and ".ics" in r.text
