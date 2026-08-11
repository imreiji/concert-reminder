"""Discord persistent buttons (`app.bot.views`), tested directly against a
fake Interaction and a real in-memory async engine -- same shape as
test_bot_reminders.py, since discord.py's gateway is never involved.
"""

from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.bot import views as views_module
from app.db.models import (
    Base,
    Concert,
    ConcertDay,
    LegOptOut,
    ReminderRule,
    Round,
    RoundOutcome,
    RoundOutcomeDay,
    User,
)
from app.db.service import record_round_day_result, record_round_outcome
from app.domain.types import Anchor, LegResult, LotteryOutcome, RoundKind


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
        self.edited: dict | None = None

    async def send_message(self, *args, **kwargs):
        self.sent = {"args": args, "kwargs": kwargs}

    async def edit_message(self, *args, **kwargs):
        """The progressive result buttons EDIT the DM they were pressed on
        instead of replying under it -- the question changes as legs get
        answered, so a thread of replies would leave the stale question
        pressable above the new one."""
        self.edited = {"args": args, "kwargs": kwargs}


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


async def test_show_deadlines_marks_every_round_on_a_dead_concert(db):
    """The DM twin of the concert page: the list SHOWS cancelled rounds rather
    than hiding them (the reader asked about this one concert), but on a
    concert whose every leg is cancelled the suffix has to reach all of them.
    `is_round_cancelled` alone leaves a General round -- tied to no leg --
    unmarked while every day line beside it reads (cancelled), which is the
    same lie at DM scale."""
    async with db() as s:
        s.add(User(discord_id=42, username="reiji", language="en"))
        concert = Concert(title="T", event_id="t-dead", created_by=42)
        s.add(concert)
        await s.flush()
        day = ConcertDay(
            concert_id=concert.id, label="Day 1",
            starts_at_utc=dt(6, 20), cancelled=True,
        )
        s.add(day)
        await s.flush()
        s.add(Round(
            concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="Day 1 presale",
            closes_at_utc=dt(6, 10), applies_to=[day.id],
        ))
        s.add(Round(
            concert_id=concert.id, kind=RoundKind.GENERAL_SALE, label="General sale",
            closes_at_utc=dt(6, 15),
        ))
        await s.commit()
        cid = concert.id

    button = views_module.ShowDeadlinesButton(cid)
    interaction = FakeInteraction(42)
    await button.callback(interaction)
    lines = interaction.response.sent["args"][0].splitlines()

    assert len(lines) == 3, "nothing is hidden -- both rounds and the leg still list"
    assert all("(cancelled)" in line for line in lines), lines


async def dead_concert_with_a_rule(db, *, dead: bool = True):
    """A concert with one leg and one General round, a user holding a
    concert-scoped rule on it, and the leg cancelled (or not). Returns the
    concert id."""
    async with db() as s:
        s.add(User(discord_id=42, username="reiji", language="en"))
        concert = Concert(title="T", event_id="t-reinstate", created_by=42)
        s.add(concert)
        await s.flush()
        s.add(ConcertDay(
            concert_id=concert.id, label="Day 1", starts_at_utc=dt(6, 20), cancelled=dead,
        ))
        s.add(Round(
            concert_id=concert.id, kind=RoundKind.GENERAL_SALE, label="General sale",
            closes_at_utc=dt(6, 15),
        ))
        s.add(ReminderRule(
            user_id=42, concert_id=concert.id, anchor=Anchor.CLOSES, offset_days=-3,
        ))
        await s.commit()
        return concert.id


async def test_reinstate_on_a_dead_concert_promises_no_reminders(db):
    """`reinstate_user_rules` returns how many RULES it re-synced -- the rules
    survive a cancellation untouched, only their queue rows go. So on a concert
    whose every leg is cancelled it returns n > 0 while the planner arms
    nothing, and the count reply ("you'll be notified again for any active
    ones") names notifications that can never arrive. The DM carrying this
    button now fires for whole-event death, so this is the reply that case
    gets."""
    cid = await dead_concert_with_a_rule(db)

    button = views_module.ReinstateRemindersButton(cid)
    interaction = FakeInteraction(42)
    await button.callback(interaction)
    text = interaction.response.sent["args"][0]

    assert "This event is cancelled" in text
    assert "Turned" not in text, "no reminder was re-armed -- the planner drops every round"
    assert "You'll be notified again" not in text, (
        "the notification it promised can never arrive while every leg is off"
    )


async def test_reinstate_on_a_live_concert_still_counts_the_rules(db):
    """One live leg is the case the count reply was written for -- unchanged."""
    cid = await dead_concert_with_a_rule(db, dead=False)

    button = views_module.ReinstateRemindersButton(cid)
    interaction = FakeInteraction(42)
    await button.callback(interaction)
    text = interaction.response.sent["args"][0]

    assert "Turned 1 reminder back on" in text
    assert "This event is cancelled" not in text


# ── Progressive per-day result capture (task 8) ──────────────────────────
#
# Every one of these presses a button against a REAL round in the DB and then
# asserts two things: what got written, and what the edited message now
# offers. The second half is the point -- these buttons are persistent, so
# each callback re-derives its reply from the database rather than from the
# message it was pressed on.


def custom_ids(view) -> list[str]:
    return [cid for cid in (getattr(c, "custom_id", None) for c in view.children) if cid]


async def three_leg_round(db, *, language: str = "en", outcome: LotteryOutcome | None = None):
    """A concert with three legs and one lottery round covering all of them,
    optionally with a round-level outcome already recorded.
    Returns (round_id, [day_id, day_id, day_id])."""
    async with db() as s:
        s.add(User(discord_id=42, username="reiji", language=language))
        concert = Concert(title="Tour", event_id="tour", created_by=42)
        s.add(concert)
        await s.flush()
        days = [
            ConcertDay(
                concert_id=concert.id, label=f"{n}日目",
                label_zh=f"第{n}天", label_en=f"Day {n}", starts_at_utc=dt(6, 19 + n),
            )
            for n in (1, 2, 3)
        ]
        for d in days:
            s.add(d)
        round_ = Round(
            concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="1次先行",
            label_zh="第一轮", label_en="1st round", results_at_utc=dt(6, 26),
        )
        s.add(round_)
        await s.flush()
        if outcome is not None:
            s.add(RoundOutcome(user_id=42, round_id=round_.id, outcome=outcome))
        await s.commit()
        return round_.id, [d.id for d in days]


async def test_won_day_press_asks_about_the_remaining_legs(db):
    """The heart of the flow: answering one leg records THAT leg and re-asks
    about the others -- never about the one just answered."""
    rid, (d1, d2, d3) = await three_leg_round(db, outcome=LotteryOutcome.APPLIED)

    interaction = FakeInteraction(42)
    await views_module.WonDayButton(rid, d1, "Day 1").callback(interaction)

    async with db() as s:
        rows = {
            r.day_id: r.result for r in (await s.execute(
                select(RoundOutcomeDay).where(RoundOutcomeDay.round_id == rid)
            )).scalars()
        }
    assert rows == {d1: LegResult.WON}

    edited = interaction.response.edited
    assert edited is not None, "the press must edit the DM, not reply under it"
    ids = custom_ids(edited["kwargs"]["view"])
    assert f"dk:wonday:{rid}:{d2}" in ids
    assert f"dk:wonday:{rid}:{d3}" in ids
    assert f"dk:wonday:{rid}:{d1}" not in ids, "the answered leg must not be asked again"
    assert f"dk:lostday:{rid}:{d2}" in ids
    assert f"dk:skipday:{rid}:{d2}" in ids
    # A leg is already secured, so the shortcut is "lost the rest", never
    # "lost (all)" -- which would throw away the ticket just recorded.
    assert f"dk:lostrest:{rid}" in ids
    assert f"dk:lostall:{rid}" not in ids
    assert "Day 1" in edited["kwargs"]["content"]


async def test_lost_rest_settles_the_round_and_asks_about_payment(db):
    rid, (d1, _d2, _d3) = await three_leg_round(db, outcome=LotteryOutcome.APPLIED)
    await views_module.WonDayButton(rid, d1, "Day 1").callback(FakeInteraction(42))

    interaction = FakeInteraction(42)
    await views_module.LostRestButton(rid).callback(interaction)

    async with db() as s:
        results = sorted(
            r.result for r in (await s.execute(
                select(RoundOutcomeDay).where(RoundOutcomeDay.round_id == rid)
            )).scalars()
        )
        outcome = (await s.execute(
            select(RoundOutcome.outcome).where(RoundOutcome.round_id == rid)
        )).scalar_one()
    assert results == [LegResult.LOST, LegResult.LOST, LegResult.WON]
    assert outcome is LotteryOutcome.WON, "a partial win keeps the round secured"

    ids = custom_ids(interaction.response.edited["kwargs"]["view"])
    assert ids == [f"dk:paidnow:{rid}", f"dk:paylater:{rid}"]


async def test_paid_now_records_paid_and_clears_the_buttons(db):
    rid, (d1, _d2, _d3) = await three_leg_round(db, outcome=LotteryOutcome.APPLIED)
    await views_module.WonDayButton(rid, d1, "Day 1").callback(FakeInteraction(42))
    await views_module.LostRestButton(rid).callback(FakeInteraction(42))

    interaction = FakeInteraction(42)
    await views_module.PaidNowButton(rid).callback(interaction)

    async with db() as s:
        outcome = (await s.execute(
            select(RoundOutcome.outcome).where(RoundOutcome.round_id == rid)
        )).scalar_one()
    assert outcome is LotteryOutcome.PAID
    assert custom_ids(interaction.response.edited["kwargs"]["view"]) == [f"dk:clear:{rid}"], (
        "a settled reply is exactly where a mis-press is noticed, so it carries the "
        "way back rather than a bare 'all set'"
    )


async def test_pay_later_writes_nothing(db):
    rid, (d1, _d2, _d3) = await three_leg_round(db, outcome=LotteryOutcome.APPLIED)
    await views_module.WonDayButton(rid, d1, "Day 1").callback(FakeInteraction(42))
    await views_module.LostRestButton(rid).callback(FakeInteraction(42))

    interaction = FakeInteraction(42)
    await views_module.PayLaterButton(rid).callback(interaction)

    async with db() as s:
        outcome = (await s.execute(
            select(RoundOutcome.outcome).where(RoundOutcome.round_id == rid)
        )).scalar_one()
    assert outcome is LotteryOutcome.WON, "'not yet' is an answer, not a write"
    assert custom_ids(interaction.response.edited["kwargs"]["view"]) == [f"dk:clear:{rid}"], (
        "the end of the branch a mis-pressed 'Won (all)' walks into: the round is "
        "left WON with its payment reminder armed, so this reply above all needs "
        "the way back -- and its sibling 'Marked as paid — all set!' has one"
    )


async def test_won_day_press_on_an_already_paid_round_keeps_paid(db):
    """A stale DM: the reader resolved this round on the site (and paid) after
    the reminder went out, then pressed a button on the old message. The write
    must not demote PAID back to WON -- which would re-arm the payment
    reminder for a ticket already paid for -- and the reply must render the
    state as it is now."""
    rid, (d1, _d2, _d3) = await three_leg_round(db, outcome=LotteryOutcome.WON)
    async with db() as s:
        await record_round_outcome(s, 42, rid, LotteryOutcome.PAID)
        await s.commit()

    interaction = FakeInteraction(42)
    await views_module.WonDayButton(rid, d1, "Day 1").callback(interaction)

    async with db() as s:
        outcome = (await s.execute(
            select(RoundOutcome.outcome).where(RoundOutcome.round_id == rid)
        )).scalar_one()
    assert outcome is LotteryOutcome.PAID
    assert custom_ids(interaction.response.edited["kwargs"]["view"]) == [f"dk:clear:{rid}"], (
        "nothing is left to ASK about a round paid for in full -- only the way back"
    )


async def test_won_all_on_an_already_paid_round_writes_nothing(db):
    """The all-legs shortcuts are as stale-prone as the per-day ones: this DM
    can be pressed after the round was resolved and PAID on the site, and
    `record_round_outcome` would happily overwrite PAID back to WON --
    re-arming the payment reminder for a ticket already paid for."""
    rid, _days = await three_leg_round(db, outcome=LotteryOutcome.WON)
    async with db() as s:
        await record_round_outcome(s, 42, rid, LotteryOutcome.PAID)
        await s.commit()
        before = (await s.execute(
            select(RoundOutcome).where(RoundOutcome.round_id == rid)
        )).scalar_one().updated_at

    interaction = FakeInteraction(42)
    await views_module.WonAllButton(rid).callback(interaction)

    async with db() as s:
        row = (await s.execute(
            select(RoundOutcome).where(RoundOutcome.round_id == rid)
        )).scalar_one()
    assert row.outcome is LotteryOutcome.PAID
    assert row.updated_at == before, "a stale press must not write at all"
    assert custom_ids(interaction.response.edited["kwargs"]["view"]) == [f"dk:clear:{rid}"]


async def test_lost_all_on_an_already_paid_round_writes_nothing(db):
    """The same guard from the other direction, where the loss is worse: LOST
    can be set from any state, so an unguarded stale press would wipe a
    secured -- and paid for -- round."""
    rid, _days = await three_leg_round(db, outcome=LotteryOutcome.WON)
    async with db() as s:
        await record_round_outcome(s, 42, rid, LotteryOutcome.PAID)
        await s.commit()
        before = (await s.execute(
            select(RoundOutcome).where(RoundOutcome.round_id == rid)
        )).scalar_one().updated_at

    interaction = FakeInteraction(42)
    await views_module.LostAllButton(rid).callback(interaction)

    async with db() as s:
        row = (await s.execute(
            select(RoundOutcome).where(RoundOutcome.round_id == rid)
        )).scalar_one()
    assert row.outcome is LotteryOutcome.PAID
    assert row.updated_at == before
    assert custom_ids(interaction.response.edited["kwargs"]["view"]) == [f"dk:clear:{rid}"]


async def test_won_all_after_a_lost_leg_wins_the_legs_still_open(db):
    """'Won (all)' on a round already being resolved leg by leg -- the reader
    recorded "Lost — Day 1" on the site first. The round-level WON alone would
    leave a contradiction (outcome WON, not one WON row) whose follow-ups can
    erase the win, so the legs still open get real WON rows. The recorded loss
    stands: "all" means all the ones still open."""
    rid, (d1, d2, d3) = await three_leg_round(db, outcome=LotteryOutcome.APPLIED)
    async with db() as s:
        await record_round_day_result(s, 42, rid, d1, LegResult.LOST)
        await s.commit()

    interaction = FakeInteraction(42)
    await views_module.WonAllButton(rid).callback(interaction)

    async with db() as s:
        outcome = (await s.execute(
            select(RoundOutcome.outcome).where(RoundOutcome.round_id == rid)
        )).scalar_one()
        rows = {
            r.day_id: r.result for r in (await s.execute(
                select(RoundOutcomeDay).where(RoundOutcomeDay.round_id == rid)
            )).scalars()
        }
    assert outcome is LotteryOutcome.WON
    assert rows == {d1: LegResult.LOST, d2: LegResult.WON, d3: LegResult.WON}
    assert custom_ids(interaction.response.edited["kwargs"]["view"]) == [
        f"dk:paidnow:{rid}", f"dk:paylater:{rid}"
    ]


async def test_lost_day_on_the_last_open_leg_settles_the_round(db):
    """The settle tail, driven through the bot: once every leg is lost and
    none was won, the round itself is LOST -- and the reply says so instead of
    asking another question."""
    rid, (d1, d2, d3) = await three_leg_round(db, outcome=LotteryOutcome.APPLIED)
    await views_module.LostDayButton(rid, d1, "Day 1").callback(FakeInteraction(42))
    await views_module.LostDayButton(rid, d2, "Day 2").callback(FakeInteraction(42))

    interaction = FakeInteraction(42)
    await views_module.LostDayButton(rid, d3, "Day 3").callback(interaction)

    async with db() as s:
        outcome = (await s.execute(
            select(RoundOutcome.outcome).where(RoundOutcome.round_id == rid)
        )).scalar_one()
        results = list((await s.execute(
            select(RoundOutcomeDay.result).where(RoundOutcomeDay.round_id == rid)
        )).scalars())
    assert outcome is LotteryOutcome.LOST
    assert results == [LegResult.LOST] * 3
    edited = interaction.response.edited
    assert custom_ids(edited["kwargs"]["view"]) == [f"dk:clear:{rid}"]
    assert "Sorry to hear it" in edited["kwargs"]["content"]


async def test_won_all_on_a_multi_leg_round_resolves_every_leg(db):
    """'Won (all)' writes the round-level outcome, and the no-rows-means-all
    convention makes that answer every covered leg -- so the reply moves on to
    the payment question instead of asking about the days it just answered."""
    rid, _days = await three_leg_round(db, outcome=LotteryOutcome.APPLIED)

    interaction = FakeInteraction(42)
    await views_module.WonAllButton(rid).callback(interaction)

    async with db() as s:
        outcome = (await s.execute(
            select(RoundOutcome.outcome).where(RoundOutcome.round_id == rid)
        )).scalar_one()
        day_rows = (await s.execute(
            select(RoundOutcomeDay.id).where(RoundOutcomeDay.round_id == rid)
        )).scalars().all()
    assert outcome is LotteryOutcome.WON
    assert day_rows == [], "a whole-round win stays implicit (no-rows-means-all)"
    assert custom_ids(interaction.response.edited["kwargs"]["view"]) == [
        f"dk:paidnow:{rid}", f"dk:paylater:{rid}"
    ]


async def test_lost_all_settles_the_round_with_no_further_questions(db):
    rid, _days = await three_leg_round(db, outcome=LotteryOutcome.APPLIED)

    interaction = FakeInteraction(42)
    await views_module.LostAllButton(rid).callback(interaction)

    async with db() as s:
        outcome = (await s.execute(
            select(RoundOutcome.outcome).where(RoundOutcome.round_id == rid)
        )).scalar_one()
    assert outcome is LotteryOutcome.LOST
    assert custom_ids(interaction.response.edited["kwargs"]["view"]) == [f"dk:clear:{rid}"]


async def test_skip_day_opts_out_of_that_leg_only(db):
    """'Not going' is a LegOptOut, never a lottery result: the reader is
    declining the night, not reporting how the draw went."""
    rid, (d1, d2, d3) = await three_leg_round(db, outcome=LotteryOutcome.APPLIED)

    interaction = FakeInteraction(42)
    await views_module.SkipDayButton(rid, d1, "Day 1").callback(interaction)

    async with db() as s:
        opted = (await s.execute(select(LegOptOut.concert_day_id))).scalars().all()
        day_rows = (await s.execute(
            select(RoundOutcomeDay.id).where(RoundOutcomeDay.round_id == rid)
        )).scalars().all()
    assert opted == [d1]
    assert day_rows == [], "skipping a leg records no result for it"

    ids = custom_ids(interaction.response.edited["kwargs"]["view"])
    assert f"dk:wonday:{rid}:{d1}" not in ids
    assert f"dk:wonday:{rid}:{d2}" in ids and f"dk:wonday:{rid}:{d3}" in ids
    # Nothing is secured yet, so the shortcut is still the whole-round loss.
    assert f"dk:lostall:{rid}" in ids


async def test_skip_day_with_another_concerts_leg_writes_nothing(db):
    """Ids arrive from a custom_id, so they are re-validated server side --
    set_leg_opt_out validates nothing itself and would raise an FK error at
    commit on a made-up one."""
    rid, _days = await three_leg_round(db, outcome=LotteryOutcome.APPLIED)
    async with db() as s:
        other = Concert(title="Other", event_id="other", created_by=42)
        s.add(other)
        await s.flush()
        stranger = ConcertDay(concert_id=other.id, label="よそ", starts_at_utc=dt(7, 1))
        s.add(stranger)
        await s.commit()
        stranger_id = stranger.id

    interaction = FakeInteraction(42)
    await views_module.SkipDayButton(rid, stranger_id, "Elsewhere").callback(interaction)

    async with db() as s:
        opted = (await s.execute(select(LegOptOut.id))).scalars().all()
    assert opted == []
    assert interaction.response.edited is not None, "a no-op press still re-renders"


async def test_progressive_buttons_localize_leg_labels(db):
    """The follow-up view is built under the CLICKING user's language, so its
    labels must carry the zh leg variant and never the Japanese original."""
    rid, (d1, _d2, _d3) = await three_leg_round(
        db, language="zh", outcome=LotteryOutcome.APPLIED
    )

    interaction = FakeInteraction(42)
    await views_module.WonDayButton(rid, d1, "Day 1").callback(interaction)

    # A DynamicItem proxies custom_id but not label -- the wrapped Button
    # carries it, so the leg's name has to be read one level down.
    labels = " ".join(
        str(getattr(getattr(c, "item", c), "label", "") or "")
        for c in interaction.response.edited["kwargs"]["view"].children
    )
    assert "第2天" in labels
    assert "2日目" not in labels, "a zh reader must not be shown the Japanese original"


def test_followup_view_stays_inside_discords_row_budget():
    """Five rows, five components each. Each unanswered leg takes a row of its
    own, so a long tour is asked about a batch at a time -- exceeding the
    budget would raise while BUILDING the reply, losing the press entirely."""
    unresolved = tuple((100 + n, f"Day {n}") for n in range(9))
    view = views_module.build_result_followup_view(7, unresolved, any_won=True)
    assert len(view.children) == views_module.MAX_DAY_BUTTONS * 3 + 1
    assert max(c.row for c in view.children) <= 4
    assert custom_ids(view)[-1] == "dk:lostrest:7"


def test_leg_labels_are_trimmed_to_discords_limit():
    """An over-long label is an HTTPException at send time, which the
    scheduler classes as transient and retries forever -- the same trap
    safe_button_url guards against."""
    button = views_module.WonDayButton(7, 11, "月" * 200)
    assert len(button.item.label) <= 80


def test_dynamic_items_registers_every_progressive_button():
    """Miss one here and its custom_id has no handler after a bot restart:
    the button renders and does nothing at all."""
    names = {cls.__name__ for cls in views_module.DYNAMIC_ITEMS}
    assert {
        "WonAllButton", "LostAllButton", "WonDayButton", "LostDayButton",
        "SkipDayButton", "LostRestButton", "PaidNowButton", "PayLaterButton",
        "ClearOutcomeButton", "ConfirmClearButton", "KeepAnswerButton",
    } <= names
    assert len(views_module.DYNAMIC_ITEMS) == 22
    assert len(set(views_module.DYNAMIC_ITEMS)) == 22, "no class registered twice"


# ── Taking an answer back ────────────────────────────────────────────────
#
# Two halves: the flat Won/Lost pair may no longer overwrite a secured round,
# and every reply now carries a button that un-answers it. Each test presses a
# real button against a real round and asserts on the row that survived (or
# didn't) plus the reply that came back.


async def one_leg_round(db, *, outcome: LotteryOutcome | None = None) -> int:
    """A single-leg concert with one lottery round -- the shape that gets the
    FLAT Won/Lost pair rather than the per-leg questions."""
    async with db() as s:
        s.add(User(discord_id=42, username="reiji", language="en"))
        concert = Concert(title="Solo", event_id="solo", created_by=42)
        s.add(concert)
        await s.flush()
        s.add(ConcertDay(concert_id=concert.id, label="Day 1", starts_at_utc=dt(6, 20)))
        round_ = Round(
            concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND, label="1st round",
            results_at_utc=dt(6, 26),
        )
        s.add(round_)
        await s.flush()
        if outcome is not None:
            s.add(RoundOutcome(user_id=42, round_id=round_.id, outcome=outcome))
        await s.commit()
        return round_.id


async def test_flat_lost_button_will_not_overwrite_a_won_round(db):
    """The unguarded press that existed until now: a months-old results DM
    wiping a PAID ticket with no confirmation and no way back. LOST is settable
    from any state by design (the web is the correction path), so nothing below
    this button refuses it -- `refuse_if_secured` on the LostButton callback is
    the only thing standing between a stale press and a forfeited ticket."""
    rid = await one_leg_round(db, outcome=LotteryOutcome.WON)
    async with db() as s:
        await record_round_outcome(s, 42, rid, LotteryOutcome.PAID)
        await s.commit()
        before = (await s.execute(
            select(RoundOutcome).where(RoundOutcome.round_id == rid)
        )).scalar_one().updated_at

    interaction = FakeInteraction(42)
    await views_module.LostButton(rid).callback(interaction)

    async with db() as s:
        row = (await s.execute(
            select(RoundOutcome).where(RoundOutcome.round_id == rid)
        )).scalar_one()
    assert row.outcome is LotteryOutcome.PAID
    assert row.updated_at == before, "a refused press must not write at all"
    assert "already recorded as won" in interaction.response.sent["args"][0]
    assert custom_ids(interaction.response.sent["kwargs"]["view"]) == [f"dk:clear:{rid}"], (
        "the refusal is a signpost, not a dead end"
    )


async def test_flat_won_button_will_not_demote_a_paid_round(db):
    """The same guard from the other direction: WON over PAID demotes the round
    and re-arms the payment reminder for a ticket already paid for."""
    rid = await one_leg_round(db, outcome=LotteryOutcome.WON)
    async with db() as s:
        await record_round_outcome(s, 42, rid, LotteryOutcome.PAID)
        await s.commit()
        before = (await s.execute(
            select(RoundOutcome).where(RoundOutcome.round_id == rid)
        )).scalar_one().updated_at

    interaction = FakeInteraction(42)
    await views_module.WonButton(rid).callback(interaction)

    async with db() as s:
        row = (await s.execute(
            select(RoundOutcome).where(RoundOutcome.round_id == rid)
        )).scalar_one()
    assert row.outcome is LotteryOutcome.PAID
    assert row.updated_at == before


async def test_flat_lost_button_still_records_an_unsecured_round(db):
    """The guard must not swallow the ordinary press it was added around: a
    round nobody has secured is exactly what this button is for."""
    rid = await one_leg_round(db, outcome=LotteryOutcome.APPLIED)

    interaction = FakeInteraction(42)
    await views_module.LostButton(rid).callback(interaction)

    async with db() as s:
        outcome = (await s.execute(
            select(RoundOutcome.outcome).where(RoundOutcome.round_id == rid)
        )).scalar_one()
    assert outcome is LotteryOutcome.LOST
    assert "Sorry to hear it" in interaction.response.sent["args"][0]
    assert custom_ids(interaction.response.sent["kwargs"]["view"]) == [f"dk:clear:{rid}"], (
        "the reply to a successful press carries the way back too -- that is where "
        "a mis-press is noticed"
    )


async def test_paid_button_is_not_caught_by_the_guard(db):
    """"Paid" writes PAID onto a round that is by definition already WON, so
    guarding it would make the payment button permanently inert."""
    rid = await one_leg_round(db, outcome=LotteryOutcome.WON)

    await views_module.PaidButton(rid).callback(FakeInteraction(42))

    async with db() as s:
        outcome = (await s.execute(
            select(RoundOutcome.outcome).where(RoundOutcome.round_id == rid)
        )).scalar_one()
    assert outcome is LotteryOutcome.PAID


async def test_backtrack_clears_an_unsecured_round_immediately(db):
    """A loss forfeits nothing, so a confirmation there would be theatre and
    would make the common correction two presses. One press, cleared."""
    rid = await one_leg_round(db, outcome=LotteryOutcome.LOST)

    interaction = FakeInteraction(42)
    await views_module.ClearOutcomeButton(rid).callback(interaction)

    async with db() as s:
        rows = (await s.execute(
            select(RoundOutcome.outcome).where(RoundOutcome.round_id == rid)
        )).scalars().all()
    assert rows == [], "the record is gone, not rewritten"
    edited = interaction.response.edited
    assert "Cleared" in edited["kwargs"]["content"]
    assert edited["kwargs"]["view"] is None, "nothing left to confirm"


async def test_backtrack_asks_before_clearing_a_secured_round(db):
    """A DM has no dialog, so the question has to be a second press -- and the
    first press must write nothing while it is being asked."""
    rid = await one_leg_round(db, outcome=LotteryOutcome.WON)

    interaction = FakeInteraction(42)
    await views_module.ClearOutcomeButton(rid).callback(interaction)

    async with db() as s:
        outcome = (await s.execute(
            select(RoundOutcome.outcome).where(RoundOutcome.round_id == rid)
        )).scalar_one()
    assert outcome is LotteryOutcome.WON, "not yet cleared -- the reader was only asked"
    edited = interaction.response.edited
    assert "You hold a ticket" in edited["kwargs"]["content"]
    assert custom_ids(edited["kwargs"]["view"]) == [f"dk:clearok:{rid}", f"dk:keep:{rid}"]


async def test_backtrack_asks_before_clearing_a_paid_round(db):
    """PAID is the state with the most to lose, and it is a different enum
    member from WON: a guard naming only WON drops a paid-for ticket on one
    press."""
    rid = await one_leg_round(db, outcome=LotteryOutcome.PAID)

    interaction = FakeInteraction(42)
    await views_module.ClearOutcomeButton(rid).callback(interaction)

    async with db() as s:
        outcome = (await s.execute(
            select(RoundOutcome.outcome).where(RoundOutcome.round_id == rid)
        )).scalar_one()
    assert outcome is LotteryOutcome.PAID
    assert custom_ids(interaction.response.edited["kwargs"]["view"]) == [
        f"dk:clearok:{rid}", f"dk:keep:{rid}"
    ]


async def test_the_confirmation_names_every_leg_the_clear_takes(db):
    """The DM clears the WHOLE round, day rows included, so a reader looking at
    a three-night tour has to be told that before pressing. The count comes from
    the legs the round COVERS, not from the day rows recorded: a round won as a
    whole carries none (no-rows-means-all), and counting those would promise to
    clear nothing."""
    rid, _days = await three_leg_round(db, outcome=LotteryOutcome.WON)

    interaction = FakeInteraction(42)
    await views_module.ClearOutcomeButton(rid).callback(interaction)

    content = interaction.response.edited["kwargs"]["content"]
    assert "covers 3 performances" in content


async def test_the_confirmation_stays_silent_about_legs_on_a_single_leg_round(db):
    """"This round covers 1 performances" is the sentence a bare .format would
    ship; the count is named only when there is more than one leg to name."""
    rid = await one_leg_round(db, outcome=LotteryOutcome.WON)

    interaction = FakeInteraction(42)
    await views_module.ClearOutcomeButton(rid).callback(interaction)

    assert "performances" not in interaction.response.edited["kwargs"]["content"]


async def test_confirming_the_clear_removes_the_day_rows_too(db):
    """The whole round, never one leg: a partial win cleared leg-by-leg from a
    DM would leave the round WON on the strength of rows the reader thought they
    had just deleted."""
    rid, (d1, d2, _d3) = await three_leg_round(db, outcome=LotteryOutcome.APPLIED)
    async with db() as s:
        await record_round_day_result(s, 42, rid, d1, LegResult.WON)
        await record_round_day_result(s, 42, rid, d2, LegResult.LOST)
        await s.commit()

    interaction = FakeInteraction(42)
    await views_module.ConfirmClearButton(rid).callback(interaction)

    async with db() as s:
        outcomes = (await s.execute(
            select(RoundOutcome.outcome).where(RoundOutcome.round_id == rid)
        )).scalars().all()
        day_rows = (await s.execute(
            select(RoundOutcomeDay.id).where(RoundOutcomeDay.round_id == rid)
        )).scalars().all()
    assert outcomes == []
    assert day_rows == [], "a whole-round clear takes every leg's row with it"
    assert interaction.response.edited["kwargs"]["view"] is None


async def test_keeping_the_answer_writes_nothing(db):
    """'Keep it' is an answer, not a write -- the twin of 'Not yet'."""
    rid = await one_leg_round(db, outcome=LotteryOutcome.PAID)

    interaction = FakeInteraction(42)
    await views_module.KeepAnswerButton(rid).callback(interaction)

    async with db() as s:
        outcome = (await s.execute(
            select(RoundOutcome.outcome).where(RoundOutcome.round_id == rid)
        )).scalar_one()
    assert outcome is LotteryOutcome.PAID
    edited = interaction.response.edited
    assert "Kept" in edited["kwargs"]["content"]
    assert edited["kwargs"]["view"] is None


async def test_backtrack_on_a_deleted_round_says_so(db):
    """These buttons outlive the round they name -- a DM sent months ago can be
    pressed after the concert was deleted, and `clear_round_outcome` returns
    silently, which alone would render 'Cleared' for a round that never was."""
    rid = await one_leg_round(db, outcome=LotteryOutcome.LOST)
    async with db() as s:
        await s.delete(await s.get(Round, rid))
        await s.commit()

    interaction = FakeInteraction(42)
    await views_module.ClearOutcomeButton(rid).callback(interaction)

    edited = interaction.response.edited
    assert "no longer exists" in edited["kwargs"]["content"]
    assert edited["kwargs"]["view"] is None


async def test_backtrack_localizes_its_reply(db):
    """The button is built and its reply composed under the CLICKING user's
    language -- `_apply_locale` runs before either."""
    rid = await one_leg_round(db, outcome=LotteryOutcome.LOST)
    async with db() as s:
        user = await s.get(User, 42)
        user.language = "ja"
        await s.commit()

    interaction = FakeInteraction(42)
    await views_module.ClearOutcomeButton(rid).callback(interaction)

    assert "取り消しました" in interaction.response.edited["kwargs"]["content"]
