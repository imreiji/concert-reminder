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
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db.models import (
    Concert,
    ConcertAudit,
    ConcertDay,
    ConcertSubscription,
    ConcertTag,
    LegOptOut,
    Notification,
    OpsCheckState,
    PresetItem,
    ReminderPreset,
    ReminderQueue,
    ReminderRule,
    Round,
    RoundLabelPhrase,
    RoundOutcome,
    RoundQualifier,
    Tag,
    TagMember,
    TagSubscription,
    User,
)
from app.domain.board import OPEN_COLUMN_LIMIT, Column, column_for, pill_tone
from app.domain.reminders import DayInfo, RoundInfo, RuleInfo, anchor_time, plan_for_rule
from app.domain.timezones import fmt_day_month
from app.domain.types import Anchor, LotteryOutcome, RoundKind, SubscriptionState, TagKind
from app.domain.upgrades import is_upgrade_eligible
from app.i18n import N_, get_locale, gettext_in, loc_field
from app.i18n import gettext as _


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


async def delete_user(session: AsyncSession, discord_id: int) -> bool:
    """Erase a user (GDPR "right to be forgotten"). Returns False if unknown.

    Deliberately a single DELETE: the schema does the rest. Everything
    personal (sessions, rules, presets + their items, tag subscriptions,
    notifications, round outcomes) hangs off ondelete=CASCADE and vanishes;
    the shared catalogue this user authored (concerts, tags, audit rows)
    hangs off ondelete=SET NULL and survives with an anonymised author --
    one person leaving must not delete community content everyone else
    depends on. Requires PRAGMA foreign_keys=ON, which production sets.

    No route or UI calls this: erasure is a manual, owner-initiated
    operation for now.
    """
    user = await session.get(User, discord_id)
    if user is None:
        return False
    await session.delete(user)
    await session.flush()
    return True


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
    filtering sync_rule already does. Four passes now:

      * per-leg opt-out -- every leg a round covers has a LegOptOut row for
        this user.
      * cross-round "secured elsewhere" -- every leg a round covers is
        already secured (WON/PAID) by some OTHER round on this concert.
      * upgrade eligibility (this pass replaces the cross-round pass for
        UPGRADE rounds only) -- see below.
      * same-round anchor -- this rule's own anchor (RESULTS/PAYMENT) is
        moot given this round's own outcome.

    Why an UPGRADE round is EXEMPT from the cross-round pass (it looks wrong
    at a glance): an upgrade round is a nested second campaign only holders
    of a qualifying round's ticket may enter, and it covers the SAME legs as
    those base rounds. The cross-round pass would therefore drop it for
    exactly the users who secured a ticket -- its entire audience -- because
    their leg is "already secured elsewhere". But holding a ticket is the
    PREREQUISITE for entering the upgrade, not a reason to hide it. So for an
    UPGRADE round we skip the cross-round pass and instead drop it for a user
    who is NOT is_upgrade_eligible (no secured qualifying ticket). Its own
    WON/PAID outcome still feeds secured_by for OTHER rounds, and the
    same-round anchor pass still applies to it unchanged."""
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

    # Qualifier map for upgrade eligibility: upgrade_round_id -> [qualifying
    # round ids]. Read straight from the association table rather than the
    # lazy Round.qualifiers relationship, which would trip async lazy-load.
    # An upgrade round with no rows means "any secured ticket qualifies".
    qualifiers_by_round: dict[int, list[int]] = {}
    for up_id, q_id in (await session.execute(
        select(RoundQualifier.upgrade_round_id, RoundQualifier.qualifying_round_id).where(
            RoundQualifier.upgrade_round_id.in_(all_round_ids)
        )
    )).all() if all_round_ids else []:
        qualifiers_by_round.setdefault(up_id, []).append(q_id)

    # Rounds this user has SECURED (WON or PAID) -- the WON/PAID-vs-everything
    # distinction lives here, at the caller, so the pure helper stays simple.
    user_secured_round_ids = {
        rid for rid, outcome in outcomes.items()
        if outcome in (LotteryOutcome.WON, LotteryOutcome.PAID)
    }
    all_day_ids = set((await session.execute(
        select(ConcertDay.id).where(ConcertDay.concert_id == concert_id)
    )).scalars())

    # Per-user leg opt-out: this user's LegOptOut rows over this concert's
    # days. A round drops only when EVERY leg it covers is opted out -- the
    # per-user analogue of is_round_cancelled's every-leg (not any-leg)
    # cancellation rule. Kept symmetric on purpose: a two-leg round with
    # only one leg opted out survives, exactly as a two-leg round with only
    # one leg cancelled survives.
    opted_out_day_ids = set((await session.execute(
        select(LegOptOut.concert_day_id).where(
            LegOptOut.user_id == user_id,
            LegOptOut.concert_day_id.in_(all_day_ids),
        )
    )).scalars()) if all_day_ids else set()

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
        # Leg opt-out: suppress only when the round names specific legs AND
        # every one of them is opted out. An all-legs round (empty/None
        # applies_to) is tied to no specific leg, so no set of leg opt-outs
        # can cover it -- never suppressed, mirroring is_round_cancelled
        # leaving empty applies_to alone. Uses raw applies_to, not the
        # all_day_ids fallback the outcome passes use, precisely so the
        # empty case falls through untouched.
        if r.applies_to and all(d in opted_out_day_ids for d in r.applies_to):
            continue
        if r.kind is RoundKind.UPGRADE:
            # Exemption: skip the cross-round "secured elsewhere" pass (see
            # the docstring) and gate on eligibility instead. Exclude the
            # upgrade round's OWN outcome from the secured set -- you cannot
            # qualify for an upgrade by winning the upgrade itself; only
            # base-round tickets qualify (matters for the empty-qualifier
            # any-secured case).
            if not is_upgrade_eligible(
                qualifiers_by_round.get(r.id, []),
                user_secured_round_ids - {r.id},
            ):
                continue
        else:
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


async def _concert_opted_out(session: AsyncSession, user_id: int, concert_id: int) -> bool:
    """Whether this user holds a concert-level OPTED_OUT override -- read by
    sync_rule so a pruned concert plans (and thus keeps) no reminders."""
    state = (await session.execute(
        select(ConcertSubscription.state).where(
            ConcertSubscription.user_id == user_id,
            ConcertSubscription.concert_id == concert_id,
        )
    )).scalar_one_or_none()
    return state is SubscriptionState.OPTED_OUT


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
            # An upgrade round is never a fallback for a lost base round: the
            # user just lost, so they hold no qualifying ticket and could not
            # enter it. Eligibility gating would silence any rule we armed
            # anyway -- don't arm one in the first place.
            Round.kind != RoundKind.UPGRADE,
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
    if lost_round.kind is RoundKind.UPGRADE:
        # Losing an upgrade ends that nested side-campaign -- there is no
        # "next round" to fall back to; the user still holds their base
        # ticket. Auto-arming a later base round here would be wrong.
        return
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

    # A concert-level OPTED_OUT override prunes the whole concert for this
    # user: no rounds are live, so plan_for_rule yields nothing and the
    # "no longer planned -> delete" pass below clears any queued reminders.
    # This is the read-side half of set_concert_subscription's invariant-2
    # resync -- it is what actually makes a pruned concert stop reminding.
    # (Per-leg opt-out is a separate, finer pass -- Task 3.)
    scope_concert_id = rule.concert_id if rule.round_id is None else (
        round_.concert_id if round_ is not None else None
    )
    if scope_concert_id is not None and await _concert_opted_out(
        session, rule.user_id, scope_concert_id
    ):
        live_rounds = []

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
    # Recipient's DM language; the scheduler sets the locale from it before
    # composing. Defaulted so this leads the dataclass's default-valued block
    # (a required field cannot follow a defaulted one) -- every caller passes
    # it by keyword anyway.
    user_language: str = "en"
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
                user_language=user.language,
                concert_title=loc_field(concert, "title", user.language),
                anchor=row.anchor,
                fire_at_utc=row.fire_at_utc,
                round_id=round_.id if round_ else None,
                # Per-RECIPIENT locale, not get_locale(): one due_reminders
                # pass builds rows for many users outside any request, so the
                # ContextVar would hand everyone the same language.
                round_label=loc_field(round_, "label", user.language) if round_ else None,
                round_kind=round_.kind.value if round_ else None,
                outcome=outcomes.get((user.discord_id, round_.id)) if round_ else None,
                anchor_time_utc=(
                    anchor_time(_round_info(round_), row.anchor)
                    if round_
                    else (day.starts_at_utc if day else None)
                ),
                url=round_.url if round_ else None,
                day_label=loc_field(day, "label", user.language) if day else None,
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
    Anchor.OPENS: N_("opens"),
    Anchor.CLOSES: N_("closes"),
    Anchor.RESULTS: N_("results announced"),
    Anchor.PAYMENT: N_("payment due"),
    Anchor.EVENT_START: N_("event"),
}

LABEL_BY_ROUND_KIND: dict[RoundKind, str] = {
    RoundKind.LOTTERY_ROUND: N_("Lottery round"),
    RoundKind.ELIGIBILITY_ITEM_SALE: N_("Eligibility item sale"),
    RoundKind.STREAM_TICKET_SALE: N_("Stream ticket sale"),
    RoundKind.GENERAL_SALE: N_("General sale"),
    RoundKind.RESULT_ANNOUNCEMENT: N_("Result announcement"),
    RoundKind.PAYMENT_DEADLINE: N_("Payment deadline"),
    RoundKind.FCFS_SALE: N_("First come, first served"),
    RoundKind.TOUR_PACKAGE: N_("Overseas tour package"),
    RoundKind.UPGRADE: N_("Upgrade round"),
    RoundKind.OTHER: N_("Other"),
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
    # Which round this row came from, so a Home "Coming up" row knows where to
    # POST an outcome. Optional because EVENT_START rows are derived from a
    # ConcertDay and have no round at all -- there is nothing to record against.
    round_id: int | None = None


async def upcoming_deadlines(
    session: AsyncSession, now: datetime | None = None, limit: int = 10,
    concert_ids: set[int] | None = None,
) -> list[UpcomingDeadline]:
    """Global (not reminder-rule-scoped, not per-user) chronological
    deadline list for the index page. Reuses is_round_cancelled the same
    way sync_rule/notify_newly_cancelled_legs already do.

    `concert_ids` narrows the source rows to those concerts (None = every
    concert). The narrowing happens BEFORE the sort and the limit, which is
    the whole reason it lives here rather than in the caller: filtering an
    already-truncated global list would silently return fewer than `limit`
    rows. my_upcoming_deadlines is the per-user caller."""
    now = now or _now()
    day_q = select(ConcertDay)
    round_q = select(Round)
    if concert_ids is not None:
        if not concert_ids:
            return []
        day_q = day_q.where(ConcertDay.concert_id.in_(concert_ids))
        round_q = round_q.where(Round.concert_id.in_(concert_ids))
    days = list((await session.execute(day_q)).scalars())
    rounds = list((await session.execute(round_q)).scalars())
    cancelled_day_ids = {d.id for d in days if d.cancelled}
    concert_ids = {d.concert_id for d in days} | {r.concert_id for r in rounds}
    concerts = {
        c.id: c for c in
        (await session.execute(select(Concert).where(Concert.id.in_(concert_ids)))).scalars()
    } if concert_ids else {}

    # The label is COPIED into the dataclass here, so a loc() in the template
    # can never reach it -- resolve the viewer's variant at the copy site.
    # Web-request path: the request ContextVar is the right locale source.
    locale = get_locale()
    out: list[UpcomingDeadline] = []
    for d in days:
        if d.cancelled or d.starts_at_utc <= now:
            continue
        concert = concerts.get(d.concert_id)
        if concert is None:
            continue
        out.append(UpcomingDeadline(
            concert_title=loc_field(concert, "title", locale),
            event_id=concert.event_id, label=loc_field(d, "label", locale),
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
                concert_title=loc_field(concert, "title", locale),
                event_id=concert.event_id, label=loc_field(r, "label", locale),
                anchor=anchor, at_utc=ts, url=r.url, round_id=r.id,
            ))

    out.sort(key=lambda e: e.at_utc)
    return out[:limit]


# ── Personal board ("where do I stand") ──────────────────────────────────


async def tracked_concert_ids(session: AsyncSession, user_id: int) -> set[int]:
    """Concerts this user is deemed to be following.

        tracked = (tag-matched ids  -  opted-out ids)  |  subscribed ids

    A ConcertSubscription row is an *override* on the tag-derived default
    (see the model): no row means "follow the tag default", an OPTED_OUT
    row prunes a tag-matched concert, a SUBSCRIBED row forces one on even
    with no matching tag. This is the SINGLE definition of "tracked" --
    callers must not re-derive it anywhere else.
    """
    tag_matched = set((await session.execute(
        select(ConcertTag.concert_id)
        .join(TagSubscription, TagSubscription.tag_id == ConcertTag.tag_id)
        .where(TagSubscription.user_id == user_id)
        .distinct()
    )).scalars())
    overrides = await concert_subscription_states(session, user_id)
    opted_out = {cid for cid, st in overrides.items() if st is SubscriptionState.OPTED_OUT}
    subscribed = {cid for cid, st in overrides.items() if st is SubscriptionState.SUBSCRIBED}
    return (tag_matched - opted_out) | subscribed


async def concert_subscription_states(
    session: AsyncSession, user_id: int
) -> dict[int, SubscriptionState]:
    """Every explicit per-concert override this user holds, keyed by concert
    id. The read surface Preferences and setup need to show pruned concerts,
    which `tracked_concert_ids` excludes by definition."""
    res = await session.execute(
        select(ConcertSubscription.concert_id, ConcertSubscription.state)
        .where(ConcertSubscription.user_id == user_id)
    )
    return {concert_id: state for concert_id, state in res}


async def followed_tag_counts(
    session: AsyncSession, user_id: int, now: datetime | None = None
) -> dict[int, tuple[int, int]]:
    """Per-followed-tag concert tallies for Preferences' Following rows:
    {tag_id: (total_concerts, upcoming_concerts)}. "Upcoming" is a concert
    with a live (non-cancelled) day still in the future -- the number that
    makes a Notify/Auto-apply toggle meaningful, since a tag with nothing
    upcoming will not fire either way. Scoped to the tags this user actually
    follows, so the map has exactly one entry per Following row."""
    now = now or _now()
    followed = set((await session.execute(
        select(TagSubscription.tag_id).where(TagSubscription.user_id == user_id)
    )).scalars())
    if not followed:
        return {}
    totals = dict((await session.execute(
        select(ConcertTag.tag_id, func.count(func.distinct(ConcertTag.concert_id)))
        .where(ConcertTag.tag_id.in_(followed))
        .group_by(ConcertTag.tag_id)
    )).all())
    upcoming = dict((await session.execute(
        select(ConcertTag.tag_id, func.count(func.distinct(ConcertTag.concert_id)))
        .join(ConcertDay, ConcertDay.concert_id == ConcertTag.concert_id)
        .where(
            ConcertTag.tag_id.in_(followed),
            ConcertDay.cancelled.is_(False),
            ConcertDay.starts_at_utc > now,
        )
        .group_by(ConcertTag.tag_id)
    )).all())
    return {tid: (totals.get(tid, 0), upcoming.get(tid, 0)) for tid in followed}


async def upcoming_concert_count(
    session: AsyncSession, concert_ids: set[int], now: datetime | None = None
) -> int:
    """How many of `concert_ids` have a live day still in the future -- the
    "N upcoming" half of Preferences' Following summary, applied to the
    tracked set. Empty in, zero out (no query)."""
    if not concert_ids:
        return 0
    now = now or _now()
    return (await session.execute(
        select(func.count(func.distinct(ConcertDay.concert_id)))
        .where(
            ConcertDay.concert_id.in_(concert_ids),
            ConcertDay.cancelled.is_(False),
            ConcertDay.starts_at_utc > now,
        )
    )).scalar_one()


async def set_concert_subscription(
    session: AsyncSession, user_id: int, concert_id: int, state: SubscriptionState
) -> None:
    """Upsert this user's per-concert override to `state`."""
    existing = (await session.execute(
        select(ConcertSubscription).where(
            ConcertSubscription.user_id == user_id,
            ConcertSubscription.concert_id == concert_id,
        )
    )).scalar_one_or_none()
    if existing is None:
        session.add(
            ConcertSubscription(user_id=user_id, concert_id=concert_id, state=state)
        )
    else:
        existing.state = state
    await session.flush()
    # Invariant 2: the override changes which reminders should exist for this
    # concert, so re-sync this user's rules exactly as record_round_outcome
    # does after an outcome write. An OPTED_OUT concert now plans nothing
    # (sync_rule drops its rounds), so unsent queue rows are deleted; going
    # back to SUBSCRIBED/default re-arms them. Skip the resync and a pruned
    # concert keeps sending reminders -- the bug this branch fixes.
    await reinstate_user_rules(session, user_id, concert_id)


async def clear_concert_subscription(
    session: AsyncSession, user_id: int, concert_id: int
) -> None:
    """Delete this user's override for the concert -- back to the tag
    default. Branch 6's un-prune calls this."""
    existing = (await session.execute(
        select(ConcertSubscription).where(
            ConcertSubscription.user_id == user_id,
            ConcertSubscription.concert_id == concert_id,
        )
    )).scalar_one_or_none()
    if existing is not None:
        await session.delete(existing)
    await session.flush()
    # Same invariant-2 resync as set_concert_subscription: un-pruning must
    # let this concert's reminders resume.
    await reinstate_user_rules(session, user_id, concert_id)


async def set_leg_opt_out(
    session: AsyncSession, user_id: int, day_id: int, opted_out: bool
) -> None:
    """Toggle a per-leg opt-out by row presence: add a row when opting out,
    delete it when opting back in. The suppression this drives is a
    read-side planner pass added in Task 3 -- there is no concert-level
    resync here."""
    existing = (await session.execute(
        select(LegOptOut).where(
            LegOptOut.user_id == user_id,
            LegOptOut.concert_day_id == day_id,
        )
    )).scalar_one_or_none()
    if opted_out and existing is None:
        session.add(LegOptOut(user_id=user_id, concert_day_id=day_id))
    elif not opted_out and existing is not None:
        await session.delete(existing)
    await session.flush()


@dataclass(frozen=True)
class Rung:
    """One step of a concert's round ladder as this user experienced it.

    `state` is presentation-ready: "lost" | "won" | "paid" | "applied" render
    the recorded outcome, and rounds with no outcome fall back to where they
    sit in time -- "live" (open right now) or "todo" (not open yet, or open
    with nothing recorded and already closed). `detail` is the one moment
    worth showing next to the rung: the payment deadline once you have won,
    otherwise the close (falling back to the open). Templates render it with
    fmt_dual; the dataclass stays timezone-agnostic.
    """

    round_id: int
    label: str
    state: str
    detail: datetime | None = None


@dataclass(frozen=True)
class BoardCard:
    """One concert on the board, in exactly one column."""

    concert: Concert
    column: Column
    rungs: list[Rung]
    next_deadline: datetime | None
    outcome_by_round: dict[int, LotteryOutcome]
    # The countdown pill's CSS tone -- computed here (read side) from the
    # pure domain.board.pill_tone, not in the template, so the urgency rule
    # lives in exactly one place.
    pill_tone: str


def _round_is_open(round_: Round, now: datetime) -> bool:
    """Open = you could act on it right now. A round with only a close set is
    open until it closes (plenty of catalogue rows have no explicit open); a
    round with neither timestamp is never open, since there is no window."""
    if round_.opens_at_utc is None and round_.closes_at_utc is None:
        return False
    if round_.opens_at_utc is not None and round_.opens_at_utc > now:
        return False
    return round_.closes_at_utc is None or round_.closes_at_utc > now


def _rung_state(outcome: LotteryOutcome | None, is_open: bool) -> str:
    if outcome in (
        LotteryOutcome.LOST, LotteryOutcome.WON, LotteryOutcome.PAID, LotteryOutcome.APPLIED
    ):
        return outcome.value
    return "live" if is_open else "todo"


def _next_deadline(rounds: list[Round], now: datetime) -> datetime | None:
    """The soonest future moment across a concert's rounds, over all four
    anchors -- what "closes next" and the open-column ordering both key on."""
    future = [
        ts
        for r in rounds
        for ts in (r.opens_at_utc, r.closes_at_utc, r.results_at_utc, r.payment_deadline_at_utc)
        if ts is not None and ts > now
    ]
    return min(future) if future else None


async def board_cards(
    session: AsyncSession, user_id: int, now: datetime | None = None,
    concert_ids: set[int] | None = None,
) -> tuple[dict[Column, list[BoardCard]], int]:
    """This user's campaigns, bucketed into the four board columns.

    Returns (columns, open_total). `open_total` is the PRE-cap size of the
    open column, so a template can render "+N more" -- columns[Column.OPEN]
    itself is truncated to OPEN_COLUMN_LIMIT, soonest deadline first.

    `concert_ids` lets Home resolve `tracked_concert_ids` once and share it
    with `my_deadline_rows`; None resolves it here.
    """
    now = now or _now()
    locale = get_locale()
    columns: dict[Column, list[BoardCard]] = {c: [] for c in Column}

    ids = concert_ids if concert_ids is not None else await tracked_concert_ids(
        session, user_id
    )
    if not ids:
        return columns, 0

    concerts = list((await session.execute(
        select(Concert)
        .where(Concert.id.in_(ids))
        .options(
            selectinload(Concert.days),
            selectinload(Concert.rounds),
            # The card renders an artist/group eyebrow, so tags must be loaded
            # HERE -- a lazy load inside async template rendering raises
            # MissingGreenlet, not a warning.
            selectinload(Concert.tags),
        )
    )).scalars())

    # One outcome query for every round on the board, not one per concert --
    # the per-concert shape is the obvious one and turns the board into N+1.
    all_round_ids = [r.id for c in concerts for r in c.rounds]
    outcomes: dict[int, LotteryOutcome] = {
        o.round_id: o.outcome
        for o in (await session.execute(
            select(RoundOutcome).where(
                RoundOutcome.user_id == user_id, RoundOutcome.round_id.in_(all_round_ids)
            )
        )).scalars()
    } if all_round_ids else {}

    for concert in concerts:
        cancelled_day_ids = {d.id for d in concert.days if d.cancelled}
        live_rounds = [
            r for r in concert.rounds if not is_round_cancelled(r, cancelled_day_ids)
        ]
        # Ladder order: when a round opens, falling back to when it closes.
        # Rounds with neither timestamp sort last, in id order, rather than
        # blowing up the comparison.
        live_rounds.sort(
            key=lambda r: (
                r.opens_at_utc is None and r.closes_at_utc is None,
                r.opens_at_utc or r.closes_at_utc or now,
                r.id,
            )
        )

        card_outcomes = {r.id: outcomes[r.id] for r in live_rounds if r.id in outcomes}
        column = column_for(
            [
                (outcomes[r.id], r.kind is RoundKind.UPGRADE)
                for r in live_rounds
                if r.id in outcomes
            ],
            has_open_round=any(_round_is_open(r, now) for r in live_rounds),
        )
        if column is None:
            continue

        rungs = [
            Rung(
                round_id=r.id,
                # Copied out of the ORM object, so resolve here (web request
                # -> get_locale()); the template only sees the string.
                label=loc_field(r, "label", locale),
                state=_rung_state(card_outcomes.get(r.id), _round_is_open(r, now)),
                detail=(
                    r.payment_deadline_at_utc
                    if card_outcomes.get(r.id) is LotteryOutcome.WON
                    and r.payment_deadline_at_utc is not None
                    else r.closes_at_utc or r.opens_at_utc
                ),
            )
            for r in live_rounds
        ]
        next_deadline = _next_deadline(live_rounds, now)
        columns[column].append(BoardCard(
            concert=concert,
            column=column,
            rungs=rungs,
            next_deadline=next_deadline,
            outcome_by_round=card_outcomes,
            pill_tone=pill_tone(column, next_deadline, now),
        ))

    # Soonest first everywhere; a card with no future deadline sorts last.
    for cards in columns.values():
        cards.sort(key=lambda c: (c.next_deadline is None, c.next_deadline or now, c.concert.id))

    open_total = len(columns[Column.OPEN])
    columns[Column.OPEN] = columns[Column.OPEN][:OPEN_COLUMN_LIMIT]
    return columns, open_total


async def my_upcoming_deadlines(
    session: AsyncSession, user_id: int, now: datetime | None = None, limit: int = 10,
    concert_ids: set[int] | None = None,
) -> list[UpcomingDeadline]:
    """The index page's deadline list, narrowed to concerts this user tracks.

    Delegates to upcoming_deadlines with an id filter rather than filtering
    its result: that keeps the cancelled-leg rule (is_round_cancelled) in one
    place, and keeps the limit meaningful (see upcoming_deadlines' docstring).

    `concert_ids` lets a caller that already resolved `tracked_concert_ids`
    (Home renders the board from the same set) pass it in; None resolves it.
    """
    ids = concert_ids if concert_ids is not None else await tracked_concert_ids(
        session, user_id
    )
    return await upcoming_deadlines(session, now=now, limit=limit, concert_ids=ids)


# How many "Coming up" rows Home shows. It lives here, next to the function
# that uses it as a default, because TWO callers render the same fragment:
# GET / builds it first, and POST /rounds/{id}/outcome swaps it back in after
# a capture action. If those two disagreed on the count, recording an outcome
# would silently lengthen or shorten the list. One constant, one default, no
# literals at either call site.
DEADLINE_ROWS_LIMIT = 10


@dataclass(frozen=True)
class DeadlineRow:
    """One "Coming up" row: the deadline itself, this user's standing on it,
    and the little bit of concert context the row shows underneath the title.

    `outcome` is what decides which capture buttons the row offers, so it is
    resolved here rather than in the template -- and it is None both for a
    round nobody has acted on and for a row with no round at all (an
    EVENT_START row derived from a ConcertDay). The template distinguishes
    those two by `deadline.round_id`, which is the only thing that can be
    posted to.

    `can_capture` and `can_report_result` are the two gates on WHICH buttons
    the row may offer, resolved here for the same reason: they are round
    timing rules, not presentation. They matter because `upcoming_deadlines`
    emits one row per future ANCHOR, so a single round can produce three or
    four rows and each one would otherwise carry its own independent capture
    buttons -- including on a round that has not opened yet, where recording
    APPLIED is both false and irreversible (`record_round_outcome` refuses to
    overwrite a starting state).
    """

    deadline: UpcomingDeadline
    outcome: LotteryOutcome | None
    venue: str | None = None
    starts_at_utc: datetime | None = None
    # An UPGRADE round relabels the capture buttons ("Entered upgrade" /
    # "Skipping"). A row only reaches Home at all when the viewer is eligible
    # for it, so this never rides on a row whose buttons they cannot press.
    is_upgrade: bool = False
    # Is this row's round something you could have acted on at all yet?
    can_capture: bool = False
    # Has this round's result become knowable, so "I won"/"I lost" are real
    # answers rather than guesses?
    can_report_result: bool = False


def _round_has_opened(round_: Round, now: datetime) -> bool:
    """You cannot have applied to a round that has not opened. A round with no
    open time at all counts as opened -- plenty of catalogue rows only carry a
    close, and those are actionable now."""
    return round_.opens_at_utc is None or round_.opens_at_utc <= now


def _result_moment(round_: Round) -> datetime | None:
    """When this round's outcome becomes knowable: the announced results time
    if there is one, otherwise the close (results follow applications
    closing). None means the round carries neither, so there is nothing left
    to wait for."""
    return round_.results_at_utc or round_.closes_at_utc


async def _qualifiers_by_upgrade_round(
    session: AsyncSession, round_ids: list[int]
) -> dict[int, list[int]]:
    """upgrade_round_id -> [qualifying round ids], from the round_qualifiers
    association table in ONE query for every id given -- never one query per
    round. An upgrade round absent from the returned map has no qualifier rows,
    which the empty-qualifier convention reads as "any secured ticket on this
    concert qualifies" (see domain/upgrades.py).

    Read from the association table directly rather than the lazy
    Round.qualifiers relationship, which would trip MissingGreenlet under async
    template rendering."""
    out: dict[int, list[int]] = {}
    if not round_ids:
        return out
    for up_id, q_id in (await session.execute(
        select(RoundQualifier.upgrade_round_id, RoundQualifier.qualifying_round_id).where(
            RoundQualifier.upgrade_round_id.in_(round_ids)
        )
    )).all():
        out.setdefault(up_id, []).append(q_id)
    return out


def _eligible_upgrade_ids(
    rounds: list[Round],
    outcomes: dict[int, LotteryOutcome],
    qualifiers_by_round: dict[int, list[int]],
) -> set[int]:
    """Which of `rounds`'s UPGRADE rounds this user is eligible for, given the
    user's `outcomes` for THIS concert and the pre-loaded qualifier map.

    Concert-scoped on purpose: `outcomes` must hold only this concert's rounds,
    so the empty-qualifier "any secured ticket" case cannot be satisfied by a
    ticket on a different concert. Mirrors the suppression pass exactly --
    WON/PAID counts as secured, and a round's own outcome is excluded from its
    secured set (you cannot qualify for an upgrade by winning the upgrade)."""
    secured = {
        rid for rid, o in outcomes.items()
        if o in (LotteryOutcome.WON, LotteryOutcome.PAID)
    }
    return {
        r.id for r in rounds
        if r.kind is RoundKind.UPGRADE
        and is_upgrade_eligible(qualifiers_by_round.get(r.id, []), secured - {r.id})
    }


def capture_gates(
    round_: Round | None, outcome: LotteryOutcome | None, now: datetime,
    qualifies: bool = True,
) -> tuple[bool, bool]:
    """The two gates on WHICH capture buttons a row may offer, as ONE
    definition shared by every surface that offers them (Home's "Coming up"
    rows and the concert page's per-leg round rows).

    They are shared rather than re-derived because the template rules keyed
    off them are shared too (`_capture_actions.html`): a second copy here
    would let one surface start offering "I have applied" on a round the
    other still calls unopened, and nothing would fail.

    `round_` is None for a row with no round behind it at all (an EVENT_START
    row derived from a ConcertDay), where neither gate can ever open.

    `qualifies` is False only for an UPGRADE round the viewer is not eligible
    for: they hold no qualifying ticket, so offering "I have applied" would let
    them record an entry to a campaign they cannot join. Callers resolve it
    from the outcomes they already hold (see `_eligible_upgrade_ids`); it
    defaults True so every ordinary round is unaffected."""
    can_capture = round_ is not None and _round_has_opened(round_, now) and qualifies
    moment = _result_moment(round_) if round_ is not None else None
    can_report_result = (
        can_capture
        and outcome is LotteryOutcome.APPLIED
        and (moment is None or moment <= now)
    )
    return can_capture, can_report_result


async def my_deadline_rows(
    session: AsyncSession,
    user_id: int,
    now: datetime | None = None,
    limit: int = DEADLINE_ROWS_LIMIT,
    concert_ids: set[int] | None = None,
) -> list[DeadlineRow]:
    """`my_upcoming_deadlines`, decorated with everything a Home row renders.

    Three extra reads, all batched over the already-truncated row set rather
    than per row: this user's RoundOutcome for the rounds on show, the rounds
    themselves (for the two capture gates), and the concerts those rows belong
    to (for the venue and first live date shown under the title).

    `concert_ids` is the caller's already-computed `tracked_concert_ids`, so a
    page rendering both the board and these rows resolves it once instead of
    twice; None means resolve it here.
    """
    now = now or _now()
    deadlines = await my_upcoming_deadlines(
        session, user_id, now=now, limit=limit, concert_ids=concert_ids
    )
    if not deadlines:
        return []

    round_ids = {d.round_id for d in deadlines if d.round_id is not None}
    outcomes: dict[int, LotteryOutcome] = {
        o.round_id: o.outcome
        for o in (await session.execute(
            select(RoundOutcome).where(
                RoundOutcome.user_id == user_id, RoundOutcome.round_id.in_(round_ids)
            )
        )).scalars()
    } if round_ids else {}
    rounds: dict[int, Round] = {
        r.id: r
        for r in (await session.execute(
            select(Round).where(Round.id.in_(round_ids))
        )).scalars()
    } if round_ids else {}

    event_ids = {d.event_id for d in deadlines}
    concerts = {
        c.event_id: c
        for c in (await session.execute(
            select(Concert)
            .where(Concert.event_id.in_(event_ids))
            .options(selectinload(Concert.days), selectinload(Concert.tags))
        )).scalars()
    }

    # Eligibility for any UPGRADE rounds among these deadline rows. A row for an
    # upgrade the viewer cannot enter is noise -- its capture buttons would be
    # false testimony -- so it is dropped below. Resolved in two BATCHED queries
    # over the whole row set, never one per row: the qualifier sets, and the
    # viewer's secured (WON/PAID) rounds across the concerts those upgrades
    # belong to. Secured is scoped per concert so an empty-qualifier upgrade
    # cannot be satisfied by a secured ticket on a different concert.
    upgrade_ids = [rid for rid, r in rounds.items() if r.kind is RoundKind.UPGRADE]
    eligible_upgrade_ids: set[int] = set()
    if upgrade_ids:
        qualifiers_by_round = await _qualifiers_by_upgrade_round(session, upgrade_ids)
        up_concert_ids = {rounds[rid].concert_id for rid in upgrade_ids}
        secured_by_concert: dict[int, set[int]] = {}
        for rid, cid in (await session.execute(
            select(RoundOutcome.round_id, Round.concert_id)
            .join(Round, Round.id == RoundOutcome.round_id)
            .where(
                RoundOutcome.user_id == user_id,
                Round.concert_id.in_(up_concert_ids),
                RoundOutcome.outcome.in_([LotteryOutcome.WON, LotteryOutcome.PAID]),
            )
        )).all():
            secured_by_concert.setdefault(cid, set()).add(rid)
        for rid in upgrade_ids:
            secured = secured_by_concert.get(rounds[rid].concert_id, set())
            if is_upgrade_eligible(qualifiers_by_round.get(rid, []), secured - {rid}):
                eligible_upgrade_ids.add(rid)

    rows = []
    for d in deadlines:
        concert = concerts.get(d.event_id)
        live_days = sorted(
            (day for day in concert.days if not day.cancelled), key=lambda day: day.starts_at_utc
        ) if concert else []
        venue_tags = [
            loc_field(t, "name", get_locale())
            for t in concert.tags if t.kind is TagKind.VENUE
        ] if concert else []
        round_ = rounds.get(d.round_id) if d.round_id is not None else None
        outcome = outcomes.get(d.round_id) if d.round_id is not None else None
        is_upgrade = round_ is not None and round_.kind is RoundKind.UPGRADE
        if is_upgrade and round_.id not in eligible_upgrade_ids:
            continue  # drop rows for an upgrade this viewer cannot enter
        can_capture, can_report_result = capture_gates(
            round_, outcome, now,
            qualifies=(not is_upgrade) or round_.id in eligible_upgrade_ids,
        )
        rows.append(DeadlineRow(
            deadline=d,
            outcome=outcome,
            is_upgrade=is_upgrade,
            can_capture=can_capture,
            can_report_result=can_report_result,
            # Same display rule as the tile macro: >1 venue tag collapses to
            # "Multiple", one wins outright, and the free-text venue is only a
            # fallback when there is no VENUE tag at all.
            venue=(
                _("Multiple") if len(venue_tags) > 1
                else (venue_tags[0] if venue_tags
                      else (loc_field(concert, "venue", get_locale()) if concert else None))
            ),
            starts_at_utc=live_days[0].starts_at_utc if live_days else None,
        ))
    return rows


# ── First-run capture flow (/setup) ──────────────────────────────────────

# Ordering priority for a tile's "because" attribution: the group/franchise a
# follower recognises first leads, then artists, then venues.
_BECAUSE_KIND_ORDER = {
    TagKind.GROUP: 0, TagKind.FRANCHISE: 1, TagKind.ARTIST: 2, TagKind.VENUE: 3,
}


@dataclass(frozen=True)
class SetupTile:
    """One pruning tile on screen 1: a tracked upcoming concert, why it is
    here, whether it is currently kept (False iff a concert-level opt-out
    override exists), and the little bit of context the tile shows -- venue,
    first live date, and the nearest future round moment. Timestamps render
    dual via fmt_dual; the dataclass stays timezone-agnostic."""

    concert: Concert
    because: list[str]
    kept: bool
    venue: str | None
    starts_at_utc: datetime | None
    next_round_label: str | None
    next_round_anchor: Anchor | None
    next_round_at_utc: datetime | None


@dataclass(frozen=True)
class SetupAskRow:
    """One application question on screen 2: a (concert, round) the user could
    still be in. `status` is "open" (acting now) or "awaiting" (closed, result
    pending); `moment_utc` is the close when open, else the result moment."""

    concert: Concert
    round_: Round
    status: str
    moment_utc: datetime | None


@dataclass(frozen=True)
class SetupTallies:
    """Screen 3's reveal, computed fresh over the surviving tracked upcoming
    set. `payment_concert` is the concert with the soonest pending payment,
    for the narrative line shown only when payment_due > 0."""

    tracking: int
    applied: int
    payment_due: int
    next_deadline_utc: datetime | None
    payment_concert: Concert | None


def _setup_tile_venue(concert: Concert) -> str | None:
    """Same >1-venue rule my_deadline_rows uses: many VENUE tags collapse to
    "Multiple", one wins, the free-text venue is the fallback with no tag."""
    venue_tags = [t for t in concert.tags if t.kind is TagKind.VENUE]
    if len(venue_tags) > 1:
        return _("Multiple")
    if venue_tags:
        return loc_field(venue_tags[0], "name", get_locale())
    return loc_field(concert, "venue", get_locale())


def _next_round_anchor(
    live_rounds: list[Round], now: datetime
) -> tuple[str, Anchor, datetime] | None:
    """The soonest FUTURE round anchor across a concert's live rounds, as
    (round label, anchor, moment). None when no live round has a future
    anchor. Mirrors _next_deadline's four-anchor sweep, but keeps the label
    and anchor so the tile can say WHICH moment ("FC presale closes ...").

    The label is copied out of the Round here, so the viewer's variant is
    resolved now -- setup is a web-request path, hence get_locale().
    """
    locale = get_locale()
    best: tuple[str, Anchor, datetime] | None = None
    for r in live_rounds:
        for anchor, ts in (
            (Anchor.OPENS, r.opens_at_utc),
            (Anchor.CLOSES, r.closes_at_utc),
            (Anchor.RESULTS, r.results_at_utc),
            (Anchor.PAYMENT, r.payment_deadline_at_utc),
        ):
            if ts is None or ts <= now:
                continue
            if best is None or ts < best[2]:
                best = (loc_field(r, "label", locale), anchor, ts)
    return best


async def _tracked_upcoming_concerts(
    session: AsyncSession, user_id: int, now: datetime
) -> tuple[list[Concert], set[int]]:
    """The setup flow's working set: every concert this user tracks (branch
    4's override-folded `tracked_concert_ids`) UNIONED with every concert
    they have pruned, loaded (days/rounds/tags eager) and filtered to
    *upcoming* -- a live (non-cancelled) day in the future, or any live round
    anchor in the future.

    Pruned concerts are unioned back in precisely because `tracked_concert_ids`
    excludes them by definition, yet screen 1 must render them unchecked so
    they can be brought back. Returns (concerts, opted_out_ids) so the tiles,
    the applications pass and the tallies all share one load and one
    upcoming-filter instead of re-deriving it three ways."""
    tracked = await tracked_concert_ids(session, user_id)
    overrides = await concert_subscription_states(session, user_id)
    opted_out = {cid for cid, st in overrides.items() if st is SubscriptionState.OPTED_OUT}
    candidate_ids = tracked | opted_out
    if not candidate_ids:
        return [], opted_out

    concerts = list((await session.execute(
        select(Concert)
        .where(Concert.id.in_(candidate_ids))
        .options(
            selectinload(Concert.days),
            selectinload(Concert.rounds),
            selectinload(Concert.tags),
        )
    )).scalars())

    upcoming = []
    for c in concerts:
        cancelled_day_ids = {d.id for d in c.days if d.cancelled}
        live_rounds = [r for r in c.rounds if not is_round_cancelled(r, cancelled_day_ids)]
        future_day = any(not d.cancelled and d.starts_at_utc > now for d in c.days)
        if future_day or _next_deadline(live_rounds, now) is not None:
            upcoming.append(c)
    return upcoming, opted_out


async def setup_prune_tiles(
    session: AsyncSession, user_id: int, now: datetime | None = None
) -> list[SetupTile]:
    """Screen 1's tiles: every tracked upcoming concert, INCLUDING currently
    pruned ones (which render unchecked). Ordered soonest-next-moment first,
    same rationale as the board."""
    now = now or _now()
    concerts, opted_out = await _tracked_upcoming_concerts(session, user_id, now)
    if not concerts:
        return []

    sub_tag_ids = set((await session.execute(
        select(TagSubscription.tag_id).where(TagSubscription.user_id == user_id)
    )).scalars())

    tiles = []
    for c in concerts:
        cancelled_day_ids = {d.id for d in c.days if d.cancelled}
        live_rounds = [r for r in c.rounds if not is_round_cancelled(r, cancelled_day_ids)]
        live_days = sorted(
            (d for d in c.days if not d.cancelled), key=lambda d: d.starts_at_utc
        )
        because = [
            loc_field(t, "name", get_locale()) for t in sorted(
                c.tags, key=lambda t: (_BECAUSE_KIND_ORDER.get(t.kind, 9), t.name)
            )
            if t.id in sub_tag_ids
        ]
        nxt = _next_round_anchor(live_rounds, now)
        tiles.append(SetupTile(
            concert=c,
            because=because,
            kept=c.id not in opted_out,
            venue=_setup_tile_venue(c),
            starts_at_utc=live_days[0].starts_at_utc if live_days else None,
            next_round_label=nxt[0] if nxt else None,
            next_round_anchor=nxt[1] if nxt else None,
            next_round_at_utc=nxt[2] if nxt else None,
        ))

    def sort_key(t: SetupTile) -> tuple[bool, datetime, int]:
        # Soonest future moment across the round anchor and a future first
        # date; a tile with neither (should not happen given the upcoming
        # filter, but stay total) sorts last.
        moments = [
            m for m in (
                t.next_round_at_utc,
                t.starts_at_utc if t.starts_at_utc and t.starts_at_utc > now else None,
            ) if m is not None
        ]
        nm = min(moments) if moments else None
        return (nm is None, nm or now, t.concert.id)

    tiles.sort(key=sort_key)
    return tiles


def _round_asks_application(
    round_: Round, outcome: LotteryOutcome | None, now: datetime
) -> bool:
    """Screen 2's eligibility predicate: does the setup flow ask whether the
    user already applied to this round? True iff no outcome is recorded, the
    round has opened, it carries at least one apply-window / result timestamp,
    and its result moment is unset or still in the future -- the "middle path"
    rule that a round already decided is never asked about.

    Branch-5 hook: an open UPGRADE round will widen this to also ask about
    its qualifying CLOSED round ("Do you hold this ticket?"), the one
    exception to the middle-path rule. The widening lands here; nothing else
    in this branch anticipates it."""
    if outcome is not None:
        return False
    if not _round_has_opened(round_, now):
        return False
    if not any((round_.opens_at_utc, round_.closes_at_utc, round_.results_at_utc)):
        return False
    moment = _result_moment(round_)
    return moment is None or moment > now


async def setup_application_rows(
    session: AsyncSession, user_id: int, now: datetime | None = None
) -> list[SetupAskRow]:
    """Screen 2's rows: over SURVIVING (non-pruned) tracked upcoming concerts,
    every live round passing `_round_asks_application`. Ordered by
    `moment_utc`, None last."""
    now = now or _now()
    concerts, opted_out = await _tracked_upcoming_concerts(session, user_id, now)
    surviving = [c for c in concerts if c.id not in opted_out]
    if not surviving:
        return []

    all_round_ids = [r.id for c in surviving for r in c.rounds]
    outcomes: dict[int, LotteryOutcome] = {
        o.round_id: o.outcome for o in (await session.execute(
            select(RoundOutcome).where(
                RoundOutcome.user_id == user_id, RoundOutcome.round_id.in_(all_round_ids)
            )
        )).scalars()
    } if all_round_ids else {}

    rows = []
    for c in surviving:
        cancelled_day_ids = {d.id for d in c.days if d.cancelled}
        for r in c.rounds:
            if is_round_cancelled(r, cancelled_day_ids):
                continue
            if not _round_asks_application(r, outcomes.get(r.id), now):
                continue
            is_open = _round_is_open(r, now)
            rows.append(SetupAskRow(
                concert=c, round_=r,
                status="open" if is_open else "awaiting",
                moment_utc=r.closes_at_utc if is_open else _result_moment(r),
            ))
    rows.sort(key=lambda x: (x.moment_utc is None, x.moment_utc or now, x.round_.id))
    return rows


async def setup_tallies(
    session: AsyncSession, user_id: int, now: datetime | None = None
) -> SetupTallies:
    """Screen 3's four numbers, computed over the surviving tracked upcoming
    concerts' live rounds (per the spec's table)."""
    now = now or _now()
    concerts, opted_out = await _tracked_upcoming_concerts(session, user_id, now)
    surviving = [c for c in concerts if c.id not in opted_out]

    all_round_ids = [r.id for c in surviving for r in c.rounds]
    outcomes: dict[int, LotteryOutcome] = {
        o.round_id: o.outcome for o in (await session.execute(
            select(RoundOutcome).where(
                RoundOutcome.user_id == user_id, RoundOutcome.round_id.in_(all_round_ids)
            )
        )).scalars()
    } if all_round_ids else {}

    applied = 0
    payment_due = 0
    next_deadline: datetime | None = None
    payment_candidates: list[tuple[datetime, Concert]] = []
    for c in surviving:
        cancelled_day_ids = {d.id for d in c.days if d.cancelled}
        live_rounds = [r for r in c.rounds if not is_round_cancelled(r, cancelled_day_ids)]
        for r in live_rounds:
            oc = outcomes.get(r.id)
            if oc is LotteryOutcome.APPLIED:
                applied += 1
            if (
                oc is LotteryOutcome.WON
                and r.payment_deadline_at_utc is not None
                and r.payment_deadline_at_utc > now
            ):
                payment_due += 1
                payment_candidates.append((r.payment_deadline_at_utc, c))
        nd = _next_deadline(live_rounds, now)
        if nd is not None and (next_deadline is None or nd < next_deadline):
            next_deadline = nd

    payment_concert = (
        min(payment_candidates, key=lambda x: x[0])[1] if payment_candidates else None
    )
    return SetupTallies(
        tracking=len(surviving), applied=applied, payment_due=payment_due,
        next_deadline_utc=next_deadline, payment_concert=payment_concert,
    )


async def apply_prune_selection(
    session: AsyncSession, user_id: int, shown_ids: set[int], keep_ids: set[int],
    now: datetime | None = None,
) -> tuple[int, int]:
    """Screen 1's batch write. Over `shown_ids` intersected with the RECOMPUTED
    tracked-upcoming tile set (ids outside are ignored, so a forged id can at
    worst edit the tamperer's own overrides):

    - shown, unchecked, not already pruned -> write a concert-level opt-out.
    - shown, checked, currently pruned      -> clear the override (back to the
      tag-derived default; per the resolved decision this never writes an
      explicit `subscribed` row).

    Both writes go through branch 4's `set_concert_subscription` /
    `clear_concert_subscription`, which own the invariant-2 reminder resync --
    this function never touches ConcertSubscription or the queue directly.
    Returns (pruned, unpruned) counts."""
    now = now or _now()
    tiles = await setup_prune_tiles(session, user_id, now)
    kept_by_id = {t.concert.id: t.kept for t in tiles}

    pruned = unpruned = 0
    for cid in shown_ids & kept_by_id.keys():
        currently_kept = kept_by_id[cid]
        if cid not in keep_ids and currently_kept:
            await set_concert_subscription(session, user_id, cid, SubscriptionState.OPTED_OUT)
            pruned += 1
        elif cid in keep_ids and not currently_kept:
            await clear_concert_subscription(session, user_id, cid)
            unpruned += 1
    return pruned, unpruned


async def record_setup_applications(
    session: AsyncSession, user_id: int, round_ids: set[int], now: datetime | None = None
) -> int:
    """Screen 2's batch write. Recomputes the qualifying round set via
    `setup_application_rows` and records APPLIED for each requested id that is
    actually in it -- ignoring ids outside the set, which is what
    server-enforces the middle-path rule against forged decided-round ids.
    Goes through `record_round_outcome` only (invariant 2, no second write
    path); its own starting-state rule already refuses to overwrite. Returns
    the count recorded."""
    now = now or _now()
    rows = await setup_application_rows(session, user_id, now)
    qualifying = {r.round_.id for r in rows}

    count = 0
    for rid in round_ids & qualifying:
        await record_round_outcome(session, user_id, rid, LotteryOutcome.APPLIED, now)
        count += 1
    return count


# ── Concert page: rounds grouped by leg ───────────────────────────────────


@dataclass(frozen=True)
class RoundRow:
    """One round as the concert page renders it: the round itself, the
    viewer's standing on it, and the same two capture gates a Home row
    carries -- resolved here, from `capture_gates`, so both surfaces cannot
    disagree about which buttons a round may offer.

    `primary_anchor`/`primary_at_utc` are the one moment the row leads with.
    A round can carry four timestamps and the row shows them all, but only
    one is bold, and picking it is a timing rule rather than presentation.
    """

    round_: Round
    outcome: LotteryOutcome | None
    can_capture: bool
    can_report_result: bool
    primary_anchor: Anchor | None = None
    primary_at_utc: datetime | None = None
    # `upgrade_locked` is True for an UPGRADE round a signed-in viewer is NOT
    # eligible for: the page shows a "Requires a ticket from ..." line naming
    # `qualifier_labels` instead of capture buttons they cannot honestly press.
    # An eligible viewer (and a signed-out one) sees the normal capture row.
    upgrade_locked: bool = False
    qualifier_labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class LegRounds:
    """One leg and the rounds that apply to it. Cancelled legs get a group
    like any other -- invariant 2 keeps the row alive and the page dims it
    rather than hiding it, so dropping it here would lose its rounds."""

    day: ConcertDay
    rounds: list[RoundRow]


def _primary_anchor(round_: Round, now: datetime) -> tuple[Anchor | None, datetime | None]:
    """The next moment on this round still ahead of `now`; failing that, the
    last one behind it. The fallback matters because a concert page shows
    finished rounds too -- leading a closed round with nothing would render a
    blank where every other row has a date."""
    moments = [
        (anchor, ts)
        for anchor, ts in (
            (Anchor.OPENS, round_.opens_at_utc),
            (Anchor.CLOSES, round_.closes_at_utc),
            (Anchor.RESULTS, round_.results_at_utc),
            (Anchor.PAYMENT, round_.payment_deadline_at_utc),
        )
        if ts is not None
    ]
    if not moments:
        return None, None
    moments.sort(key=lambda m: m[1])
    ahead = [m for m in moments if m[1] > now]
    return ahead[0] if ahead else moments[-1]


async def concert_round_rows(
    session: AsyncSession,
    user_id: int | None,
    concert: Concert,
    now: datetime | None = None,
) -> tuple[list[LegRounds], list[RoundRow]]:
    """Every round on `concert`, grouped for the concert page: one group per
    leg (in date order, cancelled legs included), plus the all-legs group.

    A round belongs to a leg when that leg's id is in its `applies_to`. Two
    cases skip the per-leg groups and land in the all-legs list instead: an
    empty/None `applies_to` (never tied to a leg in the first place), and an
    `applies_to` covering every LIVE leg -- "applies to all of them" is not a
    per-leg fact, and repeating the same round under each leg would bury the
    ones that really are leg-specific. A round covering some but not all legs
    is a real fact about each, so it appears under each of them.

    `user_id` is None for a caller with no standing to show; the rows still
    render, just with `outcome` None throughout. Outcomes load in ONE query
    for the whole concert, not one per round.
    """
    now = now or _now()
    locale = get_locale()
    days = list((await session.execute(
        select(ConcertDay)
        .where(ConcertDay.concert_id == concert.id)
        .order_by(ConcertDay.starts_at_utc, ConcertDay.id)
    )).scalars())
    rounds = list((await session.execute(
        select(Round)
        .where(Round.concert_id == concert.id)
        # Same ordering the board uses: soonest close first, undated last.
        .order_by(Round.closes_at_utc.is_(None), Round.closes_at_utc,
                  Round.opens_at_utc, Round.id)
    )).scalars())
    if not rounds and not days:
        return [], []

    outcomes: dict[int, LotteryOutcome] = {}
    if user_id is not None and rounds:
        outcomes = {
            o.round_id: o.outcome
            for o in (await session.execute(
                select(RoundOutcome).where(
                    RoundOutcome.user_id == user_id,
                    RoundOutcome.round_id.in_([r.id for r in rounds]),
                )
            )).scalars()
        }

    # Upgrade eligibility for the whole concert, ONE query for the qualifier
    # sets (outcomes are already loaded above). A signed-in viewer ineligible
    # for an upgrade round gets a locked row (the requirement line) instead of
    # capture buttons; a signed-out viewer has no standing to gate on, so no
    # row locks. `label_by_id` turns qualifier ids into the labels the
    # requirement line names.
    upgrade_ids = [r.id for r in rounds if r.kind is RoundKind.UPGRADE]
    qualifiers_by_round = await _qualifiers_by_upgrade_round(session, upgrade_ids)
    eligible_up = _eligible_upgrade_ids(rounds, outcomes, qualifiers_by_round)
    # The qualifier requirement line names other rounds by label, and those
    # labels are copied into RoundRow.qualifier_labels -- resolve the viewer's
    # variant here (concert page = web request).
    label_by_id = {r.id: loc_field(r, "label", locale) for r in rounds}

    day_ids = {d.id for d in days}
    live_leg_ids = {d.id for d in days if not d.cancelled}
    by_leg: dict[int, list[RoundRow]] = {d.id: [] for d in days}
    all_legs: list[RoundRow] = []

    for r in rounds:
        outcome = outcomes.get(r.id)
        is_upgrade = r.kind is RoundKind.UPGRADE
        eligible = r.id in eligible_up
        # Lock only a signed-in ineligible viewer out of an upgrade round --
        # signed out (user_id None) there is no eligibility to judge, so the
        # round renders like any other.
        upgrade_locked = is_upgrade and user_id is not None and not eligible
        can_capture, can_report_result = capture_gates(
            r, outcome, now, qualifies=(not is_upgrade) or eligible
        )
        anchor, at_utc = _primary_anchor(r, now)
        row = RoundRow(
            round_=r, outcome=outcome,
            can_capture=can_capture, can_report_result=can_report_result,
            primary_anchor=anchor, primary_at_utc=at_utc,
            upgrade_locked=upgrade_locked,
            qualifier_labels=tuple(
                label_by_id[q] for q in qualifiers_by_round.get(r.id, []) if q in label_by_id
            ),
        )
        # Ids for legs that no longer exist are dropped rather than trusted:
        # applies_to is plain JSON with no FK behind it, so a deleted leg can
        # leave one dangling.
        targets = {i for i in (r.applies_to or []) if i in day_ids}
        # `live_leg_ids and` guards the vacuous case: with every leg
        # cancelled, "covers every live leg" would be true of every round and
        # the per-leg groups would all empty out.
        if not targets or (live_leg_ids and live_leg_ids <= targets):
            all_legs.append(row)
            continue
        for leg_id in targets:
            by_leg[leg_id].append(row)

    return [LegRounds(day=d, rounds=by_leg[d.id]) for d in days], all_legs


def _needs_you(row: RoundRow, now: datetime) -> bool:
    """Does this round still want something from this reader?

    Two ways it can. You have live standing -- APPLIED (waiting on a result)
    or WON (you owe a payment). Or you have no standing at all and the round
    is open right now, so the decision is still yours to make.

    Everything else is settled and says nothing useful in an urgency panel:
    LOST and NOT_APPLIED are over, PAID is secured, and a round that closed
    without you is a chance already gone. `can_capture` alone is not enough
    for the no-standing case -- it only means the round has OPENED, and a
    long-closed round would otherwise sit at the top of the page forever.
    """
    if row.outcome in (LotteryOutcome.APPLIED, LotteryOutcome.WON):
        return True
    if row.outcome is not None:
        return False
    closes = row.round_.closes_at_utc
    return row.can_capture and (closes is None or closes > now)


def _next_moment_key(row: RoundRow, now: datetime) -> tuple[int, float]:
    """Sort key for "which of these wants me first": still-ahead moments in
    chronological order, then rounds carrying no timestamp at all, then past
    moments most-recent first. Same preference `_primary_anchor` applies
    WITHIN a round, applied here ACROSS rounds so the two cannot disagree."""
    at = row.primary_at_utc
    if at is None:
        return (1, 0.0)
    if at > now:
        return (0, at.timestamp())
    return (2, -at.timestamp())


def concert_next_moment(
    rows: Iterable[RoundRow], now: datetime | None = None
) -> RoundRow | None:
    """The one round the concert page's "Next for you" block leads with, or
    None when there is nothing worth leading with.

    None is a real answer, not a failure: with no standing anywhere and
    nothing open, an empty urgency panel is worse than no panel, so the page
    omits the block entirely rather than rendering a heading over a blank.

    Rounds are de-duplicated by id, because a round covering some-but-not-all
    legs appears under each of those legs and must not compete with itself.
    """
    now = now or _now()
    seen: set[int] = set()
    best: RoundRow | None = None
    best_key: tuple[int, float] | None = None
    for row in rows:
        if row.round_.id in seen:
            continue
        seen.add(row.round_.id)
        if not _needs_you(row, now):
            continue
        key = _next_moment_key(row, now)
        if best_key is None or key < best_key:
            best, best_key = row, key
    return best


# ── Discover status ───────────────────────────────────────────────────────


def discoverable_concert_criterion():
    """What /discover actually shows, as a SQL criterion.

    Hide a concert whose every existing leg is cancelled -- it has no valid
    dates left (still reachable directly at /concerts/{event_id}). A concert
    with NO days at all (a fresh draft) keeps showing, sorted last.

    One definition, two callers: the /discover query itself and Home's
    teaser count, which used to count every Concert row and so advertised
    more than the link led to.

    .correlate(Concert) is required: /discover's "event" sort also outerjoins
    ConcertDay onto the same statement, and without it SQLAlchemy's
    auto-correlation sees ConcertDay in both places and correlates it away
    from these subqueries too, leaving them with zero FROM clauses."""
    has_any_day = exists().where(ConcertDay.concert_id == Concert.id).correlate(Concert)
    has_live_day = (
        exists()
        .where(ConcertDay.concert_id == Concert.id, ConcertDay.cancelled.is_(False))
        .correlate(Concert)
    )
    return ~has_any_day | has_live_day


async def discoverable_concert_count(session: AsyncSession) -> int:
    """How many concerts /discover would list -- what Home's teaser counts."""
    return (await session.execute(
        select(func.count()).select_from(Concert).where(discoverable_concert_criterion())
    )).scalar_one()


async def discoverable_tag_counts(session: AsyncSession) -> dict[int, int]:
    """How many /discover-listed concerts carry each tag -- the sidebar chip
    counts (demo `.chip .n`, e.g. "Love Live! 64"). Scoped to
    discoverable_concert_criterion, the same filter the tile grid itself
    uses, so a count never promises more than the grid actually holds. One
    GROUP BY for the whole sidebar rather than a query per chip."""
    rows = (await session.execute(
        select(ConcertTag.tag_id, func.count())
        .select_from(ConcertTag)
        .join(Concert, Concert.id == ConcertTag.concert_id)
        .where(discoverable_concert_criterion())
        .group_by(ConcertTag.tag_id)
    )).all()
    return dict(rows)


def _open_round_criterion(now: datetime):
    """A round counts as open the same way the Python-side `_round_is_open`
    does: it must carry at least one timestamp, and `now` must fall in
    [opens, closes). Expressed in SQL so discoverable_open_round_count can
    filter in one query rather than loading every round to check in Python."""
    return (
        exists()
        .where(
            Round.concert_id == Concert.id,
            or_(Round.opens_at_utc.isnot(None), Round.closes_at_utc.isnot(None)),
            or_(Round.opens_at_utc.is_(None), Round.opens_at_utc <= now),
            or_(Round.closes_at_utc.is_(None), Round.closes_at_utc > now),
        )
        .correlate(Concert)
    )


async def discoverable_open_round_count(
    session: AsyncSession, now: datetime | None = None
) -> int:
    """How many /discover-listed concerts have a round open RIGHT NOW -- the
    "N with a round still open" half of Home's teaser, alongside
    discoverable_concert_count."""
    now = now or _now()
    return (await session.execute(
        select(func.count()).select_from(Concert).where(
            discoverable_concert_criterion(), _open_round_criterion(now)
        )
    )).scalar_one()


async def catalogue_tag_counts(session: AsyncSession) -> dict[TagKind, int]:
    """How many tags exist per kind -- the signed-out landing's "N franchises
    / N performers tagged" stat line (the other two figures reuse
    discoverable_concert_count / discoverable_open_round_count).
    Deliberately NOT scoped to discoverable_concert_criterion the way
    discoverable_tag_counts is: this is a fact about the tag vocabulary
    itself, not about which concerts currently show."""
    rows = (await session.execute(
        select(Tag.kind, func.count()).select_from(Tag).group_by(Tag.kind)
    )).all()
    return dict(rows)


async def discover_peek(
    session: AsyncSession, exclude_ids: set[int], limit: int = 4,
) -> list[Concert]:
    """Up to `limit` discoverable concerts for Home's peek grid -- a taste of
    the catalogue below the Discover teaser.

    `exclude_ids` (normally the caller's own tracked_concert_ids) keeps the
    grid a door OUT to concerts the user does not already follow, never a
    reprint of the board above it. Same eager-loads and default ordering
    (earliest live day first, undated last) as /discover's own query, since
    the card rendered from this reuses that page's shape."""
    stmt = (
        select(Concert)
        .where(discoverable_concert_criterion())
        .options(
            selectinload(Concert.days), selectinload(Concert.rounds), selectinload(Concert.tags)
        )
    )
    if exclude_ids:
        stmt = stmt.where(Concert.id.notin_(exclude_ids))
    first_day = func.min(ConcertDay.starts_at_utc)
    stmt = (
        stmt.outerjoin(
            ConcertDay,
            (ConcertDay.concert_id == Concert.id) & (ConcertDay.cancelled.is_(False)),
        )
        .group_by(Concert.id)
        .order_by(first_day.is_(None), first_day)
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars())


@dataclass(frozen=True)
class DiscoverStatus:
    """One catalogue card's single status pill, plus the two derived values
    the Discover page sorts and facets on.

    `status` is the round-status FACET and is event-only -- it never depends
    on the viewer, so a signed-out visitor gets the same value. `text`/`tone`
    are the pill, which DOES merge the viewer's standing over the event state:
    the standing replaces the countdown rather than sitting beside it, and the
    tone says who owes the next move -- "ok" you are covered, "danger" you owe
    an action, "quiet" you have no standing. The tone names match style.css's
    p-ok / p-danger / p-quiet classes directly.

    `at_utc` is the moment `text` refers to, or None when it refers to none
    (Secured, All rounds closed). The template renders it as a dual-time
    title so the pill's own short form stays a countdown, not a bare date.
    """

    status: str          # facet: "open" | "soon" | "none"
    text: str
    tone: str            # "ok" | "danger" | "quiet"
    at_utc: datetime | None = None
    next_deadline: datetime | None = None
    # The SECOND pill: the upgrade campaign as a fact of its own, beside the
    # base standing above. Only ever set for a viewer who is is_upgrade_eligible
    # for an open/applied upgrade round -- an ineligible or signed-out viewer
    # gets None and the template renders no second pill. A WON upgrade does NOT
    # use this: it collapses into the single urgent `text`/`tone` pill instead,
    # replacing the base standing (there is money owed, not two facts to show).
    # `upgrade_tone` is "accent" (template class s-up).
    upgrade_text: str | None = None
    upgrade_tone: str | None = None


def _humanize_until(then: datetime, now: datetime) -> str:
    """"4d" / "3h" / "12m" -- the coarsest unit that is still non-zero. The
    pill is scanned, not read, so one unit beats two.

    Rounded to nearest, not truncated: a deadline 3 days and 23 hours out is
    "4d" to anyone reading it, and truncation would call it "3d" -- an error
    in the alarming direction on a countdown, and one that flips the moment
    the page is refreshed."""
    minutes = max(int((then - now).total_seconds()) // 60, 0)
    if minutes >= 1440:
        return _("{n}d").format(n=int(minutes / 1440 + 0.5))
    if minutes >= 60:
        return _("{n}h").format(n=int(minutes / 60 + 0.5))
    return _("{n}m").format(n=max(minutes, 1))


def _day_month(when: datetime) -> str:
    """"22 Jul" / "7月22日", in JST like every other date this app shows,
    localized to the current request's locale (discover_statuses renders
    this into prose that is itself translated -- a ja viewer should never
    see an English day-month fragment). Delegates to the pure
    domain.timezones.fmt_day_month, which owns the actual formatting."""
    return fmt_day_month(when, get_locale())


async def discover_statuses(
    session: AsyncSession,
    concerts: list[Concert],
    user_id: int | None = None,
    now: datetime | None = None,
) -> dict[int, DiscoverStatus]:
    """One DiscoverStatus per concert id, for concerts ALREADY loaded by the
    caller with `days` and `rounds` eager-loaded.

    Deliberately not board_cards: that function answers "where do I stand on
    the concerts I track" and drops everything else on the floor, while
    Discover shows the whole catalogue -- including concerts with no outcome,
    no subscription and no open round, which are exactly the rows board_cards
    filters out.

    `user_id` None means signed out: no outcome query runs at all and every
    pill is the event state alone. Signed in, outcomes for every round on the
    page load in ONE query -- the per-concert shape is the obvious one and
    turns a catalogue page into N+1.
    """
    now = now or _now()

    outcomes: dict[int, LotteryOutcome] = {}
    if user_id is not None:
        all_round_ids = [r.id for c in concerts for r in c.rounds]
        if all_round_ids:
            outcomes = {
                o.round_id: o.outcome
                for o in (await session.execute(
                    select(RoundOutcome).where(
                        RoundOutcome.user_id == user_id,
                        RoundOutcome.round_id.in_(all_round_ids),
                    )
                )).scalars()
            }

    # Qualifier sets for every upgrade round on the page, ONE query -- reused
    # per concert below so the eligibility check adds no per-round query.
    qualifiers_by_round: dict[int, list[int]] = {}
    if user_id is not None:
        qualifiers_by_round = await _qualifiers_by_upgrade_round(
            session,
            [r.id for c in concerts for r in c.rounds if r.kind is RoundKind.UPGRADE],
        )

    out: dict[int, DiscoverStatus] = {}
    for concert in concerts:
        cancelled_day_ids = {d.id for d in concert.days if d.cancelled}
        rounds = [r for r in concert.rounds if not is_round_cancelled(r, cancelled_day_ids)]
        open_rounds = [r for r in rounds if _round_is_open(r, now)]
        opening_soon = [
            r for r in rounds if r.opens_at_utc is not None and r.opens_at_utc > now
        ]
        status = "open" if open_rounds else ("soon" if opening_soon else "none")

        # has_open_round=False makes column_for a pure STANDING computation:
        # it returns APPLIED/WON/SECURED when the user has one and None when
        # they do not, instead of falling back to Column.OPEN. Reusing it here
        # is what keeps the pill's precedence identical to the board's.
        card_outcomes = {r.id: outcomes[r.id] for r in rounds if r.id in outcomes}
        standing = column_for(
            [
                (outcomes[r.id], r.kind is RoundKind.UPGRADE)
                for r in rounds
                if r.id in outcomes
            ],
            has_open_round=False,
        )

        # Upgrade campaign, as a SECOND fact beside the base standing -- but
        # only for a viewer eligible to enter it (a held qualifying ticket).
        # Signed out, `outcomes` is empty, so nobody is eligible and no upgrade
        # pill shows -- the facet stays event-only above.
        eligible_up = _eligible_upgrade_ids(rounds, card_outcomes, qualifiers_by_round)
        # A WON upgrade owes money: it COLLAPSES the two pills into one urgent
        # standing, replacing the base pill entirely (handled first).
        won_up = next(
            (r for r in rounds if r.id in eligible_up
             and card_outcomes.get(r.id) is LotteryOutcome.WON),
            None,
        )
        if won_up is not None:
            due = won_up.payment_deadline_at_utc
            out[concert.id] = DiscoverStatus(
                status,
                _("Upgrade won — pay by {day}").format(day=_day_month(due))
                if due else _("Upgrade won — payment due"),
                "danger", due, _next_deadline(rounds, now),
            )
            continue

        # Otherwise the upgrade is its own accent pill, shown for an APPLIED
        # entry or an open round not yet entered; PAID/LOST show nothing (the
        # base standing already carries a PAID upgrade via column_for).
        upgrade_text = upgrade_tone = None
        applied_up = any(
            r.id in eligible_up and card_outcomes.get(r.id) is LotteryOutcome.APPLIED
            for r in rounds
        )
        open_up = next(
            (r for r in rounds if r.id in eligible_up
             and card_outcomes.get(r.id) is None and _round_is_open(r, now)),
            None,
        )
        if applied_up:
            upgrade_text, upgrade_tone = _("Upgrade · Applied"), "accent"
        elif open_up is not None:
            upgrade_text = (
                _("Upgrade · Closes in {n}").format(
                    n=_humanize_until(open_up.closes_at_utc, now)
                )
                if open_up.closes_at_utc else _("Upgrade · Open now")
            )
            upgrade_tone = "accent"

        if standing is Column.SECURED:
            out[concert.id] = DiscoverStatus(
                status, _("Secured"), "ok", None, _next_deadline(rounds, now),
                upgrade_text, upgrade_tone,
            )
            continue
        if standing is Column.WON:
            won = [r for r in rounds if card_outcomes.get(r.id) is LotteryOutcome.WON]
            due = min(
                (r.payment_deadline_at_utc for r in won if r.payment_deadline_at_utc), default=None
            )
            out[concert.id] = DiscoverStatus(
                status,
                _("Won — pay by {day}").format(day=_day_month(due))
                if due else _("Won — payment due"),
                "danger", due, _next_deadline(rounds, now),
                upgrade_text, upgrade_tone,
            )
            continue
        if standing is Column.APPLIED:
            applied = next(r for r in rounds if card_outcomes.get(r.id) is LotteryOutcome.APPLIED)
            out[concert.id] = DiscoverStatus(
                status,
                _("{kind} · Applied").format(kind=_(LABEL_BY_ROUND_KIND[applied.kind])),
                "ok", None, _next_deadline(rounds, now), upgrade_text, upgrade_tone,
            )
            continue

        # No standing: the event's own state, always neutral. An eligible
        # viewer always has a standing (eligibility needs a secured ticket), so
        # no upgrade pill reaches here -- but the featured round still PREFERS a
        # non-upgrade open round, falling back to the upgrade only when it is
        # the only thing open (an ineligible viewer must not be led with an
        # upgrade countdown they cannot act on).
        if open_rounds:
            non_upgrade_open = [r for r in open_rounds if r.kind is not RoundKind.UPGRADE]
            pool = non_upgrade_open or open_rounds
            closing = sorted(
                (r for r in pool if r.closes_at_utc),
                key=lambda r: r.closes_at_utc,
            )
            r = closing[0] if closing else pool[0]
            text = (
                _("{kind} · Closes in {n}").format(
                    kind=_(LABEL_BY_ROUND_KIND[r.kind]),
                    n=_humanize_until(r.closes_at_utc, now),
                )
                if r.closes_at_utc
                else _("{kind} · Open now").format(kind=_(LABEL_BY_ROUND_KIND[r.kind]))
            )
            at = r.closes_at_utc
        elif opening_soon:
            r = min(opening_soon, key=lambda r: r.opens_at_utc)
            at = r.opens_at_utc
            text = _("{kind} · Opens in {n}").format(
                kind=_(LABEL_BY_ROUND_KIND[r.kind]), n=_humanize_until(at, now)
            )
        else:
            # Covers both "every round has closed" and "no rounds entered
            # yet" -- from a browser's point of view they are the same thing:
            # there is nothing here you can act on.
            text, at = _("All rounds closed"), None
        out[concert.id] = DiscoverStatus(
            status, text, "quiet", at, _next_deadline(rounds, now),
            upgrade_text, upgrade_tone,
        )

    return out


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
    session: AsyncSession, user_id: int, now: datetime | None = None,
    locale: str | None = None,
) -> list[CalendarEvent]:
    """Every round/day the user currently has an active reminder rule
    covering (concert-wide or round-specific), each producing ONE event at
    its real deadline -- sourced from reminder_queue, which already encodes
    exactly which rounds/days are in scope per rule (sync_rule/plan_for_rule
    already did the anchor-specific filtering). Future-only: a round/day
    whose deadline already passed is left off the feed.

    `locale` localizes the concert title for a locale-aware caller (the
    /mydeadlines cog passes the recipient's language). Left None by the .ics
    feed, which has no viewer locale -- that path keeps the canonical title,
    byte-identical to before.
    """
    now = now or _now()

    def _title(concert: Concert | None) -> str:
        if concert is None:
            return "Concert"
        return loc_field(concert, "title", locale) if locale else concert.title

    def _label(obj: Round | ConcertDay) -> str:
        """Same rule as _title, for the round/leg label: an explicit caller
        locale localizes it, None (the .ics feed) keeps the canonical text.
        Deliberately NOT get_locale() -- the feed must stay byte-identical."""
        return loc_field(obj, "label", locale) if locale else obj.label

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
                concert_title=_title(concert),
                label=_label(r), at_utc=at, url=r.url, notes=r.notes,
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
                concert_title=_title(concert),
                label=_label(d), at_utc=d.starts_at_utc,
            ))

    events.sort(key=lambda e: e.at_utc)
    return events


# ── Concert edit history ──────────────────────────────────────────────────

# Deliberately just the concert's own top-level fields -- day/round/tag
# adds-removes-edits are NOT tracked here, that's a much bigger feature than
# "lightweight". event_id is included since renaming a concert's URL handle
# is exactly the kind of quiet, easy-to-miss edit an audit log is for.
TRACKED_CONCERT_FIELDS = [
    "event_id", "title", "title_en", "title_zh", "kind", "organizer", "categories",
    "eventernote_url", "official_url", "source_url", "performers_text", "notes",
    "notes_en", "notes_zh", "venue_en", "venue_zh",
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


async def find_tag_by_name_and_kind(
    session: AsyncSession, name: str, kind: TagKind
) -> Tag | None:
    """A name+kind collision -- the kind-scoped duplicate the create route
    blocks on. A second `Aqours` GROUP is a real duplicate; an `Aqours` VENUE
    beside the `Aqours` GROUP is allowed (resolved with the owner). Rename
    still uses the name-only find_tag_by_name."""
    from sqlalchemy import func as sa_func

    res = await session.execute(
        select(Tag).where(
            sa_func.lower(Tag.name) == name.strip().lower(), Tag.kind == kind
        )
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


async def resolve_group_member(
    session: AsyncSession, group_id: int, member_id: int
) -> tuple[Tag, Tag] | None:
    """Both tags plus proof that `member_id` really is a member of the GROUP
    tag `group_id` -- None if any part of that doesn't hold.

    Retroactive-apply bulk-attaches a tag to every active concert carrying
    another tag, queueing a notification per subscriber, so an unvalidated
    (group, member) pair would let any arbitrary pairing fan out a large DM
    wave. This only decides which pairs may be asked about; it does not
    change what gets attached (see the Group Tag Expansion invariant)."""
    group = await session.get(Tag, group_id)
    member = await session.get(Tag, member_id)
    if group is None or member is None or group.kind is not TagKind.GROUP:
        return None
    if await session.get(TagMember, (group_id, member_id)) is None:
        return None
    return group, member


async def active_concerts_missing_member(
    session: AsyncSession, group_id: int, member_id: int, now: datetime | None = None
) -> list[Concert]:
    """Concerts tagged with `group_id` that don't already carry `member_id`
    and have at least one live (non-cancelled) leg whose date hasn't
    passed -- the set the Tags page's retroactive-apply confirmation
    offers to bulk-attach an artist to. "Active" means the concert still has
    a live leg in the future -- the same live-leg reading the concert page's
    leg sections use, expressed here as SQL rather than shared with them,
    because this module sits below web/routes/ in the dependency direction."""
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


@dataclass(frozen=True)
class TagCounts:
    """Everything a tag chip and its dialog display about what the tag costs
    to change: how many concerts carry it, how many users follow it, how many
    members it has (groups only), and how many of its concerts are still
    upcoming (>=1 non-cancelled leg not yet past)."""

    concerts: int = 0
    followers: int = 0
    members: int = 0
    upcoming: int = 0


async def tag_directory_context(session: AsyncSession, now: datetime | None = None) -> dict:
    """Every count and grouping the Tags directory page needs, in one pass --
    the route stays assembly-only. No N+1: concert/follower/member counts come
    from three GROUP BY aggregates, and the per-concert "active" reading
    (>=1 non-cancelled leg not yet past -- the same live-leg definition
    active_concerts_missing_member uses) is computed once over all days.

    Returns a dict with:
      counts             -- {tag_id: TagCounts}
      franchise_families -- [(franchise Tag, [(group Tag, [member Tag, ...]), ...]), ...]
                            in franchise name order; groups in name order
      no_franchise_groups-- [(group Tag, [member Tag, ...]), ...] for parentless groups
      venue_regions      -- [(region_name, [venue Tag, ...]), ...] alpha, "No region" last
      ungrouped_performers -- ARTIST tags that are no group's member, name order
      summary            -- {concerts, franchises, groups, performers, venues}
      eligible_members   -- {group_id: [(member Tag, n_eligible_concerts), ...]}
    """
    now = now or _now()
    tags = list((await session.execute(select(Tag).order_by(Tag.name))).scalars())
    by_id = {t.id: t for t in tags}

    # ── three GROUP BY aggregates ──
    concert_rows = (await session.execute(
        select(ConcertTag.tag_id, func.count()).group_by(ConcertTag.tag_id)
    )).all()
    concerts_by_tag = dict(concert_rows)
    follower_rows = (await session.execute(
        select(TagSubscription.tag_id, func.count()).group_by(TagSubscription.tag_id)
    )).all()
    followers_by_tag = dict(follower_rows)
    member_rows = (await session.execute(
        select(TagMember.group_tag_id, func.count()).group_by(TagMember.group_tag_id)
    )).all()
    members_by_group = dict(member_rows)

    # ── the active/upcoming reading, computed once over all days ──
    day_rows = (await session.execute(
        select(ConcertDay.concert_id, ConcertDay.starts_at_utc, ConcertDay.cancelled)
    )).all()
    live_future_concert_ids: set[int] = set()
    for concert_id, starts_at, cancelled in day_rows:
        if not cancelled and starts_at >= now:
            live_future_concert_ids.add(concert_id)
    all_concert_tag_rows = (await session.execute(
        select(ConcertTag.concert_id, ConcertTag.tag_id)
    )).all()
    upcoming_by_tag: dict[int, int] = {}
    for concert_id, tag_id in all_concert_tag_rows:
        if concert_id in live_future_concert_ids:
            upcoming_by_tag[tag_id] = upcoming_by_tag.get(tag_id, 0) + 1

    counts = {
        t.id: TagCounts(
            concerts=concerts_by_tag.get(t.id, 0),
            followers=followers_by_tag.get(t.id, 0),
            members=members_by_group.get(t.id, 0),
            upcoming=upcoming_by_tag.get(t.id, 0),
        )
        for t in tags
    }

    # ── membership map (group_id -> [member Tag, ...] in name order) ──
    tag_member_rows = (await session.execute(
        select(TagMember.group_tag_id, TagMember.member_tag_id)
    )).all()
    members_of: dict[int, list[Tag]] = {}
    grouped_member_ids: set[int] = set()
    for group_id, member_id in tag_member_rows:
        member = by_id.get(member_id)
        if member is not None:
            members_of.setdefault(group_id, []).append(member)
            grouped_member_ids.add(member_id)
    for members in members_of.values():
        members.sort(key=lambda m: m.name)

    franchises = [t for t in tags if t.kind is TagKind.FRANCHISE]
    groups = [t for t in tags if t.kind is TagKind.GROUP]
    artists = [t for t in tags if t.kind is TagKind.ARTIST]
    venues = [t for t in tags if t.kind is TagKind.VENUE]

    def group_with_members(g: Tag) -> tuple[Tag, list[Tag]]:
        return g, members_of.get(g.id, [])

    franchise_families = [
        (f, [group_with_members(g) for g in groups if g.parent_id == f.id])
        for f in franchises
    ]
    no_franchise_groups = [group_with_members(g) for g in groups if g.parent_id is None]

    # ── venues by region, "No region" last ──
    by_region: dict[str, list[Tag]] = {}
    for v in venues:
        by_region.setdefault(v.region or "No region", []).append(v)
    venue_regions = [
        (name, by_region[name])
        for name in sorted(by_region, key=lambda r: (r == "No region", r))
    ]

    ungrouped_performers = [a for a in artists if a.id not in grouped_member_ids]

    # ── eligible members per group (powers the apply-to-existing links) ──
    eligible_members: dict[int, list[tuple[Tag, int]]] = {}
    for g in groups:
        entries: list[tuple[Tag, int]] = []
        for member in members_of.get(g.id, []):
            concerts = await active_concerts_missing_member(session, g.id, member.id, now)
            if concerts:
                entries.append((member, len(concerts)))
        eligible_members[g.id] = entries

    summary = {
        "concerts": (await session.execute(
            select(func.count()).select_from(Concert)
        )).scalar_one(),
        "franchises": len(franchises),
        "groups": len(groups),
        "performers": len(artists),
        "venues": len(venues),
    }

    return {
        "counts": counts,
        "franchise_families": franchise_families,
        "no_franchise_groups": no_franchise_groups,
        "venue_regions": venue_regions,
        "ungrouped_performers": ungrouped_performers,
        "summary": summary,
        "eligible_members": eligible_members,
    }


def match_venue_tag_id(name: str | None, venue_tags: Sequence[Tag]) -> int | None:
    """The id of the VENUE tag whose canonical `name` matches `name`, or None.

    The ramen.events parse scrapes ONE free-text venue name per event
    (`ParsedConcert.venue_name`); the import preview uses this to pre-select
    that venue in each parsed leg's picker, so the common case -- a venue
    that already has a tag -- needs no click. No match leaves the picker on
    its empty option, which is the editor's cue to mint the tag inline.

    Matching is deliberately narrow: trimmed, case-insensitive, against the
    canonical `name` column ONLY. Not name_en/name_zh (the scrape is the
    site's own rendering, and a locale variant matching by accident would
    silently bind the wrong venue), and not fuzzy (a wrong pre-selection is
    worse than none -- the editor has to notice it to undo it).

    Trimming is Python's `str.strip()`, which drops U+3000 (ideographic
    space) alongside U+0020 -- venue text pasted from Japanese sites carries
    it, and exactly that mismatch bit the earlier venue migration. This is
    also why the comparison happens HERE over an already-loaded tag list
    rather than as a SQL `lower(trim(...))`: SQLite's trim() knows only
    U+0020, so pushing it down would silently reintroduce the bug.
    """
    if not name:
        return None
    needle = name.strip().casefold()
    if not needle:
        return None
    for tag in venue_tags:
        if tag.name and tag.name.strip().casefold() == needle:
            return tag.id
    return None


async def tag_picker_context(session: AsyncSession) -> dict:
    """Data the shared tag-picker partial needs: tags grouped by kind, plus
    the two lookup maps its client-side script reads (group->members for
    auto-populating artists, and id->name for rendering selected chips).
    Returns plain dicts, NOT pre-serialized JSON -- the template hands them
    to Jinja's `| tojson`, which must serialize the object itself so it can
    escape `<`/`>`/`&` out of the surrounding <script> block.
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
    return {"by_kind": by_kind, "groups": groups_data, "tag_names": tag_names}


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


# ── Operational health alerts ────────────────────────────────────────────


async def evaluate_and_alert(session: AsyncSession, results, now: datetime) -> int:
    """Fold check results into persisted state; queue an owner DM per confirmed
    change. Returns how many alerts were queued.

    Alerts go through the notifications outbox rather than a direct DM: that is
    invariant 4, and it buys retry, ordering and Forbidden handling for free.
    `kind="ops_alert"` with `concert_id=None` falls through
    `scheduler.loop._notification_context` to the plain-text path, so the send
    code needs no changes.
    """
    # Local import on purpose: app.ops sits ABOVE db/ (it already imports
    # db.models), so importing it at module scope would invert the layering and
    # make db/service.py unimportable on its own.
    from app.domain.health import StoredState, should_alert
    from app.ops import REGISTRY

    alerting = {e.name for e in REGISTRY if e.alerting}
    queued = 0

    for result in results:
        row = await session.get(OpsCheckState, result.name)
        # Keyword arguments in BOTH directions, deliberately: StoredState has
        # two bool|None fields and three datetime|None ones, so a positional
        # copy that drifts out of dataclass order swaps changed_at with
        # last_notified_at silently -- same type, no error, wrong nag timing.
        stored = (
            StoredState(
                ok=row.ok,
                changed_at=row.changed_at,
                last_notified_at=row.last_notified_at,
                pending_ok=row.pending_ok,
                pending_since=row.pending_since,
            )
            if row is not None
            else None
        )
        decision = should_alert(stored, result.ok, now)

        would_notify = decision.notify and result.name in alerting
        # A laptop's disk is not an operational signal; without this, every
        # local dev run would accumulate junk notifications. Evaluated BEFORE
        # the state write, because last_notified_at is the 24h nag clock:
        # advancing it for an alert that was never sent silently swallows the
        # first day of alerts on a server where DISCORD_TOKEN is added later.
        suppressed = would_notify and not settings.bot_enabled

        if row is None:
            row = OpsCheckState(name=result.name)
            session.add(row)
        row.ok = decision.state.ok
        row.changed_at = decision.state.changed_at
        row.last_notified_at = (
            (stored.last_notified_at if stored is not None else None)
            if suppressed
            else decision.state.last_notified_at
        )
        row.pending_ok = decision.state.pending_ok
        row.pending_since = decision.state.pending_since

        if not would_notify or suppressed:
            continue

        status = "recovered" if result.ok else "FAILING"
        for admin_id in settings.admin_ids:
            # An admin who has never logged into the web app has no users row,
            # and Notification.user_id is a FK to it -- queuing without this
            # raises IntegrityError at flush, far from the cause. Guarded on
            # absence rather than calling ensure_user unconditionally: that
            # refreshes the username, which would overwrite a real admin's
            # name with this placeholder every time a check changed state.
            if await session.get(User, admin_id) is None:
                await ensure_user(session, admin_id, str(admin_id))
            session.add(
                Notification(
                    user_id=admin_id,
                    body=f"dekimasen.app check `{result.name}` {status}: {result.detail}",
                    kind="ops_alert",
                )
            )
            queued += 1

    await session.flush()
    return queued


# ── DM button actions (Phase 12) — pure DB logic, discord-free ───────────


async def get_default_preset(session: AsyncSession, user_id: int) -> ReminderPreset | None:
    res = await session.execute(
        select(ReminderPreset).where(
            ReminderPreset.user_id == user_id, ReminderPreset.is_default.is_(True)
        )
    )
    return res.scalar_one_or_none()


async def create_preset_from_rules(
    session: AsyncSession,
    user_id: int,
    name: str,
    rules: list[tuple[int, int, str, Anchor]],
) -> ReminderPreset:
    """Materialise a named preset and its items from (offset_days, offset_hours,
    direction, anchor) rules -- the welcome wizard's preset step.

    This is the SAME write shape POST /presets uses (invariant: no second
    preset write path): direction is not a stored column, it is encoded in the
    SIGN of the offsets (before = negative, after = positive), and a 0/0 offset
    is the "when it happens" moment. Returns the flushed preset so the caller
    can mark it default.
    """
    preset = ReminderPreset(user_id=user_id, name=name.strip() or "My reminders")
    session.add(preset)
    await session.flush()
    for offset_days, offset_hours, direction, anchor in rules:
        sign = 1 if direction == "after" else -1
        session.add(PresetItem(
            preset_id=preset.id, anchor=anchor,
            offset_days=sign * offset_days, offset_hours=sign * offset_hours,
        ))
    await session.flush()
    return preset


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
    user_language: str = "en"
    user_has_rules: bool = False
    user_has_default_preset: bool = False


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

    user = await session.get(User, user_id)
    # Per-RECIPIENT locale: this context is built once per (concert, user)
    # outside any request, so get_locale() would be the sender's language.
    # Resolved BEFORE the tag lists -- tag names have had name_en/name_zh
    # since the i18n build, but the DM tag line was still reading the raw
    # column while the title one line below went through loc_field.
    locale = user.language if user else "en"
    non_venue = [
        loc_field(t, "name", locale) for t in concert.tags if t.kind.value != "venue"
    ]
    venues = [
        loc_field(t, "name", locale) for t in concert.tags if t.kind.value == "venue"
    ]
    has_rules = (await session.execute(
        select(ReminderRule.id)
        .where(ReminderRule.user_id == user_id, ReminderRule.concert_id == concert_id)
        .limit(1)
    )).scalar_one_or_none() is not None

    return NoticeContext(
        concert_id=concert_id,
        event_id=concert.event_id,
        title=loc_field(concert, "title", locale),
        tags_line=" · ".join(non_venue),
        venue=(gettext_in(locale, "Multiple") if len(venues) > 1
               else (venues[0] if venues else loc_field(concert, "venue", locale))),
        first_deadline_label=loc_field(first[0], "label", locale) if first else None,
        first_deadline_at=first[1] if first else None,
        user_timezone=user.timezone if user else "America/Moncton",
        user_language=user.language if user else "en",
        user_has_rules=has_rules,
        user_has_default_preset=await get_default_preset(session, user_id) is not None,
    )


@dataclass(frozen=True)
class LegCancelledContext:
    """Everything needed to render the leg-cancellation embed."""

    concert_id: int
    event_id: str
    title: str
    # Recipient's DM language; _send_notification reads this via
    # getattr(ctx, "user_language", "en") and sets the locale before composing
    # the embed, so the leg-cancel prose localizes (mirrors NoticeContext).
    user_language: str = "en"


async def leg_cancelled_context(
    session: AsyncSession, concert_id: int, user_id: int | None = None
) -> LegCancelledContext | None:
    concert = await session.get(Concert, concert_id)
    if concert is None:
        return None
    user = await session.get(User, user_id) if user_id else None
    locale = user.language if user else "en"
    return LegCancelledContext(
        concert_id=concert.id,
        event_id=concert.event_id,
        title=loc_field(concert, "title", locale),
        user_language=locale,
    )


# ── Venue rollup (legs -> concert) ───────────────────────────────────────


async def sync_concert_venue_tags(session: AsyncSession, concert_id: int) -> list[Tag]:
    """Rewrite a concert's VENUE tag rows as the union of its legs' venues.

    The leg is the single place a venue is entered, so the concert level is
    derived and can never contradict it. Only VENUE rows are touched --
    franchise/group/artist attachment is deliberate and materialized (invariant
    3), and must survive untouched.

    Discover's region filter reads concert_tags client-side off each tile's
    data-tags, so keeping this rollup current is exactly what lets that filter
    stay unchanged while venues live on legs.

    Returns the tags it NEWLY attached, which every caller must hand to
    `handle_newly_tagged`. VENUE tags are subscribable (the tags page lists
    them; POST /subscriptions puts no kind restriction on them), so a user
    following "Zepp Haneda" is owed the same DM notice and preset auto-apply
    a concert-level attach gives them (invariant 4). Attaching through
    `attach_tag` rather than a bare ConcertTag insert is also what makes a
    re-run idempotent instead of a composite-PK IntegrityError.

    `desired` is filtered to Tag.kind == VENUE for the same reason `current`
    always was: the two sets must be defined over the same population. A
    non-VENUE id in the column would otherwise sit in `desired` forever
    without ever reaching `current`, so every save would re-add it and the
    second would die on the primary key -- permanently unsavable. The route
    boundary rejects such an id (see `resolve_day_venue_tags`); this is the
    second, cheaper end of the same guard.
    """
    desired = {t.id: t for t in (await session.execute(
        select(Tag)
        .join(ConcertDay, ConcertDay.venue_tag_id == Tag.id)
        .where(ConcertDay.concert_id == concert_id, Tag.kind == TagKind.VENUE)
    )).scalars()}

    current = set((await session.execute(
        select(ConcertTag.tag_id)
        .join(Tag, Tag.id == ConcertTag.tag_id)
        .where(ConcertTag.concert_id == concert_id, Tag.kind == TagKind.VENUE)
    )).scalars())

    for tag_id in current - set(desired):
        await session.execute(
            delete(ConcertTag).where(
                ConcertTag.concert_id == concert_id, ConcertTag.tag_id == tag_id
            )
        )
    newly: list[Tag] = []
    for tag_id in sorted(set(desired) - current):
        # expand=False is a no-op for a VENUE tag (only GROUP expands), but
        # stated rather than defaulted: this path must never materialize
        # anything beyond the venue itself.
        newly += await attach_tag(session, concert_id, desired[tag_id], expand=False)
    await session.flush()
    return newly


# ── Round-label phrases ───────────────────────────────────────────────────


async def record_round_label_phrase(
    session: AsyncSession, label: str, label_en: str, label_zh: str
) -> None:
    """Remember a trilingual round label so later concerts can reuse it.

    Only a COMPLETE triple is recorded: a suggestion that fills two of three
    boxes still leaves the editor typing, which is the cost this exists to
    remove. Reusing an existing triple bumps its count rather than inserting
    a duplicate -- that count is what ranks the picker.
    """
    label, label_en, label_zh = label.strip(), label_en.strip(), label_zh.strip()
    if not (label and label_en and label_zh):
        return

    # One statement, used by both the pre-check and the post-race re-select:
    # if the two ever drifted apart, the re-select would stop finding the
    # winner's row and the bump would be silently skipped.
    lookup = select(RoundLabelPhrase).where(
        RoundLabelPhrase.label == label,
        RoundLabelPhrase.label_en == label_en,
        RoundLabelPhrase.label_zh == label_zh,
    )

    existing = (await session.execute(lookup)).scalar_one_or_none()
    if existing is None:
        # The only try/except IntegrityError in the app -- everywhere else
        # pre-checks instead, and the pre-check above is still the normal
        # path. The catch exists ONLY for the race: two editors saving the
        # same never-before-seen triple in one flush window, where the loser
        # hits the unique index. What makes this site different from the
        # others is the cost of not catching. This runs inside the
        # transaction of whatever concert save triggered it, so an escaping
        # IntegrityError rolls back that editor's ENTIRE save -- their real
        # work destroyed by a convenience feature. A savepoint keeps the blast
        # radius to this insert -- SQLAlchemy flushes the session when the
        # nested transaction opens, so the caller's pending rows are already
        # persistent and ROLLBACK TO SAVEPOINT cannot reach them (pinned by
        # test_a_lost_race_does_not_take_the_callers_pending_work_with_it).
        # And losing the race means someone else already remembered this
        # triple, so falling through to the bump is the correct outcome, not
        # a consolation prize.
        try:
            async with session.begin_nested():
                session.add(
                    RoundLabelPhrase(label=label, label_en=label_en, label_zh=label_zh)
                )
        except IntegrityError:
            existing = (await session.execute(lookup)).scalar_one_or_none()

    if existing is not None:
        existing.used_count += 1
        existing.last_used_at = _now()
    await session.flush()


async def round_label_phrases(
    session: AsyncSession, limit: int = 50
) -> list[RoundLabelPhrase]:
    """The picker's list: most-used first, most-recent breaking ties."""
    return list((await session.execute(
        select(RoundLabelPhrase)
        .order_by(RoundLabelPhrase.used_count.desc(), RoundLabelPhrase.last_used_at.desc())
        .limit(limit)
    )).scalars())


async def forget_round_label_phrase(session: AsyncSession, phrase_id: int) -> bool:
    """Stop offering a phrase. Returns False when it was already gone.

    Deliberately does NOT touch rounds that used it -- a phrase is a
    suggestion, not a foreign key, so forgetting a typo leaves the concerts
    that carry it exactly as they are.
    """
    existing = await session.get(RoundLabelPhrase, phrase_id)
    if existing is None:
        return False
    await session.delete(existing)
    await session.flush()
    return True
