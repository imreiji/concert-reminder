"""The web counterpart to the DM outcome buttons: POST /rounds/{id}/outcome.

Every assertion here is about what `record_round_outcome` ACTUALLY does, not
what the route wishes it did -- the route is a thin shell and must not diverge
from `bot/views.py`'s `_handle_outcome_click`, which is the other call site.
"""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Concert, ConcertDay, Round, RoundOutcome, User
from app.db.service import upcoming_deadlines
from app.db.session import get_session
from app.domain.types import Anchor, LotteryOutcome, RoundKind
from app.web import auth
from app.web.app import create_app

USER_A, USER_B = 4242, 9999


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")  # match production: cascades must fire

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


async def seed_round(db) -> int:
    """One concert with one live day and one open lottery round."""
    now = datetime.now(UTC)
    async with db() as s:
        # PRAGMA foreign_keys=ON is on, so the users these rows point at must
        # exist before them. Logging in would create them too, but the seed
        # runs first.
        s.add_all([
            User(discord_id=USER_A, username="reiji"),
            User(discord_id=USER_B, username="someone-else"),
        ])
        await s.flush()
        concert = Concert(title="Hasunosora 6th", event_id="hasu-6th", created_by=USER_A)
        s.add(concert)
        await s.flush()
        s.add(ConcertDay(
            concert_id=concert.id, label="Day 1", starts_at_utc=now + timedelta(days=60)
        ))
        round_ = Round(
            concert_id=concert.id, label="Lottery 1", kind=RoundKind.LOTTERY_ROUND,
            opens_at_utc=now - timedelta(days=1), closes_at_utc=now + timedelta(days=7),
        )
        s.add(round_)
        await s.flush()
        await s.commit()
        return round_.id


async def outcome_for(db, user_id: int, round_id: int) -> LotteryOutcome | None:
    async with db() as s:
        row = (await s.execute(select(RoundOutcome).where(
            RoundOutcome.user_id == user_id, RoundOutcome.round_id == round_id
        ))).scalar_one_or_none()
        return row.outcome if row else None


def post_outcome(client, round_id: int, outcome: str):
    """Posts as htmx does. Without the HX-Request header the route redirects
    to Home instead of rendering fragments (the JS-disabled fallback), so the
    header is what keeps these tests on the 200-and-a-fragment path they are
    asserting about. The redirect itself is covered in test_home.py."""
    return client.post(
        f"/rounds/{round_id}/outcome",
        data={"outcome": outcome},
        headers={"HX-Request": "true"},
    )


async def test_i_have_applied_records_applied(client):
    rid = await seed_round(client.db)
    login_as(client, USER_A, "reiji")
    r = post_outcome(client, rid, "applied")
    assert r.status_code == 200
    assert await outcome_for(client.db, USER_A, rid) is LotteryOutcome.APPLIED


async def test_not_applying_records_not_applied(client):
    rid = await seed_round(client.db)
    login_as(client, USER_A, "reiji")
    assert post_outcome(client, rid, "not_applied").status_code == 200
    assert await outcome_for(client.db, USER_A, rid) is LotteryOutcome.NOT_APPLIED


async def test_paid_is_reachable_from_won(client):
    rid = await seed_round(client.db)
    login_as(client, USER_A, "reiji")
    post_outcome(client, rid, "won")
    assert post_outcome(client, rid, "paid").status_code == 200
    assert await outcome_for(client.db, USER_A, rid) is LotteryOutcome.PAID


async def test_paid_without_a_prior_won_behaves_as_the_service_defines(client):
    """`record_round_outcome` returns SILENTLY when PAID has no prior WON --
    it is not an error, it is a no-op. The route must not invent a 4xx the
    DM buttons don't produce, so this asserts a 200 with nothing written."""
    rid = await seed_round(client.db)
    login_as(client, USER_A, "reiji")
    assert post_outcome(client, rid, "paid").status_code == 200
    assert await outcome_for(client.db, USER_A, rid) is None


async def test_requires_login(client):
    rid = await seed_round(client.db)
    r = post_outcome(client, rid, "applied")
    # htmx request: HX-Redirect, not a 303 the XHR would follow and swap in.
    assert r.status_code == 204
    assert r.headers["hx-redirect"] == "/"
    assert await outcome_for(client.db, USER_A, rid) is None


async def test_unknown_round_404s(client):
    """The service returns silently for a missing round, so the route needs
    its own existence check to avoid reporting an honest-looking success."""
    await seed_round(client.db)
    login_as(client, USER_A, "reiji")
    assert post_outcome(client, 987654, "applied").status_code == 404


async def test_bad_outcome_value_422s(client):
    rid = await seed_round(client.db)
    login_as(client, USER_A, "reiji")
    assert post_outcome(client, rid, "definitely_not_an_outcome").status_code == 422


async def test_outcome_is_scoped_to_the_calling_user(client):
    """Two users on the SAME round keep independent state -- the user comes
    from the session, never from the request body."""
    rid = await seed_round(client.db)
    login_as(client, USER_A, "reiji")
    post_outcome(client, rid, "applied")
    login_as(client, USER_B, "someone-else")
    post_outcome(client, rid, "won")

    assert await outcome_for(client.db, USER_A, rid) is LotteryOutcome.APPLIED
    assert await outcome_for(client.db, USER_B, rid) is LotteryOutcome.WON


async def test_upcoming_deadline_carries_round_id_only_for_round_rows(db):
    """A Coming up row has to know which round to post to. Day-derived rows
    (EVENT_START) have no round at all, so the field stays None there."""
    await seed_round(db)
    async with db() as s:
        rows = await upcoming_deadlines(s, limit=50)

    by_anchor = {}
    for row in rows:
        by_anchor.setdefault(row.anchor, []).append(row)

    closes = by_anchor[Anchor.CLOSES][0]
    assert closes.round_id is not None

    start = by_anchor[Anchor.EVENT_START][0]
    assert start.round_id is None
