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

Phase 1 writes no `dismissed_at` and no `applied_at`: the proposals page ships
read-only and applying is phase 2. `dismissed_keys_for` and
`pending_proposals` read both columns from the start anyway, so the pass and
the page are already correct on the day a button appears -- a filter added
after the fact is a filter every existing reader is missing.
"""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Concert, RoundPollState, RoundProposal
from app.domain.round_proposals import dedupe_key
from app.domain.types import RoundKind

# One poll a day, the discovery sweep's cadence and for the same reason: the
# scheduler ticks every 60 seconds, and a run is a fetch of every quiet
# concert's official page plus an LLM call per page.
ROUND_POLL_INTERVAL = timedelta(hours=24)
ROUND_POLL_STATE_ID = 1


async def upsert_proposal(
    session: AsyncSession,
    concert_id: int,
    *,
    label: str,
    kind: RoundKind,
    opens_at_utc: datetime | None,
    closes_at_utc: datetime | None,
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


async def dismissed_keys_for(session: AsyncSession, concert_id: int) -> set[str]:
    """The keys this concert's poll must not propose again.

    Dismissed only -- an applied proposal needs no skip list, because applying
    it puts a real `Round` on the concert and `new_proposals` then filters it
    out on the held side, where the check belongs.
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
