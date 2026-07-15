"""Reminder planning: the pure math at the heart of the app.

Given a user's rule ("remind me 3 days before every lottery round closes")
and a concert's windows/days, compute exactly WHEN reminders should fire.

This module has NO imports from discord, fastapi, or sqlalchemy — it works
on plain dataclasses and datetimes, which is what makes it exhaustively
testable. The service layer (Phase 3) adapts ORM rows to these dataclasses
and writes the results into reminder_queue.

Semantics:
  * offset_days < 0  -> before the anchor   (-3 = three days before)
  * offset_days > 0  -> after the anchor    (+1 = one day after, e.g. results recap)
  * offset_days = 0  -> at the anchor moment (plus offset_hours, if any)
  * A rule scoped to one window plans only that window.
  * A rule scoped to a concert expands to all its windows (OPENS/CLOSES)
    or all its days (EVENT_START).
  * Windows missing the anchored bound (e.g. CLOSES on a window with no
    closes_at) are skipped silently — not every window has both bounds.
  * Fire times in the past are skipped: we never queue stale reminders.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.types import Anchor

# ── Inputs (thin snapshots of ORM rows) ──────────────────────────────────


@dataclass(frozen=True)
class WindowInfo:
    id: int
    opens_at_utc: datetime | None
    closes_at_utc: datetime | None


@dataclass(frozen=True)
class DayInfo:
    id: int
    starts_at_utc: datetime


@dataclass(frozen=True)
class RuleInfo:
    id: int
    anchor: Anchor
    offset_days: int
    offset_hours: int = 0
    window_id: int | None = None   # set -> rule targets one specific window
    concert_id: int | None = None  # set -> rule targets a whole concert


# ── Output ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PlannedReminder:
    rule_id: int
    anchor: Anchor
    fire_at_utc: datetime
    window_id: int | None = None
    day_id: int | None = None


# ── The math ─────────────────────────────────────────────────────────────


def offset_delta(offset_days: int, offset_hours: int) -> timedelta:
    return timedelta(days=offset_days, hours=offset_hours)


def anchor_time(window: WindowInfo, anchor: Anchor) -> datetime | None:
    """The moment a window-anchored rule measures from, or None if absent."""
    if anchor is Anchor.OPENS:
        return window.opens_at_utc
    if anchor is Anchor.CLOSES:
        return window.closes_at_utc
    return None  # EVENT_START never anchors to a window


def plan_for_rule(
    rule: RuleInfo,
    windows: list[WindowInfo],
    days: list[DayInfo],
    now: datetime,
) -> list[PlannedReminder]:
    """Compute every future reminder this rule implies.

    Deterministic and side-effect free: same inputs, same output, always.
    Callers re-run this after any window/day edit; the DB layer's dedupe
    index turns re-planning into upserts instead of duplicates.
    """
    delta = offset_delta(rule.offset_days, rule.offset_hours)
    planned: list[PlannedReminder] = []

    if rule.anchor is Anchor.EVENT_START:
        # Day-anchored. A window-scoped rule with EVENT_START is a contradiction;
        # plan nothing rather than guess.
        if rule.window_id is not None:
            return []
        for day in days:
            fire = day.starts_at_utc + delta
            if fire > now:
                planned.append(
                    PlannedReminder(
                        rule_id=rule.id, anchor=rule.anchor, fire_at_utc=fire, day_id=day.id
                    )
                )
        return planned

    # Window-anchored (OPENS / CLOSES)
    targets = windows
    if rule.window_id is not None:
        targets = [w for w in windows if w.id == rule.window_id]

    for window in targets:
        at = anchor_time(window, rule.anchor)
        if at is None:
            continue  # this window doesn't have that bound — skip, don't error
        fire = at + delta
        if fire > now:
            planned.append(
                PlannedReminder(
                    rule_id=rule.id, anchor=rule.anchor, fire_at_utc=fire, window_id=window.id
                )
            )
    return planned


def plan_for_rules(
    rules: list[RuleInfo],
    windows: list[WindowInfo],
    days: list[DayInfo],
    now: datetime,
) -> list[PlannedReminder]:
    """Plan a batch of rules against one concert's windows and days."""
    out: list[PlannedReminder] = []
    for rule in rules:
        out.extend(plan_for_rule(rule, windows, days, now))
    return out
