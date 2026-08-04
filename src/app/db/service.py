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
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import case, delete, exists, false, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db.models import (
    Broadcast,
    Concert,
    ConcertAudit,
    ConcertDay,
    ConcertSubscription,
    ConcertTag,
    DeliveryLog,
    DiscoveredEvent,
    DiscoveryState,
    LegOptOut,
    Notification,
    OpsCheckState,
    PendingDraft,
    PresetItem,
    ReminderPreset,
    ReminderQueue,
    ReminderRule,
    Round,
    RoundLabelPhrase,
    RoundOutcome,
    RoundOutcomeDay,
    RoundQualifier,
    Tag,
    TagMember,
    TagSubscription,
    User,
)
from app.domain.board import OPEN_COLUMN_LIMIT, Column, column_for, pill_tone
from app.domain.digest import DeliveryFact, build_digest
from app.domain.eventernote import ActorEvent
from app.domain.prune_list import PruneList
from app.domain.rehearsal import expected_buttons
from app.domain.reminders import DayInfo, RoundInfo, RuleInfo, anchor_time, plan_for_rule
from app.domain.slugs import tag_slug_base
from app.domain.tags_diff import ImportPlan, TagPlan
from app.domain.tags_yaml import RESTORE_NOTES, TagExport, tags_to_yaml
from app.domain.timezones import fmt_day_month, utc_to_jst
from app.domain.translations import SLOT_LABEL, missing_variants
from app.domain.types import (
    ALLOWED_PARENT_KINDS,
    Anchor,
    BroadcastMode,
    DeliveryOutcome,
    DeliverySource,
    DismissReason,
    LegResult,
    LotteryOutcome,
    RoundKind,
    SubscriptionState,
    TagKind,
)
from app.domain.upgrades import is_upgrade_eligible
from app.domain.yaml_export import YamlDay, YamlRound, concert_to_yaml
from app.domain.yaml_import import DraftBatch
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

    POST /me/delete (web/routes/preferences.py) calls this, scoped to the
    caller behind require_user and a heavy client-side confirmation; it
    also remains available as a manual owner operation. A re-created row
    after erasure starts with welcomed_at NULL, so the next login is
    onboarded afresh -- by design, not accident.
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


def _covered_day_ids(round_: Round, all_day_ids: set[int]) -> set[int]:
    """The legs a round actually covers: its applies_to narrowed to days that
    still exist, or -- empty/None applies_to being the all-legs convention --
    every day on the concert. Stale ids in applies_to (a leg deleted after the
    round was written) are dropped: a day that no longer exists is not a leg
    anyone can still be waiting on."""
    if not round_.applies_to:
        return set(all_day_ids)
    return set(round_.applies_to) & all_day_ids


async def secured_day_ids_by_round(
    session: AsyncSession, user_id: int, concert_id: int
) -> dict[int, set[int]]:
    """round_id -> the EXACT day ids this user secured through that round, for
    every round on the concert whose outcome is WON or PAID.

    Per-day resolution follows RoundOutcomeDay's no-rows-means-all convention:
    a secured round with no day rows secured every leg it covers, which is the
    common single-leg (and the "won the whole tour") case; once ANY day row
    exists for that round the answer is exactly its WON rows, so a partial win
    stops over-claiming the legs that were actually lost. A round whose rows
    are all LOST therefore maps to the empty set -- present in the dict (its
    outcome is still WON/PAID) but securing nothing.

    Rounds with no outcome, or an outcome short of WON/PAID, are absent.
    Batched: at most four queries regardless of how many rounds the concert
    has -- never one per round."""
    rounds = list((await session.execute(
        select(Round).where(Round.concert_id == concert_id)
    )).scalars())
    if not rounds:
        return {}
    round_ids = [r.id for r in rounds]

    secured_round_ids = set((await session.execute(
        select(RoundOutcome.round_id).where(
            RoundOutcome.user_id == user_id,
            RoundOutcome.round_id.in_(round_ids),
            RoundOutcome.outcome.in_([LotteryOutcome.WON, LotteryOutcome.PAID]),
        )
    )).scalars())
    if not secured_round_ids:
        return {}

    all_day_ids = set((await session.execute(
        select(ConcertDay.id).where(ConcertDay.concert_id == concert_id)
    )).scalars())

    # Both halves of the split are needed: the WON rows say what was secured,
    # while the presence of ANY row is what switches off the no-rows-means-all
    # fallback.
    won_days: dict[int, set[int]] = {}
    resolved_rounds: set[int] = set()
    for round_id, day_id, result in (await session.execute(
        select(RoundOutcomeDay.round_id, RoundOutcomeDay.day_id, RoundOutcomeDay.result).where(
            RoundOutcomeDay.user_id == user_id,
            RoundOutcomeDay.round_id.in_(sorted(secured_round_ids)),
        )
    )).all():
        resolved_rounds.add(round_id)
        if result is LegResult.WON:
            won_days.setdefault(round_id, set()).add(day_id)

    return _secured_from_outcome_rows(
        rounds, secured_round_ids, all_day_ids, resolved_rounds, won_days
    )


def _secured_from_outcome_rows(
    rounds: list[Round],
    secured_round_ids: set[int],
    all_day_ids: set[int],
    resolved_rounds: set[int],
    won_days: dict[int, set[int]],
) -> dict[int, set[int]]:
    """The secured-days fold itself, pure, over rows the caller has loaded --
    the same split, and for the same reason, as `_covered_from_secured` below:
    the single-concert helper above and the batched
    `covered_round_ids_by_concert` share ONE definition rather than each
    growing a copy that can drift.

    `secured_round_ids`, `resolved_rounds` and `won_days` may span more
    concerts than `rounds` does -- they are keyed by round id, which is global,
    so a batched caller can load them once and pass the same three to every
    concert's fold. `all_day_ids` is the one input that must be scoped to THIS
    concert: it is what `_covered_day_ids` resolves an empty applies_to
    against, and widening it would hand a round every other concert's legs."""
    secured: dict[int, set[int]] = {}
    for r in rounds:
        if r.id not in secured_round_ids:
            continue
        covered = _covered_day_ids(r, all_day_ids)
        if r.id in resolved_rounds:
            # Intersect so the answer can never claim a leg the round does not
            # cover -- day rows outlive an applies_to edit.
            secured[r.id] = won_days.get(r.id, set()) & covered
        else:
            secured[r.id] = covered
    return secured


async def covered_round_ids_by_concert(
    session: AsyncSession, user_id: int, concert_ids: set[int]
) -> dict[int, set[int]]:
    """concert_id -> the rounds this user has no reason left to act on: every
    leg they cover that this user is still waiting on is already secured by
    some OTHER round on the SAME concert. The single "stop asking about this"
    definition, shared by the reminder planner and every read surface, so a
    page can never disagree with the DMs.

    "Still waiting on" is the load-bearing part: legs this user opted out of,
    and legs that are cancelled, are subtracted first (see
    `_covered_from_secured`).

    A round the user has WON or PAID is never covered AT ALL -- not just by
    itself. "Covered" answers the apply/results question; on a round you won,
    the open question is payment, and money you owe is not settled by holding
    a seat for the same night through some other round. Two rounds won over
    the same legs would otherwise each be "secured elsewhere" by the other and
    both go quiet, leaving two tickets to pay for and nowhere to record it.

    A round covering no existing leg is never covered either, and UPGRADE
    rounds are excluded outright -- holding a ticket is the PREREQUISITE for
    entering an upgrade, not a reason to stop showing it (see
    _apply_outcome_suppression's docstring).

    Concerts where the user has secured nothing are ABSENT from the result
    rather than mapped to an empty set -- the common case, and callers merge
    the values, so there is nothing to distinguish.

    BATCHED: five queries regardless of how many concerts come in, because
    Home asks this about every concert the viewer holds a ticket on and the
    per-concert version was ~6 statements EACH. Concerts are folded
    separately in memory afterwards -- "secured elsewhere" means elsewhere on
    the same concert, and a batch that leaked one concert's wins into
    another's fold would silence rounds the user still has to enter.

    Constant STATEMENTS, not constant work: every `.in_()` here is as wide as
    the batch, so callers should pass a bounded concert set (a page's worth,
    the way Home does) rather than everything the viewer tracks."""
    if not concert_ids:
        return {}
    cids = sorted(concert_ids)

    rounds_by_concert: dict[int, list[Round]] = {}
    all_round_ids: list[int] = []
    for r in (await session.execute(
        select(Round).where(Round.concert_id.in_(cids))
    )).scalars():
        rounds_by_concert.setdefault(r.concert_id, []).append(r)
        all_round_ids.append(r.id)
    if not all_round_ids:
        return {}

    secured_round_ids = set((await session.execute(
        select(RoundOutcome.round_id).where(
            RoundOutcome.user_id == user_id,
            RoundOutcome.round_id.in_(all_round_ids),
            RoundOutcome.outcome.in_([LotteryOutcome.WON, LotteryOutcome.PAID]),
        )
    )).scalars())
    if not secured_round_ids:
        return {}

    # id, concert AND cancelled in one pass: the fold has to subtract the
    # nights that are not happening, and a second query for them would be the
    # same read twice.
    day_ids_by_concert: dict[int, set[int]] = {}
    cancelled_by_concert: dict[int, set[int]] = {}
    all_day_ids: set[int] = set()
    for did, cid, cancelled in (await session.execute(
        select(ConcertDay.id, ConcertDay.concert_id, ConcertDay.cancelled)
        .where(ConcertDay.concert_id.in_(cids))
    )).all():
        day_ids_by_concert.setdefault(cid, set()).add(did)
        all_day_ids.add(did)
        if cancelled:
            cancelled_by_concert.setdefault(cid, set()).add(did)

    # Both halves of the split are needed: the WON rows say what was secured,
    # while the presence of ANY row is what switches off the
    # no-rows-means-all fallback (see secured_day_ids_by_round).
    won_days: dict[int, set[int]] = {}
    resolved_rounds: set[int] = set()
    for round_id, day_id, result in (await session.execute(
        select(RoundOutcomeDay.round_id, RoundOutcomeDay.day_id, RoundOutcomeDay.result).where(
            RoundOutcomeDay.user_id == user_id,
            RoundOutcomeDay.round_id.in_(sorted(secured_round_ids)),
        )
    )).all():
        resolved_rounds.add(round_id)
        if result is LegResult.WON:
            won_days.setdefault(round_id, set()).add(day_id)

    # One opt-out set for the whole batch: day ids are globally unique and a
    # round only ever subtracts against legs it covers, so a foreign concert's
    # opted-out leg can never reach another concert's fold.
    opted_out_day_ids = set((await session.execute(
        select(LegOptOut.concert_day_id).where(
            LegOptOut.user_id == user_id,
            LegOptOut.concert_day_id.in_(sorted(all_day_ids)),
        )
    )).scalars()) if all_day_ids else set()

    covered: dict[int, set[int]] = {}
    for cid, rounds in rounds_by_concert.items():
        day_ids = day_ids_by_concert.get(cid, set())
        secured_by = _secured_from_outcome_rows(
            rounds, secured_round_ids, day_ids, resolved_rounds, won_days
        )
        if not secured_by:
            continue
        covered[cid] = _covered_from_secured(
            rounds, secured_by, day_ids,
            opted_out_day_ids, cancelled_by_concert.get(cid, set()),
        )
    return covered


async def covered_round_ids(
    session: AsyncSession, user_id: int, concert_id: int
) -> set[int]:
    """One concert's covered rounds -- a thin view of
    `covered_round_ids_by_concert`, which holds the definition. Kept because
    most callers genuinely have one concert in hand; there is still exactly
    one derivation underneath."""
    return (await covered_round_ids_by_concert(
        session, user_id, {concert_id}
    )).get(concert_id, set())


def _covered_from_secured(
    rounds: list[Round], secured_by: dict[int, set[int]], all_day_ids: set[int],
    opted_out_day_ids: set[int] = frozenset(), cancelled_day_ids: set[int] = frozenset(),
) -> set[int]:
    """The fold itself, pure, over data the caller has already loaded.

    It exists so the reminder planner (`_apply_outcome_suppression`, which
    holds all three inputs already) and every read surface (through
    `covered_round_ids`) share ONE definition without either paying for the
    other's queries. They were two copies of this loop until a defect in one
    -- a won round covered by another won round, its payment silenced -- had
    to be fixed in both.

    Opt-out and cancellation COMPOUND with a win rather than sitting beside
    it. "Won Saturday, not going Sunday" leaves nothing a second Sat+Sun round
    could still give this reader, but a literal read of the covered set sees
    Sunday unsecured and keeps nagging -- on Home, on the concert page and in
    the DMs -- for a night they already said they were skipping. So the legs
    they opted out of and the legs that are not happening come OUT of the set
    before the subset check: what is left is what they are genuinely still
    waiting on, and a round securing all of THAT is done asking.

    What the subtraction must NOT do is turn this into "covered by default".
    A round whose every leg is opted out (or cancelled) subtracts down to
    nothing, and nothing is a subset of anything -- so the check alone would
    call it covered on the strength of no win at all, quietly taking over the
    every-leg opt-out pass and the every-leg cancellation rule, which own that
    case and say it in language a reader can act on. Hence the second
    condition: at least one leg this round covers has to be genuinely secured
    elsewhere. "Covered" then keeps meaning what it says on the page -- you
    already hold this night -- and opt-out/cancellation only ever excuse the
    REMAINING legs, never supply the whole answer."""
    covered_ids: set[int] = set()
    for r in rounds:
        if r.kind is RoundKind.UPGRADE:
            continue
        if r.id in secured_by:
            continue  # own outcome WON/PAID: still owed a payment, never covered
        covered = _covered_day_ids(r, all_day_ids)
        if not covered:
            continue
        secured_elsewhere: set[int] = set()
        for other_id, days in secured_by.items():
            if other_id != r.id:
                secured_elsewhere |= days
        if not covered & secured_elsewhere:
            continue  # no leg of this round is secured: not this pass's case
        remaining = covered - opted_out_day_ids - cancelled_day_ids
        if remaining <= secured_elsewhere:
            covered_ids.add(r.id)
    return covered_ids


def _round_fully_opted_out(round_: Round, opted_out_day_ids: set[int]) -> bool:
    """Invariant 8's round rule, as ONE predicate every surface consumes: a
    round suppresses for a user only when it names specific legs (non-empty
    applies_to) AND every one of them is opted out -- the per-user analogue of
    is_round_cancelled's every-leg rule. Empty/None applies_to (the all-legs /
    General convention) is tied to no specific leg, so no set of leg opt-outs
    can cover it; raw applies_to on purpose, never the all-day-ids fallback,
    precisely so that case falls through untouched. Partial opt-out survives
    BY DESIGN, mirroring partial cancellation."""
    return bool(round_.applies_to) and all(
        d in opted_out_day_ids for d in round_.applies_to
    )


async def _apply_outcome_suppression(
    session: AsyncSession, user_id: int, rounds: list[Round], anchor: Anchor
) -> list[Round]:
    """Drop rounds this user's outcomes make irrelevant, before the pure
    planner ever sees them -- same pattern as the cancelled-round
    filtering sync_rule already does. Four passes now:

      * per-leg opt-out -- every leg a round covers has a LegOptOut row for
        this user.
      * cross-round "secured elsewhere" -- every leg a round covers that this
        user is still waiting on (its legs minus the ones they opted out of
        and the ones that are cancelled) is already secured (WON/PAID) by some
        OTHER round on this concert, and this round is not itself one the user
        WON (a won round still owes a payment, whoever else holds the seat).
        Shared with every read surface through `_covered_from_secured`, so the
        DMs and the pages cannot disagree. Note the overlap with the opt-out
        pass above is deliberate and harmless: that pass drops a round whose
        EVERY leg is opted out, this one folds a PARTIAL opt-out into the
        secured check.
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
    # id AND cancelled together: `_covered_from_secured` subtracts the nights
    # that are not happening, and the read side loads the same pair.
    day_rows = (await session.execute(
        select(ConcertDay.id, ConcertDay.cancelled).where(
            ConcertDay.concert_id == concert_id
        )
    )).all()
    all_day_ids = {did for did, _cancelled in day_rows}
    cancelled_day_ids = {did for did, cancelled in day_rows if cancelled}

    # Per-user leg opt-out: this user's LegOptOut rows over this concert's
    # days. A round drops only when EVERY leg it covers is opted out -- the
    # per-user analogue of is_round_cancelled's every-leg (not any-leg)
    # cancellation rule. Kept symmetric on purpose: a two-leg round with
    # only one leg opted out survives, exactly as a two-leg round with only
    # one leg cancelled survives.
    opted_out_day_ids = await user_opted_out_day_ids(session, user_id, all_day_ids)

    # Per-round contribution, so each round's own outcome can be excluded
    # when checking IT for cross-round suppression -- "secured elsewhere"
    # must not let a round's own WON/PAID outcome suppress itself; a round
    # can only be cross-suppressed by OTHER rounds covering its legs. The
    # derivation is shared with every read surface (see
    # secured_day_ids_by_round) so the DMs and the pages agree on which legs
    # a partial win actually secured.
    secured_by = await secured_day_ids_by_round(session, user_id, concert_id)
    covered_ids = _covered_from_secured(
        all_concert_rounds, secured_by, all_day_ids,
        opted_out_day_ids, cancelled_day_ids,
    )

    survivors = []
    for r in rounds:
        # Leg opt-out: suppress only when the round names specific legs AND
        # every one of them is opted out. An all-legs round (empty/None
        # applies_to) is tied to no specific leg, so no set of leg opt-outs
        # can cover it -- never suppressed, mirroring is_round_cancelled
        # leaving empty applies_to alone. Uses raw applies_to, not the
        # all_day_ids fallback the outcome passes use, precisely so the
        # empty case falls through untouched.
        # Leg opt-out: the one rule, see _round_fully_opted_out.
        if _round_fully_opted_out(r, opted_out_day_ids):
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
        elif r.id in covered_ids:
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


async def user_opted_out_day_ids(
    session: AsyncSession, user_id: int, day_ids: Iterable[int]
) -> set[int]:
    """This user's LegOptOut rows among `day_ids`, as a set -- ONE query,
    whatever the surface. Every read surface that asks "is this leg opted
    out?" loads through here, so none of them can invent a second shape for
    the question (the failure mode invariant 8's entry describes: the rule
    existed in exactly one pass and every other surface never asked)."""
    ids = sorted(day_ids)
    if not ids:
        return set()
    return set((await session.execute(
        select(LegOptOut.concert_day_id).where(
            LegOptOut.user_id == user_id,
            LegOptOut.concert_day_id.in_(ids),
        )
    )).scalars())


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


async def unresolved_day_ids(
    session: AsyncSession, user_id: int, round_: Round
) -> list[int]:
    """The legs of this round this user is still waiting on: covered days
    (see _covered_day_ids) minus the ones already resolved by a
    RoundOutcomeDay row, minus the ones they opted out of, minus cancelled
    ones. Ordered by starts_at_utc then id, so the capture surfaces ask about
    legs in the order they happen.

    All three exclusions are the same idea from different directions -- a leg
    with a recorded result, a leg you are not going to, and a leg that is not
    happening are all legs nobody is waiting on an answer for. That is also
    what makes this the terminal check for a LOST day (see
    _settle_lost_days): "empty" means the round has nothing left to resolve."""
    days = list((await session.execute(
        select(ConcertDay).where(ConcertDay.concert_id == round_.concert_id)
    )).scalars())
    covered = _covered_day_ids(round_, {d.id for d in days})
    if not covered:
        return []

    resolved = set((await session.execute(
        select(RoundOutcomeDay.day_id).where(
            RoundOutcomeDay.user_id == user_id,
            RoundOutcomeDay.round_id == round_.id,
        )
    )).scalars())
    opted_out = set((await session.execute(
        select(LegOptOut.concert_day_id).where(
            LegOptOut.user_id == user_id,
            LegOptOut.concert_day_id.in_(sorted(covered)),
        )
    )).scalars())

    remaining = [
        d for d in days
        if d.id in covered
        and not d.cancelled
        and d.id not in resolved
        and d.id not in opted_out
    ]
    remaining.sort(key=lambda d: (d.starts_at_utc, d.id))
    return [d.id for d in remaining]


async def _round_outcome_value(
    session: AsyncSession, user_id: int, round_id: int
) -> LotteryOutcome | None:
    """This user's round-level outcome, or None if they have recorded none."""
    return (await session.execute(
        select(RoundOutcome.outcome).where(
            RoundOutcome.user_id == user_id, RoundOutcome.round_id == round_id
        )
    )).scalar_one_or_none()


async def _materialize_implicit_won_rows(
    session: AsyncSession, user_id: int, round_: Round
) -> None:
    """Write out the day rows a secured round has been asserting implicitly,
    before the first EXPLICIT row makes them unreadable.

    A WON/PAID round with zero day rows means "won every covered leg" (the
    no-rows-means-all convention). But the moment ANY row exists, the secured
    set is read as exactly the WON rows -- so writing a single LOST row would
    silently throw away every other leg's ticket, and could then settle the
    round itself to LOST. Materializing first makes the implicit state
    explicit and unchanged (same secured set, same outcome), and every
    subsequent per-day write edits from there.

    A no-op for rounds that are not secured, or that already have rows."""
    if await _round_outcome_value(session, user_id, round_.id) not in (
        LotteryOutcome.WON, LotteryOutcome.PAID
    ):
        return
    already = (await session.execute(
        select(RoundOutcomeDay.id).where(
            RoundOutcomeDay.user_id == user_id, RoundOutcomeDay.round_id == round_.id
        ).limit(1)
    )).scalar_one_or_none()
    if already is not None:
        return
    all_day_ids = set((await session.execute(
        select(ConcertDay.id).where(ConcertDay.concert_id == round_.concert_id)
    )).scalars())
    for day_id in sorted(_covered_day_ids(round_, all_day_ids)):
        session.add(RoundOutcomeDay(
            user_id=user_id, round_id=round_.id, day_id=day_id, result=LegResult.WON,
        ))
    await session.flush()


async def _won_day_ids(session: AsyncSession, user_id: int, round_id: int) -> set[int]:
    """This user's WON day rows on one round -- the "is any leg of this round
    actually secured" question the LOST settle path turns on."""
    return set((await session.execute(
        select(RoundOutcomeDay.day_id).where(
            RoundOutcomeDay.user_id == user_id,
            RoundOutcomeDay.round_id == round_id,
            RoundOutcomeDay.result == LegResult.WON,
        )
    )).scalars())


async def has_day_results(session: AsyncSession, user_id: int, round_id: int) -> bool:
    """Whether this user is resolving this round LEG BY LEG -- i.e. whether any
    RoundOutcomeDay row exists for it.

    It is the switch that turns the no-rows-means-all fallback off (see
    `_leg_result_for`), so a caller about to record a WHOLE-round answer needs
    it to know whether that answer stands on its own or has to be spelled out
    leg by leg to mean anything."""
    return (await session.execute(
        select(RoundOutcomeDay.id).where(
            RoundOutcomeDay.user_id == user_id, RoundOutcomeDay.round_id == round_id
        ).limit(1)
    )).scalar_one_or_none() is not None


async def round_result_state(
    session: AsyncSession, user_id: int, round_: Round, locale: str,
) -> tuple[tuple[tuple[int, str], ...], bool, LotteryOutcome | None]:
    """`(unresolved legs, any leg secured, round outcome)` for one user --
    the snapshot Discord's progressive result buttons re-derive on EVERY
    press (`bot/views.py`).

    It exists because a DM outlives the state it was built for: those buttons
    are persistent, so the same message can be pressed months later against a
    round already resolved on the web, and the reply has to render what is
    true NOW rather than what the message said. Deriving that from
    `DueReminder.covered_days` would be exactly the mistake -- that tuple is
    leg PRESENCE, fixed when the DM was composed, and knows nothing about
    what has since been recorded or opted out of.

    The no-rows-means-all convention (see `_leg_result_for`) is what the first
    branch handles: a round settled as a WHOLE, with zero day rows, settled
    every leg it covers, so nothing is waiting on an answer -- without it a
    "Won (all)" press would write the round-level outcome and then be asked
    about the very days it just answered for.

    `locale` is explicit rather than read from the ContextVar: the caller is a
    click handler that has just set the clicking user's language, and passing
    it keeps this callable from the scheduler's per-recipient path too."""
    outcome = await _round_outcome_value(session, user_id, round_.id)
    day_rows = (await session.execute(
        select(RoundOutcomeDay.day_id, RoundOutcomeDay.result).where(
            RoundOutcomeDay.user_id == user_id,
            RoundOutcomeDay.round_id == round_.id,
        )
    )).all()
    # A round-level WON/PAID counts as secured whether or not any WON day row
    # spells it out: the row may simply not exist yet (no-rows-means-all), or
    # the only rows may be losses on other legs. Reading this off the rows
    # alone offers "Lost (all)" as the shortcut on a round already won --
    # a button that throws the win away.
    any_won = any(result == LegResult.WON for _day_id, result in day_rows) or outcome in (
        LotteryOutcome.WON, LotteryOutcome.PAID
    )
    if not day_rows and outcome in (
        LotteryOutcome.WON, LotteryOutcome.PAID, LotteryOutcome.LOST,
        LotteryOutcome.NOT_APPLIED,
    ):
        return (), any_won, outcome

    unresolved = await unresolved_day_ids(session, user_id, round_)
    if not unresolved:
        return (), any_won, outcome
    label_by_id = {
        d.id: loc_field(d, "label", locale)
        for d in (await session.execute(
            select(ConcertDay).where(ConcertDay.id.in_(unresolved))
        )).scalars()
    }
    return tuple((did, label_by_id.get(did, "")) for did in unresolved), any_won, outcome


async def _settle_lost_days(
    session: AsyncSession, user_id: int, round_: Round, lost_day_ids: set[int],
    now: datetime,
) -> None:
    """Shared tail of every LOST day write: either the round is done, or it
    is a partial loss inside a round that is still alive.

    Done means no leg of it was won AND nothing is left unresolved -- then the
    round-level LOST is the truth, and record_round_outcome's existing
    semantics (re-sync + whole-round auto-arm) settle it. Otherwise the round
    keeps whatever outcome it has (a partial win stays WON) and only the lost
    LEGS move: re-sync this user's rules so the planner sees the new day rows,
    then arm the next round covering just those legs -- narrowing the arm to
    lost_day_ids is what stops a lost Saturday from arming a round that only
    sells Friday."""
    if not await _won_day_ids(session, user_id, round_.id) and not await unresolved_day_ids(
        session, user_id, round_
    ):
        await record_round_outcome(session, user_id, round_.id, LotteryOutcome.LOST, now)
        return
    await reinstate_user_rules(session, user_id, round_.concert_id, now)
    if lost_day_ids:
        await _auto_arm_next_round(session, user_id, round_, now, day_ids=lost_day_ids)


async def record_round_day_result(
    session: AsyncSession, user_id: int, round_id: int, day_id: int,
    result: LegResult, now: datetime | None = None,
) -> None:
    """One leg of one round resolved for one user -- the per-day sibling of
    record_round_outcome, and (with record_remaining_days_lost) the only
    writer of RoundOutcomeDay (invariant 2).

    A day the round does not cover, or a round that no longer exists, writes
    nothing: ids arrive from Discord custom_ids and form posts, so they are
    re-validated server-side and a forged or stale one simply does nothing --
    the same rule /setup's capture screens follow.

    Both layers stay consistent: a WON leg flips the round outcome to WON
    (a win is a win, whatever the other legs do), while a LOST leg settles
    the round only when nothing is left to wait on (see _settle_lost_days).
    A round already WON or PAID keeps that outcome -- routing a per-day win
    through record_round_outcome there would overwrite PAID back to WON and
    re-arm the payment reminder for a ticket already paid for."""
    now = now or _now()
    round_ = await session.get(Round, round_id)
    if round_ is None:
        return
    all_day_ids = set((await session.execute(
        select(ConcertDay.id).where(ConcertDay.concert_id == round_.concert_id)
    )).scalars())
    if day_id not in _covered_day_ids(round_, all_day_ids):
        return
    # Validation first, materialization second: a forged id must write
    # nothing at all, materialized rows included.
    await _materialize_implicit_won_rows(session, user_id, round_)

    existing = (await session.execute(
        select(RoundOutcomeDay).where(
            RoundOutcomeDay.user_id == user_id,
            RoundOutcomeDay.round_id == round_id,
            RoundOutcomeDay.day_id == day_id,
        )
    )).scalar_one_or_none()
    if existing is None:
        session.add(RoundOutcomeDay(
            user_id=user_id, round_id=round_id, day_id=day_id, result=result,
        ))
    else:
        existing.result = result
    await session.flush()

    if result is LegResult.WON:
        if await _round_outcome_value(session, user_id, round_id) in (
            LotteryOutcome.WON, LotteryOutcome.PAID
        ):
            # Already secured at round level -- only the day layer moved, so
            # skip the outcome write (it would demote PAID) and just re-plan:
            # the secured set changed, and the planner reads it.
            await reinstate_user_rules(session, user_id, round_.concert_id, now)
        else:
            await record_round_outcome(session, user_id, round_id, LotteryOutcome.WON, now)
        return
    await _settle_lost_days(session, user_id, round_, {day_id}, now)


async def record_remaining_days_lost(
    session: AsyncSession, user_id: int, round_id: int, now: datetime | None = None,
) -> None:
    """["Lost the rest"] -- one LOST row for every leg still unresolved, then
    the same settle the last individual LOST would have run. Writing the rows
    in one pass (rather than looping record_round_day_result) keeps the settle
    to a single decision: with everything resolved, the round is LOST unless
    some leg was won.

    Materializing first (see _materialize_implicit_won_rows) is what makes
    this a no-op on a round already won outright -- there is nothing
    unresolved to lose."""
    now = now or _now()
    round_ = await session.get(Round, round_id)
    if round_ is None:
        return
    await _materialize_implicit_won_rows(session, user_id, round_)
    remaining = await unresolved_day_ids(session, user_id, round_)
    for day_id in remaining:
        session.add(RoundOutcomeDay(
            user_id=user_id, round_id=round_id, day_id=day_id, result=LegResult.LOST,
        ))
    await session.flush()
    await _settle_lost_days(session, user_id, round_, set(remaining), now)


async def _next_round_for_leg(
    session: AsyncSession, lost_round: Round, day_ids: set[int] | None = None
) -> Round | None:
    """The next round for the same leg(s) a just-lost round applied to --
    the earliest-opening round (of those with an opens_at_utc set) whose
    applies_to overlaps the lost round's (or is empty -- General rounds
    cover every leg), opening strictly after the lost round's own close
    (falling back to its open time if it has no close).

    `day_ids` narrows the overlap to specific legs, for a per-day loss inside
    a round that was not lost as a whole: only rounds covering THOSE legs are
    candidates. Callers with a whole-round loss pass nothing and get the
    round's own applies_to, unchanged."""
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
    lost_legs: set[int] | None
    if day_ids is not None:
        lost_legs = day_ids
    else:
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
    session: AsyncSession, user_id: int, lost_round: Round, now: datetime | None = None,
    day_ids: set[int] | None = None,
) -> None:
    """After a LOST outcome: find the next round for the same leg and
    auto-create a real ReminderRule for its OPENS anchor, using the
    user's default preset offset if they have one, else immediate.

    `day_ids` narrows which legs count as lost (see _next_round_for_leg) --
    a per-day loss arms only rounds covering the legs actually lost."""
    now = now or _now()
    if lost_round.kind is RoundKind.UPGRADE:
        # Losing an upgrade ends that nested side-campaign -- there is no
        # "next round" to fall back to; the user still holds their base
        # ticket. Auto-arming a later base round here would be wrong.
        return
    next_round = await _next_round_for_leg(session, lost_round, day_ids)
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
    # ...and a concert whose every leg is cancelled contributes NO live rounds
    # at all, whatever any individual round's applies_to says. is_round_cancelled
    # deliberately exempts a General round (empty applies_to) because it is tied
    # to no leg, which is right while one leg still stands and wrong once the
    # whole show is off -- that is a concert-level fact the per-round predicate
    # cannot see, so it is asked here (all_legs_cancelled) instead of widening it.
    # Same mechanism as everything else in this block: fewer candidates, and the
    # "no longer planned -> delete" pass below clears the queue. No second
    # deletion path -- invariant 2's re-planning safety depends on that one pass.
    if rule.round_id is not None:
        round_ = await session.get(Round, rule.round_id)
        if round_ is None:
            live_rounds: list[Round] = []
        else:
            concert_days = list((await session.execute(
                select(ConcertDay).where(ConcertDay.concert_id == round_.concert_id)
            )).scalars())
            cancelled_day_ids = {d.id for d in concert_days if d.cancelled}
            live_rounds = (
                []
                if is_round_cancelled(round_, cancelled_day_ids)
                or all_legs_cancelled(concert_days)
                else [round_]
            )
        days: list[DayInfo] = []
    else:
        rres = await session.execute(select(Round).where(Round.concert_id == rule.concert_id))
        dres = await session.execute(
            select(ConcertDay).where(ConcertDay.concert_id == rule.concert_id)
        )
        all_rounds = list(rres.scalars())
        all_days = list(dres.scalars())
        cancelled_day_ids = {d.id for d in all_days if d.cancelled}
        live_rounds = (
            []
            if all_legs_cancelled(all_days)
            else [r for r in all_rounds if not is_round_cancelled(r, cancelled_day_ids)]
        )
        # Per-user leg opt-out, applied to DAY candidates exactly as the
        # cancelled filter beside it: fewer candidates in, and the existing
        # "no longer planned -> delete" pass clears any queued show-start
        # rows. Without this, an event_start rule planned rows for legs the
        # user said they are skipping -- and set_leg_opt_out's own resync
        # re-planned them (the write that should clear the rows was the one
        # that restored them). Round suppression is the separate
        # _apply_outcome_suppression pass below; this is the day half.
        opted_out_day_ids = await user_opted_out_day_ids(
            session, rule.user_id, [d.id for d in all_days]
        )
        days = [
            _day_info(d) for d in all_days
            if not d.cancelled and d.id not in opted_out_day_ids
        ]

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
    """Call BEFORE sync_concert (which will delete the queue rows this
    inspects -- once sync has run, the loss it warns about is invisible).
    Queues one Notification per user who is about to lose EVERY one of their
    unsent reminders on this concert as a direct result of these legs being
    newly cancelled -- a user with other live legs/rounds to fall back on
    gets nothing. Returns how many notifications were queued.

    WHICH rounds count as lost is a concert-level question, not a per-round
    one. Normally it is the rounds naming a newly-cancelled leg that
    is_round_cancelled now retires; but if these cancellations leave the
    concert with no live leg at all (all_legs_cancelled), EVERY round on it
    is lost, General rounds included -- they name no leg, so the per-round
    test never reaches them, yet sync_rule's concert-level rule deletes their
    queue rows moments later. Miss that and the "does this user still have a
    live reminder here?" probe below finds a row that is already doomed and
    stays silent: the reader loses everything and is never told."""
    if not newly_cancelled_day_ids:
        return 0
    now = now or _now()

    # Post-cancellation leg state -- the caller has already flagged the legs,
    # so this sees the concert as it will be once sync runs.
    all_days = list((await session.execute(
        select(ConcertDay).where(ConcertDay.concert_id == concert_id)
    )).scalars())
    all_cancelled_day_ids = {d.id for d in all_days if d.cancelled}
    rounds = list(
        (await session.execute(select(Round).where(Round.concert_id == concert_id))).scalars()
    )
    concert_is_dead = all_legs_cancelled(all_days)
    affected_round_ids = {
        r.id for r in rounds
        if concert_is_dead
        or (
            set(r.applies_to or []) & newly_cancelled_day_ids
            and is_round_cancelled(r, all_cancelled_day_ids)
        )
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
    # Denormalization sources for delivery_log. Carried on the dataclass rather
    # than re-queried at log time: the scheduler already has this row in hand,
    # and a second SELECT per delivered reminder would undo due_reminders'
    # fixed-round-trip batching.
    concert_id: int | None = None
    day_id: int | None = None
    # round-anchored:
    round_id: int | None = None
    round_label: str | None = None
    round_kind: str | None = None
    outcome: LotteryOutcome | None = None
    anchor_time_utc: datetime | None = None
    url: str | None = None
    # day-anchored:
    day_label: str | None = None
    # (day_id, label) for every LIVE leg a RESULTS-anchored round covers, in
    # performance order -- and only when there are at least two of them, since
    # a one-leg round has nothing to disambiguate. Empty for every other row.
    # bot/messages.py turns a non-empty tuple into per-day result buttons.
    covered_days: tuple[tuple[int, str], ...] = ()
    # The item-sale round this round requires, when it names one: label in
    # the RECIPIENT's language, close time only while that sale is still
    # open (same rule as RoundRow -- a closed sale's time is history).
    requires_label: str | None = None
    requires_closes_at_utc: datetime | None = None


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
    # The item-sale rounds the batch's rounds point at but didn't already
    # load themselves -- one extra bounded SELECT, keeping the fixed-round-
    # trip property (a batch of any size still costs the same number of
    # queries, never one per row).
    required_ids = {
        r.required_item_round_id
        for r in rounds.values()
        if r.required_item_round_id is not None
    } - set(rounds)
    required_rounds: dict[int, Round] = dict(rounds)
    if required_ids:
        required_rounds.update({
            r.id: r for r in (await session.execute(
                select(Round).where(Round.id.in_(required_ids))
            )).scalars()
        })
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

    # The legs behind each multi-leg RESULTS row, for the per-day capture
    # buttons. Gated on RESULTS specifically so a batch that raises no such
    # question costs no extra query at all -- and batched by CONCERT, not by
    # round, so ten rounds on one tour still read their legs once.
    legs_by_round: dict[int, list[ConcertDay]] = {}
    results_round_ids = {
        row.round_id for row in rows
        if row.anchor is Anchor.RESULTS and row.round_id in rounds
    }
    if results_round_ids:
        leg_concert_ids = {rounds[rid].concert_id for rid in results_round_ids}
        legs_by_concert: dict[int, list[ConcertDay]] = {}
        leg_rows = (await session.execute(
            select(ConcertDay)
            .where(ConcertDay.concert_id.in_(leg_concert_ids))
            .order_by(ConcertDay.starts_at_utc, ConcertDay.id)
        )).scalars()
        for leg in leg_rows:
            legs_by_concert.setdefault(leg.concert_id, []).append(leg)
        for rid in results_round_ids:
            round_ = rounds[rid]
            # Cancelled legs are excluded before the count, not after: a
            # two-leg round with one leg cancelled is a one-leg question.
            live = [d for d in legs_by_concert.get(round_.concert_id, []) if not d.cancelled]
            covered = _covered_day_ids(round_, {d.id for d in live})
            if len(covered) >= 2:
                legs_by_round[rid] = [d for d in live if d.id in covered]

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
        req = (
            required_rounds.get(round_.required_item_round_id)
            if round_ and round_.required_item_round_id is not None
            else None
        )
        out.append(
            DueReminder(
                queue_id=row.id,
                discord_id=user.discord_id,
                user_timezone=user.timezone,
                user_language=user.language,
                concert_title=loc_field(concert, "title", user.language),
                anchor=row.anchor,
                fire_at_utc=row.fire_at_utc,
                # Both already in hand: `concert` is the row the title came
                # from, `row.day_id` is on the queue row itself. No new query.
                concert_id=concert.id,
                day_id=row.day_id,
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
                # Same per-RECIPIENT rule as round_label above: a round can
                # also carry a CLOSES row, so the anchor is re-checked here
                # rather than trusted from legs_by_round's membership.
                covered_days=tuple(
                    (d.id, loc_field(d, "label", user.language))
                    for d in legs_by_round.get(row.round_id, ())
                ) if row.anchor is Anchor.RESULTS else (),
                requires_label=(
                    loc_field(req, "label", user.language) if req else None
                ),
                requires_closes_at_utc=(
                    req.closes_at_utc
                    if req and req.closes_at_utc and req.closes_at_utc > now
                    else None
                ),
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
    """Rounds opening or closing within the horizon — powers the bot's
    /upcoming, its only caller. Two exclusions, the same pair
    sync_rule/upcoming_deadlines apply: implicitly-cancelled rounds (every leg
    they apply to is cancelled, is_round_cancelled) and every round on a
    concert with no live leg left (all_legs_cancelled). The second is not
    redundant -- a General round names no leg, so it survives the first
    predicate however dead the show is.

    Not to be confused with ShowDeadlinesButton (bot/views.py), which answers
    a different question -- "everything about THIS concert", asked about one
    concert on purpose -- and so labels cancelled rounds rather than hiding
    them, exactly as the concert page stays reachable for a dead show."""
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
    # Scoped to the concerts actually in play: a round's applies_to only ever
    # names legs of its own concert, so a global day scan buys nothing here.
    days = list((await session.execute(
        select(ConcertDay).where(ConcertDay.concert_id.in_({c.id for c, _ in pairs}))
    )).scalars())
    cancelled_day_ids = {d.id for d in days if d.cancelled}
    days_by_concert: dict[int, list[ConcertDay]] = {}
    for d in days:
        days_by_concert.setdefault(d.concert_id, []).append(d)
    dead_concert_ids = {
        cid for cid, ds in days_by_concert.items() if all_legs_cancelled(ds)
    }
    return [
        (c, r) for c, r in pairs
        if c.id not in dead_concert_ids and not is_round_cancelled(r, cancelled_day_ids)
    ]


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
    RoundKind.GOODS_SALE: N_("Goods sale"),
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
    # Which ConcertDay an EVENT_START row came from, so a per-user caller
    # (my_deadline_rows) can apply the reader's leg opt-outs. None for round
    # rows -- those carry round_id instead.
    day_id: int | None = None


async def upcoming_deadlines(
    session: AsyncSession, now: datetime | None = None, limit: int = 10,
    concert_ids: set[int] | None = None,
) -> list[UpcomingDeadline]:
    """Global (not reminder-rule-scoped, not per-user) chronological
    deadline list for the index page. Reuses is_round_cancelled the same
    way sync_rule/notify_newly_cancelled_legs already do, plus the
    concert-level all_legs_cancelled rule sync_rule now carries.

    TWO callers share this: Home's "Coming up" (via my_upcoming_deadlines)
    and /discover's public "Coming up soon" list -- so the dead-concert
    filtering here is also what keeps that list agreeing with the tile grid
    above it, which discoverable_concert_criterion has always hidden them from.

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
    # A concert whose every leg is cancelled is off, so nothing on it is still
    # a question the reader can answer -- drop it wholesale, beside the
    # per-round is_round_cancelled filtering below. A General round survives
    # that predicate (it is tied to no leg), which is exactly why this
    # concert-level pass is needed and why it does not belong inside it.
    days_by_concert: dict[int, list[ConcertDay]] = {}
    for d in days:
        days_by_concert.setdefault(d.concert_id, []).append(d)
    dead_concert_ids = {
        cid for cid, ds in days_by_concert.items() if all_legs_cancelled(ds)
    }
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
            anchor=Anchor.EVENT_START, at_utc=d.starts_at_utc, day_id=d.id,
        ))

    for r in rounds:
        if r.concert_id in dead_concert_ids or is_round_cancelled(r, cancelled_day_ids):
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
    session: AsyncSession, user_id: int, day_id: int, opted_out: bool,
    now: datetime | None = None,
) -> None:
    """Toggle a per-leg opt-out by row presence: add a row when opting out,
    delete it when opting back in.

    Then re-plan this user's rules for the leg's concert (invariant 8). The
    suppression itself is a read-side pass folded into `sync_rule`, which is
    why this used to write and stop -- but `reminder_queue` is a MATERIALIZED
    outbox (invariant 2), so a read-side rule only governs what the NEXT sync
    decides. Without this, opting out of every leg of a round left its
    already-queued reminders in place and the scheduler duly delivered them:
    the reader said "not going" and kept being reminded, which is precisely
    what invariant 8 forbids.

    It lives here rather than in the two routes that write (the day-result
    capture and the leg opt-out toggle) so neither can forget it, and so a
    third caller inherits it -- the same reason `record_round_outcome` owns
    its own resync.

    The concert lookup stays forgiving: a `day_id` naming no leg has no
    concert to re-plan, and these ids arrive from form posts and Discord
    custom_ids, so it returns quietly rather than raising."""
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

    concert_id = (await session.execute(
        select(ConcertDay.concert_id).where(ConcertDay.id == day_id)
    )).scalar_one_or_none()
    if concert_id is None:
        return
    await reinstate_user_rules(session, user_id, concert_id, now)


@dataclass(frozen=True)
class Rung:
    """One step of a concert's round ladder as this user experienced it.

    `state` is presentation-ready: "lost" | "won" | "paid" | "applied" |
    "skipped" render the recorded outcome, and rounds with no outcome fall
    back to where they sit in time -- "live" (open right now) or "todo" (not
    open yet, or open with nothing recorded and already closed). `detail` is
    the one moment
    worth showing next to the rung: the payment deadline once you have won,
    otherwise the close (falling back to the open). Templates render it with
    fmt_dual; the dataclass stays timezone-agnostic.

    `is_upgrade` carries the round's kind through to `visible_rungs` for the
    one placement rule that turns on it: a won-but-unpaid UPGRADE outranks a
    PAID base ticket, exactly as in `column_for`. `state` alone cannot say so
    -- "won" reads the same either way -- and without it the card can sit in
    "Won -- pay" while showing the paid rung.
    """

    round_id: int
    label: str
    state: str
    detail: datetime | None = None
    is_upgrade: bool = False


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
    # Every leg cancelled: the show is off. Only the BADGE lives here -- what
    # keeps such a card off "Open now" (and off the board entirely without
    # standing) is the has_open_round=False fed to column_for in board_cards,
    # not this flag. The template must never gate placement on it.
    cancelled: bool = False


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
    """The rung's presentation state: a recorded outcome if there is one,
    otherwise where the round sits in time.

    NOT_APPLIED maps to its OWN state, "skipped", rather than to the timing
    fallback. It used to fall through to "todo", which made a round the user
    declined indistinguishable from one that has not opened -- so the capped
    ladder (`visible_rungs`) could spend the card's "what's next" slot on a
    closed round the user had already said no to and hide a genuinely open one
    behind the fold, while the concert page counted that same round under its
    "skipped" fold chip. The name is deliberately `_FOLD_KINDS`' word, so the
    two declutter surfaces say the same thing about one round.

    "skipped" is SETTLED, not pending: `visible_rungs` may use it as the state
    rung (it is a non-"todo" rung, like "lost") but never as the next
    actionable one, and it carries no standing in `_RUNG_STANDING` -- exactly
    as `column_for`'s `_RANK` places nothing for NOT_APPLIED."""
    if outcome is LotteryOutcome.NOT_APPLIED:
        return "skipped"
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

    # This reader's leg opt-outs across the whole board, ONE query (the days
    # are already eager-loaded). Consulted per concert below.
    opted_out_day_ids = await user_opted_out_day_ids(
        session, user_id, [d.id for c in concerts for d in c.days]
    )

    for concert in concerts:
        # Every leg cancelled: the show is off. Three things follow, all of
        # them concert-level facts `is_round_cancelled` cannot see, and all
        # driven by this one flag.
        dead = all_legs_cancelled(concert.days)
        cancelled_day_ids = {d.id for d in concert.days if d.cancelled}
        # (1) A dead concert's card is built from EVERY round, not the
        # is_round_cancelled survivors. On a dead concert every leg-bound
        # round is implicitly cancelled -- and a 先行 lottery normally names
        # its legs -- so filtering here would throw away the very standing the
        # card exists to record, leaving the reader who won a ticket with no
        # card at all. Outcomes and rungs come from the same list on purpose:
        # place a card by an outcome whose round is not on its ladder and the
        # card names a column nothing on it explains (see visible_rungs).
        card_rounds = list(concert.rounds) if dead else [
            r for r in concert.rounds
            if not is_round_cancelled(r, cancelled_day_ids)
            # The per-user analogue of the line above (invariant 8): a round
            # whose every named leg this reader opted out of neither opens the
            # card, nor drives its countdown, nor contributes standing -- and
            # with nothing else placing the card, the card leaves the board,
            # exactly as a leg-cancelled round already behaves. The dead path
            # deliberately keeps every round: a dead card is standing-only,
            # offers no actions and counts down to nothing, so there is
            # nothing for an opt-out to suppress there.
            and not _round_fully_opted_out(r, opted_out_day_ids)
        ]
        # Ladder order: when a round opens, falling back to when it closes.
        # Rounds with neither timestamp sort last, in id order, rather than
        # blowing up the comparison.
        card_rounds.sort(
            key=lambda r: (
                r.opens_at_utc is None and r.closes_at_utc is None,
                r.opens_at_utc or r.closes_at_utc or now,
                r.id,
            )
        )

        # (2) A dead concert has no OPEN round, whatever its rounds' own
        # timestamps say -- a General round survives is_round_cancelled (it is
        # tied to no leg) and would otherwise keep the card in "Open now",
        # inviting an application to a show that is not happening. Fed to
        # column_for, that single substitution carries the whole rule: with no
        # standing column_for returns None and the `column is None` exit below
        # drops the card, matching what Discover already does; with standing
        # the outcome ranks place it in Applied / Won / Secured, because a
        # cancelled show you hold a ticket for is news, not noise. A second
        # skip branch here would be a second rule to keep in sync. The rungs
        # read the same set, so no rung on a dead card can read "open" either.
        open_round_ids = (
            set() if dead else {r.id for r in card_rounds if _round_is_open(r, now)}
        )
        card_outcomes = {r.id: outcomes[r.id] for r in card_rounds if r.id in outcomes}
        column = column_for(
            [
                (outcomes[r.id], r.kind is RoundKind.UPGRADE)
                for r in card_rounds
                if r.id in outcomes
            ],
            has_open_round=bool(open_round_ids),
        )
        if column is None:
            continue

        rungs = [
            Rung(
                round_id=r.id,
                # Copied out of the ORM object, so resolve here (web request
                # -> get_locale()); the template only sees the string.
                label=loc_field(r, "label", locale),
                state=_rung_state(card_outcomes.get(r.id), r.id in open_round_ids),
                is_upgrade=r.kind is RoundKind.UPGRADE,
                detail=(
                    r.payment_deadline_at_utc
                    if card_outcomes.get(r.id) is LotteryOutcome.WON
                    and r.payment_deadline_at_utc is not None
                    else r.closes_at_utc or r.opens_at_utc
                ),
            )
            for r in card_rounds
        ]
        # (3) No countdown on a dead card: a badged card that also read
        # "closes in 3 days" would make the same invitation this rule removes
        # everywhere else. Dropped at the source, so nothing downstream -- the
        # pill, its tone (pill_tone reads None as quiet), the card ordering --
        # can resurrect it.
        next_deadline = None if dead else _next_deadline(card_rounds, now)
        columns[column].append(BoardCard(
            concert=concert,
            column=column,
            rungs=rungs,
            next_deadline=next_deadline,
            outcome_by_round=card_outcomes,
            pill_tone=pill_tone(column, next_deadline, now),
            cancelled=dead,
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


# How much "Coming up" Home shows. It lives here, next to the two functions
# that use it as a default, because TWO callers render the same fragment:
# GET / builds it first, and POST /rounds/{id}/outcome swaps it back in after
# a capture action. If those two disagreed on the count, recording an outcome
# would silently lengthen or shorten the list. One constant, one default, no
# literals at either call site.
#
# It counts CONCERTS, not rows: Home renders `my_deadline_blocks`, one block
# per concert. The name is unchanged because it is still the same limit on the
# same list -- `my_deadline_rows` keeps it as a ROW cap for the internal fetch
# blocks are built from (widened there by ANCHOR_FAN_OUT).
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
    # The legs of this round still waiting on an answer, as (day_id, label)
    # in performance order -- non-empty only when the round covers two or
    # more live legs and its result is knowable, which is exactly when
    # "did you win?" stops having one answer. Empty everywhere else, so the
    # flat won/lost pair stays the single-leg story it always was.
    capture_days: tuple[tuple[int, str], ...] = ()
    # Whether some leg of this round is already won -- what turns the
    # remaining question from "won or lost?" into "lost the rest?".
    any_day_won: bool = False
    # Whether ANY day row exists for this round, won or lost. It is the switch
    # that turns the no-rows-means-all fallback off (see `_leg_result_for`), so
    # a whole-round WON write against a round that has one secures NOTHING --
    # which is why the capture surfaces withdraw "Won (all)" once it is true.
    has_day_results: bool = False
    # The round's close, carried so the block lead rule can ask `_wants_you`
    # without re-loading the round. None for a row with no round behind it.
    closes_at_utc: datetime | None = None


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
    qualifies: bool = True, covered: bool = False, cancelled: bool = False,
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
    defaults True so every ordinary round is unaffected.

    `covered` is True for a round every one of whose legs this viewer already
    secured through some OTHER round (`covered_round_ids`). Both gates shut:
    they are going to every leg it sells, so "I have applied" and "I won" have
    no answer left worth recording. The row itself still renders -- the
    concert page shows the whole campaign, quietly.

    `cancelled` is True when the CONCERT is off entirely (`all_legs_cancelled`
    -- every leg it has is cancelled). It shuts both gates for the same reason
    `covered` does and takes the same shape deliberately: one input, resolved
    once by the caller, rather than a second rule each surface has to remember.
    It is a concert-level fact and cannot be re-derived from `round_`: a
    General round names no leg, so `is_round_cancelled` rightly still calls it
    live, and it is exactly the round that would otherwise keep offering "I
    have applied" on a show that is not happening -- an answer
    `record_round_outcome` would never let the reader take back."""
    if covered or cancelled:
        return False, False
    can_capture = round_ is not None and _round_has_opened(round_, now) and qualifies
    moment = _result_moment(round_) if round_ is not None else None
    can_report_result = (
        can_capture
        and outcome is LotteryOutcome.APPLIED
        and (moment is None or moment <= now)
    )
    return can_capture, can_report_result


def _can_resolve_days(
    round_: Round, outcome: LotteryOutcome | None, now: datetime,
    unresolved: list[int], covered_live: set[int],
) -> bool:
    """Should this row ask about legs one at a time instead of the round as a
    whole? Four conditions, all of them about the round rather than the
    reader's screen, which is why they live here and not in a template:

      * it covers two or more LIVE legs -- one leg has only one answer, and
        the flat "I won"/"I lost" pair already is that answer;
      * the reader is in it (APPLIED) or partway through it (WON) -- there is
        nothing per-leg to say about a round you skipped or already lost;
      * its result is knowable (`_result_moment` unset or passed), the same
        rule `capture_gates` applies to the flat pair;
      * something is actually left unresolved.

    `covered_live` is the round's covered legs minus the cancelled ones. It is
    a parameter rather than derived here because a Round alone cannot answer
    it: the all-legs convention (empty `applies_to`) means "every leg of the
    concert", which only the caller's day list knows."""
    if len(covered_live) < 2:
        return False
    if outcome not in (LotteryOutcome.APPLIED, LotteryOutcome.WON):
        return False
    moment = _result_moment(round_)
    if moment is not None and moment > now:
        return False
    return bool(unresolved)


async def _day_capture_context(
    session: AsyncSession, user_id: int | None, round_: Round,
    outcome: LotteryOutcome | None, now: datetime, days: Sequence[ConcertDay],
    locale: str,
) -> tuple[tuple[tuple[int, str], ...], bool]:
    """`(capture_days, any_day_won)` for one round and one viewer -- the one
    place both capture surfaces (Home's rows, the concert page's rows) resolve
    them, so they cannot start offering different legs.

    `_can_resolve_days` stays the single definition of the rule; the same
    conditions appear once more above it here, in the order that needs no
    query first, purely so the common round -- nobody applied, or the result
    is not out -- reads no rows at all. Only the handful of rounds a viewer is
    actually partway through cost a query.

    A round secured with NO day rows is the no-rows-means-all whole-round win
    (§A of the design): every covered leg was won, so nothing is unresolved
    and the row moves on to the payment question rather than asking about
    days it has already been told about."""
    if user_id is None or outcome not in (LotteryOutcome.APPLIED, LotteryOutcome.WON):
        return (), False
    covered_live = _covered_day_ids(round_, {d.id for d in days}) - {
        d.id for d in days if d.cancelled
    }
    if len(covered_live) < 2:
        return (), False
    moment = _result_moment(round_)
    if moment is not None and moment > now:
        return (), False

    any_day_won = bool(await _won_day_ids(session, user_id, round_.id))
    if outcome is LotteryOutcome.WON and not any_day_won:
        return (), False
    unresolved = await unresolved_day_ids(session, user_id, round_)
    if not _can_resolve_days(round_, outcome, now, unresolved, covered_live):
        return (), any_day_won
    label_by_id = {d.id: loc_field(d, "label", locale) for d in days}
    return tuple((did, label_by_id.get(did, "")) for did in unresolved), any_day_won


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

    # This reader's leg opt-outs across every concert on show -- ONE query.
    # Two row shapes consult it below: an EVENT_START row suppresses when its
    # own day is opted out, and a round row suppresses when the round's every
    # named leg is (_round_fully_opted_out). Partial opt-outs survive, same
    # as everywhere else.
    opted_out_ids = await user_opted_out_day_ids(
        session, user_id,
        [day.id for c in concerts.values() for day in c.days],
    )

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

    # "Stop asking about this": rounds every one of whose legs this viewer
    # already secured through some OTHER round. The reminder planner has
    # always dropped them (_apply_outcome_suppression's cross-round pass), so
    # resolving them here is what stops Coming up and the DM stream
    # disagreeing about what is still worth asking.
    #
    # Only a concert where this user holds a ticket can produce one, and that
    # is ONE query for the whole row set -- Home is the hottest page in the
    # app, and the common reader (nothing secured anywhere) must not pay a
    # per-concert derivation to be told "nothing is covered". The derivation
    # itself is then batched over that whole set: it used to run once per
    # secured concert, ~6 statements each, which is what made Home's query
    # count scale with how much of the page the viewer had standing on.
    secured_concert_ids = set((await session.execute(
        select(Round.concert_id)
        .join(RoundOutcome, RoundOutcome.round_id == Round.id)
        .where(
            RoundOutcome.user_id == user_id,
            Round.concert_id.in_({c.id for c in concerts.values()}),
            RoundOutcome.outcome.in_([LotteryOutcome.WON, LotteryOutcome.PAID]),
        )
    )).scalars()) if concerts else set()
    covered_ids: set[int] = set()
    for ids in (await covered_round_ids_by_concert(
        session, user_id, secured_concert_ids
    )).values():
        covered_ids |= ids

    # Which of these rounds this viewer is resolving LEG BY LEG. One batched
    # query for the whole row set, the same shape as `outcomes` above -- never
    # one per row, and never per (round, anchor) pair, since a single round
    # emits several rows and they must all agree.
    rounds_with_day_rows: set[int] = set((await session.execute(
        select(RoundOutcomeDay.round_id).where(
            RoundOutcomeDay.user_id == user_id,
            RoundOutcomeDay.round_id.in_(round_ids),
        ).distinct()
    )).scalars()) if round_ids else set()

    locale = get_locale()
    day_capture: dict[int, tuple[tuple[tuple[int, str], ...], bool]] = {}

    rows = []
    for d in deadlines:
        if d.round_id is not None and d.round_id in covered_ids:
            continue
        if d.day_id is not None and d.day_id in opted_out_ids:
            continue  # the show itself, on a leg this reader said they are skipping
        concert = concerts.get(d.event_id)
        live_days = sorted(
            (day for day in concert.days if not day.cancelled), key=lambda day: day.starts_at_utc
        ) if concert else []
        venue_tags = [
            loc_field(t, "name", locale)
            for t in concert.tags if t.kind is TagKind.VENUE
        ] if concert else []
        round_ = rounds.get(d.round_id) if d.round_id is not None else None
        if round_ is not None and _round_fully_opted_out(round_, opted_out_ids):
            continue
        outcome = outcomes.get(d.round_id) if d.round_id is not None else None
        is_upgrade = round_ is not None and round_.kind is RoundKind.UPGRADE
        if is_upgrade and round_.id not in eligible_upgrade_ids:
            continue  # drop rows for an upgrade this viewer cannot enter
        can_capture, can_report_result = capture_gates(
            round_, outcome, now,
            qualifies=(not is_upgrade) or round_.id in eligible_upgrade_ids,
        )
        # One round can produce several rows (one per future anchor), so the
        # per-day work list is resolved once per ROUND, not once per row.
        if round_ is not None and concert is not None:
            if round_.id not in day_capture:
                day_capture[round_.id] = await _day_capture_context(
                    session, user_id, round_, outcome, now, concert.days, locale
                )
            capture_days, any_day_won = day_capture[round_.id]
        else:
            capture_days, any_day_won = (), False
        rows.append(DeadlineRow(
            deadline=d,
            outcome=outcome,
            is_upgrade=is_upgrade,
            can_capture=can_capture,
            can_report_result=can_report_result,
            capture_days=capture_days,
            any_day_won=any_day_won,
            has_day_results=d.round_id in rounds_with_day_rows,
            closes_at_utc=round_.closes_at_utc if round_ is not None else None,
            # Same display rule as the tile macro: >1 venue tag collapses to
            # "Multiple", one wins outright, no venue tag means no venue.
            venue=(
                _("Multiple") if len(venue_tags) > 1
                else (venue_tags[0] if venue_tags else None)
            ),
            starts_at_utc=live_days[0].starts_at_utc if live_days else None,
        ))
    return rows


# How many anchor rows per concert the block layer's internal fetch allows
# for, and how many blocks Home renders before its page-level fold.
ANCHOR_FAN_OUT = 6
VISIBLE_BLOCKS = 6


@dataclass(frozen=True)
class ConcertBlock:
    """One concert's slice of "Coming up": the round that wants this reader
    first, plus the rest folded behind it. Built ON my_deadline_rows, never
    beside it -- the per-row decoration (gates, outcome, venue, covered and
    upgrade filtering) is exactly what a member line needs."""

    event_id: str
    concert_title: str
    venue: str | None
    starts_at_utc: datetime | None
    lead: DeadlineRow
    others: tuple[DeadlineRow, ...] = ()


async def my_deadline_blocks(
    session: AsyncSession,
    user_id: int,
    now: datetime | None = None,
    limit: int = DEADLINE_ROWS_LIMIT,
    concert_ids: set[int] | None = None,
) -> list[ConcertBlock]:
    """Home's "Coming up", grouped: one block per concert, capped at `limit`
    CONCERTS (not rows).

    Two collapses. Per ROUND: upcoming_deadlines emits one row per future
    anchor in chronological order, so keeping the FIRST row per round id is
    exactly the moment the concert page's _primary_anchor picks -- the two
    surfaces agree by construction rather than by a second rule. Per
    CONCERT: the remaining rows become one block.

    The internal fetch is `limit * ANCHOR_FAN_OUT` rows because
    my_upcoming_deadlines truncates BEFORE decoration: ten anchor rows can
    be two concerts, so grouping a limit-sized fetch would under-fill. The
    window bounds work; it is not a promise, exactly as today's truncation
    is not.
    """
    now = now or _now()
    rows = await my_deadline_rows(
        session, user_id, now=now, limit=limit * ANCHOR_FAN_OUT,
        concert_ids=concert_ids,
    )

    seen_rounds: set[int] = set()
    by_event: dict[str, list[DeadlineRow]] = {}
    for row in rows:
        round_id = row.deadline.round_id
        if round_id is not None:
            if round_id in seen_rounds:
                continue  # a later anchor of a round already represented
            seen_rounds.add(round_id)
        # A row with no round id is the show itself (one per leg): nothing to
        # collapse onto, so every one survives as its own member line.
        by_event.setdefault(row.deadline.event_id, []).append(row)

    blocks: list[ConcertBlock] = []
    for event_id, members in by_event.items():
        ordered = sorted(members, key=lambda r: (
            not _wants_you(r.outcome, r.can_capture, r.closes_at_utc, now),
            r.deadline.at_utc,
        ))
        lead = ordered[0]
        others = sorted(ordered[1:], key=lambda r: r.deadline.at_utc)
        blocks.append(ConcertBlock(
            event_id=event_id,
            concert_title=lead.deadline.concert_title,
            venue=lead.venue,
            starts_at_utc=lead.starts_at_utc,
            lead=lead,
            others=tuple(others),
        ))
    blocks.sort(key=lambda b: (b.lead.deadline.at_utc, b.event_id))
    return blocks[:limit]


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
    "Multiple", one wins, no venue tag means no venue."""
    venue_tags = [t for t in concert.tags if t.kind is TagKind.VENUE]
    if len(venue_tags) > 1:
        return _("Multiple")
    if venue_tags:
        return loc_field(venue_tags[0], "name", get_locale())
    return None


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
    upcoming-filter instead of re-deriving it three ways.

    A concert whose every leg is cancelled is not upcoming, whatever its
    round anchors still say: `is_round_cancelled` deliberately exempts a
    General round (it names no leg), so a dead concert kept a live round with
    a future close and rode that into all three screens -- screen 2 offering
    to record an APPLIED that `record_round_outcome` would never let the
    reader take back. Asked once here, through the same `all_legs_cancelled`
    the rest of the branch uses, so the flow keeps ONE definition of upcoming
    rather than teaching the applications pass a rule the tiles do not know."""
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
        if all_legs_cancelled(c.days):
            continue
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

    Deliberately concert-blind: whether the concert itself is off (every leg
    cancelled) is filtered UPSTREAM, in `_tracked_upcoming_concerts`, so any
    caller reaching this predicate must come through there -- call it over a
    raw round set and a dead concert's General round starts asking again.

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
        # Covered rounds ask nothing: every leg they sell is already secured
        # through another round, so "did you apply?" has no useful answer.
        # Only concerts where the user actually holds something can produce
        # one, and the derivation shorts out on the empty case after a query
        # or two -- checking first keeps /setup from running it over every
        # tracked concert.
        covered: set[int] = set()
        if any(
            outcomes.get(r.id) in (LotteryOutcome.WON, LotteryOutcome.PAID) for r in c.rounds
        ):
            covered = await covered_round_ids(session, user_id, c.id)
        for r in c.rounds:
            if is_round_cancelled(r, cancelled_day_ids):
                continue
            if r.id in covered:
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

    A row is per (round, LEG): the same round renders under each leg it
    applies to, and `leg_result` is that leg's own resolution for this viewer
    -- a lottery covering Saturday and Sunday really can come back "won
    Saturday, lost Sunday", and one row for both could only lie about one of
    them. Everything else on the row is a fact about the round, so the per-leg
    copies share it.
    """

    round_: Round
    outcome: LotteryOutcome | None
    can_capture: bool
    can_report_result: bool
    primary_anchor: Anchor | None = None
    primary_at_utc: datetime | None = None
    # Every leg this round covers is already secured through another round:
    # it renders quietly, with both capture gates shut (see capture_gates).
    covered: bool = False
    # The whole CONCERT is off -- every leg cancelled (`all_legs_cancelled`).
    # Carried on the row, resolved once per concert, so the two consumers that
    # need it (the gates above and `_needs_you`'s veto below) read one input
    # rather than each asking the predicate again. Deliberately not folded into
    # `covered`: that word is a fact about this reader's other tickets and the
    # fold chips say so out loud, while this is a fact about the world.
    concert_cancelled: bool = False
    # This LEG's resolution for this viewer, or None when they have no
    # standing on it yet.
    leg_result: LegResult | None = None
    # The round's still-unresolved legs, whether one is already won, and
    # whether the round has any day row at all -- same meanings as on
    # DeadlineRow, so the shared capture macro reads one vocabulary.
    capture_days: tuple[tuple[int, str], ...] = ()
    any_day_won: bool = False
    has_day_results: bool = False
    # `upgrade_locked` is True for an UPGRADE round a signed-in viewer is NOT
    # eligible for: the page shows a "Requires a ticket from ..." line naming
    # `qualifier_labels` instead of capture buttons they cannot honestly press.
    # An eligible viewer (and a signed-out one) sees the normal capture row.
    upgrade_locked: bool = False
    qualifier_labels: tuple[str, ...] = ()
    # The item-sale round this round requires (display only): its
    # viewer-locale label, and its close time WHILE that sale is still open
    # (the actionable half -- "you still need to buy this, sale ends 6/15";
    # a closed sale's time is history and is dropped here, not in the
    # template, because round timing is not presentation).
    requires_label: str | None = None
    requires_closes_at_utc: datetime | None = None
    # The reverse line on an item-sale round: the rounds that require it.
    needed_for_labels: tuple[str, ...] = ()
    # Every leg this round names is opted out by this viewer (invariant 8's
    # round rule, _round_fully_opted_out) -- a round-level fact, identical on
    # each per-leg copy. It vetoes "Next for you" (_needs_you) and the
    # catch-up dialog, and NOTHING else: the rows keep rendering with their
    # gates open, because the concert page shows the whole campaign in
    # context and is where you opt back in, and an opt-out never hides the
    # record (a RoundOutcome survives it).
    opted_out: bool = False


@dataclass(frozen=True)
class LegRounds:
    """One leg and the rounds that apply to it, each as its own RoundRow.
    Cancelled legs get a group like any other -- invariant 2 keeps the row
    alive and the page dims it rather than hiding it, so dropping it here
    would lose its rounds.

    `rounds` is the FULL set and stays that way: `concert_next_moment` picks
    the header strip's round out of it, and the strip must still be able to
    name a round the body has folded. `visible`/`folded` are the presentation
    split (`_split_leg_rounds`) laid ALONGSIDE it, and they partition it
    exactly -- adding a third bucket here, or filtering `rounds` itself, is
    what would put the header and the body out of step.

    `fold_counts` is the composition of `folded` as ordered
    ("lost"|"skipped"|"covered"|"upcoming", n) pairs, zero counts dropped. The
    chips explain PART of the fold: the summary's own number is `len(folded)`,
    and a folded round matching no category (a round that opened, closed and
    was never recorded) is counted in none of them.
    """

    day: ConcertDay
    rounds: list[RoundRow]
    visible: tuple[RoundRow, ...] = ()
    folded: tuple[RoundRow, ...] = ()
    fold_counts: tuple[tuple[str, int], ...] = ()


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
    leg, in date order, cancelled legs included.

    Every round renders under EACH live leg it applies to -- including the
    ones that apply to all of them. That is a deliberate reversal of the old
    separate "all legs" section (which the owner had to cross-reference to
    read one leg's story), and per-leg outcomes are what make it truthful: a
    round covering Saturday and Sunday can come back won on one and lost on
    the other, and only a per-leg row can say so. A two-leg concert whose
    rounds all cover both legs therefore shows each round twice; each leg
    reads as a complete story, which is the trade taken.

    Which legs a round names: the ids in its `applies_to` that still exist,
    or -- empty/None being the all-legs convention -- every LIVE leg. A round
    naming only cancelled legs stays under those legs (it is still a fact
    about them); an all-legs round on a wholly cancelled concert falls back to
    every leg rather than vanishing.

    The second returned list is the fallback for a concert with NO legs at
    all, where there is no group to put a round under; it is empty for every
    concert that has any.

    `user_id` is None for a caller with no standing to show; the rows still
    render, just with `outcome`/`leg_result` None throughout. Outcomes and day
    results each load in ONE query for the whole concert, not one per round.
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

    opted_out_day_ids = (
        await user_opted_out_day_ids(session, user_id, [d.id for d in days])
        if user_id is not None else set()
    )

    outcomes: dict[int, LotteryOutcome] = {}
    # (round_id, day_id) -> this viewer's resolution of that leg. One query
    # for the whole concert; a round absent from it entirely falls back to the
    # no-rows-means-all convention below.
    day_results: dict[tuple[int, int], LegResult] = {}
    covered_ids: set[int] = set()
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
        day_results = {
            (rid, did): result
            for rid, did, result in (await session.execute(
                select(RoundOutcomeDay.round_id, RoundOutcomeDay.day_id, RoundOutcomeDay.result)
                .where(
                    RoundOutcomeDay.user_id == user_id,
                    RoundOutcomeDay.round_id.in_([r.id for r in rounds]),
                )
            )).all()
        }
        covered_ids = await covered_round_ids(session, user_id, concert.id)

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

    rounds_by_id = {r.id: r for r in rounds}
    # round id -> labels of the rounds that require its item, insertion order.
    needed_for: dict[int, list[str]] = {}
    for r in rounds:
        if r.required_item_round_id in rounds_by_id:
            needed_for.setdefault(r.required_item_round_id, []).append(
                label_by_id[r.id]
            )

    day_ids = {d.id for d in days}
    live_leg_ids = {d.id for d in days if not d.cancelled}
    # Asked ONCE for the whole concert, then carried on every row: the show
    # being off shuts each round's capture gates and vetoes the page's "Next
    # for you" pick, and both must read the same answer.
    concert_cancelled = all_legs_cancelled(days)
    # Which rounds are being resolved leg by leg -- the switch that turns off
    # the no-rows-means-all fallback (see _leg_result_for).
    rounds_with_day_rows = {rid for rid, _did in day_results}
    by_leg: dict[int, list[RoundRow]] = {d.id: [] for d in days}
    dateless: list[RoundRow] = []

    for r in rounds:
        outcome = outcomes.get(r.id)
        is_upgrade = r.kind is RoundKind.UPGRADE
        eligible = r.id in eligible_up
        covered = r.id in covered_ids
        # Lock only a signed-in ineligible viewer out of an upgrade round --
        # signed out (user_id None) there is no eligibility to judge, so the
        # round renders like any other.
        upgrade_locked = is_upgrade and user_id is not None and not eligible
        can_capture, can_report_result = capture_gates(
            r, outcome, now, qualifies=(not is_upgrade) or eligible, covered=covered,
            cancelled=concert_cancelled,
        )
        capture_days, any_day_won = await _day_capture_context(
            session, user_id, r, outcome, now, days, locale
        )
        anchor, at_utc = _primary_anchor(r, now)
        requires_target = (
            rounds_by_id.get(r.required_item_round_id) if r.required_item_round_id else None
        )
        row = RoundRow(
            round_=r, outcome=outcome,
            can_capture=can_capture, can_report_result=can_report_result,
            primary_anchor=anchor, primary_at_utc=at_utc,
            covered=covered, concert_cancelled=concert_cancelled,
            capture_days=capture_days, any_day_won=any_day_won,
            has_day_results=r.id in rounds_with_day_rows,
            upgrade_locked=upgrade_locked,
            qualifier_labels=tuple(
                label_by_id[q] for q in qualifiers_by_round.get(r.id, []) if q in label_by_id
            ),
            requires_label=label_by_id[requires_target.id] if requires_target else None,
            requires_closes_at_utc=(
                requires_target.closes_at_utc
                if requires_target
                and requires_target.closes_at_utc
                and requires_target.closes_at_utc > now
                else None
            ),
            needed_for_labels=tuple(needed_for.get(r.id, ())),
            opted_out=_round_fully_opted_out(r, opted_out_day_ids),
        )
        if not days:
            dateless.append(row)
            continue
        # Ids for legs that no longer exist are dropped rather than trusted:
        # applies_to is plain JSON with no FK behind it, so a deleted leg can
        # leave one dangling. A round left naming nothing real is treated as
        # the all-legs case rather than disappearing off the page.
        targets = {i for i in (r.applies_to or []) if i in day_ids}
        if not targets:
            targets = live_leg_ids or day_ids
        covered_days = _covered_day_ids(r, day_ids)
        has_day_rows = r.id in rounds_with_day_rows
        for d in days:
            if d.id not in targets:
                continue
            by_leg[d.id].append(replace(
                row,
                leg_result=_leg_result_for(
                    r, d.id, outcome, day_results, covered_days, has_day_rows
                ),
            ))

    groups = []
    for d in days:
        rows = by_leg[d.id]
        visible, folded, counts = _split_leg_rounds(rows, d, now)
        groups.append(LegRounds(
            day=d, rounds=rows, visible=visible, folded=folded, fold_counts=counts,
        ))
    return groups, dateless


# The fold's chip categories, in the order the summary emits them. A row is
# tallied under the FIRST one it matches, so the order is the precedence too.
_FOLD_KINDS = ("lost", "skipped", "covered", "upcoming")


def _split_leg_rounds(
    rows: list[RoundRow], day: ConcertDay, now: datetime,
) -> tuple[tuple[RoundRow, ...], tuple[RoundRow, ...], tuple[tuple[str, int], ...]]:
    """Which of a leg's rounds still bear on this reader, and what the rest are.

    ONE rule (spec `2026-07-27-ladder-declutter-design.md` §A), four clauses --
    a round stays visible on its leg when ANY of them holds:

    1. **It explains your standing**: `leg_result` is WON. The receipt, per the
       owner's decision that a secured leg keeps its winning round as a full
       row -- visible even once PAID and wholly settled, so you never have to
       expand a fold to see which round got you in.
    2. **It still wants something from you**: `_wants_you`, the predicate Home's
       block lead and the "Next for you" strip already share. This is its third
       consumer and it is consumed, never redefined. Note it is `_wants_you` and
       not `_needs_you`: the covered veto is not applied here, because a covered
       round has both capture gates shut and therefore fails `_wants_you`'s
       no-standing arm anyway, while a covered round you APPLIED to is still a
       result you are waiting on and belongs in front of you.
    3. **It is an upgrade you can enter**: eligibility is already derived per
       viewer, and `upgrade_locked` is its inverse. A locked upgrade folds like
       anything else.
    4. **It is the next round you could still enter**: the single soonest round
       on this leg that has not opened, and only while the leg is NOT secured.
       `_wants_you` gates on the round having OPENED, so without this clause the
       upcoming ladder would vanish entirely; on a secured leg every later base
       round is moot, which is what `covered_round_ids` already says.

       UPGRADE rounds are excluded from the candidates outright, for two
       reasons that happen to point the same way. A LOCKED one is not a round
       you "could still enter" at all -- it is precisely what clause 3 exists to
       fold, and because the slot is singular, promoting it would ALSO bury the
       base round the reader can actually enter behind the fold. An ELIGIBLE one
       is already visible on clause 3's own merit, so spending the slot on it
       would fold the next base round for nothing. Either way the slot belongs
       to a base round.

    A CANCELLED leg folds entirely -- nothing on it can bear on anyone. The leg
    itself still renders (invariant 2); only its rounds go behind the fold.

    Folded rows keep the order they came in, which is chronological.
    """
    if day.cancelled:
        return (), tuple(rows), _fold_counts(rows, now)

    secured = any(row.leg_result is LegResult.WON for row in rows)
    # Clause 4's "single soonest". Rounds with no opening time at all are not
    # upcoming -- there is no moment to be the next one -- and UPGRADE rounds
    # are out entirely, eligible or not (see the docstring): clause 3 is the
    # only clause that ever speaks for them, so the slot always goes to a base
    # round the reader could genuinely enter.
    next_open_id: int | None = None
    if not secured:
        unopened = [
            row for row in rows
            if row.round_.kind is not RoundKind.UPGRADE
            and row.round_.opens_at_utc is not None and row.round_.opens_at_utc > now
        ]
        if unopened:
            next_open_id = min(
                unopened, key=lambda row: (row.round_.opens_at_utc, row.round_.id)
            ).round_.id

    visible: list[RoundRow] = []
    folded: list[RoundRow] = []
    for row in rows:
        keeps = (
            row.leg_result is LegResult.WON
            or _wants_you(row.outcome, row.can_capture, row.round_.closes_at_utc, now)
            or (row.round_.kind is RoundKind.UPGRADE and not row.upgrade_locked)
            or row.round_.id == next_open_id
        )
        (visible if keeps else folded).append(row)
    return tuple(visible), tuple(folded), _fold_counts(folded, now)


def _fold_counts(
    folded: Sequence[RoundRow], now: datetime,
) -> tuple[tuple[str, int], ...]:
    """The fold's composition, first match wins per row, `_FOLD_KINDS` order.

    Deliberately partial: a row matching nothing here (a round that opened,
    closed and was never recorded -- a chance simply gone) is counted in no
    chip. The summary's own "+N more rounds" is the total; the chips only say
    what part of it is."""
    counts = dict.fromkeys(_FOLD_KINDS, 0)
    for row in folded:
        if row.leg_result is LegResult.LOST or row.outcome is LotteryOutcome.LOST:
            counts["lost"] += 1
        elif row.outcome is LotteryOutcome.NOT_APPLIED:
            counts["skipped"] += 1
        elif row.covered:
            counts["covered"] += 1
        elif row.round_.opens_at_utc is not None and row.round_.opens_at_utc > now:
            counts["upcoming"] += 1
    return tuple((kind, n) for kind, n in counts.items() if n)


def _leg_result_for(
    round_: Round, day_id: int, outcome: LotteryOutcome | None,
    day_results: dict[tuple[int, int], LegResult], covered_days: set[int],
    has_day_rows: bool,
) -> LegResult | None:
    """How ONE leg of one round turned out for this viewer.

    An explicit `RoundOutcomeDay` row wins outright. Failing that, the
    no-rows-means-all convention is made visible: a round settled as a whole
    -- WON/PAID, or LOST -- with ZERO day rows settled every leg it covers the
    same way, which is the common single-outcome case and every row that
    predates per-day capture.

    `has_day_rows` is what keeps that fallback honest. Once ANY row exists the
    round is being resolved leg by leg, so a leg without one is still
    unresolved -- reading the round's WON off it would claim a ticket for a
    Sunday nobody has heard about yet. A leg the round does not cover, and a
    round with no settled outcome, resolve to None the same way: no standing
    to show."""
    explicit = day_results.get((round_.id, day_id))
    if explicit is not None:
        return explicit
    if has_day_rows or day_id not in covered_days:
        return None
    if outcome in (LotteryOutcome.WON, LotteryOutcome.PAID):
        return LegResult.WON
    if outcome is LotteryOutcome.LOST:
        return LegResult.LOST
    return None


def _wants_you(
    outcome: LotteryOutcome | None, can_capture: bool,
    closes_at_utc: datetime | None, now: datetime,
) -> bool:
    """Does this round still want something from this reader?

    Primitives, not a row type, because TWO row shapes ask it: the concert
    page's RoundRow (via `_needs_you`) and Home's DeadlineRow (via the block
    lead in `my_deadline_blocks`). One rule, so the two surfaces cannot drift
    on what "wants me first" means.

    Two ways it can. You have live standing -- APPLIED (waiting on a result)
    or WON (you owe a payment). Or you have no standing at all and the round
    is open right now, so the decision is still yours to make.

    Everything else is settled and says nothing useful in an urgency panel:
    LOST and NOT_APPLIED are over, PAID is secured, and a round that closed
    without you is a chance already gone. `can_capture` alone is not enough
    for the no-standing case -- it only means the round has OPENED, and a
    long-closed round would otherwise sit at the top of the page forever.
    """
    if outcome in (LotteryOutcome.APPLIED, LotteryOutcome.WON):
        return True
    if outcome is not None:
        return False
    return can_capture and (closes_at_utc is None or closes_at_utc > now)


def _needs_you(row: RoundRow, now: datetime) -> bool:
    """`_wants_you` for a concert-page row, plus that page's own two vetoes.

    A covered round wants nothing whatever its outcome says: every leg it
    sells is already secured elsewhere, and leading the urgency panel with a
    round that offers no buttons is a panel you cannot act on. It is not part
    of the shared rule because Home never meets the case -- `my_deadline_rows`
    drops covered rows outright, so no DeadlineRow can carry one.

    A round on a CANCELLED concert wants nothing either, and for the stronger
    reason: the show is not happening. The gates being shut is not enough on
    its own -- a reader left APPLIED or WON satisfies `_wants_you` on standing
    alone -- and Home never meets this case either, since `upcoming_deadlines`
    drops a dead concert at the source (task 1).

    An opted-out round wants nothing either: the reader said they are
    skipping every leg it names.
    """
    return (
        not row.covered
        and not row.opted_out
        and not row.concert_cancelled
        and _wants_you(row.outcome, row.can_capture, row.round_.closes_at_utc, now)
    )


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
    A concert whose every leg is cancelled always answers None for the same
    reason -- `_needs_you` vetoes each of its rows -- so this is the existing
    contract meeting a new case, not a new one.

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


def all_legs_cancelled(days: Sequence[ConcertDay]) -> bool:
    """True when the concert HAS legs and every one is cancelled.

    The Python twin of `discoverable_concert_criterion` above -- the same
    question ("is this show still happening at all?") asked of days the
    caller already holds, so the personal surfaces can ask it without a
    query. `~has_any_day | has_live_day` inverted: a concert is dead when it
    has days AND none of them is live. A dateless draft has no legs to
    cancel, so it is not dead -- the same exemption the SQL half makes.

    The two forms are pinned to each other by
    `tests/test_discover.py::test_the_predicate_agrees_with_the_discover_criterion`;
    change either and that test is what tells you the halves have drifted.

    This is NOT `is_round_cancelled`'s job and must never be folded into it:
    that predicate answers a per-round question (are all of THIS round's legs
    gone?) and a General round, tied to no leg, is rightly exempt. Whether the
    concert as a whole is off is a concert-level fact it cannot see."""
    return bool(days) and all(d.cancelled for d in days)


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
    "notes_en", "notes_zh",
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


async def assign_tag_slug(session: AsyncSession, tag: Tag) -> str:
    """Give `tag` a unique handle. Call after `session.add(tag)`, before commit.

    The handle is a tag's identity -- names are not unique (owner ruling
    2026-07-29) -- so this is the single place one is minted, and every create
    path goes through it.

    When the name yields no ASCII at all (a Japanese-only tag) the base is the
    KIND, so the de-duplication below numbers it: `artist`, `artist-2`. The spec
    called for `{kind}-{id}`, and that is not buildable -- the id needs a flush,
    a flush needs a non-null slug, and `slug` is NOT NULL, so it would take a
    throwaway placeholder written purely to be overwritten. The row id bought
    nothing anyway: a handle only has to be unique and improvable, and it is
    stable from the moment it is assigned either way.

    De-duplication reads the DB AND the pending session, because a caller may
    add several tags before committing (the catalogue import will) and two
    pending rows must not agree on a handle -- the unique constraint would only
    catch that at flush time, by which point the useful context is gone.

    `no_autoflush` is load-bearing: `tag` is already in `session.new` with a
    null slug, and letting the SELECT autoflush it would hit the NOT NULL
    constraint before this function ever gets to fill the column in.
    """
    base = tag_slug_base(tag.name, tag.name_en) or tag.kind.value
    with session.no_autoflush:
        taken = {
            slug for (slug,) in await session.execute(
                select(Tag.slug).where(Tag.slug.is_not(None))
            )
        }
    taken |= {t.slug for t in session.new if isinstance(t, Tag) and t.slug}
    candidate, suffix = base, 2
    while candidate in taken:
        candidate = f"{base}-{suffix}"
        suffix += 1
    tag.slug = candidate
    return candidate


async def create_tag_row(
    session: AsyncSession,
    *,
    name: str,
    kind: TagKind,
    slug: str | None = None,
    name_en: str | None = None,
    name_zh: str | None = None,
    parent_id: int | None = None,
    voiced_by_tag_id: int | None = None,
    region: str | None = None,
    city: str | None = None,
    city_en: str | None = None,
    city_zh: str | None = None,
    address: str | None = None,
    location_url: str | None = None,
    eventernote_url: str | None = None,
    created_by: int | None = None,
) -> Tag:
    """Build and add a Tag. The ONE place a tag row is constructed.

    `slug=None` means MINT one (`assign_tag_slug` de-duplicates); a value means
    the caller already owns the handle and it is used verbatim. That distinction
    is the whole reason this exists: the three editor routes generate a handle,
    while the catalogue import carries handles in the file and must not have them
    silently renamed. A caller passing a slug is responsible for having
    normalised it -- `domain.tags_yaml.parse_tags` does.

    Does NOT commit, and does NOT notify: creating a tag is not attaching one
    (invariant 4), which is why `quick_create_tag` is silent too.
    """
    tag = Tag(
        name=name.strip(),
        kind=kind,
        slug=slug,
        name_en=name_en,
        name_zh=name_zh,
        parent_id=parent_id,
        # CHARACTER-only, and the caller owns the check that it names an
        # ARTIST -- this constructor validates nothing (the catalogue importer
        # warns-and-skips where the editor route 422s, and both would lose
        # their voice if the rule moved down here).
        voiced_by_tag_id=voiced_by_tag_id,
        region=region,
        city=city,
        city_en=city_en,
        city_zh=city_zh,
        address=address,
        location_url=location_url,
        eventernote_url=eventernote_url,
        created_by=created_by,
    )
    session.add(tag)
    if slug is None:
        await assign_tag_slug(session, tag)
    return tag


async def would_create_tag_cycle(
    session: AsyncSession, tag_id: int, parent_id: int
) -> bool:
    """Would parenting `tag_id` to `parent_id` close a loop?

    GROUP -> GROUP made loops possible for the first time, and nothing in this
    codebase walks parent_id transitively -- so a cycle would not be noticed
    until something did, and then it would hang rather than fail. The guard
    belongs at the write boundary, which is the only place a loop can be
    created.

    The `seen` set is not belt-and-braces: it terminates the walk on data that
    is ALREADY looped (written before this guard existed, or by a direct DB
    edit), where following parents alone would spin forever.
    """
    if tag_id == parent_id:
        return True
    seen: set[int] = {tag_id}
    cursor: int | None = parent_id
    while cursor is not None:
        if cursor in seen:
            # Reaching tag_id means the proposed parent is BELOW us, so the
            # edge would close a loop. Reaching any other repeat means the
            # table already contains a loop that does not involve us -- not a
            # new cycle, but the reason the walk must stop rather than spin.
            return cursor == tag_id
        seen.add(cursor)
        cursor = await session.scalar(select(Tag.parent_id).where(Tag.id == cursor))
    return False


@dataclass
class TagImportReport:
    """What an import did, for the result page. HANDLES, not ids: the operator
    reads this next to the file they pasted."""

    created: list[str] = field(default_factory=list)
    filled: list[str] = field(default_factory=list)
    resolved: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    # REFUSED, not merely untouched: a kind mismatch. Kept distinct from
    # `unchanged` because "nothing to do" and "I would not touch this" read the
    # same in a count and mean very different things to whoever pasted the file.
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)



async def concert_export_yaml(session: AsyncSession, concert: Concert) -> str:
    """One concert as a draft-vocabulary YAML document.

    Shared by GET /concerts/{event_id}/export.yaml and the admin catalogue
    zip, which must not drift: a restore file that differed from the one an
    editor downloads would be a second format nobody agreed to.

    Loads the legs with their venue_tag eagerly. ConcertDay.venue_tag is
    lazy="raise", so a missed selectinload here is a MissingGreenlet 500
    rather than a slow export. Emits the tags' CANONICAL columns, never
    loc() -- an export is data, and its contents must not change with
    whoever happened to download it.
    """
    await session.refresh(concert, ["days", "rounds", "tags"])
    # The legs, with their venue tags: the export's city/venue/venue_address
    # come off the tag when the leg has one, and ConcertDay.venue_tag is
    # lazy="raise", so the eager load below is load-bearing -- without it every
    # export is a MissingGreenlet 500. A leg with NO venue tag exports no venue.
    days = list((await session.execute(
        select(ConcertDay)
        .where(ConcertDay.concert_id == concert.id)
        .options(selectinload(ConcertDay.venue_tag))
        .order_by(ConcertDay.starts_at_utc, ConcertDay.id)
    )).scalars())
    days_by_id = {d.id: d.label for d in days}
    # The tag's CANONICAL columns, never loc(): an export is data, and its
    # contents must not change with whoever happened to download it.
    yaml_days = [
        YamlDay(
            label=d.label, label_en=d.label_en, label_zh=d.label_zh,
            starts_at_utc=d.starts_at_utc,
            city=d.venue_tag.city if d.venue_tag else None,
            venue=d.venue_tag.name if d.venue_tag else None,
            venue_address=d.venue_tag.address if d.venue_tag else None,
            venue_handle=d.venue_tag.slug if d.venue_tag else None,
            doors_at_utc=d.doors_at_utc,
            # Provenance, carried so an export -> re-import round trip keeps the
            # exact-match the discovery diff depends on. A leg that predates
            # discovery has none and the key is simply not written.
            eventernote_event_id=d.eventernote_event_id,
        )
        for d in days
    ]
    # ja label of every round on this concert, keyed by id -- so the export
    # can name a requires-link by LABEL (a restore has no ids to reuse) the
    # same way applies_to already names a leg by label.
    round_labels_by_id = {r.id: r.label for r in concert.rounds}
    yaml_rounds = [
        YamlRound(
            label=r.label, label_en=r.label_en, label_zh=r.label_zh, kind=r.kind.value,
            applies_to_labels=[days_by_id[d] for d in (r.applies_to or []) if d in days_by_id],
            opens_at_utc=r.opens_at_utc, closes_at_utc=r.closes_at_utc,
            results_at_utc=r.results_at_utc, payment_deadline_at_utc=r.payment_deadline_at_utc,
            url=r.url, notes=r.notes,
            requires_label=(
                round_labels_by_id.get(r.required_item_round_id)
                if r.required_item_round_id else None
            ),
        )
        for r in concert.rounds
    ]

    return concert_to_yaml(
        # So a re-import lands on this exact URL rather than minting a new one.
        event_id=concert.event_id,
        title=concert.title,
        kind=concert.kind.value if concert.kind else None,
        franchises=[t.name for t in concert.tags if t.kind is TagKind.FRANCHISE],
        groups=[t.name for t in concert.tags if t.kind is TagKind.GROUP],
        # CHARACTERS, without which `export.zip` is not a faithful backup of an
        # im@s concert: on a restore the derived seiyuu survives (she is an
        # ARTIST row) but the character is lost, and with her the reason the
        # concert reads the way it does. import_commit has accepted
        # `character_tags` since the kind shipped; only the file could not
        # express one.
        characters=[t.name for t in concert.tags if t.kind is TagKind.CHARACTER],
        artists=[t.name for t in concert.tags if t.kind is TagKind.ARTIST],
        venues=[t.name for t in concert.tags if t.kind is TagKind.VENUE],
        series_handles={
            "franchises": [t.slug for t in concert.tags if t.kind is TagKind.FRANCHISE],
            "groups": [t.slug for t in concert.tags if t.kind is TagKind.GROUP],
            "characters": [t.slug for t in concert.tags if t.kind is TagKind.CHARACTER],
            "artists": [t.slug for t in concert.tags if t.kind is TagKind.ARTIST],
        },
        days=yaml_days, rounds=yaml_rounds, notes=concert.notes,
        title_en=concert.title_en, title_zh=concert.title_zh,
        organizer=concert.organizer, categories=concert.categories,
        notes_en=concert.notes_en, notes_zh=concert.notes_zh,
        eventernote_url=concert.eventernote_url, official_url=concert.official_url,
        source_url=concert.source_url,
        performers=(
            [line.strip() for line in concert.performers_text.splitlines() if line.strip()]
            if concert.performers_text else []
        ),
    )



async def current_tag_exports(session: AsyncSession) -> list[TagExport]:
    """The whole catalogue as TagExport rows, ordered kind then handle.

    ONE builder, shared by the zip export and the import differ. Two would
    drift, and a differ comparing against a slightly different snapshot than the
    export wrote is the sort of bug that only surfaces months later, in a
    restore, when it is least welcome.
    """
    tags = list((await session.execute(
        select(Tag).options(selectinload(Tag.members)).order_by(Tag.kind, Tag.slug)
    )).scalars())
    by_id = {t.id: t for t in tags}
    return [
        TagExport(
            handle=t.slug, name=t.name, kind=t.kind.value,
            name_en=t.name_en, name_zh=t.name_zh,
            parent=by_id[t.parent_id].slug if t.parent_id in by_id else None,
            # The seiyuu's HANDLE, never her id: an id means nothing across a
            # restore into an empty database. `apply_tag_import` runs the same
            # conversion in reverse, in its second pass.
            voiced_by=(
                by_id[t.voiced_by_tag_id].slug if t.voiced_by_tag_id in by_id else None
            ),
            members=tuple(sorted(m.slug for m in t.members)),
            region=t.region, city=t.city, city_en=t.city_en, city_zh=t.city_zh,
            address=t.address, location_url=t.location_url,
            eventernote_url=t.eventernote_url,
        )
        for t in tags
    ]


async def catalogue_export_files(session: AsyncSession) -> list[tuple[str, str]]:
    """(path in zip, text) for the whole catalogue, deterministically ordered.

    CATALOGUE TABLES ONLY -- concerts, days, rounds, qualifiers, tags,
    tag_members. Never a JOIN to a user table, and `created_by` is never
    emitted: nothing to leak beats a filter to get wrong. `users`,
    `web_sessions`, `round_outcomes`, `concert_subscriptions`, `leg_opt_outs`,
    `reminder_rules`, `reminder_queue`, `notifications` and `delivery_log` are
    all personal and none is touched.
    """
    exports = await current_tag_exports(session)
    files = [("tags.yaml", tags_to_yaml(exports)), ("RESTORE.txt", RESTORE_NOTES)]

    concerts = list((await session.execute(
        select(Concert).order_by(Concert.event_id)
    )).scalars())
    for concert in concerts:
        files.append(
            (f"concerts/{concert.event_id}.yaml", await concert_export_yaml(session, concert))
        )
    return files


@dataclass
class ImportChoices:
    """What the operator decided, keyed by (handle, field) and (handle, member).

    Values are the literal strings "mine"/"theirs" and "add"/"remove" and
    nothing else. The browser never sends a VALUE -- the data always comes from
    re-parsing the pasted file -- so a forged form cannot inject anything, only
    choose between two things that were already in front of it.
    """

    fields: dict[tuple[str, str], str] = field(default_factory=dict)
    members: dict[tuple[str, str], str] = field(default_factory=dict)


# The COMPARABLE_FIELDS that hold a HANDLE rather than a value. They compare like
# any other string, but nothing can WRITE one until every tag in the file exists,
# so pass 1 skips them and pass 2 resolves them.
#
# Today the ORM attribute a missing entry would hit does not exist (the columns
# are `parent_id`/`voiced_by_tag_id`), so the setattr would be an inert write to
# an unmapped attribute -- but it would still put the handle in `report.filled`,
# claiming pass 1 wrote something it cannot write, and it would become a real
# corruption the day a relationship of that name is added.
_HANDLE_FIELDS = frozenset({"parent", "voiced_by"})


def _takes_handle(entry: TagPlan, choices: ImportChoices, name: str) -> bool:
    """Does this handle field get written at all?

    A new tag takes everything the file says; an existing one takes a FILL
    automatically (writing into emptiness loses nothing) and a CONFLICT only when
    the operator picked the file's value. No answer means keep mine.
    """
    return (
        entry.is_new
        or name in entry.fills
        or choices.fields.get((entry.handle, name)) == "theirs"
    )


async def apply_tag_import(
    session: AsyncSession,
    plan: ImportPlan,
    choices: ImportChoices,
    created_by: int | None = None,
) -> TagImportReport:
    """Write what the plan says, resolved by the operator's choices.

    EVERY DEFAULT IS THE ONE THAT CHANGES NOTHING: an unanswered conflict keeps
    the catalogue's value, and a member removal happens only when explicitly
    chosen. A truncated or forged form therefore cannot overwrite or delete.

    Two passes, for the same reason the original importer had two: `parent` and
    members are HANDLES, so nothing can resolve until every tag in the file
    exists.

    Writes TagMember rows directly rather than through `attach_tag`, which is
    deliberate -- attach_tag is about CONCERT attachment and carries invariant
    3's expansion with it, and this must touch no concert at all.

    Does not commit; the caller owns the transaction, so a rejected file leaves
    nothing behind.
    """
    report = TagImportReport(warnings=list(plan.warnings))
    by_slug = {
        slug: tag_id for tag_id, slug in await session.execute(select(Tag.id, Tag.slug))
    }

    for entry in plan.tags:
        if entry.kind_mismatch:
            report.skipped.append(entry.handle)
            continue
        tag = entry.incoming
        if entry.is_new:
            row = await create_tag_row(
                session,
                name=tag.name, kind=tag.kind, slug=tag.handle,
                name_en=tag.name_en, name_zh=tag.name_zh,
                region=tag.region, city=tag.city, city_en=tag.city_en,
                city_zh=tag.city_zh, address=tag.address,
                location_url=tag.location_url, eventernote_url=tag.eventernote_url,
                created_by=created_by,
            )
            await session.flush()
            by_slug[tag.handle] = row.id
            report.created.append(entry.handle)
            continue

        row = await session.get(Tag, by_slug[entry.handle])
        touched = False
        for name, value in entry.fills.items():
            if name in _HANDLE_FIELDS:
                continue  # a handle, not a value -- resolved in pass 2
            setattr(row, name, value)
            touched = True
        resolved = False
        for conflict in entry.conflicts:
            if choices.fields.get((entry.handle, conflict.field)) != "theirs":
                continue  # KEEP MINE is the default, and "no answer" means it
            if conflict.field in _HANDLE_FIELDS:
                resolved = True
                continue  # resolved in pass 2
            setattr(row, conflict.field, conflict.incoming)
            resolved = True
        if touched:
            report.filled.append(entry.handle)
        if resolved:
            report.resolved.append(entry.handle)
        if not touched and not resolved and not entry.needs_choice:
            report.unchanged.append(entry.handle)

    # Pass 2: parent, voiced_by and membership, once every tag exists.
    for entry in plan.tags:
        if entry.kind_mismatch or entry.handle not in by_slug:
            continue
        row = await session.get(Tag, by_slug[entry.handle])
        wanted_parent = entry.incoming.parent
        if _takes_handle(entry, choices, "parent") and wanted_parent:
            parent_id = by_slug.get(wanted_parent)
            allowed = ALLOWED_PARENT_KINDS.get(row.kind, ())
            if parent_id is None:
                report.warnings.append(
                    f"{entry.handle}: parent {wanted_parent!r} is in neither the file "
                    f"nor the catalogue -- left without a parent"
                )
            # The SAME table POST /tags enforces, deliberately shared rather
            # than restated: this path kept a franchise-only rule of its own
            # after the editor widened, and a catalogue file therefore could not
            # express a subunit at all.
            elif (await session.get(Tag, parent_id)).kind not in allowed:
                report.warnings.append(
                    f"{entry.handle}: a {row.kind.value} tag cannot have "
                    f"{wanted_parent!r} as a parent -- left without one"
                )
            # GROUP -> GROUP made loops possible for the first time, and this is
            # a write path fed by a hand-editable file, so the guard belongs
            # here as much as on the route.
            elif await would_create_tag_cycle(session, row.id, parent_id):
                report.warnings.append(
                    f"{entry.handle}: parent {wanted_parent!r} would close a loop -- "
                    f"left without a parent"
                )
            else:
                row.parent_id = parent_id

        # Her seiyuu, by handle. The TARGET must be an ARTIST, checked here for
        # the same reason `parent` and `members` are checked three lines either
        # side: a blank column makes this an auto-applied FILL, so a hand-edited
        # `voiced_by: k-arena` would be written with nobody deciding anything --
        # and the next `attach_tag` of that character materialises whatever the
        # id names onto the concert, so a VENUE renders as a performer and its
        # followers get a "new event" DM out of `handle_newly_tagged`.
        #
        # `detach_tag`'s kind guard does NOT cover this: it protects the SOURCE
        # (a non-character carrying voiced_by) and says nothing about the target.
        #
        # Refusing a non-ARTIST also refuses SELF-voicing for free. A character
        # pointed at herself is a real trap: `performer_clusters` puts her in
        # `paired_seiyuu` and filters her out of `entries`, so she vanishes from
        # the Performing panel altogether.
        wanted_voice = entry.incoming.voiced_by
        if _takes_handle(entry, choices, "voiced_by") and wanted_voice:
            voice_id = by_slug.get(wanted_voice)
            if voice_id is None:
                report.warnings.append(
                    f"{entry.handle}: voiced_by {wanted_voice!r} is in neither the file "
                    f"nor the catalogue -- left unvoiced"
                )
            elif (voice := await session.get(Tag, voice_id)).kind is not TagKind.ARTIST:
                report.warnings.append(
                    f"{entry.handle}: voiced_by {wanted_voice!r} is a "
                    f"{voice.kind.value}, and only an artist can voice a character "
                    f"-- left unvoiced"
                )
            else:
                row.voiced_by_tag_id = voice_id

        # A NEW tag simply gets the file's members; an existing one only gets
        # the additions the operator ticked.
        additions = list(entry.incoming.members) if entry.is_new else entry.member_additions
        for member in additions:
            if not entry.is_new and choices.members.get((entry.handle, member)) != "add":
                continue
            member_id = by_slug.get(member)
            if member_id is None:
                report.warnings.append(
                    f"{entry.handle}: member {member!r} is in neither the file nor the "
                    f"catalogue -- that membership dropped"
                )
                continue
            if (await session.get(Tag, member_id)).kind is TagKind.GROUP:
                report.warnings.append(
                    f"{entry.handle}: member {member!r} is a group, and groups do not "
                    f"nest -- dropped"
                )
                continue
            session.add(
                TagMember(group_tag_id=by_slug[entry.handle], member_tag_id=member_id)
            )
        for member in entry.member_removals:
            if choices.members.get((entry.handle, member)) != "remove":
                continue  # NEVER by default -- the one destructive act in here
            member_id = by_slug.get(member)
            if member_id is not None:
                await session.execute(
                    delete(TagMember).where(
                        TagMember.group_tag_id == by_slug[entry.handle],
                        TagMember.member_tag_id == member_id,
                    )
                )
    return report


async def find_tags_by_name_and_kind(
    session: AsyncSession, name: str, kind: TagKind
) -> list[Tag]:
    """EVERY tag of this kind with this name, case-insensitively, oldest first.

    PLURAL because names are not unique and never will be: two performers may
    genuinely share one (owner ruling, 2026-07-29). A name is a hint for a
    human, never an identity -- `slug` is the identity.

    This replaced two single-result lookups, `find_tag_by_name` (name only) and
    `find_tag_by_name_and_kind`, both of which used `scalar_one_or_none` and so
    raised `MultipleResultsFound` the moment a duplicate existed. Neither
    survives: deleting them is what removes the bug class, rather than leaving a
    function that is safe only while the data happens to cooperate. A caller
    wanting "one" must say which one it means -- `[0]` for the oldest.

    Ordered by id so that choice is deterministic.
    """
    from sqlalchemy import func as sa_func

    res = await session.execute(
        select(Tag)
        .where(sa_func.lower(Tag.name) == name.strip().lower(), Tag.kind == kind)
        .order_by(Tag.id)
    )
    return list(res.scalars())


async def group_members(session: AsyncSession, group_tag_id: int) -> list[Tag]:
    res = await session.execute(
        select(Tag)
        .join(TagMember, Tag.id == TagMember.member_tag_id)
        .where(TagMember.group_tag_id == group_tag_id)
        .order_by(Tag.name)
    )
    return list(res.scalars())


@dataclass(frozen=True)
class PerformerEntry:
    """One chip. `seiyuu` is set ONLY when the tag is a CHARACTER and her voice
    actor is ALSO attached to this concert -- the both-ends rule. Otherwise it
    is None and the chip renders plain, which is what makes a lone character
    and a lone artist look identical, deliberately."""

    tag: Tag
    seiyuu: Tag | None = None


@dataclass(frozen=True)
class PerformerCluster:
    """One labelled row of the concert page's Performing panel: a GROUP tag
    and the concert's attached performers belonging to it. `group is None` is
    the trailing cluster of performers in no attached group. `depth` is 1 when
    this group's DIRECT parent is an attached GROUP -- a subunit with no parent
    group present is an ordinary top-level cluster (owner rule)."""

    group: Tag | None
    performers: tuple[PerformerEntry, ...] = ()
    depth: int = 0


async def performer_clusters(
    session: AsyncSession, concert: Concert
) -> list[PerformerCluster]:
    """The concert's attached performers, grouped by their attached GROUP tags.

    CALLER PRECONDITION: `concert.tags` must already be loaded (selectinload
    or a refresh) -- a bare `session.get(Concert, ...)` hands this the very
    MissingGreenlet the rest of the docstring is about.

    DISPLAY ONLY. The input is the materialized `concert_tags` set exactly as
    invariant 3 left it (attach expanded it once, editors pruned it); nothing
    here writes, and a later membership edit still never reaches an existing
    concert -- it only changes which cluster an already-attached artist falls
    into.

    Three things about the shape of this, each deliberate:

    * It is service-side, not a template loop. The relationship a template
      would have to reach for is `Tag.members`, a lazy self-referential m2m,
      and touching that during async template rendering raises
      `MissingGreenlet` -- a 500 this project has shipped once. This function
      never touches the relationship at all: it reads the `tag_members`
      association table directly, in one awaited query, so there is no lazy
      load left to fire.
    * `group_members()` is NOT reused. It is per-group, so a franchise concert
      with several units would issue one query per unit. Membership is read
      here in ONE batched query over `tag_members` for the attached group ids.
    * A performer in two attached groups appears under BOTH (owner decision,
      2026-07-27): the repetition is information -- she really is in both --
      and deduplicating would leave the main group's cluster looking
      incomplete to exactly the reader who came to see it. Do not "optimize"
      it away.

    Two relationships are DRAWN here, and both obey one rule: a relationship
    shows only when BOTH of its ends are attached to this concert.

    * A CHARACTER pairs with her seiyuu into one entry (the split-pill chip)
      only when that seiyuu is attached too, and she is then dropped from the
      standalone list because she is rendered inside the pill. Either end
      alone renders exactly as an ordinary artist does.
    * A GROUP nests under its parent only when the parent is an attached
      GROUP. "Attached GROUP", not "attached tag": a group's parent_id is
      usually a FRANCHISE, and a franchise opens no cluster to nest beneath,
      so asking the looser question would drop every franchise bill's
      performer blocks off the page.

    Both are DERIVED per concert, never stored -- which is also why nothing
    here needs provenance for a seiyuu: she is paired exactly when some
    attached character names her, the same derivation the prune rule and the
    editor already use.
    """
    groups = [t for t in concert.tags if t.kind is TagKind.GROUP]
    people = [
        t for t in concert.tags if t.kind in (TagKind.ARTIST, TagKind.CHARACTER)
    ]
    attached_ids = {t.id for t in concert.tags}
    by_id = {t.id: t for t in concert.tags}

    # Pair each character with her seiyuu, but only when BOTH ends are here.
    # The seiyuu is then dropped from the standalone list: she is rendered
    # inside the split pill. A seiyuu attached in her own right survives this
    # filter and is listed as herself (owner rule) -- and she reaches the
    # trailer for free, because a group's members are CHARACTER tags now, so
    # she is not in members_by_group at all.
    paired_seiyuu: set[int] = {
        t.voiced_by_tag_id for t in people
        if t.kind is TagKind.CHARACTER
        and t.voiced_by_tag_id is not None
        and t.voiced_by_tag_id in attached_ids
    }
    # `people` keeps concert.tags' order (Tag.name) inside every cluster.
    entries = [
        PerformerEntry(
            t,
            by_id[t.voiced_by_tag_id]
            if t.kind is TagKind.CHARACTER and t.voiced_by_tag_id in attached_ids
            else None,
        )
        for t in people
        if t.id not in paired_seiyuu
    ]

    # Solo-artist concerts are common and have nothing to look up.
    if not groups:
        return [PerformerCluster(None, tuple(entries))] if entries else []

    res = await session.execute(
        select(TagMember.group_tag_id, TagMember.member_tag_id).where(
            TagMember.group_tag_id.in_([g.id for g in groups])
        )
    )
    members_by_group: dict[int, set[int]] = {g.id: set() for g in groups}
    for group_tag_id, member_tag_id in res:
        members_by_group[group_tag_id].add(member_tag_id)

    # Parent-first ordering with depth, and NO second query: `concert.tags`
    # already carries every attached group, so a parent is present exactly
    # when its id is among them. A `session.get(Tag, g.parent_id)` here would
    # be one SELECT per unit on the franchise bills this is for.
    group_ids = {g.id for g in groups}
    children: dict[int, list[Tag]] = {}
    roots: list[Tag] = []
    for g in groups:
        if g.parent_id in group_ids:
            children.setdefault(g.parent_id, []).append(g)
        else:
            roots.append(g)

    clusters: list[PerformerCluster] = []
    emitted: set[int] = set()

    def _emit(g: Tag, depth: int) -> None:
        if g.id in emitted:
            return
        emitted.add(g.id)
        clusters.append(
            PerformerCluster(
                g,
                tuple(e for e in entries if e.tag.id in members_by_group[g.id]),
                depth,
            )
        )
        # Depth stays 1 however deep the chain runs: the rail is one indent,
        # not a ladder. Recursing anyway is what keeps a third rung ON the
        # page -- emitting a root's direct children only would drop it.
        for child in children.get(g.id, ()):
            _emit(child, 1)

    for g in roots:
        _emit(g, 0)
    # A parent cycle (A under B under A, or a group that is its own parent)
    # has no root between its members, so the walk above never reaches them.
    # `apply_tag_import` writes parent_id and is not cycle-guarded, so the
    # shape is reachable from a hand-edited catalogue file -- and an attached
    # group must appear on the page whatever its parent says. This is also
    # why no separate self-parent guard is needed above: a 1-cycle is a cycle
    # and lands here like any other. `emitted` makes the sweep a no-op in the
    # ordinary case, and stops the walk from recursing through a cycle.
    for g in groups:
        _emit(g, 0)

    grouped = {mid for ids in members_by_group.values() for mid in ids}
    trailer = tuple(e for e in entries if e.tag.id not in grouped)
    if trailer:
        clusters.append(PerformerCluster(None, trailer))
    return clusters


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
      franchise_families -- [(franchise Tag, [(group Tag, [member Tag, ...], depth),
                            ...]), ...] in franchise name order; groups in name
                            order, each immediately followed by its subunits at
                            depth+1 (see the walk below -- no group is ever
                            omitted, whatever its parent_id says)
      no_franchise_groups-- the same row triples for groups under no franchise
      venue_regions      -- [(region_name, [venue Tag, ...]), ...] alpha, "No region" last
      ungrouped_performers -- ARTIST tags that are no group's member, name order
      summary            -- {concerts, franchises, groups, performers, venues,
                            untranslated}
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

    # ── group rows, with subunits nested under their parent group ──
    # GROUP -> GROUP became legal on 2026-08-01, and until this walk existed
    # the two buckets below ("under this franchise" / "no parent at all")
    # named no bucket for a group parented to a GROUP. A subunit therefore
    # fell out of the chips directory entirely -- and so did every performer
    # whose only membership was in it, since `grouped_member_ids` already
    # counted them as grouped and `ungrouped_performers` skips those. A
    # signed-in non-editor saw neither anywhere on /tags, the table view
    # being editor-only.
    #
    # The walk happens HERE and yields a FLAT, pre-ordered list of
    # (group, members, depth). Not a children map recursed over in the
    # template: a parent cycle would loop forever there, and a cycle is
    # reachable (older rows predate `would_create_tag_cycle`, and nothing
    # walks parent_id transitively to notice). `seen` terminates the walk and
    # `leftovers` below carries the property this fix is actually about --
    # EVERY group renders exactly once, whatever its parent_id says.
    children_of: dict[int, list[Tag]] = {}
    for g in groups:
        parent = by_id.get(g.parent_id) if g.parent_id else None
        if parent is not None and parent.kind is TagKind.GROUP:
            children_of.setdefault(parent.id, []).append(g)
    walked: set[int] = set()

    def group_rows(g: Tag, depth: int = 0) -> list[tuple[Tag, list[Tag], int]]:
        if g.id in walked:
            return []
        walked.add(g.id)
        rows = [(g, members_of.get(g.id, []), depth)]
        for child in children_of.get(g.id, []):
            rows.extend(group_rows(child, depth + 1))
        return rows

    franchise_families = [
        (f, [row for g in groups if g.parent_id == f.id for row in group_rows(g)])
        for f in franchises
    ]
    no_franchise_groups = [
        row for g in groups if g.parent_id is None for row in group_rows(g)
    ]
    # Whatever the walk never reached: a group inside a parent cycle, or one
    # whose parent_id names a tag that is neither a franchise nor a group. It
    # renders at the top of the parentless bucket rather than nowhere at all.
    no_franchise_groups += [
        row for g in groups if g.id not in walked for row in group_rows(g)
    ]

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
        # How many tags still have a hole in their NAME trio -- the backlog,
        # made countable. Deliberately the name only: a VENUE's city trio is
        # all-or-nothing AND optional, so a venue with no city at all is
        # legitimately complete, and folding it in would make one number mean
        # two different things. Same rule the create backstop, the browser
        # guard and the edit-page notice use -- never re-implemented here.
        "untranslated": sum(
            1 for t in tags
            if missing_variants(
                t.name or "", t.name_en or "", t.name_zh or "", mandatory=True,
            )
        ),
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


def match_tag_ids_by_slug(
    slugs: Sequence[str], tags: Sequence[Tag]
) -> tuple[list[int], list[str]]:
    """Resolve HANDLES to ids: (matched ids, unmatched handles).

    EXACT, unlike its by-name sibling, and that is the entire point: a handle
    identifies one tag, so there is no first-tag-wins rule to explain and no
    locale variant to match by accident. Ids come back deduplicated in
    first-mention order; unmatched handles keep their input order so the preview
    can list them verbatim.
    """
    by_slug = {t.slug: t.id for t in tags}
    ids: list[int] = []
    missing: list[str] = []
    for slug in slugs:
        tag_id = by_slug.get(slug)
        if tag_id is None:
            missing.append(slug)
        elif tag_id not in ids:
            ids.append(tag_id)
    return ids, missing


def match_venue_tag_id_by_slug(slug: str | None, venue_tags: Sequence[Tag]) -> int | None:
    """A leg's venue by handle. Exact, for the same reason as above."""
    if not slug:
        return None
    return next((t.id for t in venue_tags if t.slug == slug), None)


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


def match_tag_ids_by_name(
    names: Sequence[str], tags: Sequence[Tag]
) -> tuple[list[int], list[str]]:
    """Resolve draft-supplied tag NAMES to ids: (matched ids, unmatched names).

    The pasted-draft path's counterpart to match_venue_tag_id above, with one
    deliberate difference: it matches name_en and name_zh too, not just the
    canonical column. A draft is written by an agent that read sources in
    whichever language the site used, and every tag name here is resolved
    into a picker the editor immediately SEES (a wrong match is a lit chip
    to un-click, not a silently bound FK) -- the accidental-locale-match
    risk that keeps the venue matcher narrow doesn't apply.

    Same trim reasoning as the neighbor: Python's str.strip() drops U+3000,
    and the comparison stays in Python over the already-loaded tag list so
    SQLite's U+0020-only trim() can never be substituted in.

    Ids come back deduplicated in first-mention order; unmatched names keep
    their input order so the preview can list them verbatim.

    Two more rules the callers depend on:

    - COLLISIONS ARE FIRST-TAG-WINS. A name can legitimately match several
      tags at once (one tag's `name`, another's `name_en`), and the scan
      breaks at the first hit in `tags` order -- so the winner is whichever
      the caller's query listed first, not a "best" match. That is
      deliberate rather than arbitrary: the result is a pre-selected picker
      chip the editor sees and can un-click, so a stable, cheap rule beats
      a scoring one nobody can predict.
    - BLANK NAMES DROP FROM BOTH LISTS. A name that is empty or whitespace
      once trimmed contributes neither an id nor an unmatched entry -- it
      simply vanishes. Drafts routinely carry blank list entries from a
      trailing YAML dash, and surfacing those as "couldn't find ''" in the
      preview would be noise, not information. Callers must therefore not
      assume len(matched) + len(unmatched) == len(names).
    """
    matched: list[int] = []
    unmatched: list[str] = []
    for name in names:
        needle = name.strip().casefold()
        if not needle:
            continue
        for tag in tags:
            if any(
                col and col.strip().casefold() == needle
                for col in (tag.name, tag.name_en, tag.name_zh)
            ):
                if tag.id not in matched:
                    matched.append(tag.id)
                break
        else:
            unmatched.append(name.strip())
    return matched, unmatched


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
        members = await group_members(session, g.id)
        groups_data[g.id] = {
            "name": g.name,
            "franchise": g.parent_id,
            # SPLIT BY KIND, and load-bearing rather than tidy. A GROUP's members
            # may be ARTIST tags, CHARACTER tags or a mix -- the im@s reformat
            # produces the second -- and the picker posts each row into its own
            # field: `members` feeds autoArtists() -> artist_tags,
            # `character_members` feeds autoCharacters() -> character_tags.
            #
            # Unsplit, ticking such a group put CHARACTER ids into artist_tags,
            # `resolve_tags(..., ARTIST)` answered 422 and the concert could not
            # be created at all. Worse, the workaround an editor reaches for
            # after that -- × the auto-added chips -- SUCCEEDED and attached the
            # group alone: the creation form expands with expand=False, so
            # neither the characters nor (via attach_tag's chained step) their
            # seiyuu ever landed, and a follower of the performer was not
            # matched. The loud half and the silent half are one bug.
            #
            # A member of any OTHER kind is dropped rather than defaulted into
            # the artist row: a franchise or venue somehow made a member is not
            # a performer, and offering it as one only reproduces the 422.
            "members": [
                {"id": m.id, "name": m.name} for m in members if m.kind is TagKind.ARTIST
            ],
            "character_members": [
                {"id": m.id, "name": m.name}
                for m in members
                if m.kind is TagKind.CHARACTER
            ],
        }
    # {character id: her seiyuu's ARTIST id}. The picker reads it for ONE thing:
    # keeping a derived seiyuu OUT of the artist row while her character is
    # selected (owner ruling 2026-08-01 -- she is auto-correlated and shown as
    # `cv. xxx`, never offered as a tick). Without it, a seiyuu who is also a
    # direct ARTIST member of a selected group is re-ticked by autoArtists(),
    # posted as artist_tags, and therefore lands in edit_concert's `after_ids`
    # -- so she is never detached when her character goes, which is the prune
    # rule unreachable from the editor all over again.
    character_seiyuu = {
        t.id: t.voiced_by_tag_id
        for t in by_kind.get("character", [])
        if t.voiced_by_tag_id is not None
    }
    tag_names = {t.id: t.name for t in tags}
    # Which tags must show their handle beside their name: ONLY those sharing a
    # (name, kind) with another tag. Two identical chips are unusable, but
    # showing every handle would put noise on the overwhelming majority that do
    # not collide. Decided HERE rather than in the template's JS, because "are
    # these the same tag to a reader" is a question about the data.
    #
    # A parallel map rather than restructuring tag_names, whose {id: name} shape
    # several templates' inline scripts already read -- far smaller blast radius
    # than changing a contract in place.
    by_name_and_kind: dict[tuple[str, str], list[int]] = {}
    for t in tags:
        by_name_and_kind.setdefault((t.name.strip().lower(), t.kind.value), []).append(t.id)
    slug_by_id = {t.id: t.slug for t in tags}
    tag_disambiguators = {
        tag_id: slug_by_id[tag_id]
        for ids in by_name_and_kind.values()
        if len(ids) > 1
        for tag_id in ids
    }
    return {
        "by_kind": by_kind,
        "groups": groups_data,
        "character_seiyuu": character_seiyuu,
        "tag_names": tag_names,
        "tag_disambiguators": tag_disambiguators,
    }


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

    # THE CHAINED STEP. Every character now attached pulls in its seiyuu.
    # Without it a group-credited im@s show materialises characters only, and
    # tracked_concert_ids -- which matches materialised rows -- never matches
    # anyone following the performer. That is the whole feature.
    #
    # Bounded by construction, and NOT the nested-groups rule returning: a
    # seiyuu is an ARTIST, so group -> character -> seiyuu terminates in two
    # steps and cannot recurse.
    #
    # Deliberately NOT gated on `expand`. That flag exists so the creation
    # form's explicit artist list is not overridden; attaching the seiyuu
    # overrides nothing, and gating it would leave concerts made on that form
    # unmatched for her followers.
    seiyuu_ids = {
        t.voiced_by_tag_id for t in added
        if t.kind is TagKind.CHARACTER and t.voiced_by_tag_id is not None
    }
    for seiyuu_id in sorted(seiyuu_ids):
        if not await _is_attached(session, concert_id, seiyuu_id):
            seiyuu = await session.get(Tag, seiyuu_id)
            if seiyuu is not None:
                session.add(ConcertTag(concert_id=concert_id, tag_id=seiyuu.id))
                added.append(seiyuu)

    await session.flush()
    return added


async def detach_tag(
    session: AsyncSession,
    concert_id: int,
    tag_id: int,
    keep_tag_ids: Collection[int] = (),
) -> None:
    """Remove a tag from a concert -- and, for a CHARACTER, her seiyuu with her.

    Owner rule (2026-08-01), with TWO refinements, both load-bearing:

    * the seiyuu goes ONLY IF no other still-attached character shares her. A
      seiyuu can voice two characters on one bill, and detaching her because
      one was pruned would silently drop the other's performer.
    * the seiyuu goes ONLY IF the caller has not said it is keeping her.
      `keep_tag_ids` is that statement, and it is what makes the concert
      editor's detach-then-attach order safe: `edit_concert` computes the
      final tag set up front, so a seiyuu the editor ticked EXPLICITLY while
      unticking her character is in that set and must not be cascaded off.
      Without it she is in `keep_ids & before_ids` -- in NEITHER of the
      route's two diffs -- so nothing puts her back, the first save loses her
      silently, and a second identical save restores her. The set is the
      caller's DESIRED end state, not the current attachment, so it stays a
      statement of intent rather than a read of the row this call is deleting.

      It stayed load-bearing through the 2026-08-01 ruling that a derived
      seiyuu is never PRE-ticked, which was expected to make it inert and did
      not: ticking her is still a gesture the picker offers, and it now means
      something sharper than it used to -- "credit the performer, not the
      character" -- so honouring it matters more, not less. Measured by
      removing the parameter and running the suite; two editor tests fail.

    KNOWN EDGE, accepted rather than solved: concert_tags does not record WHY a
    tag was attached -- group expansion has had that blind spot since it
    shipped -- so a seiyuu who was ALSO there in her own right is removed when
    the character is pruned, and the editor re-adds her. Building provenance to
    fix that would touch every attach path for a rare case. Under the ruling
    that case is contradictory data rather than a supported state (an event
    credits the character OR the performer), so the missing provenance is now
    the DEFINITION of the behaviour: a standalone tick over an attached
    character is accepted, not remembered, and re-reads as derived.
    """
    tag = await session.get(Tag, tag_id)
    await _detach_one(session, concert_id, tag_id)

    if tag is None or tag.kind is not TagKind.CHARACTER or tag.voiced_by_tag_id is None:
        await session.flush()
        return

    if tag.voiced_by_tag_id in keep_tag_ids:
        await session.flush()
        return

    still_needed = await session.scalar(
        select(func.count())
        .select_from(ConcertTag)
        .join(Tag, Tag.id == ConcertTag.tag_id)
        .where(
            ConcertTag.concert_id == concert_id,
            Tag.kind == TagKind.CHARACTER,
            Tag.voiced_by_tag_id == tag.voiced_by_tag_id,
        )
    )
    if not still_needed:
        await _detach_one(session, concert_id, tag.voiced_by_tag_id)
    await session.flush()


async def _detach_one(session: AsyncSession, concert_id: int, tag_id: int) -> None:
    """The single-row delete detach_tag used to be."""
    row = (await session.execute(
        select(ConcertTag).where(
            ConcertTag.concert_id == concert_id, ConcertTag.tag_id == tag_id
        )
    )).scalar_one_or_none()
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

    A DEAD concert (`all_legs_cancelled`) does neither, for anybody: no notice
    is queued and no preset applies. This pipeline fires on TAGGING, not on
    cancelling, which is why it was not on the cancelled-concerts branch's list
    -- and it is reached automatically, since `sync_concert_venue_tags` runs it
    on every venue rollup, so a routine leg edit on a cancelled tour used to DM
    every venue follower a "New event" with an "Apply here" button. Owner
    ruling (2026-07-28): a notice nobody can act on is not worth sending, and
    invisible rules on a dead event are justified only by a revival that may
    never come. This only SKIPS -- no send path is added or rerouted
    (invariant 4). The question is asked ONCE here rather than per subscriber:
    it is a fact about the concert, not about any reader.
    """
    if not new_tags:
        return 0
    # Its own query rather than `concert.days`: callers reach this with the
    # concert in every state (freshly flushed and legless, refreshed, or loaded
    # by event_id with nothing eager), and a lazy load on an unloaded
    # collection mid-request is a MissingGreenlet 500. Same shape
    # `leg_cancelled_context` uses one screen over.
    days = list((await session.execute(
        select(ConcertDay).where(ConcertDay.concert_id == concert.id)
    )).scalars())
    if all_legs_cancelled(days):
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
    session: AsyncSession, limit: int = 100, now: datetime | None = None
) -> list[Notification]:
    """Unsent notices whose hold, if any, has expired.

    `send_after_utc IS NULL` is the common case and means "send now" -- every
    notice in the app except a broadcast leaves it NULL. The IS NULL branch is
    load-bearing rather than defensive: in SQL `NULL <= now` evaluates to NULL,
    not true, so comparing without it would silently stop the entire outbox.
    """
    now = now or _now()
    res = await session.execute(
        select(Notification)
        .where(
            Notification.sent_at_utc.is_(None),
            or_(
                Notification.send_after_utc.is_(None),
                Notification.send_after_utc <= now,
            ),
        )
        .order_by(Notification.created_at)
        .limit(limit)
    )
    return list(res.scalars())


async def mark_notification_sent(session: AsyncSession, notification_id: int) -> None:
    row = await session.get(Notification, notification_id)
    if row is not None:
        row.sent_at_utc = _now()
        await session.flush()


# ── Delivery log ─────────────────────────────────────────────────────────

# The digest reports on deliveries, so logging its own delivery would make the
# next tick report that, forever, once a minute. Only self-reporting kinds
# belong here. A broadcast does NOT: it terminates after one hop (broadcast ->
# logged -> one digest line -> digest delivered -> not logged -> stop), and
# recording it is the point -- whether a remedy reached its recipients,
# FORBIDDEN ones included, is the question the broadcast was sent asking.
UNREPORTED_NOTE_KINDS = frozenset({"delivery_digest"})


async def record_deliveries(
    session: AsyncSession,
    batch_at_utc: datetime,
    reminder_results: list[tuple[DueReminder, DeliveryOutcome]],
    notification_results: list[tuple[Notification, DeliveryOutcome]],
) -> list[DeliveryLog]:
    """Write one delivery_log row per attempted delivery. Returns the rows.

    The rows themselves rather than a count, so tick() can hand them straight
    to queue_delivery_digest without a second SELECT for the batch it just
    wrote.

    Flushes, never commits: the caller owns transaction boundaries, and in
    tick() this runs in its own commit AFTER the delivery bookkeeping is
    already durable.
    """
    rows: list[DeliveryLog] = []

    for item, outcome in reminder_results:
        rows.append(
            DeliveryLog(
                batch_at_utc=batch_at_utc,
                user_id=item.discord_id,
                source=DeliverySource.REMINDER,
                outcome=outcome,
                anchor=item.anchor,
                concert_title=item.concert_title,
                leg_label=item.day_label,
                round_label=item.round_label,
                concert_id=item.concert_id,
                round_id=item.round_id,
                day_id=item.day_id,
                sent_at_utc=batch_at_utc,
            )
        )

    # One batched lookup for the titles, not one per row: a new_event fan-out
    # is exactly the case with many notifications sharing few concerts.
    note_concert_ids = {
        n.concert_id
        for n, _ in notification_results
        if n.concert_id is not None and n.kind not in UNREPORTED_NOTE_KINDS
    }
    titles: dict[int, str] = {}
    if note_concert_ids:
        res = await session.execute(
            select(Concert.id, Concert.title, Concert.title_en).where(
                Concert.id.in_(note_concert_ids)
            )
        )
        # Resolved at the English locale, through loc_field rather than a
        # hand-rolled coalesce, so "empty string counts as unfilled" stays one
        # rule. English because the only readers of this column are the digest
        # and /admin/deliveries, both English-only by design -- a notification
        # embed is localized per recipient, so no single stored string could
        # reproduce "what was sent" anyway.
        titles = {r.id: loc_field(r, "title", "en") for r in res.all()}

    for note, outcome in notification_results:
        if note.kind in UNREPORTED_NOTE_KINDS:
            continue
        rows.append(
            DeliveryLog(
                batch_at_utc=batch_at_utc,
                user_id=note.user_id,
                source=DeliverySource.NOTIFICATION,
                outcome=outcome,
                note_kind=note.kind,
                concert_title=titles.get(note.concert_id) if note.concert_id else None,
                concert_id=note.concert_id,
                sent_at_utc=batch_at_utc,
            )
        )

    if rows:
        session.add_all(rows)
        await session.flush()
    return rows


async def queue_delivery_digest(
    session: AsyncSession, batch_at_utc: datetime, rows: list[DeliveryLog]
) -> int:
    """Queue the admin digest for this batch. Returns admins queued.

    Goes through the notifications outbox rather than a direct DM -- that is
    invariant 4, and it buys retry, ordering and Forbidden handling for free.
    kind="delivery_digest" with concert_id=None falls through
    scheduler.loop._notification_context to the plain-text path, so the send
    code needs no changes. That kind is also in UNREPORTED_NOTE_KINDS, which
    is what stops this digest reporting its own delivery next tick.
    """
    if not rows or not settings.bot_enabled:
        return 0

    body = build_digest(
        [
            DeliveryFact(
                source=r.source,
                outcome=r.outcome,
                user_id=r.user_id,
                concert_title=r.concert_title,
                leg_label=r.leg_label,
                round_label=r.round_label,
                anchor=r.anchor,
                note_kind=r.note_kind,
                concert_id=r.concert_id,
                round_id=r.round_id,
                day_id=r.day_id,
            )
            for r in rows
        ],
        batch_at_utc,
    )
    if not body:
        return 0

    queued = 0
    for admin_id in settings.admin_ids:
        # An admin who has never logged in has no users row, and
        # Notification.user_id is a FK to it -- the same guard
        # evaluate_and_alert needs, and for the same reason.
        if await session.get(User, admin_id) is None:
            await ensure_user(session, admin_id, str(admin_id))
        session.add(Notification(user_id=admin_id, body=body, kind="delivery_digest"))
        queued += 1
    await session.flush()
    return queued


# Matches deploy/backup.sh's S3 lifecycle so the system has ONE retention
# number rather than two that can drift apart.
DELIVERY_LOG_RETENTION_DAYS = 30


async def prune_delivery_log(session: AsyncSession, now: datetime | None = None) -> int:
    """Delete delivery_log rows older than the retention window. Returns rows
    deleted. Flushes, never commits -- the caller owns the transaction."""
    now = now or _now()
    cutoff = now - timedelta(days=DELIVERY_LOG_RETENTION_DAYS)
    res = await session.execute(delete(DeliveryLog).where(DeliveryLog.batch_at_utc < cutoff))
    await session.flush()
    return res.rowcount or 0


@dataclass(frozen=True)
class BatchSummary:
    """One tick's deliveries, aggregated on read. There is no stored count
    anywhere, so these can never disagree with the rows they describe."""

    batch_at_utc: datetime
    sent: int
    users: int
    failed: int


async def delivery_batches(session: AsyncSession, limit: int = 50) -> list[BatchSummary]:
    """Newest first. Capped rather than paginated: the retention window is 30
    days and a batch only exists if it delivered something."""
    res = await session.execute(
        select(
            DeliveryLog.batch_at_utc,
            func.count(DeliveryLog.id),
            func.count(func.distinct(DeliveryLog.user_id)),
            func.sum(case((DeliveryLog.outcome != DeliveryOutcome.SUCCESS.value, 1), else_=0)),
        )
        .group_by(DeliveryLog.batch_at_utc)
        .order_by(DeliveryLog.batch_at_utc.desc())
        .limit(limit)
    )
    return [
        BatchSummary(batch_at_utc=at, sent=total - (failed or 0), users=users, failed=failed or 0)
        for at, total, users, failed in res.all()
    ]


async def delivery_failures(session: AsyncSession, limit: int = 100) -> list[DeliveryLog]:
    """Every non-SUCCESS row in the window, newest first, independent of
    batch. The digest says something broke in the last minute; this says
    whether it has been breaking all week."""
    res = await session.execute(
        select(DeliveryLog)
        .where(DeliveryLog.outcome != DeliveryOutcome.SUCCESS.value)
        .order_by(DeliveryLog.batch_at_utc.desc())
        .limit(limit)
    )
    return list(res.scalars())


async def delivery_batch_rows(
    session: AsyncSession, batch_at_utc: datetime
) -> list[DeliveryLog]:
    res = await session.execute(
        select(DeliveryLog).where(DeliveryLog.batch_at_utc == batch_at_utc).order_by(DeliveryLog.id)
    )
    return list(res.scalars())


# ── Admin broadcast ──────────────────────────────────────────────────────

# Long enough to reread what you sent and see the mistake; short enough that a
# real incident remedy is not uselessly delayed. A constant, not a setting --
# one fewer thing to get wrong at 3am (owner ruling, 2026-07-28).
HOLD_SECONDS = 120
# Discord's hard ceiling is 2000 characters and the localized frame costs some
# of them. Rejected at the boundary rather than truncated on send: a broadcast
# that silently says less than what was approved is the failure this feature
# exists to avoid.
BROADCAST_BODY_MAX = 1900
# Above this many recipients, the admin must type the count to proceed. Keyed
# on SIZE, not mode, so a 400-person explicit list is gated exactly like ALL.
TYPED_CONFIRM_THRESHOLD = 10
_DUPLICATE_WINDOW = timedelta(hours=1)


@dataclass(frozen=True)
class Recipients:
    """`ids` is (discord_id, language) pairs -- the language is needed at queue
    time, because the localized frame is applied per recipient there rather
    than at send time, which is what keeps the scheduler's send code
    unchanged."""

    ids: tuple[tuple[int, str], ...]
    unmatched: tuple[str, ...]


async def resolve_recipients(
    session: AsyncSession, mode: BroadcastMode, param: str | None
) -> Recipients:
    """Resolve a mode + param to a concrete recipient set.

    Every mode is RESOLVED, never derived: the set cannot change between the
    preview an admin approved and the send that executes. Modes that would be
    derived -- everyone tracking a concert, followers of a tag -- were
    rejected for exactly that reason, since the count an admin confirmed
    would then be a lie by the time the send ran.
    """
    if mode is BroadcastMode.ALL:
        res = await session.execute(select(User.discord_id, User.language))
        return Recipients(ids=tuple((i, lang) for i, lang in res.all()), unmatched=())

    if mode is BroadcastMode.BATCH:
        if not param:
            return Recipients(ids=(), unmatched=())
        try:
            batch_at = datetime.fromisoformat(param)
        except ValueError:
            # `mode_param` is a free-text field on the compose form, so a
            # typo'd timestamp is ordinary user error, not a server fault.
            # Reported rather than raised, for the same reason an unmatched
            # EXPLICIT id is: this function stays total, the preview shows
            # "0 recipients" plus the offending text, and neither route needs
            # its own try/except to avoid a 500.
            return Recipients(ids=(), unmatched=(param,))
        res = await session.execute(
            select(User.discord_id, User.language)
            .join(DeliveryLog, DeliveryLog.user_id == User.discord_id)
            .where(DeliveryLog.batch_at_utc == batch_at)
            .distinct()
        )
        return Recipients(ids=tuple((i, lang) for i, lang in res.all()), unmatched=())

    # EXPLICIT: report what did not match rather than dropping it silently.
    # Quietly discarding a mistyped id is how you conclude you messaged
    # someone you did not.
    tokens = [t.strip() for t in (param or "").replace(",", " ").split() if t.strip()]
    wanted: list[int] = []
    unmatched: list[str] = []
    for token in tokens:
        if token.isdigit():
            wanted.append(int(token))
        else:
            unmatched.append(token)
    found: dict[int, str] = {}
    if wanted:
        res = await session.execute(
            select(User.discord_id, User.language).where(User.discord_id.in_(wanted))
        )
        found = {i: lang for i, lang in res.all()}
    unmatched += [str(i) for i in wanted if i not in found]
    return Recipients(ids=tuple(found.items()), unmatched=tuple(unmatched))


def _framed_body(body: str, language: str) -> str:
    """The recipient-facing frame, resolved in THEIR language.

    Applied here, at queue time, rather than at send time: one Notification is
    written per recipient anyway and their language is already in hand, so
    pre-framing means the scheduler's plain-text path (`await
    user.send(note.body)`) needs no changes at all.

    The brand itself is never translated, exactly as the language names
    EN/中文/日本語 are not. Only the frame around it is.

    The N_() is load-bearing despite being the identity function: `pybabel
    extract` matches on the CALLED NAME, and `gettext_in` is not one of the
    keywords in babel.cfg, so without the marker this msgid is invisible to the
    ritual -- the next `pybabel update` would file the hand-added entry as
    obsolete and the frame would silently fall back to English for everyone.
    (`gettext_in(locale, "Multiple")` gets away with a bare literal only
    because "Multiple" is independently extracted from three templates.)
    """
    return f"**{gettext_in(language, N_('From dekimasen.app'))}**\n\n{body}"


async def queue_broadcast(
    session: AsyncSession,
    created_by: int,
    mode: BroadcastMode,
    param: str | None,
    body: str,
    now: datetime | None = None,
) -> Broadcast:
    """Write the audit row and one held Notification per recipient.

    Through the outbox, never a direct send (invariant 4). Recipients are
    re-resolved here rather than trusted from the preview form, so
    recipient_count records what was actually queued.
    """
    now = now or _now()
    body = body.strip()
    if not body:
        raise ValueError("broadcast body is empty")
    if len(body) > BROADCAST_BODY_MAX:
        raise ValueError(f"broadcast body exceeds {BROADCAST_BODY_MAX} characters")

    recipients = await resolve_recipients(session, mode, param)
    send_after = now + timedelta(seconds=HOLD_SECONDS)
    broadcast = Broadcast(
        created_by=created_by,
        created_at_utc=now,
        mode=mode,
        mode_param=param,
        body=body,
        recipient_count=len(recipients.ids),
        send_after_utc=send_after,
    )
    session.add(broadcast)
    await session.flush()

    session.add_all(
        [
            Notification(
                user_id=discord_id,
                body=_framed_body(body, language),
                kind="admin_broadcast",
                send_after_utc=send_after,
                broadcast_id=broadcast.id,
            )
            for discord_id, language in recipients.ids
        ]
    )
    await session.flush()
    return broadcast


async def cancel_broadcast(
    session: AsyncSession, broadcast_id: int, now: datetime | None = None
) -> tuple[int, int]:
    """Delete this broadcast's UNSENT notifications. Returns
    (cancelled, already_delivered).

    Both numbers are returned because a tick can drain rows between the click
    and this call. Reporting "cancelled -- 12 of 40 had already been delivered"
    is the point: a rail that lies about what it undid is worse than no rail.
    """
    now = now or _now()
    delivered = (
        await session.execute(
            select(func.count(Notification.id)).where(
                Notification.broadcast_id == broadcast_id,
                Notification.sent_at_utc.is_not(None),
            )
        )
    ).scalar_one()
    res = await session.execute(
        delete(Notification).where(
            Notification.broadcast_id == broadcast_id,
            Notification.sent_at_utc.is_(None),
        )
    )
    row = await session.get(Broadcast, broadcast_id)
    if row is not None and row.cancelled_at_utc is None:
        row.cancelled_at_utc = now
    await session.flush()
    return (res.rowcount or 0, delivered)


async def recent_broadcasts(session: AsyncSession, limit: int = 50) -> list[Broadcast]:
    res = await session.execute(
        select(Broadcast).order_by(Broadcast.created_at_utc.desc()).limit(limit)
    )
    return list(res.scalars())


async def duplicate_body_recently(
    session: AsyncSession, body: str, now: datetime | None = None
) -> bool:
    """Has this exact text gone out in the last hour? Catches the stale-tab
    resubmit and answers "did I already send this?" during an incident."""
    now = now or _now()
    res = await session.execute(
        select(Broadcast.id).where(
            Broadcast.body == body.strip(),
            Broadcast.created_at_utc >= now - _DUPLICATE_WINDOW,
        )
    )
    return res.first() is not None


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
        # N_() for the same reason _framed_body needs it: `gettext_in` is not
        # one of babel.cfg's extraction keywords, so a bare literal here is
        # invisible to `pybabel extract`. This msgid only survived because
        # three TEMPLATES independently produce it -- reword those and this
        # DM text silently reverts to English, with nothing failing, since a
        # msgid that stops being extracted also stops being checked.
        venue=(gettext_in(locale, N_("Multiple")) if len(venues) > 1
               else (venues[0] if venues else None)),
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
    # Whether the WHOLE show is off (all_legs_cancelled), not just the leg that
    # triggered the notice. The embed's prose differs: one leg of a tour going
    # down is "a performance was cancelled", but a dead concert is the only
    # channel telling this reader that EVERY reminder here is gone -- a payment
    # reminder on a won ticket included. A concert-level fact, so it is resolved
    # here rather than re-derived in the bot layer.
    concert_cancelled: bool = False


async def leg_cancelled_context(
    session: AsyncSession, concert_id: int, user_id: int | None = None
) -> LegCancelledContext | None:
    concert = await session.get(Concert, concert_id)
    if concert is None:
        return None
    user = await session.get(User, user_id) if user_id else None
    locale = user.language if user else "en"
    days = list((await session.execute(
        select(ConcertDay).where(ConcertDay.concert_id == concert_id)
    )).scalars())
    return LegCancelledContext(
        concert_id=concert.id,
        event_id=concert.event_id,
        title=loc_field(concert, "title", locale),
        user_language=locale,
        concert_cancelled=all_legs_cancelled(days),
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


# ── Translation gaps (the edit pages' "what's missing" notice) ────────────


@dataclass(frozen=True)
class VariantGap:
    """One language, and the fields on this record still missing it.

    Grouped by LANGUAGE rather than by field because that is the shape of
    the work: an editor who can write 中文 wants one list of everything
    waiting for them, not the same language repeated down a column.
    """

    language: str  # "日本語" / "English" / "中文"
    fields: tuple[str, ...]  # localized field labels, in record order


def _regroup_gaps(pairs: Iterable[tuple[str, tuple[str, ...]]]) -> list[VariantGap]:
    """(field label, missing slots) pairs -> one VariantGap per language.

    Slot order stays ja/en/zh (missing_variants' own order) and field order
    stays the caller's, which is the order the fields appear on the page --
    so the notice reads top-to-bottom like the form under it.
    """
    by_slot: dict[str, list[str]] = {}
    for label, missing in pairs:
        for slot in missing:
            by_slot.setdefault(slot, []).append(label)
    return [
        VariantGap(language=SLOT_LABEL[slot], fields=tuple(by_slot[slot]))
        for slot in ("ja", "en", "zh")
        if slot in by_slot
    ]


def concert_variant_gaps(
    concert: Concert, days: Sequence[ConcertDay], rounds: Sequence[Round]
) -> list[VariantGap]:
    """What this concert is still missing, in the viewer's language.

    Read-only and advisory: `edit_concert` never blocks on any of this (see
    tests/test_variant_enforcement.py's asymmetry section). The phase ships
    without a backfill, so nearly every pre-i18n record has a Japanese title
    and nothing else -- enforcing here would wall the owner out of their own
    catalogue. Naming the gap while they are looking at the record is the
    whole intervention.

    Same field set the create boundary enforces, and the same row wording
    ("Leg 2 label"), so the notice and a 422 never disagree. `days`/`rounds`
    are passed in rather than read off `concert` so no relationship is
    lazy-loaded mid-render, and they are numbered in the order the edit page
    renders them.

    There is no venue field here: a leg's venue is a VENUE tag, whose own
    name variants are governed on the Tags page, not per concert. (The old
    Concert.venue/_en/_zh columns were dropped once every venue lived on a
    tag.)
    """
    pairs: list[tuple[str, tuple[str, ...]]] = [
        (_("Title"), missing_variants(
            concert.title or "", concert.title_en or "", concert.title_zh or "",
            mandatory=True,
        )),
        (_("Notes"), missing_variants(
            concert.notes or "", concert.notes_en or "", concert.notes_zh or "",
        )),
    ]
    for n, d in enumerate(days, 1):
        pairs.append((
            _("Leg {n} label").format(n=n),
            missing_variants(d.label or "", d.label_en or "", d.label_zh or ""),
        ))
    for n, r in enumerate(rounds, 1):
        pairs.append((
            _("Round {n} label").format(n=n),
            missing_variants(r.label or "", r.label_en or "", r.label_zh or ""),
        ))
    return _regroup_gaps(pairs)


def tag_variant_gaps(tag: Tag) -> list[VariantGap]:
    """The same notice for a tag. `city` only applies to a VENUE."""
    pairs: list[tuple[str, tuple[str, ...]]] = [
        (_("Name"), missing_variants(
            tag.name or "", tag.name_en or "", tag.name_zh or "", mandatory=True,
        )),
    ]
    if tag.kind is TagKind.VENUE:
        pairs.append((_("City"), missing_variants(
            tag.city or "", tag.city_en or "", tag.city_zh or "",
        )))
    return _regroup_gaps(pairs)


# ── Rehearsal harness (local only) ───────────────────────────────────────

# The rehearsal concert is identified by a constant event_id rather than a
# column. That is deliberate: it means the pull-forward action can only ever
# reach THIS concert's queue rows, because there is no id for a caller to
# pass. Not added to RESERVED_EVENT_IDS -- that set exists to stop collisions
# with the /concerts/new and /concerts/import routes, and there is no
# /concerts/rehearsal route.
REHEARSAL_EVENT_ID = "rehearsal"
# The ARTIST tag the seed attaches so `handle_newly_tagged` actually fires.
# Survives teardown deliberately: it is shared taxonomy, and reusing it means a
# reseed re-fires the notice (the operator has no rules on the NEW concert yet).
REHEARSAL_TAG_NAME = "リハーサル・アーティスト"


async def get_rehearsal_concert(session: AsyncSession) -> Concert | None:
    res = await session.execute(
        select(Concert).where(Concert.event_id == REHEARSAL_EVENT_ID)
    )
    return res.scalar_one_or_none()


async def teardown_rehearsal(session: AsyncSession) -> bool:
    """Delete the rehearsal concert. Returns whether one existed.

    Deletes the Concert row only and lets the existing cascades take days,
    rounds, queue rows, outcomes and audits. It never touches users, presets
    or subscriptions -- those are the operator's real local state.
    """
    concert = await get_rehearsal_concert(session)
    if concert is None:
        return False
    await session.delete(concert)
    await session.flush()
    return True


async def seed_rehearsal(
    session: AsyncSession, user_id: int, now: datetime | None = None
) -> Concert:
    """Build the canonical scenario, replacing any previous one.

    Two legs, three rounds, and one reminder rule per anchor. Anchors are set
    at realistic future distances and the rules are real, so `sync_concert`
    and the pure planner compute genuine fire times -- the harness later pulls
    those rows forward rather than fabricating them.
    """
    now = now or _now()
    await teardown_rehearsal(session)

    concert = Concert(
        event_id=REHEARSAL_EVENT_ID,
        title="リハーサル公演",
        title_en="Rehearsal Concert",
        title_zh="彩排演出",
        created_by=user_id,
    )
    session.add(concert)
    await session.flush()

    day1 = ConcertDay(
        concert_id=concert.id,
        label="Day 1",
        label_en="Day 1",
        label_zh="第一天",
        starts_at_utc=now + timedelta(days=30),
    )
    day2 = ConcertDay(
        concert_id=concert.id,
        label="Day 2",
        label_en="Day 2",
        label_zh="第二天",
        starts_at_utc=now + timedelta(days=31),
    )
    session.add_all([day1, day2])
    await session.flush()

    # R1 carries all four anchors and both legs: the whole ladder from one
    # round, and a WON on it exercises the per-day RoundOutcomeDay
    # materialization that a single-leg round never reaches.
    lottery = Round(
        concert_id=concert.id,
        kind=RoundKind.LOTTERY_ROUND,
        label="一次先行",
        label_en="1st lottery",
        label_zh="第一轮抽选",
        applies_to=[day1.id, day2.id],
        opens_at_utc=now + timedelta(days=1),
        closes_at_utc=now + timedelta(days=7),
        results_at_utc=now + timedelta(days=10),
        payment_deadline_at_utc=now + timedelta(days=14),
    )
    # R2 exists to prove SUPPRESSION: once R1 is won on Day 1, the
    # secured-elsewhere pass should silently delete this round's reminders.
    # A round that stops arriving is the hardest thing to notice by hand.
    fcfs = Round(
        concert_id=concert.id,
        kind=RoundKind.FCFS_SALE,
        label="一般発売",
        label_en="General sale",
        label_zh="一般发售",
        applies_to=[day1.id],
        opens_at_utc=now + timedelta(days=3),
        closes_at_utc=now + timedelta(days=8),
    )
    # R3 is invisible until the viewer holds a ticket -- the eligibility gate,
    # proven end to end rather than by unit test.
    upgrade = Round(
        concert_id=concert.id,
        kind=RoundKind.UPGRADE,
        label="アップグレード先行",
        label_en="Upgrade lottery",
        label_zh="升级抽选",
        applies_to=[day1.id, day2.id],
        opens_at_utc=now + timedelta(days=11),
        closes_at_utc=now + timedelta(days=13),
    )
    session.add_all([lottery, fcfs, upgrade])
    await session.flush()

    session.add(
        RoundQualifier(upgrade_round_id=upgrade.id, qualifying_round_id=lottery.id)
    )

    # A followed tag, attached HERE -- before any rule exists on this concert.
    # handle_newly_tagged skips a user who already has rules on the concert, so
    # seeding the rules first would make Start queue nothing at all and step 1
    # of the walk would demonstrate the new-event DM by not sending it.
    #
    # This is the only way the pipeline half reaches `handle_newly_tagged`, and
    # that is the fan-out worth rehearsing: it is the path that DMs every
    # follower of a tag, and the likeliest way this app ever messages the wrong
    # people. The shape catalogue can render the embed, but only this exercises
    # the delivery.
    # First match, not "the" match: names are not unique, so this asks for A tag
    # called this rather than THE one. Re-seeding must not mint a second.
    existing = await find_tags_by_name_and_kind(session, REHEARSAL_TAG_NAME, TagKind.ARTIST)
    tag = existing[0] if existing else None
    if tag is None:
        tag = Tag(
            name=REHEARSAL_TAG_NAME,
            name_en="Rehearsal Artist",
            name_zh="彩排歌手",
            kind=TagKind.ARTIST,
            created_by=user_id,
        )
        session.add(tag)
        await assign_tag_slug(session, tag)
        await session.flush()

    following = (
        await session.execute(
            select(TagSubscription).where(
                TagSubscription.user_id == user_id, TagSubscription.tag_id == tag.id
            )
        )
    ).scalar_one_or_none()
    if following is None:
        default = await get_default_preset(session, user_id)
        session.add(
            TagSubscription(
                user_id=user_id,
                tag_id=tag.id,
                notify=True,
                preset_id=default.id if default else None,
            )
        )
        await session.flush()

    newly = await attach_tag(session, concert.id, tag, expand=False)
    await handle_newly_tagged(session, concert, newly)

    # Track it explicitly TOO. The tag above already makes it tracked, but an
    # explicit subscription keeps the harness working if the operator later
    # unfollows the tag by hand mid-walk.
    await set_concert_subscription(session, user_id, concert.id, SubscriptionState.SUBSCRIBED)

    # One rule per anchor, at zero offset. The offset is irrelevant to what
    # this proves -- pull-forward moves the fire time regardless -- and zero
    # keeps the seeded plan legible in the harness's own state table.
    for anchor in (Anchor.OPENS, Anchor.CLOSES, Anchor.RESULTS, Anchor.PAYMENT):
        session.add(
            ReminderRule(
                user_id=user_id, concert_id=concert.id, anchor=anchor,
                offset_days=0, offset_hours=0,
            )
        )
    session.add(
        ReminderRule(
            user_id=user_id, concert_id=concert.id, anchor=Anchor.EVENT_START,
            offset_days=0, offset_hours=0,
        )
    )
    await session.flush()

    await sync_concert(session, concert.id)
    return concert


async def rehearsal_queue_rows(session: AsyncSession) -> list[ReminderQueue]:
    """Every queue row belonging to the rehearsal concert, soonest first.

    Scoped by joining through the concert's rounds and days rather than by an
    id the caller supplies -- see REHEARSAL_EVENT_ID's note.
    """
    concert = await get_rehearsal_concert(session)
    if concert is None:
        return []
    round_ids = set(
        (await session.execute(
            select(Round.id).where(Round.concert_id == concert.id)
        )).scalars()
    )
    day_ids = set(
        (await session.execute(
            select(ConcertDay.id).where(ConcertDay.concert_id == concert.id)
        )).scalars()
    )
    if not round_ids and not day_ids:
        return []
    res = await session.execute(
        select(ReminderQueue)
        .where(
            or_(
                ReminderQueue.round_id.in_(round_ids) if round_ids else false(),
                ReminderQueue.day_id.in_(day_ids) if day_ids else false(),
            )
        )
        .order_by(ReminderQueue.fire_at_utc)
    )
    return list(res.scalars())


async def pull_rehearsal_forward(
    session: AsyncSession, now: datetime | None = None
) -> ReminderQueue | None:
    """Rewrite the soonest UNSENT rehearsal queue row's fire time into the
    past, so the next real tick delivers it. Returns the row, or None.

    This is the only thing the harness fakes, and it fakes the wait, not the
    work: sync_rule and the pure planner already computed this row and its
    anchor. Everything downstream -- suppression, gating, the send path, the
    buttons -- runs exactly as in production.
    """
    now = now or _now()
    for row in await rehearsal_queue_rows(session):
        if row.sent_at_utc is None:
            row.fire_at_utc = now - timedelta(seconds=1)
            await session.flush()
            return row
    return None


async def cancel_rehearsal_show(
    session: AsyncSession, now: datetime | None = None
) -> int:
    """Cancel the rehearsal concert's remaining live legs and queue the notices.

    Order is load-bearing: notify_newly_cancelled_legs must run BEFORE
    sync_concert, which deletes the very queue rows it inspects to decide who
    is losing a reminder. Get it backwards and the notice is silently never
    queued -- see that function's own docstring.

    It cancels EVERY live leg rather than only the last one, and that is not
    over-reach: it is the only way this harness can show a real leg_cancelled
    DM. That notice is CONCERT-scoped by design -- notify_newly_cancelled_legs
    stays deliberately silent for anyone who still holds a live reminder
    anywhere on the concert. Cancelling Day 2 alone therefore queues nothing,
    because Day 1's EVENT_START and R1's four anchors are all still standing
    (measured: it returns 0). Emitting the notice for a half-cancelled concert
    would mean faking the one thing this button exists to demonstrate, so the
    button calls the whole show off instead. The plan's per-leg version is the
    deviation recorded in the spec.
    """
    concert = await get_rehearsal_concert(session)
    if concert is None:
        return 0
    res = await session.execute(
        select(ConcertDay)
        .where(ConcertDay.concert_id == concert.id, ConcertDay.cancelled.is_(False))
        .order_by(ConcertDay.starts_at_utc.desc())
    )
    legs = list(res.scalars())
    if not legs:
        return 0
    for leg in legs:
        leg.cancelled = True
    await session.flush()
    queued = await notify_newly_cancelled_legs(
        session, concert.id, {leg.id for leg in legs}, now
    )
    await sync_concert(session, concert.id)
    return queued


@dataclass(frozen=True)
class RehearsalRow:
    """One rehearsal queue row, as the harness page shows it."""

    queue_id: int
    anchor: Anchor
    # The round's label, or the leg's on an EVENT_START row. English: this page
    # renders on a developer machine only.
    subject: str
    fire_at_utc: datetime
    sent: bool
    # The soonest UNSENT row -- the one "Next" will pull forward.
    is_next: bool
    # What a correct DM for that row should carry, per domain/rehearsal.py.
    # Empty on every other row: an expectation is only meaningful for the row
    # about to fire, since the ones below it will be composed under an outcome
    # the walk has not reached yet.
    expected: tuple[str, ...] = ()


async def rehearsal_rows(session: AsyncSession, user_id: int) -> list[RehearsalRow]:
    """The harness's state table: every rehearsal queue row, soonest first,
    with the button expectation attached to the next one to fire.

    A read-side decoration of `rehearsal_queue_rows` -- it writes nothing, and
    the expectation is computed, never stored.
    """
    rows = await rehearsal_queue_rows(session)
    concert = await get_rehearsal_concert(session)
    if not rows or concert is None:
        return []

    rounds = {
        r.id: r for r in (await session.execute(
            select(Round).where(Round.concert_id == concert.id)
        )).scalars()
    }
    days = list((await session.execute(
        select(ConcertDay)
        .where(ConcertDay.concert_id == concert.id)
        .order_by(ConcertDay.starts_at_utc, ConcertDay.id)
    )).scalars())
    day_labels = {d.id: loc_field(d, "label", "en") for d in days}
    live_day_ids = {d.id for d in days if not d.cancelled}
    outcomes = {
        o.round_id: o.outcome for o in (await session.execute(
            select(RoundOutcome).where(
                RoundOutcome.user_id == user_id,
                RoundOutcome.round_id.in_(rounds),
            )
        )).scalars()
    } if rounds else {}

    next_id = next((r.id for r in rows if r.sent_at_utc is None), None)
    out: list[RehearsalRow] = []
    for row in rows:
        round_ = rounds.get(row.round_id) if row.round_id is not None else None
        expected: tuple[str, ...] = ()
        if row.id == next_id:
            # Cancelled legs are excluded before the count, exactly as
            # due_reminders does it: a two-leg round with one leg cancelled is
            # a one-leg question.
            legs = len(_covered_day_ids(round_, live_day_ids)) if round_ else 0
            expected = expected_buttons(
                row.anchor, outcomes.get(row.round_id) if round_ else None, legs
            )
        out.append(RehearsalRow(
            queue_id=row.id,
            anchor=row.anchor,
            subject=(
                loc_field(round_, "label", "en") if round_
                else day_labels.get(row.day_id, "—")
            ),
            fire_at_utc=row.fire_at_utc,
            sent=row.sent_at_utc is not None,
            is_next=row.id == next_id,
            expected=expected,
        ))
    return out


# ── Eventernote discovery ────────────────────────────────────────────────
#
# The diff: given performances scraped off an artist's Eventernote page,
# which ones does the catalogue not already have? A lead is reported unless
# one of these holds, checked in that order:
#
#   1. an existing ConcertDay already carries that eventernote_event_id --
#      we have it, say nothing (and if a lead for it is still open, bind it to
#      that concert, which is how a lead leaves the review queue by being
#      catalogued rather than by being dismissed);
#   2. it was dismissed -- never mention it again;
#   3. it was already announced -- do not re-announce;
#   4. otherwise it is a new lead.
#
# What is deliberately NOT on that list is a same-date-same-venue collision
# with an existing leg. 昼公演 and 夜公演 are two separate Eventernote events
# on one date at one venue, and two legs of one concert; suppressing on date
# plus venue would hide exactly the second show. That comparison is a HINT,
# computed separately by leads_matching_existing_legs so a surface can mark a
# lead "you may already have this" -- it never removes anything.


async def _bind_leads_to_concerts(
    session: AsyncSession, held: Mapping[str, int]
) -> None:
    """THE writer for `DiscoveredEvent.concert_id`, and the loop's other end.

    `open_leads` reads that column and nothing wrote it, so the maintainer's
    happy path -- lead, agent, draft, import (which stamps the Eventernote id
    onto the leg) -- left the review page dirtier rather than cleaner, and the
    only exit was pressing Dismiss by hand on a row the catalogue already
    resolved.

    Called from `record_discovered`'s branch 1 and BEFORE it drops the held ids:
    once an id is dropped it is never looked up in `discovered_events` at all,
    so a later sweep could not even see the row it needed to close. A lead that
    is not stored is simply absent here, which is the common case and costs one
    empty `IN ()`-free query.

    Only writes a row whose `concert_id` disagrees, so a lead already bound is
    not re-dirtied on every sweep. Nothing else on the row is touched: a bound
    lead is out of the queue and its `announced_at`/`dismissed_at` history is
    the record of how it got there.
    """
    if not held:
        return
    rows = (await session.execute(
        select(DiscoveredEvent)
        .where(DiscoveredEvent.source_event_id.in_(list(held)))
    )).scalars()
    changed = False
    for row in rows:
        concert_id = held[row.source_event_id]
        if row.concert_id != concert_id:
            row.concert_id = concert_id
            changed = True
    if changed:
        await session.flush()


@dataclass(frozen=True)
class DiscoveredInput:
    """One sighting of one event, whichever pipeline saw it.

    `record_discovered`'s input contract, the way `NoticeContext` is for
    notices -- kept beside the function it feeds rather than in `domain/`,
    since it names nothing domain-pure beyond `ActorEvent`.
    """

    event: ActorEvent
    # The surfacing tag, Eventernote only -- a calendar feed carries no tag.
    tag_id: int | None = None
    # Which pipeline produced it: "eventernote", or a CalendarFeed.key.
    source: str = "eventernote"
    # True when `event.date` is an application deadline, not a performance
    # date (the imas ticket calendar) -- see DiscoveredEvent.date_is_deadline.
    date_is_deadline: bool = False


async def record_discovered(
    session: AsyncSession,
    events: Sequence[DiscoveredInput],
    now: datetime,
) -> list[DiscoveredEvent]:
    """Upsert one row per source event id; return only the FRESH rows.

    A sweep passes ~86 artists' worth of events in one call, so this is two
    queries total regardless of size -- one to find the ids legs already
    hold, one to load the DiscoveredEvent rows for the rest -- never a query
    per event. A third runs only when a leg holds one of the incoming ids,
    to close those leads (`_bind_leads_to_concerts`); still batched, still
    independent of how many events arrived.
    """
    if not events:
        return []

    # One event surfaced by nine artist tags arrives here nine times; the
    # first sighting wins, which is what first_seen_via_tag_id means.
    incoming: dict[str, DiscoveredInput] = {}
    for di in events:
        incoming.setdefault(di.event.event_id, di)

    # Branch 1, one query. These are dropped entirely and never stored: a leg
    # carrying the id is the catalogue saying it already has this. The CONCERT
    # comes back with the id because branch 1 is also where a lead's life ends
    # -- see _bind_leads_to_concerts.
    held: dict[str, int] = {}
    for event_id, concert_id in (await session.execute(
        select(ConcertDay.eventernote_event_id, ConcertDay.concert_id)
        .where(ConcertDay.eventernote_event_id.in_(list(incoming)))
    )).all():
        held.setdefault(event_id, concert_id)
    await _bind_leads_to_concerts(session, held)

    remaining = [event_id for event_id in incoming if event_id not in held]
    if not remaining:
        return []

    # Branches 2 and 3, one query. Dismissed and announced leads are rows in
    # this same table, so the lookup a repeat sighting needs answers all three.
    existing = {
        row.source_event_id: row for row in (await session.execute(
            select(DiscoveredEvent)
            .where(DiscoveredEvent.source_event_id.in_(remaining))
        )).scalars()
    }

    fresh: list[DiscoveredEvent] = []
    for event_id in remaining:
        di = incoming[event_id]
        row = existing.get(event_id)
        if row is not None:
            # Seen again. Nothing else is touched: re-writing the title or the
            # surfacing tag would let a later sweep quietly rewrite a lead the
            # maintainer has already read, and re-writing dismissed_at would
            # resurrect something explicitly killed.
            row.last_seen_at = now
            continue
        row = DiscoveredEvent(
            source_event_id=di.event.event_id,
            title=di.event.title,
            event_date=di.event.date,
            venue=di.event.venue,
            first_seen_via_tag_id=di.tag_id,
            source=di.source,
            date_is_deadline=di.date_is_deadline,
            first_seen_at=now,
            last_seen_at=now,
        )
        session.add(row)
        fresh.append(row)

    # Flush, not commit: callers need the ids (to announce them), the
    # transaction stays theirs.
    await session.flush()
    return fresh


async def open_leads(session: AsyncSession) -> list[DiscoveredEvent]:
    """Leads still awaiting triage -- not dismissed, not bound to a concert.
    Newest performance date first.

    ANNOUNCED IS NOT TRIAGED, and `announced_at` is deliberately NOT a filter
    here (owner ruling, 2026-07-31). It exists for one job: stop the daily DM
    repeating a lead. The real exits from this queue are `dismissed_at` (waved
    off) and `concert_id` (it became a concert).

    Filtering on it shipped once and was a hole with no bottom: the sweep marks
    EVERY fresh lead announced, listed or merely counted (see run_sweep), so the
    first sweep's DM would name ten, say "+30 more -- /admin/discoveries", and
    send the maintainer to an empty page -- with those thirty reachable from
    nowhere and never announced again.
    """
    return list((await session.execute(
        select(DiscoveredEvent)
        .where(
            DiscoveredEvent.dismissed_at.is_(None),
            DiscoveredEvent.concert_id.is_(None),
        )
        .order_by(DiscoveredEvent.event_date.desc(), DiscoveredEvent.id.desc())
    )).scalars())


async def dismissed_reason_counts(session: AsyncSession) -> dict[str, int]:
    """How many leads were dismissed as each taxonomy class.

    Rows with a NULL reason are EXCLUDED rather than bucketed as `other`: they
    predate the column, and folding them in would invent a human judgment in
    the one place whose value is that every entry is a real one.
    """
    rows = await session.execute(
        select(DiscoveredEvent.dismiss_reason, func.count())
        .where(DiscoveredEvent.dismiss_reason.is_not(None))
        .group_by(DiscoveredEvent.dismiss_reason)
    )
    return {reason: n for reason, n in rows.all()}


async def dismiss_lead(
    session: AsyncSession, lead_id: int, now: datetime, reason: DismissReason
) -> bool:
    """Kill a lead for good, recording which taxonomy class it was.

    False when there was nothing to dismiss (an unknown id, or one already
    dismissed) so a caller can 404 rather than report a write that did not
    happen.

    `reason` is required rather than defaulted on purpose: a column added after
    the fact that quietly accepts a default is how the concert draft silently
    shipped without characters, and there is exactly one production caller.
    """
    row = await session.get(DiscoveredEvent, lead_id)
    if row is None or row.dismissed_at is not None:
        return False
    row.dismissed_at = now
    row.dismiss_reason = reason
    await session.flush()
    return True


# ── Pending drafts (multi-draft triage) ───────────────────────────────────
#
# See PendingDraft's own docstring for why this is a work batch and not the
# step state this app otherwise avoids: an agent can hand back fifty to a
# hundred parsed drafts in one paste, and reading each one's preview is not
# one sitting.


async def create_pending_drafts(
    session: AsyncSession, batch: DraftBatch, created_by: int
) -> list[PendingDraft]:
    """Persist every parsed document of a paste as its own triage row.

    One row per `ParsedDraft`, carrying that document's own verbatim text and
    its already-parsed title. `batch.errors` never reaches here -- there is
    no preview to triage for a document that failed to parse at all.
    """
    rows = [
        PendingDraft(draft_text=draft.text, title=draft.parsed.title, created_by=created_by)
        for draft in batch.drafts
    ]
    session.add_all(rows)
    await session.flush()
    return rows


async def pending_drafts(session: AsyncSession, user_id: int) -> list[PendingDraft]:
    """The still-open rows from `user_id`'s own pastes -- neither committed
    nor discarded. Scoped to the pasting user: two editors triaging their own
    batches at once is the expected case, not an exotic one."""
    rows = await session.execute(
        select(PendingDraft)
        .where(
            PendingDraft.created_by == user_id,
            PendingDraft.committed_at.is_(None),
            PendingDraft.discarded_at.is_(None),
        )
        .order_by(PendingDraft.id)
    )
    return list(rows.scalars())


async def mark_pending_committed(
    session: AsyncSession, pending_id: int, concert_id: int, now: datetime
) -> bool:
    """Stamp a row committed once its preview has produced a real concert.

    False, without re-stamping, when the row is unknown or already committed
    -- the same double-submit rule `dismiss_lead` follows, so a duplicated
    request can never silently rewrite which concert a draft claims to have
    produced.
    """
    row = await session.get(PendingDraft, pending_id)
    if row is None or row.committed_at is not None:
        return False
    row.committed_at = now
    row.concert_id = concert_id
    await session.flush()
    return True


async def discard_pending_draft(session: AsyncSession, pending_id: int, now: datetime) -> bool:
    """Stamp a row discarded -- an editor's "not this one", read past the
    preview. False when the row is unknown or already resolved either way,
    for the same double-submit reason as `mark_pending_committed`."""
    row = await session.get(PendingDraft, pending_id)
    if row is None or row.committed_at is not None or row.discarded_at is not None:
        return False
    row.discarded_at = now
    await session.flush()
    return True


@dataclass(frozen=True)
class PlannedDismissal:
    """One lead a prune list names.

    `reason` is the FILE's own say-so -- not necessarily what the row ends up
    holding. For `to_dismiss` it is exactly what apply_prune will write; for
    `already` it is only what the file asked for, since apply_prune leaves an
    already-dismissed row's own reason untouched (see apply_prune).

    `stored_reason` is what the row ALREADY carries (None if it predates the
    column, or if the lead isn't dismissed yet). For `already` this is the
    truth a screen owes a human: "file says stage, already dismissed as
    release" is the disagreement the `dismiss_reason` column exists to make
    measurable, and dropping it here would throw that away at the one place
    it is visible before nothing happens.

    `first_seen_via_tag_id` rides along purely for display -- the artist tag
    that surfaced the lead, the same column /admin/discoveries reads via its
    own `_artist_names` helper. Copying it here costs no extra query (the row
    is already fully loaded in plan_prune), and a screen asking a human to
    approve 300 permanent dismissals is far more reviewable with "which
    artist" on every line than without it. Defaulted so the several
    hand-built PrunePlan/PlannedDismissal fixtures in this file's own test
    suite, written before this field existed, still construct.

    `source`/`date_is_deadline` mirror the same two `DiscoveredEvent` columns
    the review page (/admin/discoveries) and the DM digest already read --
    the prune plan is a third surface over the same rows, and a calendar
    lead here has no Eventernote page either: the template must gate its
    events link on `source == "eventernote"` exactly as admin_discoveries.html
    does, and the FEED's own label belongs where the artist name goes when
    there is no `first_seen_via_tag_id` to look up. Both defaulted for the
    same fixture-compatibility reason as `first_seen_via_tag_id`."""

    lead_id: int
    event_id: str
    title: str
    event_date: date
    reason: DismissReason
    stored_reason: DismissReason | None
    first_seen_via_tag_id: int | None = None
    source: str = "eventernote"
    date_is_deadline: bool = False


@dataclass(frozen=True)
class PrunePlan:
    """What a prune list WOULD do, before any of it is written.

    Four buckets, all worth showing a human before they approve anything:
    - to_dismiss: an open lead this file would dismiss.
    - unknown: an event_id matching no DiscoveredEvent row at all -- usually
      a stale file (a mistyped id, or a lead that no longer exists).
    - already: a lead this file names that is already dismissed -- usually a
      re-paste of an earlier file. apply_prune leaves these rows exactly as
      it found them.
    - catalogued: a lead this file names that has since become a concert
      (`concert_id` set) -- the file predates that, and dismissing it would
      stamp a `dismiss_reason` on a row the catalogue already has, polluting
      `dismissed_reason_counts` (the classifier scorecard the column exists
      for) with a judgment about something that is no longer an open lead at
      all. Checked BEFORE `dismissed_at`: a lead that is somehow both
      catalogued and dismissed is reported as catalogued, since "this is now
      a real concert" is the more useful thing to tell the operator.
      `open_leads` (the main /admin/discoveries listing) already excludes
      these two ways -- `concert_id IS NULL` and `dismissed_at IS NULL` -- so
      this mirrors that same pair of exits rather than inventing a third.

    `warnings` carries `PruneList.warnings` forward (currently just "listed
    twice under the same reason") so the one tell of a sloppy agent file is
    not silently dropped between parsing and rendering. Defaulted to `()` for
    the same fixture-compatibility reason `PlannedDismissal.first_seen_via_
    tag_id` is.
    """

    to_dismiss: tuple[PlannedDismissal, ...]
    unknown: tuple[str, ...]
    already: tuple[PlannedDismissal, ...]
    catalogued: tuple[PlannedDismissal, ...] = ()
    warnings: tuple[str, ...] = ()


async def plan_prune(session: AsyncSession, prune: PruneList) -> PrunePlan:
    """Join a parsed prune list against the catalogue -- ONE query however
    many entries the file names (`source_event_id IN (...)`), not a
    query per entry: 300 entries must not be 300 round trips.

    Sorts every entry into exactly one of PrunePlan's four buckets and
    writes NOTHING -- looking is not doing, and this plan is rendered before
    a human has agreed to any of it.
    """
    ids = [entry.event_id for entry in prune.entries]
    rows = (await session.execute(
        select(DiscoveredEvent).where(DiscoveredEvent.source_event_id.in_(ids))
    )).scalars()
    by_event_id = {row.source_event_id: row for row in rows}

    to_dismiss: list[PlannedDismissal] = []
    unknown: list[str] = []
    already: list[PlannedDismissal] = []
    catalogued: list[PlannedDismissal] = []

    for entry in prune.entries:
        row = by_event_id.get(entry.event_id)
        if row is None:
            unknown.append(entry.event_id)
            continue
        planned = PlannedDismissal(
            lead_id=row.id, event_id=entry.event_id, title=row.title,
            event_date=row.event_date, reason=entry.reason,
            stored_reason=(
                DismissReason(row.dismiss_reason) if row.dismiss_reason else None
            ),
            first_seen_via_tag_id=row.first_seen_via_tag_id,
            source=row.source, date_is_deadline=row.date_is_deadline,
        )
        if row.concert_id is not None:
            catalogued.append(planned)
        elif row.dismissed_at is not None:
            already.append(planned)
        else:
            to_dismiss.append(planned)

    return PrunePlan(
        to_dismiss=tuple(to_dismiss), unknown=tuple(unknown), already=tuple(already),
        catalogued=tuple(catalogued), warnings=tuple(prune.warnings),
    )


async def apply_prune(session: AsyncSession, plan: PrunePlan, now: datetime) -> int:
    """Write a plan built from a FRESH parse -- one dismiss_lead call per
    lead in `to_dismiss` (never a bulk UPDATE; dismiss_lead is the single
    writer), returning how many rows were actually written.

    Does not re-derive the plan and does not touch `unknown`/`already`/
    `catalogued` at all: plan_prune already sorted those out, and dismiss_lead
    itself refuses an already-dismissed row, so even a stale plan racing a
    second apply cannot re-stamp one.

    FLUSHES per dismiss_lead call, never commits -- the caller owns the
    transaction and its outcome. That means a raise partway through the loop
    (an unexpected DB error, say) rolls the WHOLE batch back via the caller's
    session rather than leaving some leads dismissed and others not: nothing
    here durably survives until the caller commits.
    """
    written = 0
    for planned in plan.to_dismiss:
        if await dismiss_lead(session, planned.lead_id, now, planned.reason):
            written += 1
    return written


@dataclass(frozen=True)
class PruneReport:
    """What POST /admin/discoveries/prune/apply actually did -- a SNAPSHOT,
    not a plan, and deliberately a different type from PrunePlan (mirrors
    TagImportReport vs. tags_diff's ImportPlan in routes/admin.py, for the
    same reason: rendering the PLAN again after applying would show a
    submit button over lead ids that are now gone, and a second press would
    report "Dismissed 0 leads" -- reading exactly like the first press
    silently failed, when in fact it worked. `to_dismiss` deliberately has
    no counterpart here; `unknown`/`already`/`catalogued`/`warnings` carry
    forward for display since they are informational rather than
    actionable."""

    dismissed: int
    unknown: tuple[str, ...]
    already: tuple[PlannedDismissal, ...]
    catalogued: tuple[PlannedDismissal, ...]
    warnings: tuple[str, ...]


async def mark_leads_announced(
    session: AsyncSession, lead_ids: Sequence[int], now: datetime
) -> None:
    """Stamp leads as announced so the next sweep does not repeat them.

    Written through the ORM rather than a bulk UPDATE on purpose: the caller
    is holding these very rows (record_discovered just returned them), and a
    bulk statement would leave those instances claiming announced_at is None.
    """
    ids = list(lead_ids)
    if not ids:
        return
    rows = (await session.execute(
        select(DiscoveredEvent).where(DiscoveredEvent.id.in_(ids))
    )).scalars()
    for row in rows:
        row.announced_at = now
    await session.flush()


async def leads_matching_existing_legs(
    session: AsyncSession, leads: Sequence[DiscoveredEvent]
) -> set[int]:
    """The HINT set: ids of leads landing on the same date and venue as an
    existing leg.

    NOT a suppression -- see the section note. Two shows on one day at one
    venue is the normal shape of a Japanese concert day, so this can only ever
    say "you may already have this", and the caller must keep every one of
    these leads in its list.
    """
    candidates = [lead for lead in leads if lead.venue]
    if not candidates:
        return set()

    # DiscoveredEvent.event_date is a JST calendar date; ConcertDay.starts_at_utc
    # is an aware UTC instant, and the two only agree after a conversion (a leg
    # at 2026-11-14 16:00 UTC is 2026-11-15 in JST). The SQL window is
    # deliberately one day loose on each side -- it only has to be a superset;
    # utc_to_jst settles the actual date per leg below.
    dates = {lead.event_date for lead in candidates}
    midnight = datetime.min.time()
    lo = datetime.combine(min(dates), midnight, tzinfo=UTC) - timedelta(days=1)
    hi = datetime.combine(max(dates), midnight, tzinfo=UTC) + timedelta(days=1)

    rows = (await session.execute(
        select(ConcertDay.starts_at_utc, Tag.name, Tag.name_en)
        .join(Tag, Tag.id == ConcertDay.venue_tag_id)
        .where(ConcertDay.starts_at_utc >= lo, ConcertDay.starts_at_utc <= hi)
    )).all()

    # Both the Japanese name and the English variant count: Eventernote writes
    # whichever the venue is commonly listed under.
    seen: set[tuple[date, str]] = set()
    for starts_at_utc, name, name_en in rows:
        jst_date = utc_to_jst(starts_at_utc).date()
        for label in (name, name_en):
            if label:
                seen.add((jst_date, label.casefold()))

    return {
        lead.id for lead in candidates
        if (lead.event_date, lead.venue.casefold()) in seen
    }


# One sweep a day. The scheduler ticks every 60s, so the cadence has to live
# somewhere durable: in-memory state would re-sweep on every restart, and a
# sweep ends in a DM.
DISCOVERY_INTERVAL = timedelta(hours=24)
DISCOVERY_STATE_ID = 1


async def discovery_due(session: AsyncSession, now: datetime) -> bool:
    """Has it been a day? True also when the sweep has never run at all."""
    state = await session.get(DiscoveryState, DISCOVERY_STATE_ID)
    if state is None or state.last_run_at is None:
        return True
    return now - state.last_run_at >= DISCOVERY_INTERVAL


async def stamp_discovery_run(
    session: AsyncSession,
    now: datetime,
    *,
    fetched: int | None = None,
    failed: int | None = None,
    truncated: bool | None = None,
) -> None:
    """Start the 24h clock, and record how the sweep went.

    Called on EVERY sweep, including one that found nothing -- a quiet day that
    left the clock unset would re-sweep on the very next tick, which is 86
    third-party fetches a minute.

    The counts are ALWAYS assigned, defaults included. A caller with no report
    to give (scheduler.loop re-stamping after a sweep raised) means the counts
    are unknown, and unknown must clear them: leaving yesterday's 74/0 beside
    today's timestamp would read as a healthy sweep on the day the sweep died.
    The sweep CURSOR is deliberately not here for exactly that reason -- see
    `set_sweep_cursor`.

    A pending manual request is cleared HERE, and here specifically, because
    this is the one call every sweep reaches on every exit: run_sweep's own
    `finally`, and scheduler.loop's re-stamp after a sweep raised and its
    rollback undid that finally. A request that survived a failure would re-run
    the sweep 60 seconds later and fail the same way forever -- the
    86-fetches-a-minute trap the 24h clock exists to prevent. It obeys
    last_run_at's rule ("a sweep happened"), not the cursor's ("progress
    accumulated"), which is why it is a keyword-less unconditional write and
    not its own writer.
    """
    state = await session.get(DiscoveryState, DISCOVERY_STATE_ID)
    if state is None:
        state = DiscoveryState(id=DISCOVERY_STATE_ID)
        session.add(state)
    state.last_run_at = now
    state.last_fetched = fetched
    state.last_failed = failed
    state.last_truncated = truncated
    state.sweep_requested_at = None
    await session.flush()


async def request_sweep(session: AsyncSession, now: datetime) -> None:
    """Ask the scheduler to sweep on its next tick.

    THE button does not run the sweep. A sweep costs up to
    SWEEP_BUDGET_SECONDS, which no HTTP request may hold, and an inline run
    would be a second execution path for something whose whole design is a
    single carefully bounded one. This writes a request; scheduler.loop picks
    it up within TICK_SECONDS and calls the same `run_sweep` the daily job
    calls.

    Unconditional rather than set-if-absent: the stored instant is "when an
    operator last asked", and the only reader is a boolean. (A second request
    arriving while a sweep is already in flight is therefore cleared by that
    sweep's stamp -- a ~4 minute window in which one click is absorbed. Left
    alone deliberately: the page then shows no request pending, so the operator
    can simply press it again, and the alternative is threading the exact
    timestamp through both stamping paths to serve a button.)
    """
    state = await session.get(DiscoveryState, DISCOVERY_STATE_ID)
    if state is None:
        state = DiscoveryState(id=DISCOVERY_STATE_ID)
        session.add(state)
    state.sweep_requested_at = now
    await session.flush()


async def sweep_requested(session: AsyncSession) -> bool:
    """Is a manual sweep waiting for the next tick?

    A named predicate beside `discovery_due` because scheduler.loop asks the
    two together and they are NOT the same question: `discovery_due` is the
    24h clock and is only consulted when the flag is on, while a manual request
    runs the sweep whichever way the flag is set.
    """
    state = await session.get(DiscoveryState, DISCOVERY_STATE_ID)
    return state is not None and state.sweep_requested_at is not None


async def set_sweep_cursor(session: AsyncSession, tag_id: int | None) -> None:
    """Where the next sweep starts. None means the head of the list.

    Its OWN writer rather than a keyword on `stamp_discovery_run`, because the
    two obey opposite rules and folding them together would quietly break this
    one. The counts describe a single sweep, so "no report" must clear them;
    the cursor is accumulated PROGRESS through the artist list, so "no report"
    must leave it alone. scheduler.loop re-stamps after a sweep raised -- and if
    that re-stamp reset the cursor, a run of failures would pin the sweep at the
    head of the list forever, which is precisely the starvation the cursor
    exists to prevent.
    """
    state = await session.get(DiscoveryState, DISCOVERY_STATE_ID)
    if state is None:
        state = DiscoveryState(id=DISCOVERY_STATE_ID)
        session.add(state)
    state.sweep_cursor_tag_id = tag_id
    await session.flush()


async def discovery_status(session: AsyncSession) -> DiscoveryState | None:
    """The last sweep's record, or None before the first one ever runs.

    Exists so /admin/discoveries can answer "is the sweep still working?" --
    without it a broken sweep and a quiet one both render an empty table, which
    is how a site redesign becomes a silent no-op that looks like good news.
    """
    return await session.get(DiscoveryState, DISCOVERY_STATE_ID)
