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
    RuleInfo,
    WindowInfo,
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
WINDOWS = [
    WindowInfo(id=10, opens_at_utc=dt(6, 5), closes_at_utc=dt(6, 20)),   # item sale
    WindowInfo(id=11, opens_at_utc=dt(6, 10), closes_at_utc=dt(6, 25)),  # lottery R1
    WindowInfo(id=12, opens_at_utc=dt(7, 1), closes_at_utc=dt(7, 10)),   # lottery R2
    WindowInfo(id=13, opens_at_utc=dt(7, 15), closes_at_utc=None),       # results (moment)
    WindowInfo(id=14, opens_at_utc=dt(7, 20), closes_at_utc=dt(7, 31)),  # stream Day 1
]


def fire_times(planned: list[PlannedReminder]) -> set[datetime]:
    return {p.fire_at_utc for p in planned}


# ── Offsets ──────────────────────────────────────────────────────────────


def test_negative_offset_means_before():
    rule = RuleInfo(id=1, anchor=Anchor.CLOSES, offset_days=-3, window_id=11)
    (p,) = plan_for_rule(rule, WINDOWS, DAYS, NOW)
    assert p.fire_at_utc == dt(6, 22)  # 3 days before June 25 close
    assert p.window_id == 11


def test_positive_offset_means_after():
    # "1 day after results" — the recap/payment-reminder pattern
    rule = RuleInfo(id=2, anchor=Anchor.OPENS, offset_days=1, window_id=13)
    (p,) = plan_for_rule(rule, WINDOWS, DAYS, NOW)
    assert p.fire_at_utc == dt(7, 16)


def test_zero_days_with_hours_offset():
    # "6 hours before the sale opens" — same-day nudge
    rule = RuleInfo(id=3, anchor=Anchor.OPENS, offset_days=0, offset_hours=-6, window_id=14)
    (p,) = plan_for_rule(rule, WINDOWS, DAYS, NOW)
    assert p.fire_at_utc == dt(7, 20, 6)  # 12:00 - 6h


# ── Scoping ──────────────────────────────────────────────────────────────


def test_concert_rule_expands_to_all_windows_with_that_bound():
    rule = RuleInfo(id=4, anchor=Anchor.CLOSES, offset_days=-1, concert_id=99)
    planned = plan_for_rule(rule, WINDOWS, DAYS, NOW)
    # Window 13 has no closes_at -> skipped; the other four plan normally.
    assert len(planned) == 4
    assert fire_times(planned) == {dt(6, 19), dt(6, 24), dt(7, 9), dt(7, 30)}


def test_window_rule_targets_only_that_window():
    rule = RuleInfo(id=5, anchor=Anchor.OPENS, offset_days=-2, window_id=12)
    planned = plan_for_rule(rule, WINDOWS, DAYS, NOW)
    assert [p.window_id for p in planned] == [12]


def test_event_start_anchors_to_days():
    rule = RuleInfo(id=6, anchor=Anchor.EVENT_START, offset_days=-7, concert_id=99)
    planned = plan_for_rule(rule, WINDOWS, DAYS, NOW)
    assert {p.day_id for p in planned} == {1, 2}
    assert fire_times(planned) == {dt(7, 25, 9), dt(7, 26, 9)}


def test_event_start_on_window_scoped_rule_plans_nothing():
    rule = RuleInfo(id=7, anchor=Anchor.EVENT_START, offset_days=-1, window_id=11)
    assert plan_for_rule(rule, WINDOWS, DAYS, NOW) == []


# ── Past-skipping ────────────────────────────────────────────────────────


def test_reminders_in_the_past_are_not_planned():
    # 3 days before the item sale OPENS (June 5) = June 2... but "now" is June 1,
    # so it plans; move now to June 3 and it must vanish.
    rule = RuleInfo(id=8, anchor=Anchor.OPENS, offset_days=-3, window_id=10)
    assert len(plan_for_rule(rule, WINDOWS, DAYS, NOW)) == 1
    later = datetime(2026, 6, 3, 0, 0, tzinfo=UTC)
    assert plan_for_rule(rule, WINDOWS, DAYS, later) == []


def test_after_offset_can_still_be_future_when_anchor_is_past():
    # Results announced July 15; "1 day after" is still future on July 15 evening.
    now = datetime(2026, 7, 15, 18, 0, tzinfo=UTC)
    rule = RuleInfo(id=9, anchor=Anchor.OPENS, offset_days=1, window_id=13)
    (p,) = plan_for_rule(rule, WINDOWS, DAYS, now)
    assert p.fire_at_utc == dt(7, 16)


# ── Batch + determinism ─────────────────────────────────────────────────


def test_plan_for_rules_batches_and_is_deterministic():
    rules = [
        RuleInfo(id=1, anchor=Anchor.CLOSES, offset_days=-1, concert_id=99),
        RuleInfo(id=2, anchor=Anchor.EVENT_START, offset_days=-7, concert_id=99),
    ]
    a = plan_for_rules(rules, WINDOWS, DAYS, NOW)
    b = plan_for_rules(rules, WINDOWS, DAYS, NOW)
    assert a == b  # same inputs, same output — re-planning must be safe
    assert len(a) == 6  # 4 window closes + 2 day starts
