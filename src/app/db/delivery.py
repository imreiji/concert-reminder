"""`delivery_log`: a record of every DM this app sends, and the per-tick digest.

Covers BOTH drains deliberately -- reminders and notifications -- because the
likeliest way this app messages the wrong people is a `new_event` notice
fanning across a tag's followers, which is a notification, not a reminder.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.core import DueReminder, _now, ensure_user
from app.db.models import (
    Concert,
    DeliveryLog,
    Notification,
    User,
)
from app.domain.digest import DeliveryFact, build_digest
from app.domain.types import (
    DeliveryOutcome,
    DeliverySource,
)
from app.i18n import loc_field

# ── Delivery log ─────────────────────────────────────────────────────────

# The digest reports on deliveries, so logging its own delivery would make the
# next tick report that, forever, once a minute. Only self-reporting kinds
# belong here. A broadcast does NOT: it terminates after one hop (broadcast ->
# logged -> one digest line -> digest delivered -> not logged -> stop), and
# recording it is the point -- whether a remedy reached its recipients,
# FORBIDDEN ones included, is the question the broadcast was sent asking.
UNREPORTED_NOTE_KINDS = frozenset({"delivery_digest"})


async def record_deliveries(
    session: AsyncSession,
    batch_at_utc: datetime,
    reminder_results: list[tuple[DueReminder, DeliveryOutcome]],
    notification_results: list[tuple[Notification, DeliveryOutcome]],
) -> list[DeliveryLog]:
    """Write one delivery_log row per attempted delivery. Returns the rows.

    The rows themselves rather than a count, so tick() can hand them straight
    to queue_delivery_digest without a second SELECT for the batch it just
    wrote.

    Flushes, never commits: the caller owns transaction boundaries, and in
    tick() this runs in its own commit AFTER the delivery bookkeeping is
    already durable.
    """
    rows: list[DeliveryLog] = []

    for item, outcome in reminder_results:
        rows.append(
            DeliveryLog(
                batch_at_utc=batch_at_utc,
                user_id=item.discord_id,
                source=DeliverySource.REMINDER,
                outcome=outcome,
                anchor=item.anchor,
                concert_title=item.concert_title,
                leg_label=item.day_label,
                round_label=item.round_label,
                concert_id=item.concert_id,
                round_id=item.round_id,
                day_id=item.day_id,
                sent_at_utc=batch_at_utc,
            )
        )

    # One batched lookup for the titles, not one per row: a new_event fan-out
    # is exactly the case with many notifications sharing few concerts.
    note_concert_ids = {
        n.concert_id
        for n, _ in notification_results
        if n.concert_id is not None and n.kind not in UNREPORTED_NOTE_KINDS
    }
    titles: dict[int, str] = {}
    if note_concert_ids:
        res = await session.execute(
            select(Concert.id, Concert.title, Concert.title_en).where(
                Concert.id.in_(note_concert_ids)
            )
        )
        # Resolved at the English locale, through loc_field rather than a
        # hand-rolled coalesce, so "empty string counts as unfilled" stays one
        # rule. English because the only readers of this column are the digest
        # and /admin/deliveries, both English-only by design -- a notification
        # embed is localized per recipient, so no single stored string could
        # reproduce "what was sent" anyway.
        titles = {r.id: loc_field(r, "title", "en") for r in res.all()}

    for note, outcome in notification_results:
        if note.kind in UNREPORTED_NOTE_KINDS:
            continue
        rows.append(
            DeliveryLog(
                batch_at_utc=batch_at_utc,
                user_id=note.user_id,
                source=DeliverySource.NOTIFICATION,
                outcome=outcome,
                note_kind=note.kind,
                concert_title=titles.get(note.concert_id) if note.concert_id else None,
                concert_id=note.concert_id,
                sent_at_utc=batch_at_utc,
            )
        )

    if rows:
        session.add_all(rows)
        await session.flush()
    return rows


async def queue_delivery_digest(
    session: AsyncSession, batch_at_utc: datetime, rows: list[DeliveryLog]
) -> int:
    """Queue the admin digest for this batch. Returns admins queued.

    Goes through the notifications outbox rather than a direct DM -- that is
    invariant 4, and it buys retry, ordering and Forbidden handling for free.
    kind="delivery_digest" with concert_id=None falls through
    scheduler.loop._notification_context to the plain-text path, so the send
    code needs no changes. That kind is also in UNREPORTED_NOTE_KINDS, which
    is what stops this digest reporting its own delivery next tick.
    """
    if not rows or not settings.bot_enabled:
        return 0

    body = build_digest(
        [
            DeliveryFact(
                source=r.source,
                outcome=r.outcome,
                user_id=r.user_id,
                concert_title=r.concert_title,
                leg_label=r.leg_label,
                round_label=r.round_label,
                anchor=r.anchor,
                note_kind=r.note_kind,
                concert_id=r.concert_id,
                round_id=r.round_id,
                day_id=r.day_id,
            )
            for r in rows
        ],
        batch_at_utc,
    )
    if not body:
        return 0

    queued = 0
    for admin_id in settings.admin_ids:
        # An admin who has never logged in has no users row, and
        # Notification.user_id is a FK to it -- the same guard
        # evaluate_and_alert needs, and for the same reason.
        if await session.get(User, admin_id) is None:
            await ensure_user(session, admin_id, str(admin_id))
        session.add(Notification(user_id=admin_id, body=body, kind="delivery_digest"))
        queued += 1
    await session.flush()
    return queued


# Matches deploy/backup.sh's S3 lifecycle so the system has ONE retention
# number rather than two that can drift apart.
DELIVERY_LOG_RETENTION_DAYS = 30


async def prune_delivery_log(session: AsyncSession, now: datetime | None = None) -> int:
    """Delete delivery_log rows older than the retention window. Returns rows
    deleted. Flushes, never commits -- the caller owns the transaction."""
    now = now or _now()
    cutoff = now - timedelta(days=DELIVERY_LOG_RETENTION_DAYS)
    res = await session.execute(delete(DeliveryLog).where(DeliveryLog.batch_at_utc < cutoff))
    await session.flush()
    return res.rowcount or 0


@dataclass(frozen=True)
class BatchSummary:
    """One tick's deliveries, aggregated on read. There is no stored count
    anywhere, so these can never disagree with the rows they describe."""

    batch_at_utc: datetime
    sent: int
    users: int
    failed: int


async def delivery_batches(session: AsyncSession, limit: int = 50) -> list[BatchSummary]:
    """Newest first. Capped rather than paginated: the retention window is 30
    days and a batch only exists if it delivered something."""
    res = await session.execute(
        select(
            DeliveryLog.batch_at_utc,
            func.count(DeliveryLog.id),
            func.count(func.distinct(DeliveryLog.user_id)),
            func.sum(case((DeliveryLog.outcome != DeliveryOutcome.SUCCESS.value, 1), else_=0)),
        )
        .group_by(DeliveryLog.batch_at_utc)
        .order_by(DeliveryLog.batch_at_utc.desc())
        .limit(limit)
    )
    return [
        BatchSummary(batch_at_utc=at, sent=total - (failed or 0), users=users, failed=failed or 0)
        for at, total, users, failed in res.all()
    ]


async def delivery_failures(session: AsyncSession, limit: int = 100) -> list[DeliveryLog]:
    """Every non-SUCCESS row in the window, newest first, independent of
    batch. The digest says something broke in the last minute; this says
    whether it has been breaking all week."""
    res = await session.execute(
        select(DeliveryLog)
        .where(DeliveryLog.outcome != DeliveryOutcome.SUCCESS.value)
        .order_by(DeliveryLog.batch_at_utc.desc())
        .limit(limit)
    )
    return list(res.scalars())


async def delivery_batch_rows(
    session: AsyncSession, batch_at_utc: datetime
) -> list[DeliveryLog]:
    res = await session.execute(
        select(DeliveryLog).where(DeliveryLog.batch_at_utc == batch_at_utc).order_by(DeliveryLog.id)
    )
    return list(res.scalars())
