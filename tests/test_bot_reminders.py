"""/mydeadlines: the personalized counterpart to /upcoming.

Discord is never imported here beyond discord.py's own dataclasses -- the
command handler is called directly (its underlying coroutine, same as
every other app_commands.Command), with a fake interaction object, against
a real in-memory async SQLite (same fixture shape test_service.py uses).
"""

from datetime import UTC, datetime, timedelta

import pytest_asyncio
from discord import app_commands
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.bot.cogs import reminders as reminders_cog
from app.db.models import Base, Concert, ReminderRule, Round
from app.db.service import (
    due_reminders,
    ensure_user,
    set_concert_subscription,
    sync_rule,
)
from app.domain.types import Anchor, RoundKind, SubscriptionState

NOW = datetime(2099, 6, 1, tzinfo=UTC)


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
    monkeypatch.setattr(reminders_cog, "SessionMaker", maker)
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


async def call_mydeadlines(interaction: FakeInteraction, count: int = 10) -> None:
    cog = reminders_cog.Reminders(bot=None)
    await reminders_cog.Reminders.mydeadlines.callback(cog, interaction, count)


async def call_upcoming(interaction: FakeInteraction, days: int = 14) -> None:
    cog = reminders_cog.Reminders(bot=None)
    await reminders_cog.Reminders.upcoming.callback(cog, interaction, days)


async def test_upcoming_localizes_round_label(db):
    """/upcoming already localizes the concert title via loc_field(concert,
    "title", loc) one line above -- the round label in the same f-string was
    left as the raw `round_.label` column. The fixture user's language is
    "zh", not the ambient "en" default, and only the Japanese original is
    asserted absent (not merely the zh string present), so a locale-source
    mistake fails loudly rather than coincidentally passing.

    upcoming_rounds() has no injectable `now` (unlike sync_rule/due_reminders
    above) -- it always queries against the real clock -- so this round's
    closes_at_utc must be a real near-future timestamp, not the fixed-2099
    dates the other tests in this file use for sync_rule's benefit."""
    async with db() as s:
        user = await ensure_user(s, 42, "reiji")
        user.language = "zh"
        concert = Concert(title="Hasunosora 5th", event_id="hasu-5th", created_by=42)
        s.add(concert)
        await s.flush()
        round_ = Round(
            concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND,
            label="1次先行抽選", label_zh="第一轮先行", label_en="1st-round lottery",
            closes_at_utc=datetime.now(UTC) + timedelta(days=5),
        )
        s.add(round_)
        await s.commit()

    interaction = FakeInteraction(42)
    await call_upcoming(interaction)
    embed = interaction.response.sent["kwargs"]["embed"]
    assert "第一轮先行" in embed.description
    assert "1次先行抽選" not in embed.description, (
        "the round label must not leak the Japanese original to a zh viewer"
    )


async def test_mydeadlines_empty_state(db):
    interaction = FakeInteraction(42)
    await call_mydeadlines(interaction)
    assert "Nothing on your calendar yet" in interaction.response.sent["args"][0]


async def test_mydeadlines_lists_only_this_users_landscape(db):
    """The command answers from the caller's own standing (spec 2026-08-04),
    so the isolation claim is unchanged but rests on tracking rather than on
    rules: user 42 tracks this concert, user 777 does not."""
    async with db() as s:
        await ensure_user(s, 42, "reiji")
        concert = Concert(title="Hasunosora 5th", event_id="hasu-5th", created_by=42)
        s.add(concert)
        await s.flush()
        round_ = Round(
            concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="R1",
            closes_at_utc=dt(6, 25),
        )
        s.add(round_)
        await s.flush()
        await set_concert_subscription(s, 42, concert.id, SubscriptionState.SUBSCRIBED)
        await s.commit()

    interaction = FakeInteraction(42)
    await call_mydeadlines(interaction)
    embed = interaction.response.sent["kwargs"]["embed"]
    assert "Hasunosora 5th" in embed.description
    assert "R1" in embed.description
    assert "apply by" in embed.description  # closes-moment qualifier, English catalogue
    assert interaction.response.sent["kwargs"]["ephemeral"] is True

    # a different user tracking nothing sees the empty state, not someone
    # else's deadlines
    other = FakeInteraction(777, "other")
    await call_mydeadlines(other)
    assert "Nothing on your calendar yet" in other.response.sent["args"][0]


async def test_due_reminders_populates_user_language(db):
    """The scheduler needs each recipient's language to localize the DM, so
    due_reminders() must carry User.language onto every DueReminder it emits."""
    async with db() as s:
        user = await ensure_user(s, 42, "reiji")
        user.language = "ja"
        concert = Concert(title="Hasunosora 5th", event_id="hasu-5th", created_by=42)
        s.add(concert)
        await s.flush()
        round_ = Round(
            concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="R1",
            closes_at_utc=dt(6, 25),
        )
        s.add(round_)
        await s.flush()
        rule = ReminderRule(user_id=42, round_id=round_.id, anchor=Anchor.CLOSES, offset_days=-3)
        s.add(rule)
        await s.flush()
        await sync_rule(s, rule, NOW)
        await s.commit()

    async with db() as s:
        items = await due_reminders(s, datetime(2099, 6, 24, tzinfo=UTC))
    assert items
    assert all(item.user_language == "ja" for item in items)


async def test_mydeadlines_respects_count_and_clamps_range(db):
    async with db() as s:
        await ensure_user(s, 42, "reiji")
        concert = Concert(title="C", event_id="c", created_by=42)
        s.add(concert)
        await s.flush()
        for i in range(3):
            # closes only, so each no-outcome round contributes exactly one
            # moment and the count under test is unambiguous.
            round_ = Round(
                concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label=f"R{i}",
                closes_at_utc=dt(6, 25 + i),
            )
            s.add(round_)
        await s.flush()
        await set_concert_subscription(s, 42, concert.id, SubscriptionState.SUBSCRIBED)
        await s.commit()

    interaction = FakeInteraction(42)
    await call_mydeadlines(interaction, count=2)
    embed = interaction.response.sent["kwargs"]["embed"]
    assert embed.description.count("**C**") == 2  # capped at count, not all 3


async def _seed_one_concert(db) -> int:
    async with db() as s:
        await ensure_user(s, 42, "reiji")
        concert = Concert(title="Hasunosora 5th", event_id="hasu-5th-remindme", created_by=42)
        s.add(concert)
        await s.flush()
        concert_id = concert.id
        await s.commit()
    return concert_id


async def test_remindme_stores_a_sub_hour_offset(db):
    """/remindme 0 days, 30 minutes -> (0, 0, -30), decomposed on the way in."""
    interaction = FakeInteraction(42)
    concert_id = await _seed_one_concert(db)
    cog = reminders_cog.Reminders(bot=None)

    await reminders_cog.Reminders.remindme.callback(
        cog,
        interaction,
        concert=concert_id,
        anchor=app_commands.Choice(
            name="before it closes (deadlines)", value=Anchor.CLOSES.value
        ),
        days_before=0,
        minutes_before=30,
    )

    async with db() as s:
        rule = (await s.execute(select(ReminderRule))).scalar_one()
    assert (rule.offset_days, rule.offset_hours, rule.offset_minutes) == (0, 0, -30)
    assert "30 minutes before" in interaction.response.sent["args"][0]


async def test_myreminders_describes_an_hours_rule_as_hours(db):
    """The same misreport the concert page had: abs(offset_days) alone printed
    "same-day" for every sub-day rule. Pinned first with an HOURS-only rule
    (no minutes), since that is the case already broken in production."""
    interaction = FakeInteraction(42)
    async with db() as s:
        await ensure_user(s, 42, "reiji")
        concert = Concert(title="C", event_id="c-myreminders", created_by=42)
        s.add(concert)
        await s.flush()
        s.add(
            ReminderRule(
                user_id=42,
                concert_id=concert.id,
                anchor=Anchor.CLOSES,
                offset_days=0,
                offset_hours=-3,
                offset_minutes=0,
            )
        )
        await s.commit()

    cog = reminders_cog.Reminders(bot=None)
    await reminders_cog.Reminders.myreminders.callback(cog, interaction)

    sent = interaction.response.sent["args"][0]
    assert "3 hours before" in sent
    assert "same-day" not in sent


async def test_myreminders_describes_a_minutes_rule_as_minutes(db):
    """Minutes-only rule on top of the hours case above."""
    interaction = FakeInteraction(42)
    async with db() as s:
        await ensure_user(s, 42, "reiji")
        concert = Concert(title="C2", event_id="c2-myreminders", created_by=42)
        s.add(concert)
        await s.flush()
        s.add(
            ReminderRule(
                user_id=42,
                concert_id=concert.id,
                anchor=Anchor.CLOSES,
                offset_days=0,
                offset_hours=0,
                offset_minutes=-30,
            )
        )
        await s.commit()

    cog = reminders_cog.Reminders(bot=None)
    await reminders_cog.Reminders.myreminders.callback(cog, interaction)

    sent = interaction.response.sent["args"][0]
    assert "30 minutes before" in sent
    assert "same-day" not in sent
