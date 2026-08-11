"""Taking back a recorded outcome: clear_round_outcome is the only path that
deletes RoundOutcome/RoundOutcomeDay rows, and it re-plans the user's rules
itself (invariant 2)."""

from sqlalchemy import select

# tests/ is not a package; pytest puts it on sys.path (test_conftest_fixtures.py:19)
from test_lottery_outcomes import NOW, dt, seed_two_legs

from app.db.models import ReminderQueue, ReminderRule, Round, RoundOutcome, RoundOutcomeDay
from app.db.service import (
    clear_round_outcome,
    ensure_user,
    record_round_day_result,
    record_round_outcome,
    set_leg_opt_out,
    sync_rule,
)
from app.domain.types import Anchor, LegResult, LotteryOutcome, RoundKind


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
    #
    # Both legs already resolved LOST settles the round to LOST *before* this
    # clear ever runs, so the outcome assertion alone cannot tell "correctly
    # re-derived to LOST" from "the clear was a silent no-op" -- both look
    # identical on `_outcome`. The day-row assertion is what proves the
    # leg_a row was actually deleted, which a no-op would leave behind.
    s = session
    _c, leg_a, leg_b, _a_only, both, _general = await seed_two_legs(s)
    await record_round_day_result(s, 42, both.id, leg_a.id, LegResult.LOST, NOW)
    await record_round_day_result(s, 42, both.id, leg_b.id, LegResult.LOST, NOW)
    await set_leg_opt_out(s, 42, leg_a.id, True)

    await clear_round_outcome(s, 42, both.id, day_id=leg_a.id, now=NOW)

    assert await _day_rows(s, both.id) == {leg_b.id: LegResult.LOST}
    assert await _outcome(s, both.id) is LotteryOutcome.LOST


async def test_stale_day_id_writes_nothing(session):
    # a_only's applies_to is [leg_a.id] only, so leg_b is a leg it does NOT
    # cover. Seed a row for it directly (simulating one left over from before
    # applies_to narrowed, or a form post naming a leg from the wrong round)
    # rather than going through record_round_day_result, which would refuse
    # to write it in the first place.
    #
    # Mutation caught: dropping the `_covered_day_ids` gate at the top of the
    # per-leg branch. An impossible id like 999_999 matches no row either
    # way, so deleting it is a no-op whether or not the gate runs -- this
    # id is real, so only the gate stops the DELETE from finding it.
    s = session
    _c, leg_a, leg_b, a_only, _both, _general = await seed_two_legs(s)
    assert a_only.applies_to == [leg_a.id]  # leg_b is not covered by a_only
    session.add(RoundOutcomeDay(
        user_id=42, round_id=a_only.id, day_id=leg_b.id, result=LegResult.WON,
    ))
    await s.flush()

    await clear_round_outcome(s, 42, a_only.id, day_id=leg_b.id, now=NOW)

    assert await _day_rows(s, a_only.id) == {leg_b.id: LegResult.WON}


async def test_forged_day_id_writes_nothing(session):
    # A day_id matching no ConcertDay at all -- the other shape of bad input
    # a form post can send, kept alongside the stale-id case above.
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


async def test_per_leg_clear_replans_the_payment_reminder(session):
    # The per-leg half of invariant 2 -- test_clear_replans_the_queue only
    # exercises day_id=None. Mutation caught: dropping the
    # `reinstate_user_rules` call at the end of the per-leg branch. Settling
    # both legs LOST suppresses this round's payment reminder (see
    # test_lost_suppresses_this_rounds_payment_reminder); clearing the
    # mistaken leg_a re-derives the round to APPLIED, which un-suppresses
    # PAYMENT, and only the resync call brings the queued row back.
    s = session
    _c, leg_a, leg_b, _a_only, both, _general = await seed_two_legs(s)
    both.payment_deadline_at_utc = dt(7, 1)
    await s.flush()
    rule = ReminderRule(user_id=42, round_id=both.id, anchor=Anchor.PAYMENT,
                        offset_days=0, offset_hours=0)
    s.add(rule)
    await s.flush()
    await sync_rule(s, rule, NOW)
    assert (await s.execute(select(ReminderQueue.id))).scalars().all() != []

    # Settle the round LOST as a whole (a mistaken double-loss), which
    # suppresses the payment reminder.
    await record_round_day_result(s, 42, both.id, leg_a.id, LegResult.LOST, NOW)
    await record_round_day_result(s, 42, both.id, leg_b.id, LegResult.LOST, NOW)
    assert await _outcome(s, both.id) is LotteryOutcome.LOST
    assert (await s.execute(select(ReminderQueue.id))).scalars().all() == []

    # Correct the mistake on leg_a: the round re-derives to APPLIED (leg_a is
    # unresolved again, leg_b is still LOST), and PAYMENT is no longer
    # suppressed.
    await clear_round_outcome(s, 42, both.id, day_id=leg_a.id, now=NOW)

    assert await _outcome(s, both.id) is LotteryOutcome.APPLIED
    assert (await s.execute(select(ReminderQueue.id))).scalars().all() != []


async def test_per_leg_clear_settling_lost_does_not_auto_arm_the_next_round(session):
    # Same fixture as test_per_leg_clear_settles_lost_when_nothing_is_left_open
    # (both legs LOST, leg_a opted out, clear leg_a -> round re-settles LOST),
    # plus a real next-round candidate added only AFTER that initial settle --
    # so the genuine loss above could not have armed it, and the correction is
    # the only thing that could. Mutation caught: adding an
    # `_auto_arm_next_round` call to `_rederive_round_from_days`'s LOST
    # branch. The docstring promises a correction never arms a new rule --
    # "a correction is not a new loss" -- and without a fresh candidate
    # appearing after the fact, `_auto_arm_next_round`'s own
    # already-armed-check would silently hide a second call that reused the
    # SAME next round, so the candidate has to be new.
    s = session
    _c, leg_a, leg_b, _a_only, both, _general = await seed_two_legs(s)
    await record_round_day_result(s, 42, both.id, leg_a.id, LegResult.LOST, NOW)
    await record_round_day_result(s, 42, both.id, leg_b.id, LegResult.LOST, NOW)
    await set_leg_opt_out(s, 42, leg_a.id, True)
    assert await _outcome(s, both.id) is LotteryOutcome.LOST

    next_round = Round(
        concert_id=_c.id, kind=RoundKind.LOTTERY_ROUND, label="Next round",
        opens_at_utc=dt(7, 1), applies_to=[leg_a.id],
    )
    s.add(next_round)
    await s.flush()
    before = set((await s.execute(select(ReminderRule.id))).scalars())

    await clear_round_outcome(s, 42, both.id, day_id=leg_a.id, now=NOW)

    after = set((await s.execute(select(ReminderRule.id))).scalars())
    assert after == before
