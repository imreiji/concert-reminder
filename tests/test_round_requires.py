"""Taxonomy tests for the GOODS_SALE round kind: the new enum member,
ITEM_SALE_KINDS, ingest keyword mapping, and label/emoji coverage. Also
covers Round.required_item_round_id, the self-FK an item-sale round's
dependents point at (SET NULL on the target's delete)."""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base, Concert, Round
from app.db.service import ensure_user
from app.db.session import get_session
from app.domain.ingest import _guess_kind
from app.domain.types import ITEM_SALE_KINDS, RoundKind
from app.web import auth
from app.web.app import create_app
from app.web.routes.concerts import RoundRequiresError, resolve_round_requires

EDITOR_ID = 42


def test_goods_sale_is_a_round_kind():
    assert RoundKind.GOODS_SALE.value == "goods_sale"


def test_item_sale_kinds_are_the_two_item_kinds():
    assert ITEM_SALE_KINDS == {RoundKind.ELIGIBILITY_ITEM_SALE, RoundKind.GOODS_SALE}


def test_guess_kind_maps_goods_keywords():
    assert _guess_kind("グッズ販売") is RoundKind.GOODS_SALE
    assert _guess_kind("Tour Goods Pre-order") is RoundKind.GOODS_SALE
    assert _guess_kind("会場物販") is RoundKind.GOODS_SALE
    # The serial-code sale stays what it was:
    assert _guess_kind("シリアル対象CD発売") is not RoundKind.GOODS_SALE


def test_label_and_emoji_cover_every_kind():
    from app.bot.messages import KIND_EMOJI
    from app.db.service import LABEL_BY_ROUND_KIND

    for kind in RoundKind:
        assert kind in LABEL_BY_ROUND_KIND
        assert kind.value in KIND_EMOJI


@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def test_required_item_round_set_null_on_target_delete(session):
    # Build a concert with an item-sale round and a lottery round that
    # requires it, straight through the models (write-boundary validation is
    # a route concern, Tasks 3-5).
    await ensure_user(session, 1, "reiji")
    concert = Concert(title="t", event_id="t-1", created_by=1)
    session.add(concert)
    await session.flush()
    item = Round(concert_id=concert.id, kind=RoundKind.GOODS_SALE, label="グッズ")
    session.add(item)
    await session.flush()
    lottery = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="最速先行",
        required_item_round_id=item.id,
    )
    session.add(lottery)
    await session.flush()

    await session.delete(item)
    await session.flush()
    await session.refresh(lottery)
    assert lottery.required_item_round_id is None


# ── The resolver (unit) ───────────────────────────────────────────────────


def test_resolver_resolves_key_and_id_tokens():
    kinds = {1: RoundKind.GOODS_SALE, 2: RoundKind.LOTTERY_ROUND}
    keys = {"r0": 1}
    assert resolve_round_requires("r0", keys, kinds, self_id=2) == 1
    assert resolve_round_requires("1", keys, kinds, self_id=2) == 1
    assert resolve_round_requires("", keys, kinds, self_id=2) is None


def test_resolver_rejects_missing_wrong_kind_and_self():
    kinds = {1: RoundKind.GOODS_SALE, 2: RoundKind.LOTTERY_ROUND}
    with pytest.raises(RoundRequiresError):
        resolve_round_requires("99", {}, kinds, self_id=2)   # not on this concert
    with pytest.raises(RoundRequiresError):
        resolve_round_requires("2", {}, kinds, self_id=3)    # target not an item kind
    with pytest.raises(RoundRequiresError):
        resolve_round_requires("1", {}, kinds, self_id=1)    # itself


# ── create_concert wiring (HTTP) ──────────────────────────────────────────
#
# Same logged-in-editor client shape as tests/test_crud.py: a fresh in-memory
# DB per test, get_session dependency-overridden, login simulated by
# monkeypatching the auth module's Discord calls.


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


async def test_create_concert_binds_requires_by_round_key(client):
    # Two rounds in one submit: row 0 is the goods sale (key g1), row 1
    # requires it by key. Assert the saved lottery round points at the saved
    # goods round's real id.
    login_as(client, EDITOR_ID, "reiji")
    r = client.post(
        "/concerts",
        data={
            "title_en": "Requires Test", "title_zh": "Requires Test",
            "title": "Requires Test", "event_id": "requires-test",
            "round_label": ["Goods sale", "Lottery"],
            "round_label_en": ["Goods sale", "Lottery"],
            "round_label_zh": ["Goods sale", "Lottery"],
            "round_kind": ["goods_sale", "lottery_round"],
            "round_opens_at": ["2099-06-01T00:00", "2099-06-01T00:00"],
            "round_closes_at": ["2099-06-15T23:59", "2099-06-15T23:59"],
            "round_results_at": ["", ""],
            "round_payment_at": ["", ""],
            "round_url": ["", ""],
            "round_notes": ["", ""],
            "round_legs": ["", ""],
            "round_key": ["g1", "x2"],
            "round_requires": ["", "g1"],
        },
    )
    assert r.status_code == 303, r.text

    async with client.db() as s:
        concert = (await s.execute(
            select(Concert).where(Concert.event_id == "requires-test")
        )).scalar_one()
        rounds = (await s.execute(
            select(Round).where(Round.concert_id == concert.id)
        )).scalars().all()
    goods = next(r for r in rounds if r.label == "Goods sale")
    lottery = next(r for r in rounds if r.label == "Lottery")
    assert lottery.required_item_round_id == goods.id


async def test_create_concert_422_on_wrong_kind_target(client):
    # round_requires names the OTHER lottery round -> 422, nothing persisted.
    login_as(client, EDITOR_ID, "reiji")
    r = client.post(
        "/concerts",
        data={
            "title_en": "Bad Requires", "title_zh": "Bad Requires",
            "title": "Bad Requires", "event_id": "bad-requires",
            "round_label": ["L1", "L2"],
            "round_label_en": ["L1", "L2"],
            "round_label_zh": ["L1", "L2"],
            "round_kind": ["lottery_round", "lottery_round"],
            "round_opens_at": ["2099-06-01T00:00", "2099-06-01T00:00"],
            "round_closes_at": ["2099-06-15T23:59", "2099-06-15T23:59"],
            "round_results_at": ["", ""],
            "round_payment_at": ["", ""],
            "round_url": ["", ""],
            "round_notes": ["", ""],
            "round_legs": ["", ""],
            "round_key": ["k1", "k2"],
            "round_requires": ["", "k1"],  # L2 requires L1, a LOTTERY_ROUND
        },
    )
    assert r.status_code == 422, r.text

    async with client.db() as s:
        result = await s.execute(
            select(Concert).where(Concert.event_id == "bad-requires")
        )
        assert result.scalar_one_or_none() is None
