"""The local rehearsal harness. Gated off in production by config."""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import (
    Base,
    Concert,
    ConcertDay,
    ReminderQueue,
    Round,
    RoundQualifier,
    User,
)
from app.db.service import (
    REHEARSAL_EVENT_ID,
    get_rehearsal_concert,
    seed_rehearsal,
    teardown_rehearsal,
)
from app.db.session import get_session
from app.domain.types import Anchor, RoundKind
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


@pytest.mark.asyncio
async def test_seed_builds_the_canonical_scenario(db):
    async with db() as s:
        s.add(User(discord_id=ADMIN_ID, username="reiji"))
        await s.flush()
        concert = await seed_rehearsal(s, ADMIN_ID)
        await s.commit()

        assert concert.event_id == REHEARSAL_EVENT_ID
        days = (await s.execute(
            select(ConcertDay)
            .where(ConcertDay.concert_id == concert.id)
            .order_by(ConcertDay.starts_at_utc)
        )).scalars().all()
        assert len(days) == 2
        rounds = (await s.execute(select(Round).where(
            Round.concert_id == concert.id))).scalars().all()
        assert len(rounds) == 3
        kinds = {r.kind for r in rounds}
        assert kinds == {RoundKind.LOTTERY_ROUND, RoundKind.FCFS_SALE, RoundKind.UPGRADE}


@pytest.mark.asyncio
async def test_the_lottery_round_carries_all_four_anchors_and_both_legs(db):
    """One round yields the whole ladder, and spanning two legs is what
    exercises the per-day RoundOutcomeDay materialization."""
    async with db() as s:
        s.add(User(discord_id=ADMIN_ID, username="reiji"))
        await s.flush()
        concert = await seed_rehearsal(s, ADMIN_ID)
        await s.commit()
        r1 = (await s.execute(select(Round).where(
            Round.concert_id == concert.id,
            Round.kind == RoundKind.LOTTERY_ROUND))).scalar_one()
        assert r1.opens_at_utc and r1.closes_at_utc
        assert r1.results_at_utc and r1.payment_deadline_at_utc
        assert len(r1.applies_to) == 2


@pytest.mark.asyncio
async def test_the_upgrade_round_qualifies_on_the_lottery_round(db):
    """Before a WON on R1 the viewer is ineligible; after it, eligible. That
    gate is what this round exists to prove end to end."""
    async with db() as s:
        s.add(User(discord_id=ADMIN_ID, username="reiji"))
        await s.flush()
        concert = await seed_rehearsal(s, ADMIN_ID)
        await s.commit()
        upgrade = (await s.execute(select(Round).where(
            Round.concert_id == concert.id,
            Round.kind == RoundKind.UPGRADE))).scalar_one()
        lottery = (await s.execute(select(Round).where(
            Round.concert_id == concert.id,
            Round.kind == RoundKind.LOTTERY_ROUND))).scalar_one()
        pairs = (await s.execute(select(RoundQualifier).where(
            RoundQualifier.upgrade_round_id == upgrade.id))).scalars().all()
        assert [p.qualifying_round_id for p in pairs] == [lottery.id]


@pytest.mark.asyncio
async def test_seed_queues_reminders_through_the_real_planner(db):
    """The point of seeding real rules: sync_rule and the pure planner compute
    the fire times, so what the harness later pulls forward is a genuine
    plan, not a fabricated row."""
    async with db() as s:
        s.add(User(discord_id=ADMIN_ID, username="reiji"))
        await s.flush()
        await seed_rehearsal(s, ADMIN_ID)
        await s.commit()
        queued = (await s.execute(select(ReminderQueue))).scalars().all()
        anchors = {q.anchor for q in queued}
        assert Anchor.OPENS in anchors
        assert Anchor.CLOSES in anchors
        assert Anchor.RESULTS in anchors
        assert Anchor.PAYMENT in anchors
        assert Anchor.EVENT_START in anchors


@pytest.mark.asyncio
async def test_seed_is_idempotent(db):
    """Start twice leaves ONE rehearsal concert -- the harness reseeds from a
    clean slate rather than accumulating."""
    async with db() as s:
        s.add(User(discord_id=ADMIN_ID, username="reiji"))
        await s.flush()
        await seed_rehearsal(s, ADMIN_ID)
        await s.commit()
        await seed_rehearsal(s, ADMIN_ID)
        await s.commit()
        concerts = (await s.execute(select(Concert).where(
            Concert.event_id == REHEARSAL_EVENT_ID))).scalars().all()
        assert len(concerts) == 1


@pytest.mark.asyncio
async def test_teardown_removes_the_concert_but_not_the_user(db):
    """Cascades take the days, rounds, queue rows and outcomes. Users,
    presets and subscriptions are never touched."""
    async with db() as s:
        s.add(User(discord_id=ADMIN_ID, username="reiji"))
        await s.flush()
        await seed_rehearsal(s, ADMIN_ID)
        await s.commit()
        assert await teardown_rehearsal(s) is True
        await s.commit()
        assert await get_rehearsal_concert(s) is None
        assert (await s.execute(select(ReminderQueue))).scalars().all() == []
        assert await s.get(User, ADMIN_ID) is not None


@pytest.mark.asyncio
async def test_teardown_with_nothing_seeded_is_a_no_op(db):
    async with db() as s:
        assert await teardown_rehearsal(s) is False
