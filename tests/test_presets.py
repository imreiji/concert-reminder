"""Phase 10: presets, one-click apply, and the notify-and-apply pipeline.

The scenario tests mirror the intended real-world flow: a subscriber sets up
a preset once, an editor tags a new event, and everything else is automatic.
"""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import (
    Base,
    Notification,
    ReminderQueue,
    ReminderRule,
)
from app.db.session import get_session
from app.web import auth
from app.web.app import create_app

EDITOR_ID, FAN_ID = 42, 777


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
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


def build_standard_preset(client) -> None:
    """Preset #1: 3d before closes + 7d before event day."""
    client.post("/presets", data={"name": "standard"})
    client.post("/presets/1/items", data={"anchor": "closes", "days_before": 3})
    client.post("/presets/1/items", data={"anchor": "event_start", "days_before": 7})


def build_concert_with_deadlines(client) -> None:
    """Concert #1 with one lottery window and one day (as the editor)."""
    client.post("/concerts", data={"title": "Hasunosora 6th"})
    client.post(
        "/concerts/1/windows",
        data={"label": "最速先行", "kind": "lottery_round", "closes_at": "2099-06-25T23:59"},
    )
    client.post("/concerts/1/days", data={"label": "Day 1", "starts_at": "2099-08-01T18:00"})


async def _all(db, model):
    async with db() as s:
        return list((await s.execute(select(model))).scalars())


# ── Presets & one-click apply ────────────────────────────────────────────


async def test_apply_preset_creates_rules_and_queues(client):
    login_as(client, EDITOR_ID, "reiji")
    build_concert_with_deadlines(client)
    build_standard_preset(client)

    r = client.post("/concerts/1/presets/1/apply")
    assert r.status_code == 200

    rules = await _all(client.db, ReminderRule)
    assert len(rules) == 2  # one per preset item
    queue = await _all(client.db, ReminderQueue)
    assert len(queue) == 2  # window-close reminder + day reminder


async def test_apply_is_idempotent(client):
    login_as(client, EDITOR_ID, "reiji")
    build_concert_with_deadlines(client)
    build_standard_preset(client)
    client.post("/concerts/1/presets/1/apply")
    client.post("/concerts/1/presets/1/apply")
    client.post("/concerts/1/presets/1/apply")
    assert len(await _all(client.db, ReminderRule)) == 2  # clicks are harmless


def test_cannot_apply_someone_elses_preset(client):
    login_as(client, EDITOR_ID, "reiji")
    build_concert_with_deadlines(client)
    build_standard_preset(client)
    login_as(client, FAN_ID, "fan")
    assert client.post("/concerts/1/presets/1/apply").status_code == 404


# ── The notify-and-apply pipeline ────────────────────────────────────────


async def test_new_tagged_event_auto_applies_and_notifies(client):
    """The end-state flow: fan subscribes once; editor tags an event;
    fan's reminders exist and a DM notice is queued — untouched by the fan."""
    # editor creates the tag universe first
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Hasunosora", "kind": "group"})
    client.post("/tags", data={"name": "Kozue", "kind": "artist"})
    client.post("/tags/1/members", data={"member_tag_id": 2})

    # fan sets up: preset + subscription to the ARTIST with notify+preset
    login_as(client, FAN_ID, "fan")
    build_standard_preset(client)
    client.post("/subscriptions", data={"tag_id": 2, "preset_id": 1, "notify": "true"})

    # editor creates the event and tags the GROUP (expansion adds the artist)
    login_as(client, EDITOR_ID, "reiji")
    build_concert_with_deadlines(client)
    client.post("/concerts/1/tags", data={"name": "Hasunosora", "kind": "group"})

    rules = await _all(client.db, ReminderRule)
    fan_rules = [r for r in rules if r.user_id == FAN_ID]
    assert len(fan_rules) == 2  # preset auto-applied via the materialized artist tag

    notes = await _all(client.db, Notification)
    assert len(notes) == 1
    assert notes[0].user_id == FAN_ID
    assert "Hasunosora 6th" in notes[0].body
    assert "standard" in notes[0].body  # names the applied preset


async def test_subscriber_without_preset_gets_notification_only(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Gakumas", "kind": "franchise"})
    login_as(client, FAN_ID, "fan")
    client.post("/subscriptions", data={"tag_id": 1, "notify": "true"})
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "Gakumas 3rd"})
    client.post("/concerts/1/tags", data={"name": "Gakumas", "kind": "franchise"})

    assert [r for r in await _all(client.db, ReminderRule) if r.user_id == FAN_ID] == []
    notes = await _all(client.db, Notification)
    assert len(notes) == 1 and "no preset linked" in notes[0].body


async def test_user_with_existing_rules_is_skipped(client):
    """Second matching tag on the same concert must not double-apply or re-notify."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Hasunosora", "kind": "franchise"})
    client.post("/tags", data={"name": "Kozue", "kind": "artist"})
    login_as(client, FAN_ID, "fan")
    build_standard_preset(client)
    client.post("/subscriptions", data={"tag_id": 1, "preset_id": 1, "notify": "true"})
    client.post("/subscriptions", data={"tag_id": 2, "preset_id": 1, "notify": "true"})
    login_as(client, EDITOR_ID, "reiji")
    build_concert_with_deadlines(client)
    client.post("/concerts/1/tags", data={"name": "Hasunosora", "kind": "franchise"})
    client.post("/concerts/1/tags", data={"name": "Kozue", "kind": "artist"})  # second tag later

    fan_rules = [r for r in await _all(client.db, ReminderRule) if r.user_id == FAN_ID]
    assert len(fan_rules) == 2  # still just the one application
    assert len(await _all(client.db, Notification)) == 1  # and the one notice


async def test_scheduler_delivers_notifications(client):
    """The outbox drains through the same tick as reminders."""
    from app.scheduler.loop import tick

    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Hasunosora", "kind": "franchise"})
    login_as(client, FAN_ID, "fan")
    client.post("/subscriptions", data={"tag_id": 1, "notify": "true"})
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "6th"})
    client.post("/concerts/1/tags", data={"name": "Hasunosora", "kind": "franchise"})

    sent = []

    class FakeUser:
        async def send(self, body):
            sent.append(body)

    class FakeBot:
        def get_user(self, uid):
            return FakeUser()

    # point the scheduler's session factory at the test DB
    import app.scheduler.loop as loop_mod

    client.monkeypatch.setattr(loop_mod, "SessionMaker", client.db)
    client.monkeypatch.setattr(loop_mod, "SEND_GAP_SECONDS", 0)
    delivered = await tick(FakeBot())

    assert delivered == 1
    assert len(sent) == 1 and "6th" in sent[0]
    notes = await _all(client.db, Notification)
    assert notes[0].sent_at_utc is not None
