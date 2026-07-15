"""Schema behavior tests against a real (in-memory) SQLite database.

These verify the three properties the models exist to guarantee:
  1. Naive datetimes are rejected at the boundary; reads come back aware-UTC.
  2. The functional dedupe index makes duplicate queue rows impossible.
  3. Deleting a concert cascades through days/windows/rules/queue.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
    Base,
    Concert,
    ConcertDay,
    ReminderQueue,
    ReminderRule,
    User,
    Window,
)
from app.domain.types import Anchor, WindowKind

AWARE = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _fk_on(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def seed(s: Session) -> tuple[Concert, Window, ReminderRule]:
    user = User(discord_id=42, username="reiji")
    concert = Concert(title="Test Live", created_by=42)
    s.add_all([user, concert])
    s.flush()
    window = Window(
        concert_id=concert.id, kind=WindowKind.LOTTERY_ROUND, label="R1", opens_at_utc=AWARE
    )
    day = ConcertDay(concert_id=concert.id, label="Day 1", starts_at_utc=AWARE)
    rule = ReminderRule(user_id=42, concert_id=concert.id, anchor=Anchor.OPENS, offset_days=-1)
    s.add_all([window, day, rule])
    s.flush()
    return concert, window, rule


def test_naive_datetime_rejected(session):
    session.add(User(discord_id=1, username="x", created_at=datetime(2026, 1, 1)))
    with pytest.raises(Exception, match="naive datetime rejected"):
        session.flush()


def test_datetimes_roundtrip_as_aware_utc(session):
    concert, window, _ = seed(session)
    session.commit()
    session.expire_all()
    got = session.get(Window, window.id)
    assert got.opens_at_utc == AWARE
    assert got.opens_at_utc.tzinfo is not None


def test_dedupe_index_blocks_duplicate_queue_rows(session):
    _, window, rule = seed(session)
    session.add(
        ReminderQueue(rule_id=rule.id, window_id=window.id, anchor=Anchor.OPENS, fire_at_utc=AWARE)
    )
    session.commit()
    session.add(
        ReminderQueue(rule_id=rule.id, window_id=window.id, anchor=Anchor.OPENS, fire_at_utc=AWARE)
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_dedupe_applies_even_with_null_day_ids(session):
    """The coalesce() in the index is exactly for this: NULLs must not slip through."""
    _, _, rule = seed(session)
    session.add(ReminderQueue(rule_id=rule.id, anchor=Anchor.EVENT_START, fire_at_utc=AWARE))
    session.commit()
    session.add(ReminderQueue(rule_id=rule.id, anchor=Anchor.EVENT_START, fire_at_utc=AWARE))
    with pytest.raises(IntegrityError):
        session.commit()


def test_deleting_concert_cascades(session):
    concert, window, rule = seed(session)
    session.add(
        ReminderQueue(rule_id=rule.id, window_id=window.id, anchor=Anchor.OPENS, fire_at_utc=AWARE)
    )
    session.commit()
    session.delete(concert)
    session.commit()
    assert session.query(Window).count() == 0
    assert session.query(ConcertDay).count() == 0
    assert session.query(ReminderRule).count() == 0
    assert session.query(ReminderQueue).count() == 0
    assert session.query(User).count() == 1  # users survive their concerts
