"""Reminder planning math — tested against a realistic concert timeline.

Scenario used throughout (all times UTC for readability):
  A two-day concert with a serial-item sale, two lottery rounds, a result
  announcement, and per-day stream ticket sales — i.e. the real shape of a
  Hasunosora/Gakumas event cycle.
"""

from datetime import UTC, datetime

from app.domain.reminders import (
    DayInfo,
    PlannedReminder,
    RoundInfo,
    RuleInfo,
    plan_for_rule,
    plan_for_rules,
)
from app.domain.types import Anchor

NOW = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)


def dt(month: int, day: int, hour: int = 12) -> datetime:
    return datetime(2026, month, day, hour, tzinfo=UTC)


# The concert: Aug 1–2, with the usual deadline chain before it.
DAYS = [
    DayInfo(id=1, starts_at_utc=dt(8, 1, 9)),   # Day 1
    DayInfo(id=2, starts_at_utc=dt(8, 2, 9)),   # Day 2
]
ROUNDS = [
    RoundInfo(id=10, opens_at_utc=dt(6, 5), closes_at_utc=dt(6, 20)),   # item sale
    RoundInfo(id=11, opens_at_utc=dt(6, 10), closes_at_utc=dt(6, 25)),  # lottery R1
    RoundInfo(id=12, opens_at_utc=dt(7, 1), closes_at_utc=dt(7, 10)),   # lottery R2
    RoundInfo(id=13, opens_at_utc=dt(7, 15), closes_at_utc=None),       # legacy: opens-as-moment
    RoundInfo(id=14, opens_at_utc=dt(7, 20), closes_at_utc=dt(7, 31)),  # stream Day 1
    RoundInfo(  # a round using the new dedicated results/payment fields together
        id=15, opens_at_utc=None, closes_at_utc=None,
        results_at_utc=dt(7, 15), payment_deadline_at_utc=dt(7, 22),
    ),
]


def fire_times(planned: list[PlannedReminder]) -> set[datetime]:
    return {p.fire_at_utc for p in planned}


# ── Offsets ──────────────────────────────────────────────────────────────


def test_negative_offset_means_before():
    rule = RuleInfo(id=1, anchor=Anchor.CLOSES, offset_days=-3, round_id=11)
    (p,) = plan_for_rule(rule, ROUNDS, DAYS, NOW)
    assert p.fire_at_utc == dt(6, 22)  # 3 days before June 25 close
    assert p.round_id == 11


def test_positive_offset_means_after():
    # "1 day after results" — the recap/payment-reminder pattern
    rule = RuleInfo(id=2, anchor=Anchor.OPENS, offset_days=1, round_id=13)
    (p,) = plan_for_rule(rule, ROUNDS, DAYS, NOW)
    assert p.fire_at_utc == dt(7, 16)


def test_zero_days_with_hours_offset():
    # "6 hours before the sale opens" — same-day nudge
    rule = RuleInfo(id=3, anchor=Anchor.OPENS, offset_days=0, offset_hours=-6, round_id=14)
    (p,) = plan_for_rule(rule, ROUNDS, DAYS, NOW)
    assert p.fire_at_utc == dt(7, 20, 6)  # 12:00 - 6h


# ── Scoping ──────────────────────────────────────────────────────────────


def test_concert_rule_expands_to_all_rounds_with_that_bound():
    rule = RuleInfo(id=4, anchor=Anchor.CLOSES, offset_days=-1, concert_id=99)
    planned = plan_for_rule(rule, ROUNDS, DAYS, NOW)
    # Round 13 has no closes_at, round 15 has no closes_at -> both skipped;
    # the other four plan normally.
    assert len(planned) == 4
    assert fire_times(planned) == {dt(6, 19), dt(6, 24), dt(7, 9), dt(7, 30)}


def test_round_rule_targets_only_that_round():
    rule = RuleInfo(id=5, anchor=Anchor.OPENS, offset_days=-2, round_id=12)
    planned = plan_for_rule(rule, ROUNDS, DAYS, NOW)
    assert [p.round_id for p in planned] == [12]


def test_event_start_anchors_to_days():
    rule = RuleInfo(id=6, anchor=Anchor.EVENT_START, offset_days=-7, concert_id=99)
    planned = plan_for_rule(rule, ROUNDS, DAYS, NOW)
    assert {p.day_id for p in planned} == {1, 2}
    assert fire_times(planned) == {dt(7, 25, 9), dt(7, 26, 9)}


def test_event_start_on_round_scoped_rule_plans_nothing():
    rule = RuleInfo(id=7, anchor=Anchor.EVENT_START, offset_days=-1, round_id=11)
    assert plan_for_rule(rule, ROUNDS, DAYS, NOW) == []


# ── Results / payment anchors ───────────────────────────────────────────


def test_results_anchor_reads_results_field():
    rule = RuleInfo(id=10, anchor=Anchor.RESULTS, offset_days=-1, round_id=15)
    (p,) = plan_for_rule(rule, ROUNDS, DAYS, NOW)
    assert p.fire_at_utc == dt(7, 14)  # 1 day before July 15 results
    assert p.round_id == 15


def test_payment_anchor_reads_payment_field():
    rule = RuleInfo(id=11, anchor=Anchor.PAYMENT, offset_days=-2, round_id=15)
    (p,) = plan_for_rule(rule, ROUNDS, DAYS, NOW)
    assert p.fire_at_utc == dt(7, 20)  # 2 days before July 22 payment deadline
    assert p.round_id == 15


def test_results_anchor_skips_round_without_that_bound():
    # Round 11 (lottery R1) has no results_at_utc -- concert-wide RESULTS
    # rule only matches round 15, the one round that has it.
    rule = RuleInfo(id=12, anchor=Anchor.RESULTS, offset_days=0, concert_id=99)
    planned = plan_for_rule(rule, ROUNDS, DAYS, NOW)
    assert [p.round_id for p in planned] == [15]


def test_payment_anchor_skips_round_without_that_bound():
    rule = RuleInfo(id=13, anchor=Anchor.PAYMENT, offset_days=0, round_id=11)
    assert plan_for_rule(rule, ROUNDS, DAYS, NOW) == []


# ── Past-skipping ────────────────────────────────────────────────────────


def test_reminders_in_the_past_are_not_planned():
    # 3 days before the item sale OPENS (June 5) = June 2... but "now" is June 1,
    # so it plans; move now to June 3 and it must vanish.
    rule = RuleInfo(id=8, anchor=Anchor.OPENS, offset_days=-3, round_id=10)
    assert len(plan_for_rule(rule, ROUNDS, DAYS, NOW)) == 1
    later = datetime(2026, 6, 3, 0, 0, tzinfo=UTC)
    assert plan_for_rule(rule, ROUNDS, DAYS, later) == []


def test_after_offset_can_still_be_future_when_anchor_is_past():
    # Results announced July 15; "1 day after" is still future on July 15 evening.
    now = datetime(2026, 7, 15, 18, 0, tzinfo=UTC)
    rule = RuleInfo(id=9, anchor=Anchor.OPENS, offset_days=1, round_id=13)
    (p,) = plan_for_rule(rule, ROUNDS, DAYS, now)
    assert p.fire_at_utc == dt(7, 16)


# ── Batch + determinism ─────────────────────────────────────────────────


def test_plan_for_rules_batches_and_is_deterministic():
    rules = [
        RuleInfo(id=1, anchor=Anchor.CLOSES, offset_days=-1, concert_id=99),
        RuleInfo(id=2, anchor=Anchor.EVENT_START, offset_days=-7, concert_id=99),
    ]
    a = plan_for_rules(rules, ROUNDS, DAYS, NOW)
    b = plan_for_rules(rules, ROUNDS, DAYS, NOW)
    assert a == b  # same inputs, same output — re-planning must be safe
    assert len(a) == 6  # 4 round closes + 2 day starts


# ── Minute offsets ───────────────────────────────────────────────────────


def test_a_minute_offset_moves_the_fire_time_by_exactly_that_much():
    """The mutation this must not survive: offset_delta ignoring its third argument."""
    rule = RuleInfo(
        id=1, anchor=Anchor.OPENS, offset_days=0, offset_hours=0,
        offset_minutes=-5, concert_id=1,
    )
    round_ = RoundInfo(
        id=7,
        opens_at_utc=datetime(2026, 9, 1, 3, 0, tzinfo=UTC),
        closes_at_utc=None,
    )
    planned = plan_for_rule(rule, [round_], [], datetime(2026, 8, 1, tzinfo=UTC))

    assert [p.fire_at_utc for p in planned] == [datetime(2026, 9, 1, 2, 55, tzinfo=UTC)]


def test_days_hours_and_minutes_compose():
    rule = RuleInfo(
        id=1, anchor=Anchor.CLOSES, offset_days=-1, offset_hours=-2,
        offset_minutes=-30, concert_id=1,
    )
    round_ = RoundInfo(
        id=7, opens_at_utc=None,
        closes_at_utc=datetime(2026, 9, 10, 12, 0, tzinfo=UTC),
    )
    planned = plan_for_rule(rule, [round_], [], datetime(2026, 8, 1, tzinfo=UTC))

    assert planned[0].fire_at_utc == datetime(2026, 9, 9, 9, 30, tzinfo=UTC)


def test_a_positive_minute_offset_fires_after_the_anchor():
    rule = RuleInfo(
        id=1, anchor=Anchor.RESULTS, offset_days=0, offset_hours=0,
        offset_minutes=15, concert_id=1,
    )
    round_ = RoundInfo(
        id=7, opens_at_utc=None, closes_at_utc=None,
        results_at_utc=datetime(2026, 9, 20, 6, 0, tzinfo=UTC),
    )
    planned = plan_for_rule(rule, [round_], [], datetime(2026, 8, 1, tzinfo=UTC))

    assert planned[0].fire_at_utc == datetime(2026, 9, 20, 6, 15, tzinfo=UTC)
