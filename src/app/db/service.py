"""Queue synchronization and reminder retrieval.

This is the only module that both touches the database AND calls the domain
planner. Everything here is built around one idea: *re-planning must always
be safe*. Any edit to a concert triggers a full re-sync of affected rules,
and the sync semantics below turn that into upserts, not duplicates.

Sync semantics (per rule):
  * planned & not queued          -> insert
  * planned & queued, unsent      -> update fire_at if it moved
  * planned & queued, ALREADY SENT:
        - if the new fire time is in the future (deadline was postponed),
          re-arm it: clear sent_at and update fire_at. A moved deadline
          deserves a fresh reminder.
        - otherwise leave it alone (delivered, done).
  * queued, unsent, no longer planned -> delete (window removed / now past)
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import (
    Concert,
    ConcertAudit,
    ConcertDay,
    ConcertTag,
    Notification,
    ReminderPreset,
    ReminderQueue,
    ReminderRule,
    Round,
    RoundOutcome,
    Tag,
    TagMember,
    TagSubscription,
    User,
)
from app.domain.reminders import DayInfo, RoundInfo, RuleInfo, anchor_time, plan_for_rule
from app.domain.types import Anchor, LotteryOutcome, RoundKind, TagKind


def _now() -> datetime:
    return datetime.now(UTC)


# ── Users ────────────────────────────────────────────────────────────────


async def ensure_user(session: AsyncSession, discord_id: int, username: str) -> User:
    """Get-or-create the user row; refresh the username while we're at it."""
    user = await session.get(User, discord_id)
    if user is None:
        user = User(discord_id=discord_id, username=username)
        session.add(user)
        await session.flush()
    elif user.username != username:
        user.username = username
    return user


async def set_editor(
    session: AsyncSession, discord_id: int, is_editor: bool, username: str | None = None
) -> User:
    """Get-or-create the user row and set the DB-persisted editor flag.

    `username` is unknown when an admin promotes someone by raw Discord ID
    who has never logged in / used the bot — the stub is corrected by
    `ensure_user`'s refresh-on-login logic the next time they show up.
    """
    user = await session.get(User, discord_id)
    if user is None:
        user = User(discord_id=discord_id, username=username or str(discord_id))
        session.add(user)
        await session.flush()
    user.is_editor = is_editor
    return user


async def list_editors(session: AsyncSession) -> list[dict]:
    """DB-flagged editors (each noting env-lock), plus env-whitelisted ids
    that have never logged in / used the bot (no username known yet)."""
    db_editors = list((await session.execute(
        select(User).where(User.is_editor.is_(True)).order_by(User.username)
    )).scalars())
    editors = [
        {"id": u.discord_id, "username": u.username, "env": settings.is_editor(u.discord_id)}
        for u in db_editors
    ]
    seen = {u.discord_id for u in db_editors}
    for eid in sorted(settings.editor_ids - seen):
        editors.append({"id": eid, "username": None, "env": True})
    return editors


async def record_dm_outcome(session: AsyncSession, discord_id: int, blocked: bool) -> None:
    """Persist whether the most recent attempted DM to this user succeeded
    or hit discord.Forbidden -- the sitewide "DMs blocked" banner reads
    dm_blocked_since directly off the User row (see auth.current_user)."""
    user = await session.get(User, discord_id)
    if user is not None:
        user.dm_blocked_since = _now() if blocked else None


# ── Adapters: ORM -> domain dataclasses ──────────────────────────────────


def _round_info(r: Round) -> RoundInfo:
    return RoundInfo(
        id=r.id,
        opens_at_utc=r.opens_at_utc,
        closes_at_utc=r.closes_at_utc,
        results_at_utc=r.results_at_utc,
        payment_deadline_at_utc=r.payment_deadline_at_utc,
    )


def _day_info(d: ConcertDay) -> DayInfo:
    return DayInfo(id=d.id, starts_at_utc=d.starts_at_utc)


def _rule_info(r: ReminderRule) -> RuleInfo:
    return RuleInfo(
        id=r.id,
        anchor=r.anchor,
        offset_days=r.offset_days,
        offset_hours=r.offset_hours,
        round_id=r.round_id,
        concert_id=r.concert_id,
    )


def is_round_cancelled(round_: Round, cancelled_day_ids: set[int]) -> bool:
    """A round is implicitly cancelled when every leg it applies to is
    cancelled. A "General" round (empty/None applies_to) is never
    auto-cancelled this way -- it isn't tied to any specific leg.

    Public (no longer a leading-underscore module-private helper): used
    outside this module too now, by upcoming_rounds/upcoming_deadlines
    (below), the index route (web/app.py), and ShowDeadlinesButton
    (bot/views.py)."""
    if not round_.applies_to:
        return False
    return all(day_id in cancelled_day_ids for day_id in round_.applies_to)


# ── Queue sync ───────────────────────────────────────────────────────────


async def _apply_outcome_suppression(
    session: AsyncSession, user_id: int, rounds: list[Round], anchor: Anchor
) -> list[Round]:
    """Drop rounds this user's outcomes make irrelevant, before the pure
    planner ever sees them -- same pattern as the cancelled-round
    filtering sync_rule already does. Two passes: cross-round (every leg
    a round covers is already secured via WON/PAID elsewhere on this
    concert) then same-round (this rule's own anchor is moot given this
    round's outcome)."""
    if not rounds:
        return rounds

    concert_id = rounds[0].concert_id
    all_concert_rounds = list((await session.execute(
        select(Round).where(Round.concert_id == concert_id)
    )).scalars())
    all_round_ids = [r.id for r in all_concert_rounds]
    outcomes = {
        o.round_id: o.outcome for o in (await session.execute(
            select(RoundOutcome).where(
                RoundOutcome.user_id == user_id, RoundOutcome.round_id.in_(all_round_ids)
            )
        )).scalars()
    } if all_round_ids else {}
    all_day_ids = set((await session.execute(
        select(ConcertDay.id).where(ConcertDay.concert_id == concert_id)
    )).scalars())

    # Per-round contribution, so each round's own outcome can be excluded
    # when checking IT for cross-round suppression -- "secured elsewhere"
    # must not let a round's own WON/PAID outcome suppress itself; a round
    # can only be cross-suppressed by OTHER rounds covering its legs.
    secured_by: dict[int, set[int]] = {}
    for r in all_concert_rounds:
        if outcomes.get(r.id) in (LotteryOutcome.WON, LotteryOutcome.PAID):
            secured_by[r.id] = set(r.applies_to) if r.applies_to else all_day_ids

    survivors = []
    for r in rounds:
        applies = set(r.applies_to) if r.applies_to else all_day_ids
        secured_elsewhere: set[int] = set()
        for other_id, legs in secured_by.items():
            if other_id != r.id:
                secured_elsewhere |= legs
        if applies and applies <= secured_elsewhere:
            continue  # every leg this round covers is already secured elsewhere
        outcome = outcomes.get(r.id)
        if anchor is Anchor.RESULTS and outcome is LotteryOutcome.NOT_APPLIED:
            continue
        if anchor is Anchor.PAYMENT and outcome in (
            LotteryOutcome.LOST, LotteryOutcome.PAID, LotteryOutcome.NOT_APPLIED
        ):
            continue
        survivors.append(r)
    return survivors


async def record_round_outcome(
    session: AsyncSession, user_id: int, round_id: int, outcome: LotteryOutcome,
    now: datetime | None = None,
) -> None:
    """A user's DM-button click recording their lottery progress on one
    round. Permissive sequence: NOT_APPLIED/APPLIED only set the FIRST
    outcome ever recorded for a round; WON/LOST can be set regardless of
    the current state -- clicking them without ever clicking "I applied"
    first just works; PAID only reachable from WON.

    Re-syncs every one of this user's rules for the round's whole
    concert (round-scoped or concert-wide), not just rules on this one
    round -- a concert-wide rule's own candidate list also needs to drop
    a now-suppressed round, and re-running sync_rule is always safe."""
    now = now or _now()
    round_ = await session.get(Round, round_id)
    if round_ is None:
        return

    existing = (await session.execute(
        select(RoundOutcome).where(
            RoundOutcome.user_id == user_id, RoundOutcome.round_id == round_id
        )
    )).scalar_one_or_none()

    if outcome in (LotteryOutcome.NOT_APPLIED, LotteryOutcome.APPLIED) and existing is not None:
        return  # starting states only apply once
    if outcome is LotteryOutcome.PAID and (
        existing is None or existing.outcome is not LotteryOutcome.WON
    ):
        return  # PAID only reachable from WON

    if existing is None:
        session.add(RoundOutcome(user_id=user_id, round_id=round_id, outcome=outcome))
    else:
        existing.outcome = outcome
    await session.flush()

    await reinstate_user_rules(session, user_id, round_.concert_id, now)

    if outcome is LotteryOutcome.LOST:
        await _auto_arm_next_round(session, user_id, round_, now)


async def _next_round_for_leg(session: AsyncSession, lost_round: Round) -> Round | None:
    """The next round for the same leg(s) a just-lost round applied to --
    the earliest-opening round (of those with an opens_at_utc set) whose
    applies_to overlaps the lost round's (or is empty -- General rounds
    cover every leg), opening strictly after the lost round's own close
    (falling back to its open time if it has no close)."""
    candidates = list((await session.execute(
        select(Round).where(
            Round.concert_id == lost_round.concert_id,
            Round.id != lost_round.id,
            Round.opens_at_utc.is_not(None),
        )
    )).scalars())
    lost_legs = set(lost_round.applies_to) if lost_round.applies_to else None
    after = lost_round.closes_at_utc or lost_round.opens_at_utc

    def overlaps(r: Round) -> bool:
        if lost_legs is None or not r.applies_to:
            return True  # either side is "every leg" -- always overlaps
        return bool(lost_legs & set(r.applies_to))

    qualifying = [r for r in candidates if overlaps(r) and r.opens_at_utc > after]
    if not qualifying:
        return None
    return min(qualifying, key=lambda r: r.opens_at_utc)


async def _auto_arm_next_round(
    session: AsyncSession, user_id: int, lost_round: Round, now: datetime | None = None
) -> None:
    """After a LOST outcome: find the next round for the same leg and
    auto-create a real ReminderRule for its OPENS anchor, using the
    user's default preset offset if they have one, else immediate."""
    now = now or _now()
    next_round = await _next_round_for_leg(session, lost_round)
    if next_round is None:
        return  # nothing to arm yet -- sync_concert catches up when it's added

    existing = (await session.execute(
        select(ReminderRule.id).where(
            ReminderRule.user_id == user_id,
            ReminderRule.round_id == next_round.id,
            ReminderRule.anchor == Anchor.OPENS,
        )
    )).scalar_one_or_none()
    if existing is not None:
        return  # already armed

    offset_days, offset_hours = 0, 0
    preset = await get_default_preset(session, user_id)
    if preset is not None:
        await session.refresh(preset, ["items"])
        for item in preset.items:
            if item.anchor is Anchor.OPENS:
                offset_days, offset_hours = item.offset_days, item.offset_hours
                break

    rule = ReminderRule(
        user_id=user_id, round_id=next_round.id, anchor=Anchor.OPENS,
        offset_days=offset_days, offset_hours=offset_hours,
    )
    session.add(rule)
    await session.flush()
    await sync_rule(session, rule, now)


async def sync_rule(session: AsyncSession, rule: ReminderRule, now: datetime | None = None) -> None:
    """Reconcile reminder_queue with what this rule currently implies."""
    now = now or _now()

    # Gather the rounds/days in this rule's scope. Cancelled legs and rounds
    # implicitly cancelled by them (see is_round_cancelled) are filtered
    # out here, before domain/reminders.py ever sees them -- the pure
    # planner never learns the concept of "cancelled"; it just sees fewer
    # candidates, and the existing "nothing planned -> delete" sync
    # semantics clear the reminders with no new suppression logic. This
    # user's own RoundOutcome state is filtered the same way (see
    # _apply_outcome_suppression) -- the planner stays equally ignorant
    # of lottery outcomes.
    if rule.round_id is not None:
        round_ = await session.get(Round, rule.round_id)
        if round_ is None:
            live_rounds: list[Round] = []
        else:
            cancelled_day_ids = set((await session.execute(
                select(ConcertDay.id).where(
                    ConcertDay.concert_id == round_.concert_id,
                    ConcertDay.cancelled.is_(True),
                )
            )).scalars())
            live_rounds = [] if is_round_cancelled(round_, cancelled_day_ids) else [round_]
        days: list[DayInfo] = []
    else:
        rres = await session.execute(select(Round).where(Round.concert_id == rule.concert_id))
        dres = await session.execute(
            select(ConcertDay).where(ConcertDay.concert_id == rule.concert_id)
        )
        all_rounds = list(rres.scalars())
        all_days = list(dres.scalars())
        cancelled_day_ids = {d.id for d in all_days if d.cancelled}
        live_rounds = [r for r in all_rounds if not is_round_cancelled(r, cancelled_day_ids)]
        days = [_day_info(d) for d in all_days if not d.cancelled]

    live_rounds = await _apply_outcome_suppression(session, rule.user_id, live_rounds, rule.anchor)
    rounds = [_round_info(r) for r in live_rounds]

    planned = plan_for_rule(_rule_info(rule), rounds, days, now)
    planned_by_key = {(p.round_id or 0, p.day_id or 0, p.anchor): p for p in planned}

    qres = await session.execute(select(ReminderQueue).where(ReminderQueue.rule_id == rule.id))
    existing = list(qres.scalars())
    existing_keys = set()

    for row in existing:
        key = (row.round_id or 0, row.day_id or 0, row.anchor)
        existing_keys.add(key)
        p = planned_by_key.get(key)
        if p is None:
            if row.sent_at_utc is None:
                await session.delete(row)  # no longer planned and never sent
            continue
        if row.sent_at_utc is None:
            row.fire_at_utc = p.fire_at_utc  # cheap even if unchanged
        elif p.fire_at_utc > now:
            # Deadline postponed after we already reminded: re-arm.
            row.fire_at_utc = p.fire_at_utc
            row.sent_at_utc = None

    for key, p in planned_by_key.items():
        if key not in existing_keys:
            session.add(
                ReminderQueue(
                    rule_id=rule.id,
                    round_id=p.round_id,
                    day_id=p.day_id,
                    anchor=p.anchor,
                    fire_at_utc=p.fire_at_utc,
                )
            )
    await session.flush()


async def sync_concert(
    session: AsyncSession, concert_id: int, now: datetime | None = None
) -> int:
    """Re-sync every rule touching this concert (called after any edit).

    Covers concert-scoped rules and round-scoped rules on its rounds.
    Also catches up any LOST outcome whose auto-armed "next round" didn't
    exist yet at the time of the loss -- now that a round was just added
    or edited on this concert, check again. Returns the number of rules
    synced.
    """
    res = await session.execute(
        select(ReminderRule)
        .outerjoin(Round, ReminderRule.round_id == Round.id)
        .where(
            (ReminderRule.concert_id == concert_id) | (Round.concert_id == concert_id)
        )
    )
    rules = list(res.scalars())
    for rule in rules:
        await sync_rule(session, rule, now)

    lost_outcomes = list((await session.execute(
        select(RoundOutcome, Round)
        .join(Round, RoundOutcome.round_id == Round.id)
        .where(Round.concert_id == concert_id, RoundOutcome.outcome == LotteryOutcome.LOST)
    )).all())
    for outcome, lost_round in lost_outcomes:
        await _auto_arm_next_round(session, outcome.user_id, lost_round, now)

    return len(rules)


async def notify_newly_cancelled_legs(
    session: AsyncSession,
    concert_id: int,
    newly_cancelled_day_ids: set[int],
    now: datetime | None = None,
) -> int:
    """Call BEFORE sync_concert (which will delete the now-unplanned queue
    rows for these legs). Queues one Notification per user who is about to
    lose EVERY one of their unsent reminders on this concert as a direct
    result of these legs being newly cancelled -- a user with other live
    legs/rounds to fall back on gets nothing. Returns how many notifications
    were queued."""
    if not newly_cancelled_day_ids:
        return 0
    now = now or _now()

    all_cancelled_day_ids = set((await session.execute(
        select(ConcertDay.id).where(
            ConcertDay.concert_id == concert_id, ConcertDay.cancelled.is_(True)
        )
    )).scalars())
    rounds = list(
        (await session.execute(select(Round).where(Round.concert_id == concert_id))).scalars()
    )
    affected_round_ids = {
        r.id for r in rounds
        if set(r.applies_to or []) & newly_cancelled_day_ids
        and is_round_cancelled(r, all_cancelled_day_ids)
    }

    res = await session.execute(
        select(ReminderRule.user_id, ReminderQueue.id)
        .join(ReminderQueue, ReminderQueue.rule_id == ReminderRule.id)
        .where(
            ReminderQueue.sent_at_utc.is_(None),
            (ReminderQueue.day_id.in_(newly_cancelled_day_ids))
            | (ReminderQueue.round_id.in_(affected_round_ids)),
        )
    )
    doomed_by_user: dict[int, set[int]] = {}
    for user_id, queue_id in res.all():
        doomed_by_user.setdefault(user_id, set()).add(queue_id)
    if not doomed_by_user:
        return 0

    concert = await session.get(Concert, concert_id)
    queued = 0
    for user_id, doomed_ids in doomed_by_user.items():
        other_row = (await session.execute(
            select(ReminderQueue.id)
            .join(ReminderRule, ReminderQueue.rule_id == ReminderRule.id)
            .outerjoin(Round, ReminderRule.round_id == Round.id)
            .where(
                ReminderRule.user_id == user_id,
                (ReminderRule.concert_id == concert_id) | (Round.concert_id == concert_id),
                ReminderQueue.sent_at_utc.is_(None),
                ReminderQueue.id.not_in(doomed_ids),
            )
            .limit(1)
        )).scalar_one_or_none()
        if other_row is not None:
            continue  # other live legs/rounds on this concert -- no notice
        session.add(Notification(
            user_id=user_id,
            body=f"A performance for {concert.title} was cancelled, and your "
                 f"reminder(s) for it were cleared.",
            concert_id=concert_id,
            kind="leg_cancelled",
            created_at=now,
        ))
        queued += 1
    await session.flush()
    return queued


async def reinstate_user_rules(
    session: AsyncSession, user_id: int, concert_id: int, now: datetime | None = None
) -> int:
    """[Reinstate my reminders] button, after a leg-cancellation notice.
    Re-syncs this user's still-existing rules on this concert -- if the
    cancelled leg is still cancelled, sync finds nothing to plan for it and
    this is a no-op; if an editor has since un-cancelled it, the reminder
    re-arms normally. Never deletes or recreates ReminderRule rows -- they
    were never touched by the cancellation in the first place. Returns how
    many rules were re-synced."""
    res = await session.execute(
        select(ReminderRule)
        .outerjoin(Round, ReminderRule.round_id == Round.id)
        .where(
            ReminderRule.user_id == user_id,
            (ReminderRule.concert_id == concert_id) | (Round.concert_id == concert_id),
        )
    )
    rules = list(res.scalars())
    for rule in rules:
        await sync_rule(session, rule, now)
    return len(rules)


# ── Retrieval for the scheduler and /upcoming ────────────────────────────


@dataclass(frozen=True)
class DueReminder:
    """Everything the scheduler needs to deliver one reminder."""

    queue_id: int
    discord_id: int
    user_timezone: str
    concert_title: str
    anchor: Anchor
    fire_at_utc: datetime
    # round-anchored:
    round_id: int | None = None
    round_label: str | None = None
    round_kind: str | None = None
    outcome: LotteryOutcome | None = None
    anchor_time_utc: datetime | None = None
    url: str | None = None
    # day-anchored:
    day_label: str | None = None


async def due_reminders(
    session: AsyncSession, now: datetime | None = None, limit: int = 100
) -> list[DueReminder]:
    """Batch-fetched: one SELECT for the due queue rows, then one SELECT per
    related entity type (rule/user/round/day/concert) instead of up to 4
    per row -- a fixed number of round trips regardless of batch size."""
    now = now or _now()
    res = await session.execute(
        select(ReminderQueue)
        .where(ReminderQueue.sent_at_utc.is_(None), ReminderQueue.fire_at_utc <= now)
        .order_by(ReminderQueue.fire_at_utc)
        .limit(limit)
    )
    rows = list(res.scalars())
    if not rows:
        return []

    rule_ids = {row.rule_id for row in rows}
    round_ids = {row.round_id for row in rows if row.round_id is not None}
    day_ids = {row.day_id for row in rows if row.day_id is not None}

    rules = {
        r.id: r for r in
        (await session.execute(select(ReminderRule).where(ReminderRule.id.in_(rule_ids)))).scalars()
    }
    user_ids = {r.user_id for r in rules.values()}
    users = {
        u.discord_id: u for u in
        (await session.execute(select(User).where(User.discord_id.in_(user_ids)))).scalars()
    } if user_ids else {}
    rounds = {
        r.id: r for r in
        (await session.execute(select(Round).where(Round.id.in_(round_ids)))).scalars()
    } if round_ids else {}
    days = {
        d.id: d for d in
        (await session.execute(select(ConcertDay).where(ConcertDay.id.in_(day_ids)))).scalars()
    } if day_ids else {}
    concert_ids = {r.concert_id for r in rounds.values()} | {d.concert_id for d in days.values()}
    concerts = {
        c.id: c for c in
        (await session.execute(select(Concert).where(Concert.id.in_(concert_ids)))).scalars()
    } if concert_ids else {}
    # queue + rules + users + rounds + concerts + outcomes -- still a
    # fixed number of round trips regardless of batch size.
    outcomes: dict[tuple[int, int], LotteryOutcome] = {}
    if round_ids:
        outcome_rows = list((await session.execute(
            select(RoundOutcome).where(RoundOutcome.round_id.in_(round_ids))
        )).scalars())
        outcomes = {(o.user_id, o.round_id): o.outcome for o in outcome_rows}

    out: list[DueReminder] = []
    for row in rows:
        rule = rules.get(row.rule_id)
        user = users.get(rule.user_id) if rule else None
        round_ = rounds.get(row.round_id) if row.round_id is not None else None
        day = days.get(row.day_id) if row.day_id is not None else None
        parent = round_ or day
        concert = concerts.get(parent.concert_id) if parent else None
        if user is None or concert is None:
            continue  # orphaned row; cascades should prevent this, but never crash the loop
        out.append(
            DueReminder(
                queue_id=row.id,
                discord_id=user.discord_id,
                user_timezone=user.timezone,
                concert_title=concert.title,
                anchor=row.anchor,
                fire_at_utc=row.fire_at_utc,
                round_id=round_.id if round_ else None,
                round_label=round_.label if round_ else None,
                round_kind=round_.kind.value if round_ else None,
                outcome=outcomes.get((user.discord_id, round_.id)) if round_ else None,
                anchor_time_utc=(
                    anchor_time(_round_info(round_), row.anchor)
                    if round_
                    else (day.starts_at_utc if day else None)
                ),
                url=round_.url if round_ else None,
                day_label=day.label if day else None,
            )
        )
    return out


async def mark_sent(session: AsyncSession, queue_id: int, now: datetime | None = None) -> None:
    row = await session.get(ReminderQueue, queue_id)
    if row is not None:
        row.sent_at_utc = now or _now()
        await session.flush()


async def upcoming_rounds(
    session: AsyncSession, now: datetime | None = None, horizon_days: int = 14
) -> list[tuple[Concert, Round]]:
    """Rounds opening or closing within the horizon — powers /upcoming.
    Implicitly-cancelled rounds (every leg they apply to is cancelled) are
    excluded, same rule sync_rule/upcoming_deadlines already use."""
    from datetime import timedelta

    now = now or _now()
    end = now + timedelta(days=horizon_days)
    res = await session.execute(
        select(Concert, Round)
        .join(Round, Round.concert_id == Concert.id)
        .where(
            (Round.opens_at_utc.between(now, end))
            | (Round.closes_at_utc.between(now, end))
        )
        .order_by(Round.closes_at_utc.is_(None), Round.closes_at_utc, Round.opens_at_utc)
    )
    pairs = [(c, r) for c, r in res.all()]
    if not pairs:
        return pairs
    cancelled_day_ids = set((await session.execute(
        select(ConcertDay.id).where(ConcertDay.cancelled.is_(True))
    )).scalars())
    return [(c, r) for c, r in pairs if not is_round_cancelled(r, cancelled_day_ids)]


LABEL_BY_ANCHOR: dict[Anchor, str] = {
    Anchor.OPENS: "opens",
    Anchor.CLOSES: "closes",
    Anchor.RESULTS: "results announced",
    Anchor.PAYMENT: "payment due",
    Anchor.EVENT_START: "event",
}

LABEL_BY_ROUND_KIND: dict[RoundKind, str] = {
    RoundKind.LOTTERY_ROUND: "Lottery round",
    RoundKind.ELIGIBILITY_ITEM_SALE: "Eligibility item sale",
    RoundKind.STREAM_TICKET_SALE: "Stream ticket sale",
    RoundKind.GENERAL_SALE: "General sale",
    RoundKind.RESULT_ANNOUNCEMENT: "Result announcement",
    RoundKind.PAYMENT_DEADLINE: "Payment deadline",
    RoundKind.FCFS_SALE: "First come, first served",
    RoundKind.TOUR_PACKAGE: "Overseas tour package",
    RoundKind.OTHER: "Other",
}


@dataclass(frozen=True)
class UpcomingDeadline:
    """One row on the index page's global chronological "things happening
    soon" list -- every non-cancelled round/day across every concert, one
    entry per SET timestamp field (not one per round: a round with both a
    close and a payment deadline set produces two independent rows).
    Future-only, meant to be sorted soonest-first and truncated to a fixed
    count by the caller."""

    concert_title: str
    event_id: str
    label: str
    anchor: Anchor
    at_utc: datetime
    url: str | None = None


async def upcoming_deadlines(
    session: AsyncSession, now: datetime | None = None, limit: int = 10
) -> list[UpcomingDeadline]:
    """Global (not reminder-rule-scoped, not per-user) chronological
    deadline list for the index page. Reuses is_round_cancelled the same
    way sync_rule/notify_newly_cancelled_legs already do."""
    now = now or _now()
    days = list((await session.execute(select(ConcertDay))).scalars())
    rounds = list((await session.execute(select(Round))).scalars())
    cancelled_day_ids = {d.id for d in days if d.cancelled}
    concert_ids = {d.concert_id for d in days} | {r.concert_id for r in rounds}
    concerts = {
        c.id: c for c in
        (await session.execute(select(Concert).where(Concert.id.in_(concert_ids)))).scalars()
    } if concert_ids else {}

    out: list[UpcomingDeadline] = []
    for d in days:
        if d.cancelled or d.starts_at_utc <= now:
            continue
        concert = concerts.get(d.concert_id)
        if concert is None:
            continue
        out.append(UpcomingDeadline(
            concert_title=concert.title, event_id=concert.event_id, label=d.label,
            anchor=Anchor.EVENT_START, at_utc=d.starts_at_utc,
        ))

    for r in rounds:
        if is_round_cancelled(r, cancelled_day_ids):
            continue
        concert = concerts.get(r.concert_id)
        if concert is None:
            continue
        for anchor, ts in (
            (Anchor.OPENS, r.opens_at_utc),
            (Anchor.CLOSES, r.closes_at_utc),
            (Anchor.RESULTS, r.results_at_utc),
            (Anchor.PAYMENT, r.payment_deadline_at_utc),
        ):
            if ts is None or ts <= now:
                continue
            out.append(UpcomingDeadline(
                concert_title=concert.title, event_id=concert.event_id, label=r.label,
                anchor=anchor, at_utc=ts, url=r.url,
            ))

    out.sort(key=lambda e: e.at_utc)
    return out[:limit]


# ── Personal calendar feed ────────────────────────────────────────────────


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def generate_calendar_token(session: AsyncSession, user_id: int) -> str:
    """(Re)generate the user's calendar-feed token. Only the hash is stored
    (same pattern as WebSession.token_hash) -- the raw value is returned
    once, for the caller to hand back in a URL; it can never be recovered
    afterward, only replaced by generating a new one. Callers are always
    behind require_user, so the row already exists (ensure_user ran at
    login) -- fetched plainly here to avoid overwriting the username with
    a blank one."""
    user = await session.get(User, user_id)
    if user is None:
        raise ValueError(f"no such user: {user_id}")
    token = secrets.token_urlsafe(32)
    user.calendar_token_hash = _hash_token(token)
    await session.flush()
    return token


async def get_user_by_calendar_token(session: AsyncSession, token: str) -> User | None:
    res = await session.execute(
        select(User).where(User.calendar_token_hash == _hash_token(token))
    )
    return res.scalar_one_or_none()


@dataclass(frozen=True)
class CalendarEvent:
    """One deadline on a user's personal feed -- the actual moment (same
    timestamp the single-round .ics download already uses), not any
    reminder's lead time."""

    concert_title: str
    label: str
    at_utc: datetime
    url: str | None = None
    notes: str | None = None


async def user_calendar_events(
    session: AsyncSession, user_id: int, now: datetime | None = None
) -> list[CalendarEvent]:
    """Every round/day the user currently has an active reminder rule
    covering (concert-wide or round-specific), each producing ONE event at
    its real deadline -- sourced from reminder_queue, which already encodes
    exactly which rounds/days are in scope per rule (sync_rule/plan_for_rule
    already did the anchor-specific filtering). Future-only: a round/day
    whose deadline already passed is left off the feed.
    """
    now = now or _now()
    round_ids = set((await session.execute(
        select(ReminderQueue.round_id)
        .join(ReminderRule, ReminderQueue.rule_id == ReminderRule.id)
        .where(ReminderRule.user_id == user_id, ReminderQueue.round_id.is_not(None))
        .distinct()
    )).scalars())
    day_ids = set((await session.execute(
        select(ReminderQueue.day_id)
        .join(ReminderRule, ReminderQueue.rule_id == ReminderRule.id)
        .where(ReminderRule.user_id == user_id, ReminderQueue.day_id.is_not(None))
        .distinct()
    )).scalars())

    events: list[CalendarEvent] = []

    if round_ids:
        rounds = list((await session.execute(
            select(Round).where(Round.id.in_(round_ids))
        )).scalars())
        concerts = {
            c.id: c for c in (await session.execute(
                select(Concert).where(Concert.id.in_({r.concert_id for r in rounds}))
            )).scalars()
        }
        for r in rounds:
            at = r.closes_at_utc or r.opens_at_utc or r.results_at_utc or r.payment_deadline_at_utc
            if at is None or at < now:
                continue
            concert = concerts.get(r.concert_id)
            events.append(CalendarEvent(
                concert_title=concert.title if concert else "Concert",
                label=r.label, at_utc=at, url=r.url, notes=r.notes,
            ))

    if day_ids:
        days = list((await session.execute(
            select(ConcertDay).where(ConcertDay.id.in_(day_ids))
        )).scalars())
        concerts = {
            c.id: c for c in (await session.execute(
                select(Concert).where(Concert.id.in_({d.concert_id for d in days}))
            )).scalars()
        }
        for d in days:
            if d.starts_at_utc < now:
                continue
            concert = concerts.get(d.concert_id)
            events.append(CalendarEvent(
                concert_title=concert.title if concert else "Concert",
                label=d.label, at_utc=d.starts_at_utc,
            ))

    events.sort(key=lambda e: e.at_utc)
    return events


# ── Concert edit history ──────────────────────────────────────────────────

# Deliberately just the concert's own top-level fields -- day/round/tag
# adds-removes-edits are NOT tracked here, that's a much bigger feature than
# "lightweight". event_id is included since renaming a concert's URL handle
# is exactly the kind of quiet, easy-to-miss edit an audit log is for.
TRACKED_CONCERT_FIELDS = [
    "event_id", "title", "title_en", "kind", "organizer", "categories",
    "eventernote_url", "official_url", "source_url", "performers_text", "notes",
]


def _audit_value(v: object) -> object:
    """Enum members (e.g. ConcertKind) aren't JSON-serializable -- store
    their plain .value instead. Everything else here is already a
    JSON-safe str/None."""
    return v.value if hasattr(v, "value") else v


def snapshot_concert(concert: Concert) -> dict:
    """A before/after comparison point for record_concert_edit -- call once
    before mutating the concert, once after."""
    return {f: _audit_value(getattr(concert, f)) for f in TRACKED_CONCERT_FIELDS}


async def record_concert_edit(
    session: AsyncSession, concert: Concert, edited_by: int, before: dict
) -> ConcertAudit | None:
    """Diffs `before` (from snapshot_concert, taken pre-mutation) against the
    concert's current state and inserts one audit row -- ONE row per edit
    covering every field that changed, not one row per field. Returns None
    (and inserts nothing) when nothing tracked actually changed, so a no-op
    resubmit of the edit form doesn't pollute the history."""
    after = snapshot_concert(concert)
    changes = [
        {"field": f, "before": before[f], "after": after[f]}
        for f in TRACKED_CONCERT_FIELDS if before[f] != after[f]
    ]
    if not changes:
        return None
    audit = ConcertAudit(concert_id=concert.id, edited_by=edited_by, changes=changes)
    session.add(audit)
    await session.flush()
    return audit


async def concert_audit_log(
    session: AsyncSession, concert_id: int, limit: int = 20
) -> list[ConcertAudit]:
    res = await session.execute(
        select(ConcertAudit)
        .where(ConcertAudit.concert_id == concert_id)
        .order_by(ConcertAudit.edited_at_utc.desc())
        .limit(limit)
    )
    audits = list(res.scalars())
    for a in audits:
        await session.refresh(a, ["editor"])
    return audits


# ── Tags ─────────────────────────────────────────────────────────────────


async def find_tag_by_name(session: AsyncSession, name: str) -> Tag | None:
    from sqlalchemy import func as sa_func

    res = await session.execute(
        select(Tag).where(sa_func.lower(Tag.name) == name.strip().lower())
    )
    return res.scalar_one_or_none()


async def group_members(session: AsyncSession, group_tag_id: int) -> list[Tag]:
    res = await session.execute(
        select(Tag)
        .join(TagMember, Tag.id == TagMember.member_tag_id)
        .where(TagMember.group_tag_id == group_tag_id)
        .order_by(Tag.name)
    )
    return list(res.scalars())


async def active_concerts_missing_member(
    session: AsyncSession, group_id: int, member_id: int, now: datetime | None = None
) -> list[Concert]:
    """Concerts tagged with `group_id` that don't already carry `member_id`
    and have at least one live (non-cancelled) leg whose date hasn't
    passed -- the set the Tags page's retroactive-apply confirmation
    offers to bulk-attach an artist to. "Active" reuses the same
    live-leg-date-range logic concert_date_range()/concert_past already use
    on the concert detail page (routes/concerts.py), reimplemented directly
    here rather than imported from web/routes/ -- this module sits below
    routes in this project's dependency direction, so importing the other
    way would invert it for a few lines of straightforward logic."""
    now = now or _now()
    res = await session.execute(
        select(Concert)
        .join(ConcertTag, ConcertTag.concert_id == Concert.id)
        .where(ConcertTag.tag_id == group_id)
    )
    candidates = list(res.scalars())
    already_tagged = set((await session.execute(
        select(ConcertTag.concert_id).where(ConcertTag.tag_id == member_id)
    )).scalars())

    out = []
    for c in candidates:
        if c.id in already_tagged:
            continue
        await session.refresh(c, ["days"])
        live_starts = [d.starts_at_utc for d in c.days if not d.cancelled]
        if not live_starts or max(live_starts) < now:
            continue
        out.append(c)
    return out


async def tag_picker_context(session: AsyncSession) -> dict:
    """Data the shared tag-picker partial needs: tags grouped by kind, plus
    the two JSON blobs its client-side script reads (group->members for
    auto-populating artists, and id->name for rendering selected chips).
    Shared by the new-concert form and the URL-import draft form."""
    tags = list((await session.execute(select(Tag).order_by(Tag.kind, Tag.name))).scalars())
    by_kind: dict[str, list[Tag]] = {}
    for t in tags:
        by_kind.setdefault(t.kind.value, []).append(t)
    groups_data = {}
    for g in by_kind.get("group", []):
        groups_data[g.id] = {
            "name": g.name,
            "franchise": g.parent_id,
            "members": [{"id": m.id, "name": m.name} for m in await group_members(session, g.id)],
        }
    tag_names = {t.id: t.name for t in tags}
    return {"by_kind": by_kind, "groups_json": groups_data, "tag_names_json": tag_names}


async def _is_attached(session: AsyncSession, concert_id: int, tag_id: int) -> bool:
    res = await session.execute(
        select(ConcertTag).where(
            ConcertTag.concert_id == concert_id, ConcertTag.tag_id == tag_id
        )
    )
    return res.scalar_one_or_none() is not None


async def attach_tag(
    session: AsyncSession, concert_id: int, tag: Tag, expand: bool = True
) -> list[Tag]:
    """Attach a tag to a concert. Returns the list of tags newly attached.

    THE EXPANSION RULE (agreed semantics): attaching a GROUP tag also
    attaches every current member — at this moment only. Editors may then
    remove individual members (not performing); nothing re-adds them unless
    the group tag itself is detached and re-attached. Group membership
    edits never touch existing concerts.

    expand=False is for the creation form, where the editor picks artists
    explicitly (pre-checked from the group) — expansion there would undo
    their unchecks.
    """
    added: list[Tag] = []
    if not await _is_attached(session, concert_id, tag.id):
        session.add(ConcertTag(concert_id=concert_id, tag_id=tag.id))
        added.append(tag)
        if expand and tag.kind is TagKind.GROUP:
            for member in await group_members(session, tag.id):
                if not await _is_attached(session, concert_id, member.id):
                    session.add(ConcertTag(concert_id=concert_id, tag_id=member.id))
                    added.append(member)
    await session.flush()
    return added


async def detach_tag(session: AsyncSession, concert_id: int, tag_id: int) -> None:
    res = await session.execute(
        select(ConcertTag).where(
            ConcertTag.concert_id == concert_id, ConcertTag.tag_id == tag_id
        )
    )
    row = res.scalar_one_or_none()
    if row is not None:
        await session.delete(row)
        await session.flush()


# ── Presets & subscriptions (Phase 10) ───────────────────────────────────


async def apply_preset(
    session: AsyncSession, user_id: int, concert_id: int, preset: ReminderPreset
) -> int:
    """Create this preset's rules on a concert (idempotent per item).

    An item is skipped if the user already has an identical rule
    (same concert, anchor, offsets) — repeated clicks are harmless.
    Returns how many rules were actually created.
    """
    await session.refresh(preset, ["items"])
    existing = await session.execute(
        select(ReminderRule).where(
            ReminderRule.user_id == user_id, ReminderRule.concert_id == concert_id
        )
    )
    have = {(r.anchor, r.offset_days, r.offset_hours) for r in existing.scalars()}

    created = 0
    for item in preset.items:
        key = (item.anchor, item.offset_days, item.offset_hours)
        if key in have:
            continue
        rule = ReminderRule(
            user_id=user_id,
            concert_id=concert_id,
            anchor=item.anchor,
            offset_days=item.offset_days,
            offset_hours=item.offset_hours,
        )
        session.add(rule)
        await session.flush()
        await sync_rule(session, rule)
        have.add(key)
        created += 1
    return created


async def handle_newly_tagged(
    session: AsyncSession, concert: Concert, new_tags: list[Tag]
) -> int:
    """The notify-and-apply pipeline. Called when tags are attached to a concert.

    For every user subscribed to any of the newly attached tags:
      * a user who ALREADY has rules on this concert is skipped entirely
        (they know about it; prevents double-apply when a second matching
        tag lands later)
      * otherwise: linked preset auto-applies, and if notify is on, a DM
        notice is queued.
    A user matched by several tags at once (group + members) is handled once;
    if several matched subscriptions carry presets, the earliest-created wins.
    Returns the number of users processed.
    """
    if not new_tags:
        return 0
    res = await session.execute(
        select(TagSubscription)
        .where(TagSubscription.tag_id.in_([t.id for t in new_tags]))
        .order_by(TagSubscription.id)
    )
    subs_by_user: dict[int, list[TagSubscription]] = {}
    for sub in res.scalars():
        subs_by_user.setdefault(sub.user_id, []).append(sub)

    tag_names = ", ".join(t.name for t in new_tags)
    processed = 0
    for user_id, subs in subs_by_user.items():
        already = await session.execute(
            select(ReminderRule.id)
            .where(ReminderRule.user_id == user_id, ReminderRule.concert_id == concert.id)
            .limit(1)
        )
        if already.scalar_one_or_none() is not None:
            continue

        preset = None
        for sub in subs:  # earliest-created subscription with a preset wins
            if sub.preset_id is not None:
                preset = await session.get(ReminderPreset, sub.preset_id)
                if preset is not None:
                    break
        n = 0
        if preset is not None:
            n = await apply_preset(session, user_id, concert.id, preset)

        if any(s.notify for s in subs):
            if preset is not None:
                tail = f"your preset \u201c{preset.name}\u201d set {n} reminder(s)."
            else:
                tail = "no preset linked \u2014 set reminders on the site."
            session.add(Notification(
                user_id=user_id,
                concert_id=concert.id,
                kind="new_event",
                body=(  # plain-text fallback only; normally rendered as an embed
                    f"\U0001f195 New event: **{concert.title}** (tagged: {tag_names}) \u2014 {tail}"
                ),
            ))
        processed += 1
    await session.flush()
    return processed


async def due_notifications(
    session: AsyncSession, limit: int = 100
) -> list[Notification]:
    res = await session.execute(
        select(Notification)
        .where(Notification.sent_at_utc.is_(None))
        .order_by(Notification.created_at)
        .limit(limit)
    )
    return list(res.scalars())


async def mark_notification_sent(session: AsyncSession, notification_id: int) -> None:
    row = await session.get(Notification, notification_id)
    if row is not None:
        row.sent_at_utc = _now()
        await session.flush()


# ── DM button actions (Phase 12) — pure DB logic, discord-free ───────────


async def get_default_preset(session: AsyncSession, user_id: int) -> ReminderPreset | None:
    res = await session.execute(
        select(ReminderPreset).where(
            ReminderPreset.user_id == user_id, ReminderPreset.is_default.is_(True)
        )
    )
    return res.scalar_one_or_none()


async def set_default_preset(session: AsyncSession, user_id: int, preset_id: int) -> None:
    res = await session.execute(
        select(ReminderPreset).where(ReminderPreset.user_id == user_id)
    )
    for p in res.scalars():
        p.is_default = p.id == preset_id
    await session.flush()


async def apply_default_preset(
    session: AsyncSession, user_id: int, concert_id: int
) -> tuple[str, int]:
    """[Set my reminders] button. Returns (status, rules_created):
    'no_default' | 'already_covered' | 'applied'."""
    preset = await get_default_preset(session, user_id)
    if preset is None:
        return "no_default", 0
    existing = await session.execute(
        select(ReminderRule.id)
        .where(ReminderRule.user_id == user_id, ReminderRule.concert_id == concert_id)
        .limit(1)
    )
    if existing.scalar_one_or_none() is not None:
        return "already_covered", 0
    n = await apply_preset(session, user_id, concert_id, preset)
    return "applied", n


async def remove_user_rules(session: AsyncSession, user_id: int, concert_id: int) -> int:
    """[Remove these reminders] button. Deletes the user's rules on a concert
    (queue rows cascade). Returns how many rules were removed."""
    res = await session.execute(
        select(ReminderRule).where(
            ReminderRule.user_id == user_id, ReminderRule.concert_id == concert_id
        )
    )
    rules = list(res.scalars())
    for rule in rules:
        await session.delete(rule)
    await session.flush()
    return len(rules)


async def snooze_reminder(
    session: AsyncSession, queue_id: int, user_id: int, days: int = 1,
    now: datetime | None = None,
) -> str:
    """[Snooze 1 day] / [Remind me later] buttons. Re-arms a delivered
    reminder for +`days` days, capped so it can never fire after the
    deadline it's about. `days` defaults to 1 -- unchanged behavior for
    every reminder except the CLOSES one, where a modal-driven button
    supplies a user-chosen value instead.
    Returns: 'snoozed' | 'too_close' | 'not_yours' | 'gone'."""
    from datetime import timedelta

    now = now or _now()
    row = await session.get(ReminderQueue, queue_id)
    if row is None:
        return "gone"
    rule = await session.get(ReminderRule, row.rule_id)
    if rule is None or rule.user_id != user_id:
        return "not_yours"

    new_fire = now + timedelta(days=days)
    anchor_at: datetime | None = None
    if row.round_id is not None:
        round_ = await session.get(Round, row.round_id)
        if round_ is not None:
            anchor_at = anchor_time(_round_info(round_), row.anchor)
    elif row.day_id is not None:
        day = await session.get(ConcertDay, row.day_id)
        anchor_at = day.starts_at_utc if day else None

    if anchor_at is not None and anchor_at > now and new_fire >= anchor_at:
        return "too_close"  # snoozing would sleep through the deadline itself

    row.fire_at_utc = new_fire
    row.sent_at_utc = None  # re-arm
    await session.flush()
    return "snoozed"


@dataclass(frozen=True)
class NoticeContext:
    """Everything needed to render the new-event embed for one recipient."""

    concert_id: int
    event_id: str
    title: str
    tags_line: str
    venue: str | None
    first_deadline_label: str | None
    first_deadline_at: datetime | None
    user_timezone: str
    user_has_rules: bool
    user_has_default_preset: bool


async def notice_context(
    session: AsyncSession, concert_id: int, user_id: int
) -> NoticeContext | None:
    concert = await session.get(Concert, concert_id)
    if concert is None:
        return None
    await session.refresh(concert, ["tags", "rounds"])
    now = _now()
    upcoming = [
        (r, r.closes_at_utc or r.opens_at_utc)
        for r in concert.rounds
        if (r.closes_at_utc or r.opens_at_utc) and (r.closes_at_utc or r.opens_at_utc) > now
    ]
    upcoming.sort(key=lambda pair: pair[1])
    first = upcoming[0] if upcoming else None

    non_venue = [t.name for t in concert.tags if t.kind.value != "venue"]
    venues = [t.name for t in concert.tags if t.kind.value == "venue"]
    user = await session.get(User, user_id)
    has_rules = (await session.execute(
        select(ReminderRule.id)
        .where(ReminderRule.user_id == user_id, ReminderRule.concert_id == concert_id)
        .limit(1)
    )).scalar_one_or_none() is not None

    return NoticeContext(
        concert_id=concert_id,
        event_id=concert.event_id,
        title=concert.title,
        tags_line=" · ".join(non_venue),
        venue=("Multiple" if len(venues) > 1 else (venues[0] if venues else concert.venue)),
        first_deadline_label=first[0].label if first else None,
        first_deadline_at=first[1] if first else None,
        user_timezone=user.timezone if user else "America/Moncton",
        user_has_rules=has_rules,
        user_has_default_preset=await get_default_preset(session, user_id) is not None,
    )


@dataclass(frozen=True)
class LegCancelledContext:
    """Everything needed to render the leg-cancellation embed."""

    concert_id: int
    event_id: str
    title: str


async def leg_cancelled_context(
    session: AsyncSession, concert_id: int
) -> LegCancelledContext | None:
    concert = await session.get(Concert, concert_id)
    if concert is None:
        return None
    return LegCancelledContext(
        concert_id=concert.id, event_id=concert.event_id, title=concert.title
    )
