"""Taking back a recorded outcome: clear_round_outcome is the only path that
deletes RoundOutcome/RoundOutcomeDay rows, and it re-plans the user's rules
itself (invariant 2)."""

from sqlalchemy import select
from test_lottery_outcomes import NOW, dt, seed_two_legs

from app.db.models import ReminderQueue, ReminderRule, RoundOutcome, RoundOutcomeDay
from app.db.service import (
    clear_round_outcome,
    ensure_user,
    record_round_day_result,
    record_round_outcome,
    set_leg_opt_out,
    sync_rule,
)
from app.domain.types import Anchor, LegResult, LotteryOutcome


async def _outcome(s, round_id: int) -> LotteryOutcome | None:
    return (await s.execute(
        select(RoundOutcome.outcome).where(
            RoundOutcome.user_id == 42, RoundOutcome.round_id == round_id
        )
    )).scalar_one_or_none()


async def _day_rows(s, round_id: int) -> dict[int, LegResult]:
    rows = (await s.execute(
        select(RoundOutcomeDay.day_id, RoundOutcomeDay.result).where(
            RoundOutcomeDay.user_id == 42, RoundOutcomeDay.round_id == round_id
        )
    )).all()
    return {day_id: result for day_id, result in rows}


async def test_whole_round_clear_removes_outcome_and_every_day_row(session):
    # Mutation caught: a clear that deletes only the RoundOutcome and leaves
    # RoundOutcomeDay rows behind. Those rows are what secured_day_ids_by_round
    # reads, so the user would still "hold" a ticket on a round showing nothing.
    s = session
    _c, leg_a, leg_b, _a_only, both, _general = await seed_two_legs(s)
    await record_round_day_result(s, 42, both.id, leg_a.id, LegResult.WON, NOW)
    await record_round_day_result(s, 42, both.id, leg_b.id, LegResult.LOST, NOW)
    assert await _outcome(s, both.id) is LotteryOutcome.WON

    await clear_round_outcome(s, 42, both.id, now=NOW)

    assert await _outcome(s, both.id) is None
    assert await _day_rows(s, both.id) == {}


async def test_whole_round_clear_leaves_other_rounds_alone(session):
    # Mutation caught: a delete() missing its round_id predicate.
    s = session
    _c, leg_a, _leg_b, a_only, both, _general = await seed_two_legs(s)
    await record_round_outcome(s, 42, a_only.id, LotteryOutcome.LOST, NOW)
    await record_round_day_result(s, 42, both.id, leg_a.id, LegResult.WON, NOW)

    await clear_round_outcome(s, 42, both.id, now=NOW)

    assert await _outcome(s, a_only.id) is LotteryOutcome.LOST


async def test_whole_round_clear_leaves_other_users_alone(session):
    # Mutation caught: a delete() missing its user_id predicate -- one user
    # correcting their own record wiping everybody else's.
    s = session
    _c, leg_a, _leg_b, _a_only, both, _general = await seed_two_legs(s)
    await ensure_user(s, 99, "someone-else")
    await record_round_outcome(s, 42, both.id, LotteryOutcome.WON, NOW)
    await record_round_outcome(s, 99, both.id, LotteryOutcome.WON, NOW)

    await clear_round_outcome(s, 42, both.id, now=NOW)

    assert (await s.execute(
        select(RoundOutcome.outcome).where(
            RoundOutcome.user_id == 99, RoundOutcome.round_id == both.id
        )
    )).scalar_one_or_none() is LotteryOutcome.WON


async def test_per_leg_clear_keeps_the_other_legs_win(session):
    # The headline case. Mutation caught: a per-leg clear that falls through to
    # the whole-round branch, throwing away Saturday to fix Sunday.
    s = session
    _c, leg_a, leg_b, _a_only, both, _general = await seed_two_legs(s)
    await record_round_day_result(s, 42, both.id, leg_a.id, LegResult.WON, NOW)
    await record_round_day_result(s, 42, both.id, leg_b.id, LegResult.LOST, NOW)

    await clear_round_outcome(s, 42, both.id, day_id=leg_b.id, now=NOW)

    assert await _day_rows(s, both.id) == {leg_a.id: LegResult.WON}
    assert await _outcome(s, both.id) is LotteryOutcome.WON


async def test_per_leg_clear_preserves_paid(session):
    # Mutation caught: re-deriving to WON unconditionally, which demotes PAID
    # and re-arms a payment reminder for a ticket already paid for.
    s = session
    _c, leg_a, leg_b, _a_only, both, _general = await seed_two_legs(s)
    await record_round_day_result(s, 42, both.id, leg_a.id, LegResult.WON, NOW)
    await record_round_day_result(s, 42, both.id, leg_b.id, LegResult.LOST, NOW)
    await record_round_outcome(s, 42, both.id, LotteryOutcome.PAID, NOW)

    await clear_round_outcome(s, 42, both.id, day_id=leg_b.id, now=NOW)

    assert await _outcome(s, both.id) is LotteryOutcome.PAID


async def test_per_leg_clear_of_the_only_win_reopens_the_round(session):
    # Mutation caught: leaving the round WON after its last WON row is gone --
    # a round claiming a ticket no leg holds.
    s = session
    _c, leg_a, leg_b, _a_only, both, _general = await seed_two_legs(s)
    await record_round_day_result(s, 42, both.id, leg_b.id, LegResult.LOST, NOW)
    await record_round_day_result(s, 42, both.id, leg_a.id, LegResult.WON, NOW)

    await clear_round_outcome(s, 42, both.id, day_id=leg_a.id, now=NOW)

    assert await _day_rows(s, both.id) == {leg_b.id: LegResult.LOST}
    assert await _outcome(s, both.id) is LotteryOutcome.APPLIED


async def test_per_leg_clear_settles_lost_when_nothing_is_left_open(session):
    # The rare third branch: the cleared leg is opted out, so it is not
    # "unresolved" either. Mutation caught: an unconditional APPLIED, which
    # would re-open a round the reader has nothing pending on.
    s = session
    _c, leg_a, leg_b, _a_only, both, _general = await seed_two_legs(s)
    await record_round_day_result(s, 42, both.id, leg_a.id, LegResult.LOST, NOW)
    await record_round_day_result(s, 42, both.id, leg_b.id, LegResult.LOST, NOW)
    await set_leg_opt_out(s, 42, leg_a.id, True)

    await clear_round_outcome(s, 42, both.id, day_id=leg_a.id, now=NOW)

    assert await _outcome(s, both.id) is LotteryOutcome.LOST


async def test_forged_day_id_writes_nothing(session):
    # Mutation caught: dropping the _covered_day_ids check, which lets a form
    # post name another concert's leg.
    s = session
    _c, leg_a, _leg_b, a_only, _both, _general = await seed_two_legs(s)
    await record_round_day_result(s, 42, a_only.id, leg_a.id, LegResult.WON, NOW)

    await clear_round_outcome(s, 42, a_only.id, day_id=999_999, now=NOW)

    assert await _day_rows(s, a_only.id) == {leg_a.id: LegResult.WON}


async def test_missing_round_returns_silently(session):
    # Mutation caught: raising instead of returning, which would 500 the route
    # rather than letting it answer 404 itself.
    await clear_round_outcome(session, 42, 999_999, now=NOW)


async def test_clear_replans_the_queue(session):
    # The invariant-2 test, and the one that fails if the resync is dropped:
    # a NOT_APPLIED round plans no RESULTS row, so clearing it must bring that
    # row back. Asserting on reminder_queue, not on a return value.
    s = session
    _c, _leg_a, _leg_b, a_only, _both, _general = await seed_two_legs(s)
    # seed_two_legs leaves a_only.results_at_utc unset, so a RESULTS rule
    # would plan nothing regardless of outcome -- give it a results anchor.
    a_only.results_at_utc = dt(7, 1)
    await s.flush()
    rule = ReminderRule(user_id=42, round_id=a_only.id, anchor=Anchor.RESULTS,
                        offset_days=0, offset_hours=0)
    s.add(rule)
    await s.flush()
    await sync_rule(s, rule, NOW)
    assert (await s.execute(select(ReminderQueue.id))).scalars().all() != []

    await record_round_outcome(s, 42, a_only.id, LotteryOutcome.NOT_APPLIED, NOW)
    assert (await s.execute(select(ReminderQueue.id))).scalars().all() == []

    await clear_round_outcome(s, 42, a_only.id, now=NOW)
    assert (await s.execute(select(ReminderQueue.id))).scalars().all() != []
