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
# The rest of one round's ladder. Four DISTINCT values, deliberately: three of
# the assertions below are about which column a value landed in, and equal
# stamps would let a writer put the payment deadline in the results column.
CLOSES = datetime(2026, 9, 8, 14, 59, tzinfo=UTC)
RESULTS = datetime(2026, 9, 15, 9, 0, tzinfo=UTC)
PAYMENT = datetime(2026, 9, 22, 14, 59, tzinfo=UTC)
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
    closes_at_utc: datetime | None = None,
    results_at_utc: datetime | None = None,
    payment_deadline_at_utc: datetime | None = None,
    applies_to_labels: list[str] | None = None,
    evidence_yaml: str = "",
    now: datetime = NOW,
) -> RoundProposal:
    return await upsert_proposal(
        session,
        concert.id,
        label=label,
        kind=RoundKind.LOTTERY_ROUND,
        opens_at_utc=opens_at_utc,
        closes_at_utc=closes_at_utc,
        results_at_utc=results_at_utc,
        payment_deadline_at_utc=payment_deadline_at_utc,
        applies_to_labels=[] if applies_to_labels is None else applies_to_labels,
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


async def test_the_three_new_fields_round_trip(session):
    """A proposal carries the WHOLE round, not the half phase 1 stored.

    Mutation: dropping any ONE of the three from `upsert_proposal`'s write.
    Hence three SEPARATE assertions on three separate values -- asserting only
    `results_at_utc`, or asserting them as one tuple that happens to be all
    that is checked, lets the other two be dropped and stay green.

    Read back after `expire_all`, so this is what SQLite holds rather than what
    the in-memory object was handed: a column the mapper never persists (or one
    the migration never added) is otherwise invisible here.
    """
    concert = await _concert(session)

    proposal = await _propose(
        session,
        concert,
        closes_at_utc=CLOSES,
        results_at_utc=RESULTS,
        payment_deadline_at_utc=PAYMENT,
        applies_to_labels=["Day 1", "Day 2"],
    )
    proposal_id = proposal.id
    await session.flush()
    session.expire_all()

    stored = await session.get(RoundProposal, proposal_id)
    assert stored.results_at_utc == RESULTS
    assert stored.payment_deadline_at_utc == PAYMENT
    assert stored.applies_to_labels == ["Day 1", "Day 2"]
    # The two phase 1 already stored, so a widening that broke them is caught
    # here rather than by the poll's own test.
    assert stored.opens_at_utc == OPENS
    assert stored.closes_at_utc == CLOSES


async def test_a_refresh_overwrites_the_new_fields_too(session):
    """Today's reading of the page wins on every field except `first_seen_at`.

    Mutation: setting the three new fields only on the INSERT branch. Nothing
    else notices -- the row exists, the counts are right, and the proposal
    simply keeps the first reading forever. A page that corrects its 当落発表
    date, or adds the second leg it turned out to cover, keeps the same
    `dedupe_key` (only the OPEN time is part of it), so the corrected value has
    no other way in.

    Three separate assertions again, for the reason above: one of the three
    left inside the insert branch would survive a tuple compare that only
    happened to cover the other two.
    """
    concert = await _concert(session)
    first = await _propose(
        session,
        concert,
        results_at_utc=RESULTS,
        payment_deadline_at_utc=PAYMENT,
        applies_to_labels=["Day 1"],
    )
    first_id, first_seen = first.id, first.first_seen_at

    corrected_results = RESULTS + timedelta(days=3)
    corrected_payment = PAYMENT + timedelta(days=3)
    again = await _propose(
        session,
        concert,
        results_at_utc=corrected_results,
        payment_deadline_at_utc=corrected_payment,
        applies_to_labels=["Day 1", "Day 2"],
        now=NOW + timedelta(days=1),
    )

    assert await _count(session) == 1
    assert again.id == first_id
    assert again.results_at_utc == corrected_results
    assert again.payment_deadline_at_utc == corrected_payment
    assert again.applies_to_labels == ["Day 1", "Day 2"]
    # Still the one field a re-poll must never refresh.
    assert again.first_seen_at == first_seen


async def test_applies_to_labels_defaults_to_empty_not_null(session):
    """No legs named means EVERY leg -- `Round.applies_to`'s own convention.

    Mutation: making the column nullable with no default. Every reader
    downstream would then have to branch on None as well as on `[]`, and the
    one convention would have two spellings; the review page would tick every
    box for one of them and nothing for the other.

    Constructed WITHOUT the field rather than with an empty list, because that
    is the case the default exists for -- every row written before the column
    existed. Passing `[]` explicitly would pass with no default at all.
    """
    concert = await _concert(session)
    bare = RoundProposal(
        concert_id=concert.id,
        dedupe_key=dedupe_key("先行", None),
        label="先行",
        kind=RoundKind.LOTTERY_ROUND,
        first_seen_at=NOW,
    )
    session.add(bare)
    await session.flush()
    bare_id = bare.id
    session.expire_all()

    stored = await session.get(RoundProposal, bare_id)
    assert stored.applies_to_labels == []
    assert stored.applies_to_labels is not None


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
