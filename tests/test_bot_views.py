"""Discord persistent buttons (`app.bot.views`), tested directly against a
fake Interaction and a real in-memory async engine -- same shape as
test_bot_reminders.py, since discord.py's gateway is never involved.
"""

from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.bot import views as views_module
from app.db.models import Base, Concert, ConcertDay, Round, User
from app.domain.types import RoundKind


def dt(month: int, day: int, hour: int = 12) -> datetime:
    return datetime(2099, month, day, hour, tzinfo=UTC)


@pytest_asyncio.fixture()
async def db(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(views_module, "SessionMaker", maker)
    yield maker
    await engine.dispose()


class FakeUser:
    def __init__(self, discord_id: int, name: str):
        self.id = discord_id
        self.name = name


class FakeResponse:
    def __init__(self):
        self.sent: dict | None = None

    async def send_message(self, *args, **kwargs):
        self.sent = {"args": args, "kwargs": kwargs}


class FakeInteraction:
    def __init__(self, discord_id: int, name: str = "reiji"):
        self.user = FakeUser(discord_id, name)
        self.response = FakeResponse()


async def test_show_deadlines_localizes_round_and_leg_labels(db):
    """The 'Show all deadlines' button already localizes everything else in
    the message -- it calls set_locale(user.language) up front and threads
    the resulting `loc` into every fmt_dual call -- but the round and leg
    LABELS themselves used to slip through as the raw `r.label`/`d.label`
    columns. The fixture user's language is "zh", not the ambient "en"
    default, and only label_zh is asserted present/label (JA original)
    absent, so a locale-source mistake fails loudly rather than
    coincidentally passing."""
    async with db() as s:
        s.add(User(discord_id=42, username="reiji", language="zh"))
        concert = Concert(title="T", event_id="t1", created_by=42)
        s.add(concert)
        await s.flush()
        s.add(Round(
            concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND,
            label="1次先行抽選", label_zh="第一轮先行", label_en="1st-round lottery",
            closes_at_utc=dt(6, 25),
        ))
        s.add(ConcertDay(
            concert_id=concert.id, label="2日目 夜公演",
            label_zh="第二天 夜场", label_en="Day 2 evening",
            starts_at_utc=dt(6, 20),
        ))
        await s.commit()
        cid = concert.id

    button = views_module.ShowDeadlinesButton(cid)
    interaction = FakeInteraction(42)
    await button.callback(interaction)
    text = interaction.response.sent["args"][0]
    assert "第一轮先行" in text
    assert "1次先行抽選" not in text, (
        "the round label must not leak the Japanese original to a zh viewer"
    )
    assert "第二天 夜场" in text
    assert "2日目 夜公演" not in text, (
        "the leg label must not leak the Japanese original to a zh viewer"
    )
