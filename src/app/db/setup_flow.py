"""The first-run capture flow behind `/setup`, run after the welcome wizard.

Named `setup_flow` rather than `setup` deliberately: a module called `setup.py`
inside a package is a needless invitation to confuse it with packaging, and
`web/routes/setup.py` already owns the shorter name one layer up.

Holds NO step state -- every screen renders current DB truth, which is what
makes the flow tamper-safe and re-runnable.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.core import (
    _next_deadline,
    _now,
    _result_moment,
    _round_fully_opted_out,
    _round_has_opened,
    _round_is_open,
    all_legs_cancelled,
    clear_concert_subscription,
    concert_subscription_states,
    covered_round_ids,
    is_round_cancelled,
    record_round_outcome,
    set_concert_subscription,
    tracked_concert_ids,
    user_opted_out_day_ids,
)
from app.db.models import (
    Concert,
    Round,
    RoundOutcome,
    TagSubscription,
)
from app.domain.types import (
    Anchor,
    LotteryOutcome,
    SubscriptionState,
    TagKind,
)
from app.i18n import get_locale, loc_field
from app.i18n import gettext as _

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

    # This reader's leg opt-outs across the surviving set, ONE query. A round
    # whose every named leg is opted out is filtered below exactly as a
    # cancelled or covered one: screen 2's answer (APPLIED) is irreversible,
    # and this reader already said they are skipping that show.
    opted_out_day_ids = await user_opted_out_day_ids(
        session, user_id, [d.id for c in surviving for d in c.days]
    )

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
            if _round_fully_opted_out(r, opted_out_day_ids):
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

    # Same filter as setup_application_rows -- the reveal counts what screen
    # 2 asks about.
    opted_out_day_ids = await user_opted_out_day_ids(
        session, user_id, [d.id for c in surviving for d in c.days]
    )

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
        live_rounds = [
            r for r in c.rounds
            if not is_round_cancelled(r, cancelled_day_ids)
            and not _round_fully_opted_out(r, opted_out_day_ids)
        ]
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
