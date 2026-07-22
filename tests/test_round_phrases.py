"""The remembered round-label triples behind the phrase picker."""

import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, RoundLabelPhrase
from app.db.service import (
    forget_round_label_phrase,
    record_round_label_phrase,
    round_label_phrases,
)


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    # Production registers this too; cascades silently do not fire without it.
    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def test_phrase_columns_and_unique_index():
    cols = RoundLabelPhrase.__table__.columns
    for name in ("label", "label_en", "label_zh"):
        assert name in cols, f"RoundLabelPhrase.{name} missing"
        assert not cols[name].nullable, f"{name} must be NOT NULL"
        assert cols[name].type.length == 200
    assert cols["used_count"].default is not None

    # A named unique index over the whole triple: two phrases sharing a
    # Japanese label but differing in translation are distinct rows, which is
    # what lets a corrected phrase coexist with the typo it replaces.
    idx = {i.name: i for i in RoundLabelPhrase.__table__.indexes}
    assert "uq_round_label_phrase" in idx, f"got {list(idx)}"
    target = idx["uq_round_label_phrase"]
    assert target.unique
    assert [c.name for c in target.columns] == ["label", "label_en", "label_zh"]


async def test_a_phrase_round_trips(db):
    async with db() as session:
        session.add(RoundLabelPhrase(
            label="1次先行抽選", label_en="1st-round lottery", label_zh="第一轮先行",
        ))
        await session.commit()

    async with db() as session:
        row = (await session.execute(select(RoundLabelPhrase))).scalar_one()
        assert row.used_count == 1
        assert row.created_at.tzinfo is not None, "timestamps are aware UTC"


async def test_recording_a_phrase_twice_bumps_its_use_count(db):
    async with db() as session:
        await record_round_label_phrase(session, "1次先行", "1st advance", "第一轮先行")
        await record_round_label_phrase(session, "1次先行", "1st advance", "第一轮先行")
        await session.commit()

    async with db() as session:
        row = (await session.execute(select(RoundLabelPhrase))).scalar_one()
        assert row.used_count == 2, "the second save reuses the row, not a duplicate"


async def test_a_partial_triple_is_not_recorded(db):
    """A phrase is only worth remembering when all three languages are there —
    a half-filled triple would be offered as a suggestion that leaves the
    editor with blanks to fill anyway."""
    async with db() as session:
        await record_round_label_phrase(session, "1次先行", "1st advance", "")
        await record_round_label_phrase(session, "", "1st advance", "第一轮先行")
        await record_round_label_phrase(session, "  ", "  ", "  ")
        await session.commit()

    async with db() as session:
        assert (await session.execute(select(RoundLabelPhrase))).scalars().all() == []


async def test_phrases_rank_by_use_then_recency(db):
    async with db() as session:
        for _ in range(3):
            await record_round_label_phrase(session, "A", "A", "A")
        await record_round_label_phrase(session, "B", "B", "B")
        await record_round_label_phrase(session, "C", "C", "C")
        await session.commit()

    async with db() as session:
        rows = await round_label_phrases(session)
        assert rows[0].label == "A", "most-used first"
        assert [r.label for r in rows[1:]] == ["C", "B"], "then most-recent first"


async def test_forgetting_a_phrase_removes_only_the_suggestion(db):
    async with db() as session:
        await record_round_label_phrase(session, "typo", "Offical", "官方")
        await session.commit()
        row = (await session.execute(select(RoundLabelPhrase))).scalar_one()
        assert await forget_round_label_phrase(session, row.id) is True
        await session.commit()

    async with db() as session:
        assert (await session.execute(select(RoundLabelPhrase))).scalars().all() == []


async def test_forgetting_an_unknown_phrase_is_false_not_an_error(db):
    async with db() as session:
        assert await forget_round_label_phrase(session, 9999) is False
