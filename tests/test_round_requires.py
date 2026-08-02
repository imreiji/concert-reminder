"""Taxonomy tests for the GOODS_SALE round kind: the new enum member,
ITEM_SALE_KINDS, ingest keyword mapping, and label/emoji coverage. Also
covers Round.required_item_round_id, the self-FK an item-sale round's
dependents point at (SET NULL on the target's delete)."""

import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Concert, Round
from app.db.service import ensure_user
from app.domain.ingest import _guess_kind
from app.domain.types import ITEM_SALE_KINDS, RoundKind


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
