"""The first-run capture flow over HTTP: /setup (prune),
/setup/applications, /setup/ready. Renders read purely from DB truth; the
two POSTs are idempotent batch writes. Client/login fixtures mirror
test_welcome.py.
"""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import (
    Base,
    Concert,
    ConcertTag,
    Round,
    Tag,
    TagSubscription,
)
from app.db.service import concert_subscription_states, tracked_concert_ids
from app.domain.types import RoundKind, SubscriptionState, TagKind
from app.web import auth
from app.web.app import create_app

FAN_ID = 777

# Real-future timestamps: the routes call the service with the real clock.
FUTURE = datetime(2099, 6, 20, 12, tzinfo=UTC)
FUTURE_LATER = datetime(2099, 6, 25, 12, tzinfo=UTC)
PAST = datetime(2000, 1, 1, 12, tzinfo=UTC)


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

    from app.db.session import get_session
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


async def seed_followed_concert(
    client, event_id: str, title: str, tag_name: str, *, closes: datetime = FUTURE,
    kind: TagKind = TagKind.GROUP,
) -> int:
    """A concert tagged with a tag FAN_ID follows, with one future round.
    Returns the concert id."""
    async with client.db() as s:
        tag = (await s.execute(
            Tag.__table__.select().where(Tag.name == tag_name)
        )).first()
        if tag is None:
            tag_obj = Tag(name=tag_name, kind=kind)
            s.add(tag_obj)
            await s.flush()
            s.add(TagSubscription(user_id=FAN_ID, tag_id=tag_obj.id))
            tag_id = tag_obj.id
        else:
            tag_id = tag[0]
        concert = Concert(title=title, event_id=event_id, created_by=FAN_ID)
        s.add(concert)
        await s.flush()
        s.add(ConcertTag(concert_id=concert.id, tag_id=tag_id))
        s.add(Round(
            concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="FC presale",
            closes_at_utc=closes,
        ))
        await s.commit()
        return concert.id


# ── GET /setup (screen 1) ────────────────────────────────────────────────


def test_setup_requires_login(client):
    assert client.get("/setup").status_code == 401


async def test_setup_renders_found_tiles(client):
    login_as(client, FAN_ID, "fan")
    c1 = await seed_followed_concert(client, "aqours-9th", "Aqours 9th Live", "Aqours")
    c2 = await seed_followed_concert(client, "ll-fest", "Love Live Fes", "Aqours")

    r = client.get("/setup")
    assert r.status_code == 200
    assert "We found 2" in r.text
    assert "Aqours 9th Live" in r.text and "Love Live Fes" in r.text
    assert "Aqours" in r.text
    assert f'value="{c1}" checked' in r.text
    assert f'value="{c2}" checked' in r.text


async def test_setup_renders_pruned_tile_unchecked(client):
    login_as(client, FAN_ID, "fan")
    cid = await seed_followed_concert(client, "aqours-9th", "Aqours 9th Live", "Aqours")
    async with client.db() as s:
        from app.db.service import set_concert_subscription
        await set_concert_subscription(s, FAN_ID, cid, SubscriptionState.OPTED_OUT)
        await s.commit()

    r = client.get("/setup")
    assert r.status_code == 200
    assert f'value="{cid}"' in r.text
    assert f'value="{cid}" checked' not in r.text


async def test_setup_empty_state(client):
    login_as(client, FAN_ID, "fan")
    r = client.get("/setup")
    assert r.status_code == 200
    assert "/discover" in r.text


async def test_prune_submit_writes_and_redirects(client):
    login_as(client, FAN_ID, "fan")
    c1 = await seed_followed_concert(client, "aqours-9th", "Aqours 9th Live", "Aqours")
    c2 = await seed_followed_concert(client, "ll-fest", "Love Live Fes", "Aqours")

    r = client.post("/setup/prune", data={"keep": [c1], "shown": [c1, c2]})
    assert r.status_code == 303
    assert r.headers["location"] == "/setup/applications"

    async with client.db() as s:
        tracked = await tracked_concert_ids(s, FAN_ID)
        states = await concert_subscription_states(s, FAN_ID)
    assert c2 not in tracked and c1 in tracked
    assert states.get(c2) is SubscriptionState.OPTED_OUT


async def test_prune_submit_ignores_forged_ids(client):
    login_as(client, FAN_ID, "fan")
    r = client.post("/setup/prune", data={"keep": [9999], "shown": [9999]})
    assert r.status_code == 303
    async with client.db() as s:
        assert await concert_subscription_states(s, FAN_ID) == {}
