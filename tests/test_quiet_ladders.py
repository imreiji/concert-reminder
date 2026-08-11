"""The quiet-ladders predicate: which tracked concerts have no future anchor.

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
    c = await _concert(
        session, "payload",
        legs=[(at(12, 1), False)],
        rounds=[("最速先行", at(7, 1))],
    )
    c.official_url = "https://example.jp/live"
    c.title_en = "Payload Live"
    await session.flush()

    row = next(r for r in await quiet_ladder_rows(session, NOW) if r.event_id == "payload")
    assert row.title_en == "Payload Live"
    assert row.official_url == "https://example.jp/live"
    assert row.leg_dates == (date(2026, 12, 1),)
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
