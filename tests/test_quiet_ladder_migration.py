"""The blanket backfill: every existing concert is stamped, so the first
reconcile pass after deploy has no newcomers and therefore sends no DM."""

from datetime import UTC, datetime

from sqlalchemy import select

from app.db.models import Concert
from app.db.service import ensure_user


async def test_the_stamp_columns_are_nullable_and_round_trip_as_aware_utc(session):
    """NAMED FOR WHAT IT ASSERTS. Every test DB is built from Base.metadata, so
    this test cannot see the migration at all -- it pins the MODEL half: the
    columns exist, default to NULL on a new row, and come back aware (invariant
    1).

    The backfill's actual effect is asserted where it is observable:
    test_a_stamped_concert_is_never_a_newcomer (Task 4) proves a stamped row
    produces no DM, and the manual step in this plan's final verification
    section runs the real migration against a real DB. A test named
    'after_migration' that never runs a migration is the proxy-assertion trap:
    it passes for reasons unrelated to its claim."""
    await ensure_user(session, 42, "reiji")
    concert = Concert(title="Fresh", event_id="fresh", created_by=42)
    session.add(concert)
    await session.flush()

    assert concert.quiet_since_utc is None
    assert concert.ladder_rechecked_at_utc is None

    concert.quiet_since_utc = datetime(2026, 8, 11, 9, 0, tzinfo=UTC)
    await session.flush()
    stored = (await session.execute(
        select(Concert).where(Concert.id == concert.id)
    )).scalar_one()
    assert stored.quiet_since_utc == datetime(2026, 8, 11, 9, 0, tzinfo=UTC)
    assert stored.quiet_since_utc.tzinfo is not None  # invariant 1
