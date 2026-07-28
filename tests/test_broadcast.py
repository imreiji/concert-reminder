"""The admin broadcast: audit record, hold, and cancel."""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Broadcast, DeliveryLog, Notification, User
from app.db.service import (
    BROADCAST_BODY_MAX,
    HOLD_SECONDS,
    cancel_broadcast,
    due_notifications,
    duplicate_body_recently,
    queue_broadcast,
    resolve_recipients,
)
from app.domain.types import BroadcastMode, DeliveryOutcome, DeliverySource

NOW = datetime(2026, 7, 28, 14, 23, tzinfo=UTC)


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


async def _admin(session, discord_id=1):
    session.add(User(discord_id=discord_id, username="reiji"))
    await session.flush()


@pytest.mark.asyncio
async def test_broadcast_row_round_trips(db):
    async with db() as s:
        await _admin(s)
        s.add(
            Broadcast(
                created_by=1,
                created_at_utc=NOW,
                mode=BroadcastMode.BATCH,
                mode_param="2026-07-28T14:00:00+00:00",
                body="sorry about that",
                recipient_count=40,
                send_after_utc=NOW + timedelta(seconds=120),
            )
        )
        await s.commit()
        row = (await s.execute(select(Broadcast))).scalar_one()
        assert row.mode is BroadcastMode.BATCH
        assert row.cancelled_at_utc is None
        assert row.send_after_utc.tzinfo is not None


@pytest.mark.asyncio
async def test_the_audit_row_survives_deleting_the_admin(db):
    """It records an admin action against other people's DMs. Deleting the
    account that did it must not erase the fact that it happened."""
    async with db() as s:
        await _admin(s)
        s.add(
            Broadcast(
                created_by=1,
                created_at_utc=NOW,
                mode=BroadcastMode.ALL,
                body="hello",
                recipient_count=3,
                send_after_utc=NOW,
            )
        )
        await s.commit()
        await s.delete(await s.get(User, 1))
        await s.commit()
        row = (await s.execute(select(Broadcast))).scalar_one()
        assert row.created_by is None
        assert row.body == "hello"


@pytest.mark.asyncio
async def test_notification_hold_columns_default_to_null(db):
    """NULL on both means exactly today's behaviour. Every existing notice
    path depends on that being true."""
    async with db() as s:
        await _admin(s)
        s.add(Notification(user_id=1, body="x", kind="new_event"))
        await s.commit()
        note = (await s.execute(select(Notification))).scalar_one()
        assert note.send_after_utc is None
        assert note.broadcast_id is None


@pytest.mark.asyncio
async def test_deleting_a_broadcast_orphans_its_notifications(db):
    """SET NULL, not CASCADE: a queued notice is a thing that happened to a
    user, and it should not vanish because the audit row was removed."""
    async with db() as s:
        await _admin(s)
        b = Broadcast(
            created_by=1,
            created_at_utc=NOW,
            mode=BroadcastMode.ALL,
            body="hello",
            recipient_count=1,
            send_after_utc=NOW,
        )
        s.add(b)
        await s.flush()
        s.add(Notification(user_id=1, body="hello", kind="admin_broadcast", broadcast_id=b.id))
        await s.commit()
        await s.delete(await s.get(Broadcast, b.id))
        await s.commit()
        note = (await s.execute(select(Notification))).scalar_one()
        assert note.broadcast_id is None


@pytest.mark.asyncio
async def test_a_notification_with_no_hold_still_drains_immediately(db):
    """THE regression test for this task. Every existing notice -- new_event,
    leg_cancelled, ops_alert, delivery_digest -- has send_after_utc NULL, and
    a NULL must never be read as 'not yet due'. In SQL, `NULL <= now` is NULL,
    not true, so a naive comparison would silently stop the entire outbox."""
    async with db() as s:
        await _admin(s)
        s.add(Notification(user_id=1, body="x", kind="new_event"))
        await s.commit()
        assert len(await due_notifications(s, now=NOW)) == 1


@pytest.mark.asyncio
async def test_a_held_notification_is_not_drained_before_its_moment(db):
    async with db() as s:
        await _admin(s)
        s.add(
            Notification(
                user_id=1,
                body="x",
                kind="admin_broadcast",
                send_after_utc=NOW + timedelta(seconds=120),
            )
        )
        await s.commit()
        assert await due_notifications(s, now=NOW) == []


@pytest.mark.asyncio
async def test_a_held_notification_drains_once_its_moment_passes(db):
    async with db() as s:
        await _admin(s)
        s.add(
            Notification(
                user_id=1,
                body="x",
                kind="admin_broadcast",
                send_after_utc=NOW,
            )
        )
        await s.commit()
        assert len(await due_notifications(s, now=NOW + timedelta(seconds=1))) == 1


async def _users(session, *ids_and_langs):
    for discord_id, lang in ids_and_langs:
        session.add(User(discord_id=discord_id, username=str(discord_id), language=lang))
    await session.flush()


@pytest.mark.asyncio
async def test_resolve_all_returns_every_user_with_language(db):
    async with db() as s:
        await _users(s, (1, "en"), (2, "ja"))
        r = await resolve_recipients(s, BroadcastMode.ALL, None)
        assert dict(r.ids) == {1: "en", 2: "ja"}
        assert r.unmatched == ()


@pytest.mark.asyncio
async def test_resolve_batch_returns_that_batch_s_recipients_only(db):
    async with db() as s:
        await _users(s, (1, "en"), (2, "ja"), (3, "zh"))
        for uid, at in ((1, NOW), (2, NOW), (3, NOW + timedelta(minutes=5))):
            s.add(
                DeliveryLog(
                    batch_at_utc=at,
                    user_id=uid,
                    source=DeliverySource.REMINDER,
                    outcome=DeliveryOutcome.SUCCESS,
                    sent_at_utc=at,
                )
            )
        await s.commit()
        r = await resolve_recipients(s, BroadcastMode.BATCH, NOW.isoformat())
        assert {uid for uid, _ in r.ids} == {1, 2}


@pytest.mark.asyncio
async def test_resolve_explicit_reports_unknown_ids_rather_than_dropping_them(db):
    """Silently discarding a mistyped id is how you conclude you messaged
    someone you did not."""
    async with db() as s:
        await _users(s, (1, "en"))
        r = await resolve_recipients(s, BroadcastMode.EXPLICIT, "1, 999, notanumber")
        assert {uid for uid, _ in r.ids} == {1}
        assert set(r.unmatched) == {"999", "notanumber"}


@pytest.mark.asyncio
async def test_queue_writes_one_held_notification_per_recipient(db):
    async with db() as s:
        await _users(s, (1, "en"), (2, "ja"))
        b = await queue_broadcast(s, 1, BroadcastMode.ALL, None, "hello", now=NOW)
        await s.commit()
        assert b.recipient_count == 2
        assert b.send_after_utc == NOW + timedelta(seconds=HOLD_SECONDS)
        notes = (await s.execute(select(Notification))).scalars().all()
        assert len(notes) == 2
        assert all(n.kind == "admin_broadcast" for n in notes)
        assert all(n.send_after_utc == b.send_after_utc for n in notes)
        assert all(n.broadcast_id == b.id for n in notes)


@pytest.mark.asyncio
async def test_queue_rejects_an_over_long_body(db):
    """Discord's hard limit is 2000 characters and the frame costs some of
    them. A body that silently truncates on send is a broadcast that says
    something other than what was approved."""
    async with db() as s:
        await _users(s, (1, "en"))
        with pytest.raises(ValueError):
            await queue_broadcast(s, 1, BroadcastMode.ALL, None, "x" * (BROADCAST_BODY_MAX + 1))


@pytest.mark.asyncio
async def test_cancel_deletes_unsent_rows_and_counts_the_delivered(db):
    async with db() as s:
        await _users(s, (1, "en"), (2, "ja"))
        b = await queue_broadcast(s, 1, BroadcastMode.ALL, None, "hello", now=NOW)
        await s.commit()
        # Simulate the race: one row drained before the cancel landed.
        notes = (await s.execute(select(Notification))).scalars().all()
        notes[0].sent_at_utc = NOW
        await s.commit()
        cancelled, delivered = await cancel_broadcast(s, b.id, now=NOW)
        await s.commit()
        assert (cancelled, delivered) == (1, 1)
        assert (await s.get(Broadcast, b.id)).cancelled_at_utc is not None
        remaining = (await s.execute(select(Notification))).scalars().all()
        assert len(remaining) == 1 and remaining[0].sent_at_utc is not None


@pytest.mark.asyncio
async def test_cancel_after_full_delivery_reports_nothing_cancelled(db):
    """Honest rather than erroring: the page says what it could not undo."""
    async with db() as s:
        await _users(s, (1, "en"))
        b = await queue_broadcast(s, 1, BroadcastMode.ALL, None, "hello", now=NOW)
        await s.commit()
        for n in (await s.execute(select(Notification))).scalars():
            n.sent_at_utc = NOW
        await s.commit()
        assert await cancel_broadcast(s, b.id, now=NOW) == (0, 1)


@pytest.mark.asyncio
async def test_duplicate_body_within_the_hour_is_reported(db):
    async with db() as s:
        await _users(s, (1, "en"))
        await queue_broadcast(s, 1, BroadcastMode.ALL, None, "hello", now=NOW)
        await s.commit()
        assert await duplicate_body_recently(s, "hello", now=NOW + timedelta(minutes=30))
        assert not await duplicate_body_recently(s, "hello", now=NOW + timedelta(hours=2))
        assert not await duplicate_body_recently(s, "different", now=NOW)
