"""The quiet-ladders predicate: which concerts have no future anchor.

Case-by-case against the design spec's table. The last case is the one that
pins the reuse of next_anchor_at -- if this surface and the agent read API ever
grow two definitions of "future anchor", it fails."""

from datetime import UTC, date, datetime

from app.db.models import Concert, ConcertDay, Round
from app.db.service import ensure_user, quiet_ladder_rows
from app.domain.types import RoundKind

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


def at(month: int, day: int, hour: int = 12) -> datetime:
    return datetime(2026, month, day, hour, tzinfo=UTC)


async def _concert(session, event_id, *, legs=(), rounds=()):
    await ensure_user(session, 42, "reiji")
    c = Concert(title=event_id, event_id=event_id, created_by=42)
    session.add(c)
    await session.flush()
    for starts, cancelled in legs:
        session.add(ConcertDay(
            concert_id=c.id, label="Day", starts_at_utc=starts, cancelled=cancelled,
        ))
    for label, closes in rounds:
        session.add(Round(
            concert_id=c.id, kind=RoundKind.LOTTERY_ROUND,
            label=label, closes_at_utc=closes,
        ))
    await session.flush()
    return c


async def _ids(session):
    return {row.event_id for row in await quiet_ladder_rows(session, NOW)}


async def test_dateless_concert_with_no_rounds_is_quiet(session):
    """The canonical case: imported because the page says 詳細は後日発表."""
    await _concert(session, "dateless")
    assert "dateless" in await _ids(session)


async def test_future_legs_with_every_round_closed_is_quiet(session):
    """最速先行 ran and closed; 一般発売 has not been announced."""
    await _concert(
        session, "closed-ladder",
        legs=[(at(12, 1), False)],
        rounds=[("最速先行", at(7, 1))],
    )
    assert "closed-ladder" in await _ids(session)


async def test_a_future_anchor_keeps_a_concert_off_the_list(session):
    """Pins the next_anchor_at reuse. A round still to close is not quiet."""
    await _concert(
        session, "live-ladder",
        legs=[(at(12, 1), False)],
        rounds=[("一般発売", at(9, 1))],
    )
    assert "live-ladder" not in await _ids(session)


async def test_a_future_results_at_anchors_even_without_closes(session):
    """next_anchor_at scans all four moment fields, not only closes_at_utc.

    No closes_at_utc is set at all -- a re-implementation that read only
    closes_at_utc would find nothing on this round and wrongly call the
    concert quiet."""
    c = await _concert(session, "results-only", legs=[(at(12, 1), False)])
    session.add(Round(
        concert_id=c.id, kind=RoundKind.LOTTERY_ROUND, label="抽選結果",
        results_at_utc=at(9, 1),
    ))
    await session.flush()
    assert "results-only" not in await _ids(session)


async def test_a_future_round_on_only_a_cancelled_leg_is_still_quiet(session):
    """invariant 2's per-round-vs-per-concert distinction, exercised here.

    One leg is live and in the future; the other is cancelled and carries a
    round whose applies_to names ONLY that cancelled leg, with a moment still
    ahead. is_round_cancelled makes that round implicitly cancelled, so it
    must not anchor a concert that otherwise has nothing outstanding -- a
    re-implementation that ignored is_round_cancelled, or read applies_to
    against the wrong leg, would keep this concert off the list."""
    await ensure_user(session, 42, "reiji")
    c = Concert(title="mixed", event_id="mixed", created_by=42)
    session.add(c)
    await session.flush()
    live_leg = ConcertDay(
        concert_id=c.id, label="Live night", starts_at_utc=at(12, 1), cancelled=False,
    )
    dead_leg = ConcertDay(
        concert_id=c.id, label="Cancelled night", starts_at_utc=at(9, 1), cancelled=True,
    )
    session.add_all([live_leg, dead_leg])
    await session.flush()
    session.add(Round(
        concert_id=c.id, kind=RoundKind.LOTTERY_ROUND, label="Dead-leg-only round",
        closes_at_utc=at(9, 15), applies_to=[dead_leg.id],
    ))
    await session.flush()
    assert "mixed" in await _ids(session)


async def test_a_concert_already_past_is_not_quiet(session):
    """Its ladder is finished, not quiet. The list drains itself."""
    await _concert(
        session, "past",
        legs=[(at(3, 1), False)],
        rounds=[("最速先行", at(2, 1))],
    )
    assert "past" not in await _ids(session)


async def test_a_fully_cancelled_concert_is_not_quiet(session):
    await _concert(
        session, "dead",
        legs=[(at(12, 1), True)],
        rounds=[("最速先行", at(7, 1))],
    )
    assert "dead" not in await _ids(session)


async def test_the_latest_live_leg_decides_a_multi_leg_run(session):
    """A tour whose first night has passed but whose last has not is still
    quiet -- there is a show ahead with no deadline in front of it.

    NOTE: there is no 'undated leg' case to test. ConcertDay.starts_at_utc
    compiles to DATETIME NOT NULL, so 'dateless' can only mean a concert with
    ZERO legs, which test_dateless_concert_with_no_rounds_is_quiet covers.
    """
    await _concert(
        session, "tour",
        legs=[(at(3, 1), False), (at(12, 1), False)],
        rounds=[("最速先行", at(2, 1))],
    )
    assert "tour" in await _ids(session)


async def test_rows_carry_what_a_re_check_needs(session):
    # 16:00 UTC = 01:00 JST the FOLLOWING day, so this pins the _jst_date
    # conversion -- a stray `.date()` on the UTC instant would assert Dec 1,
    # not Dec 2, and still pass if the leg stayed at noon UTC.
    c = await _concert(
        session, "payload",
        legs=[(at(12, 1, 16), False)],
        rounds=[("最速先行", at(7, 1))],
    )
    c.official_url = "https://example.jp/live"
    c.title_en = "Payload Live"
    await session.flush()

    row = next(r for r in await quiet_ladder_rows(session, NOW) if r.event_id == "payload")
    assert row.title_en == "Payload Live"
    assert row.official_url == "https://example.jp/live"
    assert row.leg_dates == (date(2026, 12, 2),)
    assert [r.label for r in row.rounds] == ["最速先行"]


async def test_never_checked_sorts_before_checked(session):
    a = await _concert(session, "checked")
    b = await _concert(session, "never")
    a.quiet_since_utc = at(8, 1)
    a.ladder_rechecked_at_utc = at(8, 10)
    b.quiet_since_utc = at(8, 1)
    await session.flush()

    order = [row.event_id for row in await quiet_ladder_rows(session, NOW)]
    assert order.index("never") < order.index("checked")


async def test_the_sort_pins_the_two_within_group_keys_too(session):
    """The test above pins "never-checked before checked" but leaves the other
    two sort keys free -- both rows there shared quiet_since_utc and only one
    had a rechecked_at_utc. This pins them: within the never-checked group the
    row quiet LONGEST (oldest quiet_since_utc) sorts first; within the checked
    group the row checked LONGEST AGO (oldest ladder_rechecked_at_utc) sorts
    first.

    Values are chosen so a sort that used any single "most recent" timestamp
    across both groups, instead of the never/checked bit first, would
    interleave these four differently -- never-newer's quiet_since_utc is
    later in the calendar than checked-older's rechecked_at_utc. Insertion
    order is scrambled relative to the expected order too, so this cannot
    pass by accident.
    """
    checked_newer = await _concert(session, "checked-newer")
    never_newer = await _concert(session, "never-newer")
    checked_older = await _concert(session, "checked-older")
    never_older = await _concert(session, "never-older")

    checked_newer.quiet_since_utc = at(6, 1)
    checked_newer.ladder_rechecked_at_utc = at(8, 20)
    never_newer.quiet_since_utc = at(8, 9)
    checked_older.quiet_since_utc = at(6, 1)
    checked_older.ladder_rechecked_at_utc = at(7, 15)
    never_older.quiet_since_utc = at(7, 1)
    await session.flush()

    order = [row.event_id for row in await quiet_ladder_rows(session, NOW)]
    never_idxs = [order.index("never-older"), order.index("never-newer")]
    checked_idxs = [order.index("checked-older"), order.index("checked-newer")]

    assert max(never_idxs) < min(checked_idxs)
    assert order.index("never-older") < order.index("never-newer")
    assert order.index("checked-older") < order.index("checked-newer")
