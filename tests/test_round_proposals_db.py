"""The round-proposal table: what makes a dismissal stick, and whose stamp is whose.

Six behaviours, each paired in its docstring with the single edit that would
make it wrong -- because every one of them is silent when broken. A duplicated
proposal just grows the table, a lost dismissal just re-proposes something the
owner already refused, an unstamped clock just re-runs the pass, and a poll
writing the human's stamp just marks the owner's worklist attended.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.db.models import Concert, RoundProposal
from app.db.service import (
    dismissed_keys_for,
    ensure_user,
    pending_proposals,
    quiet_ladder_rows,
    record_ladder_polled,
    round_poll_due,
    stamp_round_poll_run,
    upsert_proposal,
)
from app.domain.round_proposals import dedupe_key
from app.domain.types import RoundKind

NOW = datetime(2026, 8, 13, 3, 0, tzinfo=UTC)
OPENS = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)
SOURCE = "https://example.jp/live/tickets"


async def _concert(session, event_id: str = "quiet-one", **kw) -> Concert:
    await ensure_user(session, 42, "reiji")
    concert = Concert(title=event_id, event_id=event_id, created_by=42, **kw)
    session.add(concert)
    await session.flush()
    return concert


async def _propose(
    session,
    concert: Concert,
    *,
    label: str = "1次先行",
    opens_at_utc: datetime | None = OPENS,
    evidence_yaml: str = "",
    now: datetime = NOW,
) -> RoundProposal:
    return await upsert_proposal(
        session,
        concert.id,
        label=label,
        kind=RoundKind.LOTTERY_ROUND,
        opens_at_utc=opens_at_utc,
        closes_at_utc=None,
        evidence_yaml=evidence_yaml,
        source_url=SOURCE,
        now=now,
    )


async def _count(session) -> int:
    return (await session.execute(select(func.count()).select_from(RoundProposal))).scalar_one()


async def test_a_second_poll_of_the_same_round_updates_rather_than_duplicates(session):
    """Mutation: making upsert an unconditional INSERT. Nothing else in the
    suite would notice; the table just grows one row per day per round.

    The second sighting deliberately carries SECONDS on its open time. That is
    not a contrived input -- `yaml_import._dt` accepts a YAML timestamp with
    seconds, so a live round can hold one while the poll's own parse of
    "%Y-%m-%d %H:%M" never can. `dedupe_key` truncates to the minute for
    exactly that reason, so a SECOND mutation is caught here too: deriving the
    key in `upsert_proposal` by formatting the datetime itself instead of
    calling `dedupe_key`, which re-proposes the same round every single day.
    """
    concert = await _concert(session)
    first = await _propose(session, concert, evidence_yaml="line: 一次先行受付開始")
    first_id, first_seen = first.id, first.first_seen_at

    tomorrow = NOW + timedelta(days=1)
    again = await _propose(
        session,
        concert,
        opens_at_utc=OPENS.replace(second=41),
        evidence_yaml="line: 一次先行受付開始（詳細）",
        now=tomorrow,
    )

    assert await _count(session) == 1
    assert again.id == first_id
    # The row is UPDATED, not merely left alone: today's reading of the page wins.
    assert again.evidence_yaml == "line: 一次先行受付開始（詳細）"
    # ...except first_seen_at, which answers "since when has this been proposed"
    # and would be a lie if every re-poll refreshed it.
    assert again.first_seen_at == first_seen


async def test_a_dismissed_key_is_reported_so_the_next_poll_can_skip_it(session):
    """Mutation: dismissed_keys_for returning an empty set -- a dismissed
    proposal then comes back tomorrow and every day after.

    Also pinned: the answer is per-concert and it is the DISMISSED rows only.
    Returning every key would make a still-pending proposal look refused, and
    the poll would stop re-confirming it.
    """
    concert = await _concert(session)
    other = await _concert(session, "another")

    refused = await _propose(session, concert, label="2次先行")
    refused.dismissed_at = NOW
    await _propose(session, concert, label="一般発売")
    elsewhere = await _propose(session, other, label="他公演先行")
    elsewhere.dismissed_at = NOW
    await session.flush()

    assert await dismissed_keys_for(session, concert.id) == {
        dedupe_key("2次先行", OPENS)
    }


async def test_pending_excludes_dismissed_and_applied(session):
    """Mutation: dropping either NULL check. Seed one of each so dropping
    ONE is visible; with only a dismissed row, dropping the applied check
    passes."""
    concert = await _concert(session)

    open_one = await _propose(session, concert, label="1次先行")
    dismissed = await _propose(session, concert, label="2次先行")
    dismissed.dismissed_at = NOW
    applied = await _propose(session, concert, label="一般発売")
    applied.applied_at = NOW
    await session.flush()

    assert [p.id for p in await pending_proposals(session)] == [open_one.id]


async def test_deleting_the_concert_takes_its_proposals(session):
    """The CASCADE. Mutation: SET NULL instead -- which needs foreign_keys=ON
    to be observable at all, hence the shared fixture.

    A proposal is ABOUT a concert: orphaned it names a page nobody can reach
    and a round nobody can apply, and it would sit in the queue forever.
    """
    concert = await _concert(session)
    survivor = await _concert(session, "still-here")
    await _propose(session, concert)
    await _propose(session, survivor, label="他公演先行")
    assert await _count(session) == 2

    await session.delete(concert)
    await session.flush()

    remaining = (await session.execute(select(RoundProposal))).scalars().all()
    assert [p.concert_id for p in remaining] == [survivor.id]


async def test_the_daily_clock_is_stamped_even_when_the_run_failed(session):
    """Mutation: stamping only on success. The pass then re-runs every 60s
    forever after one bad page -- the exact trap loop.py documents for the
    discovery sweep."""
    # Never run: due, so a fresh deploy polls rather than waiting a day.
    assert await round_poll_due(session, NOW) is True

    try:
        raise RuntimeError("the official page answered 500")
    except RuntimeError:
        pass
    finally:
        await stamp_round_poll_run(session, NOW)

    # 60 seconds later -- the very next tick -- the failed run must NOT re-run.
    assert await round_poll_due(session, NOW + timedelta(seconds=60)) is False
    assert await round_poll_due(session, NOW + timedelta(hours=23)) is False
    # ...and a day later it must, or one failure would stop the pass forever.
    assert await round_poll_due(session, NOW + timedelta(hours=24)) is True


async def test_polling_does_not_touch_the_human_recheck_stamp(session):
    """The worklist-integrity rule. Mutation: record_ladder_polled writing
    ladder_rechecked_at_utc -- which looks harmless and silently clears
    /admin/quiet-ladders' ordering.

    Asserted twice over: on the columns, and on the consequence. Both concerts
    below are quiet (no legs, no rounds), and the worklist sorts oldest check
    first. Insertion order is deliberately the WRONG order, so a poll that
    stamped the human column would tie both keys at `now` and hand the list
    back in insertion order instead.
    """
    recent = await _concert(
        session, "checked-yesterday", ladder_rechecked_at_utc=NOW - timedelta(days=1)
    )
    old = await _concert(
        session, "checked-in-march", ladder_rechecked_at_utc=NOW - timedelta(days=150)
    )
    assert [r.event_id for r in await quiet_ladder_rows(session, NOW)] == [
        "checked-in-march", "checked-yesterday",
    ]

    await record_ladder_polled(session, recent.id, NOW)
    await record_ladder_polled(session, old.id, NOW)

    assert recent.ladder_polled_at_utc == NOW
    assert old.ladder_polled_at_utc == NOW
    assert recent.ladder_rechecked_at_utc == NOW - timedelta(days=1)
    assert old.ladder_rechecked_at_utc == NOW - timedelta(days=150)
    assert [r.event_id for r in await quiet_ladder_rows(session, NOW)] == [
        "checked-in-march", "checked-yesterday",
    ]
