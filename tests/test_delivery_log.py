"""The delivery log, its digest, and the retention prune."""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Concert, DeliveryLog, Notification, User
from app.db.service import (
    DELIVERY_LOG_RETENTION_DAYS,
    UNREPORTED_NOTE_KINDS,
    DueReminder,
    prune_delivery_log,
    record_deliveries,
)
from app.domain.types import Anchor, DeliveryOutcome, DeliverySource


def test_delivery_outcome_lives_in_domain_types():
    assert DeliveryOutcome.SUCCESS.value == "success"
    assert DeliveryOutcome.FORBIDDEN.value == "forbidden"
    assert DeliveryOutcome.TRANSIENT_FAILURE.value == "transient_failure"


def test_delivery_outcome_is_a_str_enum():
    """Every other enum in this app is a StrEnum, and the DB stores .value
    strings. A plain Enum here would serialise differently."""
    assert isinstance(DeliveryOutcome.SUCCESS, str)


def test_scheduler_reexports_the_same_object():
    """scheduler/loop.py keeps working through the import, so no existing
    caller had to change. If these ever diverge, an `is` comparison in tick()
    silently stops matching."""
    from app.scheduler.loop import DeliveryOutcome as FromScheduler

    assert FromScheduler is DeliveryOutcome


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed(session):
    session.add(User(discord_id=1, username="reiji"))
    concert = Concert(event_id="c", title="スノーミク2027", title_en="Snow Miku 2027")
    session.add(concert)
    await session.flush()
    return concert


@pytest.mark.asyncio
async def test_delivery_row_round_trips(db):
    async with db() as s:
        concert = await _seed(s)
        s.add(
            DeliveryLog(
                batch_at_utc=datetime(2026, 7, 28, 14, 23, tzinfo=UTC),
                user_id=1,
                source=DeliverySource.REMINDER,
                outcome=DeliveryOutcome.SUCCESS,
                anchor=Anchor.CLOSES,
                concert_title="Snow Miku 2027",
                leg_label="Day 1",
                round_label="一次先行",
                concert_id=concert.id,
                sent_at_utc=datetime(2026, 7, 28, 14, 23, tzinfo=UTC),
            )
        )
        await s.commit()
        row = (await s.execute(select(DeliveryLog))).scalar_one()
        assert row.outcome is DeliveryOutcome.SUCCESS
        assert row.anchor is Anchor.CLOSES
        assert row.batch_at_utc.tzinfo is not None


@pytest.mark.asyncio
async def test_deleting_the_user_removes_their_rows(db):
    """This table holds personal data -- which events a named person was
    DMed about -- so POST /me/delete's cascade must reach it."""
    async with db() as s:
        await _seed(s)
        s.add(
            DeliveryLog(
                batch_at_utc=datetime(2026, 7, 28, tzinfo=UTC),
                user_id=1,
                source=DeliverySource.REMINDER,
                outcome=DeliveryOutcome.SUCCESS,
                sent_at_utc=datetime(2026, 7, 28, tzinfo=UTC),
            )
        )
        await s.commit()
        await s.delete(await s.get(User, 1))
        await s.commit()
        assert (await s.execute(select(DeliveryLog))).all() == []


@pytest.mark.asyncio
async def test_deleting_the_concert_keeps_the_row_and_the_title(db):
    """The whole point of denormalizing the labels: deleting a concert must
    not erase the record that people were DMed about it. That record IS the
    investigation when a bad edit is the suspect."""
    async with db() as s:
        concert = await _seed(s)
        s.add(
            DeliveryLog(
                batch_at_utc=datetime(2026, 7, 28, tzinfo=UTC),
                user_id=1,
                source=DeliverySource.REMINDER,
                outcome=DeliveryOutcome.SUCCESS,
                concert_title="Snow Miku 2027",
                concert_id=concert.id,
                sent_at_utc=datetime(2026, 7, 28, tzinfo=UTC),
            )
        )
        await s.commit()
        await s.delete(await s.get(Concert, concert.id))
        await s.commit()
        row = (await s.execute(select(DeliveryLog))).scalar_one()
        assert row.concert_id is None
        assert row.concert_title == "Snow Miku 2027"


BATCH = datetime(2026, 7, 28, 14, 23, tzinfo=UTC)


def _reminder(concert_id, **kw):
    base = dict(
        queue_id=7,
        discord_id=1,
        user_timezone="America/Moncton",
        concert_title="Snow Miku 2027",
        anchor=Anchor.CLOSES,
        fire_at_utc=BATCH,
        concert_id=concert_id,
        round_label="一次先行",
        day_label="Day 1",
    )
    base.update(kw)
    return DueReminder(**base)


@pytest.mark.asyncio
async def test_logs_a_reminder_delivery(db):
    async with db() as s:
        concert = await _seed(s)
        n = await record_deliveries(
            s, BATCH, [(_reminder(concert.id), DeliveryOutcome.SUCCESS)], []
        )
        await s.commit()
        assert len(n) == 1
        row = (await s.execute(select(DeliveryLog))).scalar_one()
        assert row.source is DeliverySource.REMINDER
        assert row.round_label == "一次先行"
        assert row.leg_label == "Day 1"
        assert row.anchor is Anchor.CLOSES
        assert row.note_kind is None


@pytest.mark.asyncio
async def test_logs_a_notification_delivery(db):
    async with db() as s:
        concert = await _seed(s)
        note = Notification(user_id=1, body="x", concert_id=concert.id, kind="new_event")
        s.add(note)
        await s.flush()
        await record_deliveries(s, BATCH, [], [(note, DeliveryOutcome.SUCCESS)])
        await s.commit()
        row = (await s.execute(select(DeliveryLog))).scalar_one()
        assert row.source is DeliverySource.NOTIFICATION
        assert row.note_kind == "new_event"
        assert row.anchor is None
        # Title resolved from the concert so the row survives its deletion.
        assert row.concert_title == "Snow Miku 2027"


@pytest.mark.asyncio
async def test_logs_transient_and_forbidden_too(db):
    """A digest of successes only would hide the incident it exists to show."""
    async with db() as s:
        concert = await _seed(s)
        await record_deliveries(
            s,
            BATCH,
            [
                (_reminder(concert.id), DeliveryOutcome.FORBIDDEN),
                (_reminder(concert.id, queue_id=8), DeliveryOutcome.TRANSIENT_FAILURE),
            ],
            [],
        )
        await s.commit()
        outcomes = {r.outcome for r in (await s.execute(select(DeliveryLog))).scalars()}
        assert outcomes == {DeliveryOutcome.FORBIDDEN, DeliveryOutcome.TRANSIENT_FAILURE}


@pytest.mark.asyncio
async def test_the_digest_notification_is_never_logged(db):
    """THE feedback-loop guard. Log the digest's own delivery and the next
    tick reports it, forever, once per minute. Asserted directly rather than
    inferred from the exclusion set's contents."""
    async with db() as s:
        await _seed(s)
        note = Notification(user_id=1, body="digest", kind="delivery_digest")
        s.add(note)
        await s.flush()
        n = await record_deliveries(s, BATCH, [], [(note, DeliveryOutcome.SUCCESS)])
        await s.commit()
        assert n == []
        assert (await s.execute(select(DeliveryLog))).all() == []


def test_only_the_digest_is_excluded_from_logging():
    """A broadcast is NOT excluded, deliberately -- see the next test. Only
    the digest can report on itself, and only self-reporting loops."""
    assert UNREPORTED_NOTE_KINDS == frozenset({"delivery_digest"})


@pytest.mark.asyncio
async def test_a_broadcast_delivery_is_logged(db):
    """The reason the exclusion was removed: 'did my remedy actually reach
    them?' is the question a broadcast is sent asking, and it is only
    answerable if the deliveries are recorded."""
    async with db() as s:
        await _seed(s)
        note = Notification(user_id=1, body="sorry about that", kind="admin_broadcast")
        s.add(note)
        await s.flush()
        rows = await record_deliveries(s, BATCH, [], [(note, DeliveryOutcome.SUCCESS)])
        await s.commit()
        assert len(rows) == 1
        assert rows[0].note_kind == "admin_broadcast"


@pytest.mark.asyncio
async def test_prune_deletes_past_the_window_and_spares_inside_it(db):
    now = datetime(2026, 7, 28, tzinfo=UTC)
    async with db() as s:
        await _seed(s)
        for days in (1, 29, 31, 400):
            s.add(
                DeliveryLog(
                    batch_at_utc=now - timedelta(days=days),
                    user_id=1,
                    source=DeliverySource.REMINDER,
                    outcome=DeliveryOutcome.SUCCESS,
                    sent_at_utc=now - timedelta(days=days),
                )
            )
        await s.commit()
        assert await prune_delivery_log(s, now) == 2
        await s.commit()
        remaining = sorted(
            (now - r.batch_at_utc).days
            for r in (await s.execute(select(DeliveryLog))).scalars()
        )
        assert remaining == [1, 29]


def test_retention_matches_the_backup_lifecycle():
    """One retention number in the system, not two -- deploy/backup.sh's S3
    lifecycle is 30 days."""
    assert DELIVERY_LOG_RETENTION_DAYS == 30
