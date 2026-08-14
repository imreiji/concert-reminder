"""Round poll storage: the proposals, the daily clock, and the poll's own stamp.

The pass itself (`app/round_poll.py`) holds the run order; this holds every
write it makes. Design: docs/superpowers/plans/2026-08-13-round-poll-phase-1.md.

Three rules this module exists to keep, each silent when broken:

* A RE-POLL UPDATES. The pass runs daily over the same quiet concerts, so the
  same round is proposed again tomorrow and every day after. `upsert_proposal`
  keys on `(concert_id, dedupe_key)` so a second sighting rewrites the row it
  already has -- which is the only reason a dismissal can stick.
* THE KEY IS MINTED IN EXACTLY ONE PLACE. `domain/round_proposals.py:dedupe_key`
  truncates the open time to minute precision, because a live `Round` can carry
  seconds (`yaml_import._dt` passes a YAML timestamp through verbatim) while the
  poll's own "%Y-%m-%d %H:%M" parse never can. Formatting a key here instead --
  even "the same way" -- re-opens that bug: the two sides disagree below the
  minute and the round is re-proposed forever.
* THE POLL WRITES ITS OWN STAMP. `Concert.ladder_polled_at_utc`, never
  `ladder_rechecked_at_utc`, which is the OWNER's and orders
  /admin/quiet-ladders. See `record_ladder_polled`.

Phase 1 wrote neither `dismissed_at` nor `applied_at`: the proposals page
shipped read-only. `dismissed_keys_for` and `pending_proposals` read both
columns from the start anyway, so the pass and the page were already correct
on the day a button appeared -- a filter added after the fact is a filter
every existing reader is missing. Phase 2's `mark_proposal_applied` /
`mark_proposal_dismissed` are the two writers of those columns, and the only
ones: both are pure stamps, and neither touches a `Round` -- creating the
round is the route's job (`web/routes/quiet_ladders.py`), because a `Round`
written anywhere without the `sync_concert` beside it is invariant 2's silent
failure, and this module has no business owning half of that pair.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db.core import ensure_user
from app.db.models import Concert, Notification, Round, RoundPollState, RoundProposal, User
from app.domain.round_proposals import anchors_differ, dedupe_key
from app.domain.types import RoundKind

# One poll a day, the discovery sweep's cadence and for the same reason: the
# scheduler ticks every 60 seconds, and a run is a fetch of every quiet
# concert's official page plus an LLM call per page.
ROUND_POLL_INTERVAL = timedelta(hours=24)
ROUND_POLL_STATE_ID = 1
# The digest's notice kind. A NAME, not a literal at the call site, because two
# things must agree about it and neither raises when they don't: the queue below
# and the assertion that it is absent from `UNREPORTED_NOTE_KINDS`.
ROUND_POLL_NOTE_KIND = "round_poll"


async def upsert_proposal(
    session: AsyncSession,
    concert_id: int,
    *,
    label: str,
    kind: RoundKind,
    opens_at_utc: datetime | None,
    closes_at_utc: datetime | None,
    results_at_utc: datetime | None,
    payment_deadline_at_utc: datetime | None,
    applies_to_labels: list[str],
    evidence_yaml: str,
    source_url: str,
    now: datetime,
) -> RoundProposal:
    """Record one proposed round, or refresh the one already recorded.

    Today's reading of the page WINS on every field except `first_seen_at`,
    which answers "how long has this been sitting unreviewed" and would be a
    lie if a daily re-poll refreshed it. `dismissed_at`/`applied_at` are
    likewise never cleared here: a re-sighting of a round the owner already
    refused is the normal case, not a reason to un-refuse it.

    THAT RULE IS WHY THE FOUR ANCHORS AND THE LEG LIST ARE WRITTEN BELOW THE
    INSERT BRANCH RATHER THAN INSIDE IT. Only `opens_at_utc` is part of the
    key; a page that corrects its 当落発表 date, or names a second leg, keeps
    the same `dedupe_key` and must reach the row that already exists. Setting
    the newer fields only on INSERT would mean the first reading of a proposal
    is the only one that ever counts -- and it would look identical to a page
    that never changed.

    `results_at_utc`, `payment_deadline_at_utc` and `applies_to_labels` are
    REQUIRED keyword arguments with no defaults, deliberately, and for
    `concert_to_yaml`'s reason: a field added after the writer shipped and
    quietly defaulting to empty is exactly how the three of them were parsed,
    grounded against the page and then dropped for a whole phase. There is one
    production caller.

    The key comes from `dedupe_key` and only from `dedupe_key` -- see the
    module docstring for what a second derivation costs.
    """
    key = dedupe_key(label, opens_at_utc)
    proposal = (await session.execute(
        select(RoundProposal).where(
            RoundProposal.concert_id == concert_id,
            RoundProposal.dedupe_key == key,
        )
    )).scalar_one_or_none()

    if proposal is None:
        proposal = RoundProposal(
            concert_id=concert_id, dedupe_key=key, first_seen_at=now
        )
        session.add(proposal)

    proposal.label = label
    proposal.kind = kind
    proposal.opens_at_utc = opens_at_utc
    proposal.closes_at_utc = closes_at_utc
    proposal.results_at_utc = results_at_utc
    proposal.payment_deadline_at_utc = payment_deadline_at_utc
    # A NEW list of STRINGS every time, never the caller's own object.
    #
    # New, because the ORM tracks a JSON column by identity: mutating a list
    # handed in here would leave a change SQLAlchemy never flushes.
    #
    # Strings, because this is the WRITER and the column is JSON. A leg label
    # is free editor text, and a date-shaped one (`2026-01-05`) comes back from
    # a model's unquoted YAML as a `datetime.date` -- which `json.dumps`
    # refuses, so the flush raises `StatementError` and, in the poll, abandons
    # the whole run rather than one proposal. The poll's `_applies_to_labels`
    # coerces too, and that duplication is deliberate: this guard is the one
    # nothing can route around. Phase 2's apply path adds more writers, and a
    # type rule that lives at one call site is a type rule the next call site
    # does not have.
    proposal.applies_to_labels = [str(leg).strip() for leg in applies_to_labels]
    proposal.evidence_yaml = evidence_yaml
    proposal.source_url = source_url
    await session.flush()
    return proposal


async def pending_proposals(session: AsyncSession) -> list[RoundProposal]:
    """Everything still awaiting a human, oldest sighting first.

    Pending is BOTH timestamps NULL (`FetchDomain`'s idiom). Oldest first
    because the queue is a worklist: the thing nobody has looked at for a week
    is the thing to look at, and a newest-first list buries it.
    """
    return list((await session.execute(
        select(RoundProposal)
        .where(
            RoundProposal.dismissed_at.is_(None),
            RoundProposal.applied_at.is_(None),
        )
        .order_by(RoundProposal.first_seen_at, RoundProposal.id)
    )).scalars().all())


async def pending_proposals_for(session: AsyncSession, concert_id: int) -> list[RoundProposal]:
    """`pending_proposals`, narrowed to ONE concert -- what the draft page
    (`GET /admin/quiet-ladders/proposals/{event_id}`) reads. Same
    both-timestamps-NULL definition of pending and the same oldest-first
    order, for the same reason `pending_proposals` gives: the longest-waiting
    proposal on THIS concert is still the one worth reviewing first.
    """
    return list((await session.execute(
        select(RoundProposal)
        .where(
            RoundProposal.concert_id == concert_id,
            RoundProposal.dismissed_at.is_(None),
            RoundProposal.applied_at.is_(None),
        )
        .order_by(RoundProposal.first_seen_at, RoundProposal.id)
    )).scalars().all())


async def mark_proposal_applied(
    session: AsyncSession, proposal_id: int, now: datetime
) -> bool:
    """Stamp "a human turned this into a real round". Returns False if gone.

    A STAMP AND NOTHING ELSE, and the ordering rule that goes with it lives at
    the call site: the route creates the `Round`, calls this, then calls
    `sync_concert`. Without the stamp the proposal stays pending, reappears on
    the draft page forever, and the next press puts a SECOND copy of the same
    round on a concert people already hold reminders for.

    `applied_at`, never `dismissed_at`: the two columns answer different
    questions and `dismissed_keys_for` reads only the second, deliberately --
    an applied proposal needs no skip list, because the round it produced is
    what filters it out on the held side next poll (see that function).

    Silent on a row that no longer exists, like `record_ladder_polled`: a
    concert deleted between the page render and the button press CASCADEs its
    proposals away, and that is a stale page, not an error worth a traceback.
    The caller is the one holding a 404's worth of context.
    """
    proposal = await session.get(RoundProposal, proposal_id)
    if proposal is None:
        return False
    proposal.applied_at = now
    await session.flush()
    return True


async def mark_proposal_dismissed(
    session: AsyncSession, proposal_id: int, now: datetime
) -> bool:
    """Stamp "no, never propose this again". Returns False if gone.

    The one that has to STICK. The poll runs daily over the same quiet
    concerts, so a dismissal that did not persist would come back tomorrow and
    every day after until the queue is noise nobody reads -- which is why
    `dismissed_keys_for` exists and why `upsert_proposal` never clears these
    columns on a re-sighting.

    Writes no `Round` and calls no `sync_concert`, on purpose: there is
    nothing to schedule. A dismissal that quietly fell through to the apply
    path would put the model's unreviewed reading straight into the catalogue,
    which is the exact opposite of what the operator pressed.
    """
    proposal = await session.get(RoundProposal, proposal_id)
    if proposal is None:
        return False
    proposal.dismissed_at = now
    await session.flush()
    return True


def held_rounds_by_key(rounds: Sequence[Round]) -> dict[str, Round]:
    """A concert's OWN rounds, keyed the same way a proposal already is.

    `Round` already carries every attribute `domain.round_proposals.HeldRound`
    copies (`label`, `opens_at_utc`, `closes_at_utc`, `results_at_utc`,
    `payment_deadline_at_utc`) -- keyed straight off the ORM row through
    `dedupe_key` rather than through a `HeldRound` wrapper, which would only be
    a second object naming the same five attributes for no reader here.
    """
    return {dedupe_key(r.label, r.opens_at_utc): r for r in rounds}


def classify_stored_proposal(proposal: RoundProposal, held_by_key: dict[str, Round]) -> str:
    """"new", "changed" or "resolved" for one PENDING `RoundProposal` against
    the concert's CURRENT rounds.

    Derived HERE, on every render, and never stored on the row: a proposal
    whose round is later fixed by hand (an editor retypes the closing date the
    proposal already named) simply stops being reported as changed the next
    time this runs, with no second write anywhere to keep in step -- the same
    "resolves itself" property the review page's docstring promises.

    The DIFFERS check is `domain.round_proposals.anchors_differ`, the SAME
    predicate `_differs` calls -- not a second copy of "these three fields,
    opens excluded". A `RoundProposal` row already stores its three anchors as
    typed, aware UTC columns (`upsert_proposal` ran the model's JST text
    through `proposed_stamp_utc` once, at write time), so they are handed to
    `anchors_differ` directly. Note for anyone tempted to justify that by
    "avoiding a lossy round trip": routing them back through JST text and
    re-parsing would NOT actually lose anything here -- `proposed_stamp_utc`
    only ever produces `:00` seconds/microseconds, and JST is a fixed +09:00
    offset with no DST, so UTC -> JST -> "%Y-%m-%d %H:%M" text -> UTC is exact
    for every value this column can hold. The real reason this function does
    not call `_differs` directly is a SHAPE mismatch, not a precision one:
    `_differs` wants a `ProposedRound` keyed by
    `apply_closes_jst`/`results_jst`/`payment_deadline_jst`, and wrapping a
    `RoundProposal` row in one just to satisfy that shape would be more
    machinery than the three-line comparison it exists to reach -- which is
    exactly why that comparison was pulled out into `anchors_differ` instead,
    so both shapes can call the SAME code without either constructing the
    other's wrapper.

    The key is `proposal.dedupe_key`, the column `upsert_proposal` wrote
    through `domain.round_proposals.dedupe_key` at insert time -- read back
    rather than recomputed, so this always matches on the exact key the writer
    minted, never a second derivation of it. `held_by_key` holds `Round` ORM
    rows, not `HeldRound` wrappers (see `held_rounds_by_key`'s own docstring
    for why); `anchors_differ` only ever reads the three attributes both types
    name identically, so handing it a `Round` where it is typed for a
    `HeldRound` is the same duck-typing `held_rounds_by_key` already relies on
    to skip constructing one.
    """
    held = held_by_key.get(proposal.dedupe_key)
    if held is None:
        return "new"
    if anchors_differ(
        held,
        proposal.closes_at_utc,
        proposal.results_at_utc,
        proposal.payment_deadline_at_utc,
    ):
        return "changed"
    return "resolved"


@dataclass(frozen=True)
class ProposalGroup:
    """`pending_proposals` grouped by the concert it names, for the review
    page (GET /admin/quiet-ladders/proposals). Group order follows each
    concert's earliest pending proposal -- the same oldest-first worklist
    reading `pending_proposals` already returns, just folded by concert
    instead of left flat."""

    concert_id: int
    event_id: str
    title: str
    proposals: list[RoundProposal]


async def pending_proposal_groups(session: AsyncSession) -> list[ProposalGroup]:
    """`pending_proposals`, grouped by concert, with any proposal already
    RESOLVED against the concert's CURRENT rounds filtered out.

    Round 1 of this feature's own docs review found this filter MISSING here
    while `_draft_row` (`web/routes/quiet_ladders.py`) already had it -- a
    proposal an operator fixed by hand on the ordinary concert editor kept
    showing on this page (the one the digest DM links to) with a phantom
    pending count, and only the per-concert draft page ever resolved it.
    `classify_stored_proposal` is the SAME derivation `_draft_row` uses --
    never a second comparison written here -- for the reason its own
    docstring gives: two copies of "is this still worth showing" is exactly
    the drift `anchors_differ` was pulled out to stop.

    A second query rather than a join: `pending_proposals` is the worklist
    ordering every other reader shares, and re-deriving "pending" here with a
    join would be a second definition of it free to drift from the first.
    A proposal whose concert is missing from the batch load (a delete racing
    this read -- CASCADE removes the proposal with it) is skipped rather than
    raised, the same warns-and-skips habit every reader in this package
    follows for a third party's page; the review page just shows one fewer
    row rather than 500ing.

    `Concert.rounds` is EAGER-loaded here (`selectinload`), one extra query
    for the whole batch, never per-concert and never a bare `concert.rounds`:
    that relationship has no `lazy="raise"` guard the way `venue_tag` does,
    so an unloaded access during async template rendering is a
    `MissingGreenlet` 500, not a clean failure. `held_rounds_by_key` is
    computed once per concert and cached, not once per proposal, since a
    concert can hold several pending proposals and rebuilding the same dict
    for each would be pure waste.

    A concert whose every pending proposal resolves this way is dropped from
    the result ENTIRELY, the same as if it had never had a proposal at all --
    which is what lets the worklist actually empty rather than showing a
    group with nothing left in it.
    """
    proposals = await pending_proposals(session)
    if not proposals:
        return []
    concerts = {
        c.id: c
        for c in (
            await session.execute(
                select(Concert)
                .where(Concert.id.in_({p.concert_id for p in proposals}))
                .options(selectinload(Concert.rounds))
            )
        ).scalars().all()
    }
    held_by_concert: dict[int, dict[str, Round]] = {}
    groups: dict[int, ProposalGroup] = {}
    order: list[int] = []
    for proposal in proposals:
        concert = concerts.get(proposal.concert_id)
        if concert is None:
            continue
        held_by_key = held_by_concert.get(concert.id)
        if held_by_key is None:
            held_by_key = held_rounds_by_key(concert.rounds)
            held_by_concert[concert.id] = held_by_key
        if classify_stored_proposal(proposal, held_by_key) == "resolved":
            continue
        group = groups.get(proposal.concert_id)
        if group is None:
            group = ProposalGroup(
                concert_id=concert.id,
                event_id=concert.event_id,
                title=concert.title,
                proposals=[],
            )
            groups[proposal.concert_id] = group
            order.append(proposal.concert_id)
        group.proposals.append(proposal)
    return [groups[cid] for cid in order]


async def dismissed_keys_for(session: AsyncSession, concert_id: int) -> set[str]:
    """The keys this concert's poll must not propose again.

    Dismissed only -- an applied proposal needs no skip list, because applying
    it puts a real `Round` on the concert and `classify_proposals` then
    filters it out on the held side, where the check belongs.
    """
    return set((await session.execute(
        select(RoundProposal.dedupe_key).where(
            RoundProposal.concert_id == concert_id,
            RoundProposal.dismissed_at.is_not(None),
        )
    )).scalars().all())


async def round_poll_due(session: AsyncSession, now: datetime) -> bool:
    """Has it been a day? True also when the poll has never run at all."""
    state = await session.get(RoundPollState, ROUND_POLL_STATE_ID)
    if state is None or state.last_run_at is None:
        return True
    return now - state.last_run_at >= ROUND_POLL_INTERVAL


async def stamp_round_poll_run(session: AsyncSession, now: datetime) -> None:
    """Start the 24-hour clock.

    Called on EVERY run, INCLUDING one that raised, and that is the whole
    point: a run that fails without stamping is due again 60 seconds later,
    fails the same way, and keeps doing so forever -- the trap
    `stamp_discovery_run` documents, here with an LLM call attached to every
    repeat. `scheduler/loop.py` must therefore re-stamp on the clean
    transaction after a rollback, exactly as the discovery block does.

    Deliberately reportless, unlike `stamp_discovery_run`'s counts: the poll's
    per-run report is its DIGEST DM, and a second copy in a state row is a
    second thing to keep honest for no reader.
    """
    state = await session.get(RoundPollState, ROUND_POLL_STATE_ID)
    if state is None:
        state = RoundPollState(id=ROUND_POLL_STATE_ID)
        session.add(state)
    state.last_run_at = now
    await session.flush()


async def queue_round_poll_digest(session: AsyncSession, body: str) -> int:
    """Queue the poll's digest DM to every admin. Returns admins queued.

    Invariant 4: it goes through the notifications outbox and is never sent
    from the pass or the scheduler block directly, which buys retry, ordering
    and Forbidden handling for free. `concert_id` stays NULL, so
    `scheduler.loop._notification_context` falls through to the plain-text path
    and `record_deliveries` skips the concert-title lookup -- the send code
    needs no change at all, exactly as `ops_alert` and `discovery` need none.

    Deliberately NOT in `UNREPORTED_NOTE_KINDS`. That set is only for notices
    that report ON deliveries and would otherwise report their own forever;
    this one reports on a THIRD-PARTY PAGE and is an ordinary notice that
    belongs in `delivery_log` like any other -- the `discovery` notice's
    precedent exactly.

    The transaction stays the caller's: this flushes, never commits.
    """
    if not body:
        return 0

    queued = 0
    for admin_id in sorted(settings.admin_ids):
        # An admin who has never signed into the web app has no `users` row,
        # and `Notification.user_id` is an FK to it -- at flush time, far from
        # the cause. Guarded on ABSENCE rather than calling `ensure_user`
        # unconditionally: that refreshes the username, so an unconditional
        # call would overwrite a real admin's name with this placeholder on
        # every single run. Same guard, same reason, as `evaluate_and_alert`
        # and `_record_and_announce`.
        if await session.get(User, admin_id) is None:
            await ensure_user(session, admin_id, str(admin_id))
        session.add(
            Notification(user_id=admin_id, body=body, kind=ROUND_POLL_NOTE_KIND)
        )
        queued += 1
    await session.flush()
    return queued


async def record_ladder_polled(
    session: AsyncSession, concert_id: int, now: datetime
) -> None:
    """Stamp "the poll read this concert's page".

    NEVER `ladder_rechecked_at_utc`. That column is the OWNER's -- written only
    by /admin/quiet-ladders' Checked button -- and it is what orders that
    worklist (never-checked first, then oldest check). A machine writing it
    would mark every polled concert as attended and sink it to the bottom of
    the list, which is the one page that says which ladders still need a human.
    The two questions are different and both are worth answering, so they get
    two columns; see the comment on the model.

    Silent on a concert that no longer exists: the pass holds ids read at the
    start of a run, and a concert deleted mid-run is not an error worth
    aborting the remaining ones for.
    """
    concert = await session.get(Concert, concert_id)
    if concert is None:
        return
    concert.ladder_polled_at_utc = now
    await session.flush()
