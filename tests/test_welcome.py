"""First-run guided setup: the wizard's own routes (GET /welcome dispatch,
POST /welcome/advance, POST /welcome/skip-all). The new-user-redirect half
that sends a brand-new login here lives in test_auth.py.
"""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base, ReminderPreset, TagSubscription, User
from app.db.session import get_session
from app.web import auth
from app.web.app import create_app

EDITOR_ID, FAN_ID = 42, 777


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


async def _onboarding_step(client, discord_id: int) -> int:
    async with client.db() as s:
        user = await s.get(User, discord_id)
        return user.onboarding_step


def test_welcome_requires_login(client):
    assert client.get("/welcome").status_code == 401


def test_welcome_shows_step_0_for_a_brand_new_user(client):
    login_as(client, FAN_ID, "fan")
    r = client.get("/welcome")
    assert r.status_code == 200
    assert "Follow some artists" in r.text


def test_welcome_redirects_to_index_once_done(client):
    login_as(client, FAN_ID, "fan")
    client.post("/welcome/skip-all")
    r = client.get("/welcome")
    assert r.status_code == 303
    assert r.headers["location"] == "/"


async def test_advance_increments_step_by_one(client):
    login_as(client, FAN_ID, "fan")
    r = client.post("/welcome/advance")
    assert r.status_code == 303
    assert r.headers["location"] == "/welcome"
    assert await _onboarding_step(client, FAN_ID) == 1


async def test_advance_stops_at_total_steps(client):
    login_as(client, FAN_ID, "fan")
    for _ in range(10):
        client.post("/welcome/advance")
    assert await _onboarding_step(client, FAN_ID) == 5


async def test_skip_all_jumps_straight_to_done(client):
    login_as(client, FAN_ID, "fan")
    r = client.post("/welcome/skip-all")
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    assert await _onboarding_step(client, FAN_ID) == 5


def test_final_advance_hands_off_to_setup(client):
    """Crossing into done (step 4 -> 5) is the capture flow's entry: the last
    Continue lands on /setup, not back on /welcome (which would bounce to /)."""
    login_as(client, FAN_ID, "fan")
    for _ in range(4):
        client.post("/welcome/advance")  # 0 -> 1 -> 2 -> 3 -> 4
    r = client.post("/welcome/advance")  # 4 -> 5 (done)
    assert r.status_code == 303
    assert r.headers["location"] == "/setup"


def test_earlier_advances_stay_on_welcome(client):
    login_as(client, FAN_ID, "fan")
    r = client.post("/welcome/advance")  # 0 -> 1, still mid-wizard
    assert r.status_code == 303
    assert r.headers["location"] == "/welcome"


def test_skip_all_still_lands_on_index(client):
    login_as(client, FAN_ID, "fan")
    r = client.post("/welcome/skip-all")
    assert r.headers["location"] == "/"


async def test_step_0_subscribe_form_returns_to_welcome(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Gakumas", "kind": "franchise"})
    login_as(client, FAN_ID, "fan")
    r = client.post("/subscriptions", data={"tag_id": 1, "next": "/welcome"})
    assert r.headers["location"] == "/welcome"
    async with client.db() as s:
        subs = (await s.execute(select(TagSubscription))).scalars().all()
    assert len(subs) == 1 and subs[0].user_id == FAN_ID
    # "Gakumas" alone would render either way (it's the tag name in the
    # picker); the "✓" only appears once sub_by_tag actually has this tag.
    assert "Gakumas ✓" in client.get("/welcome").text


async def test_skipping_step_1_does_not_create_a_preset(client):
    login_as(client, FAN_ID, "fan")
    client.post("/welcome/advance")  # step 0 -> 1
    r = client.get("/welcome")
    assert "Skip this" in r.text
    client.post("/welcome/advance")  # step 1 -> 2, no preset created
    async with client.db() as s:
        presets = (await s.execute(select(ReminderPreset))).scalars().all()
    assert presets == []


def test_step_1_shows_continue_once_a_preset_exists(client):
    login_as(client, FAN_ID, "fan")
    client.post("/welcome/advance")  # step 0 -> 1
    client.post("/presets", data={"name": "standard", "next": "/welcome"})
    r = client.get("/welcome")
    assert "Continue" in r.text and "Preset created" in r.text


async def test_welcome_shows_step_2_timezone(client):
    login_as(client, FAN_ID, "fan")
    client.post("/welcome/advance")  # 0 -> 1
    client.post("/welcome/advance")  # 1 -> 2
    r = client.get("/welcome")
    assert "Confirm your timezone" in r.text


async def test_step_2_set_timezone_returns_to_welcome(client):
    login_as(client, FAN_ID, "fan")
    client.post("/welcome/advance")
    client.post("/welcome/advance")
    r = client.post("/me/timezone", data={"timezone": "Asia/Tokyo", "next": "/welcome"})
    assert r.headers["location"] == "/welcome"
    # "Asia/Tokyo" alone would render regardless (it's already one of the
    # ~400 option values in the picker); only the `selected` marker proves
    # the wizard is actually showing the NEW value, not just any option.
    assert 'value="Asia/Tokyo" selected' in client.get("/welcome").text


async def test_welcome_shows_step_3_test_dm(client):
    login_as(client, FAN_ID, "fan")
    for _ in range(3):
        client.post("/welcome/advance")  # 0 -> 1 -> 2 -> 3
    r = client.get("/welcome")
    assert "Send a test DM" in r.text


async def test_welcome_shows_step_4_calendar_feed(client):
    login_as(client, FAN_ID, "fan")
    for _ in range(4):
        client.post("/welcome/advance")  # 0 -> 1 -> 2 -> 3 -> 4
    r = client.get("/welcome")
    assert "Get your calendar feed" in r.text
    assert "Skip this" in r.text


async def test_step_4_generate_feed_returns_to_welcome_with_link_shown(client):
    login_as(client, FAN_ID, "fan")
    for _ in range(4):
        client.post("/welcome/advance")
    r = client.post("/me/calendar-feed", data={"next": "/welcome"})
    assert r.headers["location"].startswith("/welcome?feed_token=")
    r = client.get(r.headers["location"])
    assert "feed link is ready" in r.text
    assert "Continue" in r.text
