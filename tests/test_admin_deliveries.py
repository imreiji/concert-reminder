"""/admin/deliveries: admin-only, and the one place identity is revealed."""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base, DeliveryLog, User
from app.db.session import get_session
from app.domain.types import Anchor, DeliveryOutcome, DeliverySource
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
    async def fake_identity(token):
        return {"id": str(discord_id), "username": name, "global_name": name, "avatar": None}

    client.monkeypatch.setattr(auth, "fetch_identity", fake_identity)
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    client.get(f"/auth/callback?code=x&state={state}")


@pytest.mark.asyncio
async def test_page_renders_for_an_admin(client, monkeypatch):
    """Every page needs at least one logged-in GET render test -- a missing
    one shipped a 500 here once (template context drift)."""
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")
    r = client.get("/admin/deliveries")
    assert r.status_code == 200
    assert "Recent failures" in r.text


def test_a_signed_in_non_admin_gets_403(client):
    """Signed-in-but-unauthorized IS an error and stays 403 -- not folded in
    with the signed-out redirect (invariant 5)."""
    login_as(client, PLAIN_ID, "someone")
    assert client.get("/admin/deliveries").status_code == 403


def test_signed_out_is_redirected_not_403(client):
    r = client.get("/admin/deliveries")
    assert r.status_code == 303
    assert r.headers["location"].startswith("/?next=")


@pytest.mark.asyncio
async def test_failures_show_before_any_batch_is_opened(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")
    # No User row is seeded here: the OAuth callback login_as drives already
    # created one, and delivery_log.user_id is a FK to it.
    async with client.db() as s:
        s.add(
            DeliveryLog(
                batch_at_utc=datetime.now(UTC),
                user_id=ADMIN_ID,
                source=DeliverySource.REMINDER,
                outcome=DeliveryOutcome.FORBIDDEN,
                anchor=Anchor.CLOSES,
                concert_title="Snow Miku 2027",
                sent_at_utc=datetime.now(UTC),
            )
        )
        await s.commit()
    r = client.get("/admin/deliveries")
    assert "forbidden" in r.text
    assert "Snow Miku 2027" in r.text


@pytest.mark.asyncio
async def test_batch_detail_names_recipients(client, monkeypatch):
    """The deliberate counts-in-the-DM / names-in-the-app split: this is the
    only surface that reveals identity, and it is admin-gated and inside the
    30-day window."""
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")
    batch = datetime(2026, 7, 28, 14, 23, tzinfo=UTC)
    async with client.db() as s:
        s.add(
            DeliveryLog(
                batch_at_utc=batch,
                user_id=ADMIN_ID,
                source=DeliverySource.REMINDER,
                outcome=DeliveryOutcome.SUCCESS,
                anchor=Anchor.CLOSES,
                concert_title="Snow Miku 2027",
                sent_at_utc=batch,
            )
        )
        await s.commit()
    r = client.get(f"/admin/deliveries/{batch.isoformat()}")
    assert r.status_code == 200
    assert str(ADMIN_ID) in r.text


@pytest.mark.asyncio
async def test_a_malformed_batch_timestamp_is_a_404(client, monkeypatch):
    """A bad link, not a server fault -- datetime.fromisoformat would
    otherwise raise straight out of the route as a 500."""
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")
    assert client.get("/admin/deliveries/not-a-timestamp").status_code == 404


@pytest.mark.asyncio
async def test_batch_summary_counts_sent_users_and_failed(db):
    """Aggregated on read: there is no stored count anywhere, so a summary can
    never disagree with the rows it describes."""
    from app.db.service import delivery_batches

    batch = datetime(2026, 7, 28, 14, 23, tzinfo=UTC)
    async with db() as s:
        s.add_all([User(discord_id=1, username="a"), User(discord_id=2, username="b")])
        await s.flush()
        for user_id, outcome in (
            (1, DeliveryOutcome.SUCCESS),
            (2, DeliveryOutcome.SUCCESS),
            (2, DeliveryOutcome.FORBIDDEN),
        ):
            s.add(
                DeliveryLog(
                    batch_at_utc=batch,
                    user_id=user_id,
                    source=DeliverySource.REMINDER,
                    outcome=outcome,
                    sent_at_utc=batch,
                )
            )
        await s.commit()
        summaries = await delivery_batches(s)
        assert len(summaries) == 1
        assert (summaries[0].sent, summaries[0].users, summaries[0].failed) == (2, 2, 1)
