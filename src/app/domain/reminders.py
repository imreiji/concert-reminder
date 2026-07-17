"""Reminder planning: the pure math at the heart of the app.

Given a user's rule ("remind me 3 days before every lottery round closes")
and a concert's rounds/days, compute exactly WHEN reminders should fire.

This module has NO imports from discord, fastapi, or sqlalchemy — it works
on plain dataclasses and datetimes, which is what makes it exhaustively
testable. The service layer (Phase 3) adapts ORM rows to these dataclasses
and writes the results into reminder_queue.

Semantics:
  * offset_days < 0  -> before the anchor   (-3 = three days before)
  * offset_days > 0  -> after the anchor    (+1 = one day after, e.g. results recap)
  * offset_days = 0  -> at the anchor moment (plus offset_hours, if any)
  * A rule scoped to one round plans only that round.
  * A rule scoped to a concert expands to all its rounds (OPENS/CLOSES/
    RESULTS/PAYMENT) or all its days (EVENT_START).
  * Rounds missing the anchored bound (e.g. CLOSES on a round with no
    closes_at) are skipped silently — not every round has all 4 bounds.
  * Fire times in the past are skipped: we never queue stale reminders.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.types import Anchor

# ── Inputs (thin snapshots of ORM rows) ──────────────────────────────────


@dataclass(frozen=True)
class RoundInfo:
    id: int
    opens_at_utc: datetime | None
    closes_at_utc: datetime | None
    results_at_utc: datetime | None = None
    payment_deadline_at_utc: datetime | None = None


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
    round_id: int | None = None    # set -> rule targets one specific round
    concert_id: int | None = None  # set -> rule targets a whole concert


# ── Output ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PlannedReminder:
    rule_id: int
    anchor: Anchor
    fire_at_utc: datetime
    round_id: int | None = None
    day_id: int | None = None


# ── The math ─────────────────────────────────────────────────────────────


def offset_delta(offset_days: int, offset_hours: int) -> timedelta:
    return timedelta(days=offset_days, hours=offset_hours)


# The one place an Anchor maps to a Round field. due_reminders() and
# snooze_reminder() in db/service.py both reuse anchor_time() below instead
# of re-implementing this switch — previously it was duplicated 3 times.
_ROUND_ANCHOR_FIELDS: dict[Anchor, str] = {
    Anchor.OPENS: "opens_at_utc",
    Anchor.CLOSES: "closes_at_utc",
    Anchor.RESULTS: "results_at_utc",
    Anchor.PAYMENT: "payment_deadline_at_utc",
}


def anchor_time(round_: RoundInfo, anchor: Anchor) -> datetime | None:
    """The moment a round-anchored rule measures from, or None if absent."""
    field = _ROUND_ANCHOR_FIELDS.get(anchor)
    return getattr(round_, field) if field else None  # EVENT_START never anchors to a round


def plan_for_rule(
    rule: RuleInfo,
    rounds: list[RoundInfo],
    days: list[DayInfo],
    now: datetime,
) -> list[PlannedReminder]:
    """Compute every future reminder this rule implies.

    Deterministic and side-effect free: same inputs, same output, always.
    Callers re-run this after any round/day edit; the DB layer's dedupe
    index turns re-planning into upserts instead of duplicates.
    """
    delta = offset_delta(rule.offset_days, rule.offset_hours)
    planned: list[PlannedReminder] = []

    if rule.anchor is Anchor.EVENT_START:
        # Day-anchored. A round-scoped rule with EVENT_START is a contradiction;
        # plan nothing rather than guess.
        if rule.round_id is not None:
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

    # Round-anchored (OPENS / CLOSES / RESULTS / PAYMENT)
    targets = rounds
    if rule.round_id is not None:
        targets = [r for r in rounds if r.id == rule.round_id]

    for round_ in targets:
        at = anchor_time(round_, rule.anchor)
        if at is None:
            continue  # this round doesn't have that bound — skip, don't error
        fire = at + delta
        if fire > now:
            planned.append(
                PlannedReminder(
                    rule_id=rule.id, anchor=rule.anchor, fire_at_utc=fire, round_id=round_.id
                )
            )
    return planned


def plan_for_rules(
    rules: list[RuleInfo],
    rounds: list[RoundInfo],
    days: list[DayInfo],
    now: datetime,
) -> list[PlannedReminder]:
    """Plan a batch of rules against one concert's rounds and days."""
    out: list[PlannedReminder] = []
    for rule in rules:
        out.extend(plan_for_rule(rule, rounds, days, now))
    return out
