"""Upgrade rounds on the web surfaces: Discover pills (Task 6), Home rows and
the concert page (Task 7).

The upgrade campaign is a SECOND fact beside the base standing, and every
surface shows it only to a viewer eligible to enter -- a held qualifying
ticket. An ineligible or signed-out viewer sees the base state alone. These
tests pin the exact pill/label text ported from the design concept and the
eligibility gating on every surface.
"""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload
from sqlalchemy.pool import StaticPool

from app.db.models import (
    Base,
    Concert,
    ConcertDay,
    ConcertTag,
    Round,
    RoundOutcome,
    RoundQualifier,
    Tag,
    TagSubscription,
    User,
)
from app.db.service import discover_statuses
from app.db.session import get_session
from app.domain.types import LotteryOutcome, RoundKind, TagKind
from app.web import auth
from app.web.app import create_app

USER = 6262
OTHER = 7373
NOW = datetime(2026, 7, 19, 12, tzinfo=UTC)


def dt(offset_days: float) -> datetime:
    return NOW + timedelta(days=offset_days)


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")  # cascades must fire, as in prod

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


def login(client, discord_id: int = USER, name: str = "reiji"):
    async def fake_identity(token):
        return {"id": str(discord_id), "username": name, "global_name": name, "avatar": None}

    client.monkeypatch.setattr(auth, "fetch_identity", fake_identity)
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    client.get(f"/auth/callback?code=x&state={state}")


# ── seeding helpers ────────────────────────────────────────────────────────


async def _upgrade_concert(
    s, *, event_id="upgrade", base_outcome=None, up_outcome=None,
    up_opens=-2.0, up_closes=3.0, up_payment=None, user=USER, subscribe=True,
    qualifier=True,
):
    """A concert with one leg, a base lottery round and an upgrade round that
    qualifies on the base. Records the given outcomes for `user`."""
    c = Concert(title="Upgrade tour", event_id=event_id, created_by=USER)
    s.add(c)
    await s.flush()
    if subscribe:
        tag = Tag(name=f"Group {event_id}", kind=TagKind.ARTIST)
        s.add(tag)
        await s.flush()
        s.add(ConcertTag(concert_id=c.id, tag_id=tag.id))
        s.add(TagSubscription(user_id=user, tag_id=tag.id))
    leg = ConcertDay(concert_id=c.id, label="Day 1", starts_at_utc=dt(60))
    s.add(leg)
    await s.flush()
    base = Round(
        concert_id=c.id, kind=RoundKind.LOTTERY_ROUND, label="最速先行",
        opens_at_utc=dt(-30), closes_at_utc=dt(-20), applies_to=[leg.id],
    )
    up = Round(
        concert_id=c.id, kind=RoundKind.UPGRADE, label="Seat upgrade",
        opens_at_utc=dt(up_opens), closes_at_utc=dt(up_closes),
        payment_deadline_at_utc=up_payment, applies_to=[leg.id],
    )
    s.add_all([base, up])
    await s.flush()
    if qualifier:
        s.add(RoundQualifier(upgrade_round_id=up.id, qualifying_round_id=base.id))
    if base_outcome is not None:
        s.add(RoundOutcome(user_id=user, round_id=base.id, outcome=base_outcome))
    if up_outcome is not None:
        s.add(RoundOutcome(user_id=user, round_id=up.id, outcome=up_outcome))
    await s.flush()
    return c, base, up


async def _statuses(s, user_id):
    concerts = list((await s.execute(
        select(Concert).options(
            selectinload(Concert.days), selectinload(Concert.rounds)
        )
    )).scalars())
    return await discover_statuses(s, concerts, user_id, NOW), {c.event_id: c for c in concerts}


async def _ensure_users(s):
    s.add_all([User(discord_id=USER, username="reiji"), User(discord_id=OTHER, username="ohter")])
    await s.flush()


# ── Task 6: Discover pills ─────────────────────────────────────────────────


async def test_secured_base_with_open_upgrade_shows_closes_pill(db):
    async with db() as s:
        await _ensure_users(s)
        c, _, _ = await _upgrade_concert(s, base_outcome=LotteryOutcome.PAID)
        statuses, _by = await _statuses(s, USER)
        st = statuses[c.id]
    assert st.text == "Secured"
    assert st.tone == "ok"
    assert st.upgrade_text is not None and st.upgrade_text.startswith("Upgrade · Closes in")
    assert st.upgrade_tone == "accent"


async def test_secured_base_with_applied_upgrade_shows_applied_pill(db):
    async with db() as s:
        await _ensure_users(s)
        c, _, _ = await _upgrade_concert(
            s, base_outcome=LotteryOutcome.PAID, up_outcome=LotteryOutcome.APPLIED
        )
        statuses, _by = await _statuses(s, USER)
        st = statuses[c.id]
    assert st.text == "Secured"
    assert st.upgrade_text == "Upgrade · Applied"
    assert st.upgrade_tone == "accent"


async def test_won_upgrade_collapses_to_a_single_urgent_pill(db):
    async with db() as s:
        await _ensure_users(s)
        c, _, _ = await _upgrade_concert(
            s, base_outcome=LotteryOutcome.PAID, up_outcome=LotteryOutcome.WON,
            up_payment=datetime(2026, 7, 24, 12, tzinfo=UTC),
        )
        statuses, _by = await _statuses(s, USER)
        st = statuses[c.id]
    # ONE pill: the urgent standing replaces the base pill, no second pill.
    assert st.text == "Upgrade won — pay by 24 Jul"
    assert st.tone == "danger"
    assert st.upgrade_text is None


async def test_won_upgrade_without_a_deadline_reads_payment_due(db):
    async with db() as s:
        await _ensure_users(s)
        c, _, _ = await _upgrade_concert(
            s, base_outcome=LotteryOutcome.PAID, up_outcome=LotteryOutcome.WON, up_payment=None
        )
        statuses, _by = await _statuses(s, USER)
        st = statuses[c.id]
    assert st.text == "Upgrade won — payment due"
    assert st.upgrade_text is None


async def test_paid_upgrade_shows_secured_alone(db):
    async with db() as s:
        await _ensure_users(s)
        c, _, _ = await _upgrade_concert(
            s, base_outcome=LotteryOutcome.PAID, up_outcome=LotteryOutcome.PAID
        )
        statuses, _by = await _statuses(s, USER)
        st = statuses[c.id]
    assert st.text == "Secured"
    assert st.upgrade_text is None


async def test_lost_upgrade_shows_base_pill_alone(db):
    async with db() as s:
        await _ensure_users(s)
        c, _, _ = await _upgrade_concert(
            s, base_outcome=LotteryOutcome.PAID, up_outcome=LotteryOutcome.LOST
        )
        statuses, _by = await _statuses(s, USER)
        st = statuses[c.id]
    assert st.text == "Secured"
    assert st.upgrade_text is None


async def test_ineligible_user_sees_no_upgrade_pill(db):
    """A user who only APPLIED to the base (no secured ticket) is not eligible
    for the upgrade and sees no upgrade pill -- just their base standing."""
    async with db() as s:
        await _ensure_users(s)
        c, _, _ = await _upgrade_concert(s, base_outcome=LotteryOutcome.APPLIED)
        statuses, _by = await _statuses(s, USER)
        st = statuses[c.id]
    assert st.upgrade_text is None
    assert "Upgrade ·" not in st.text


async def test_signed_out_sees_no_upgrade_pill_and_same_facet(db):
    async with db() as s:
        await _ensure_users(s)
        c, _, _ = await _upgrade_concert(s, base_outcome=LotteryOutcome.PAID)
        signed_in, _b = await _statuses(s, USER)
        signed_out, _b2 = await _statuses(s, None)
    assert signed_out[c.id].upgrade_text is None
    # The status FACET is event-only: identical signed in and out.
    assert signed_out[c.id].status == signed_in[c.id].status


async def test_neutral_pill_prefers_a_non_upgrade_open_round(db):
    """A viewer with no standing on a concert with BOTH an open general sale
    and an open upgrade is led with the general sale, not the upgrade they
    cannot act on."""
    async with db() as s:
        await _ensure_users(s)
        c = Concert(title="Both open", event_id="both-open", created_by=USER)
        s.add(c)
        await s.flush()
        leg = ConcertDay(concert_id=c.id, label="Day 1", starts_at_utc=dt(60))
        s.add(leg)
        await s.flush()
        s.add_all([
            Round(concert_id=c.id, kind=RoundKind.GENERAL_SALE, label="General",
                  opens_at_utc=dt(-1), closes_at_utc=dt(5), applies_to=[leg.id]),
            Round(concert_id=c.id, kind=RoundKind.UPGRADE, label="Upgrade",
                  opens_at_utc=dt(-1), closes_at_utc=dt(2), applies_to=[leg.id]),
        ])
        await s.flush()
        statuses, _by = await _statuses(s, USER)
        st = statuses[c.id]
    assert st.text.startswith("General sale")
    assert "Upgrade round" not in st.text


async def test_get_discover_renders_the_upgrade_pill_for_an_eligible_user(client):
    async def seed(s):
        await _ensure_users(s)
        await _upgrade_concert(s, base_outcome=LotteryOutcome.PAID)

    async with client.db() as s:
        await seed(s)
        await s.commit()
    login(client)

    html = client.get("/discover").text
    assert "Upgrade · Closes in" in html
    assert "s-up" in html


def test_get_discover_signed_out_still_renders(client):
    r = client.get("/discover")
    assert r.status_code == 200
