"""The reminder scheduler: every 60s, drain due reminders into Discord DMs.

Failure philosophy (each case is deliberate):
  * Bot not ready / web-only mode  -> skip the tick, rows stay queued.
  * DM delivered                   -> mark sent. Only success marks sent.
    Also clears User.dm_blocked_since.
  * Forbidden (user blocks DMs)    -> mark sent anyway, log a warning, and
    set User.dm_blocked_since (surfaced as a sitewide banner -- see
    auth.SessionUser.dm_blocked). Retrying forever would spam the log and
    never succeed; the row is dead.
  * Any other error (network...)   -> leave unsent; next tick retries.
    Doesn't touch dm_blocked_since -- an unrelated hiccup says nothing
    about whether DMs are actually blocked.
  * Whole-tick exception           -> logged, loop survives. The loop dying
    silently is the one unacceptable outcome for a reminder app.

Concurrency: sends run under a bounded semaphore rather than a fixed
per-message delay. discord.py's own HTTPClient already paces/retries per
Discord's returned rate-limit bucket headers, so a manual gap on top of
that is strictly more conservative than necessary and caps throughput at
1 msg/sec regardless of how many reminders are actually due. Every DB
touch (fetching due rows, building notification embeds, marking sent)
stays strictly sequential on the one shared AsyncSession -- it is not
safe for concurrent use -- only the actual Discord network calls run
concurrently.
"""

import asyncio
import logging
from datetime import UTC, datetime
from enum import Enum

import discord

from app.bot.messages import (
    build_leg_cancelled_message,
    build_new_event_message,
    build_reminder_message,
)
from app.db.service import (
    DueReminder,
    due_notifications,
    due_reminders,
    leg_cancelled_context,
    mark_notification_sent,
    mark_sent,
    notice_context,
    record_dm_outcome,
)
from app.db.session import SessionMaker
from app.scheduler import heartbeat

log = logging.getLogger(__name__)

TICK_SECONDS = 60
SEND_CONCURRENCY = 5  # bounded in-flight Discord calls; discord.py's own
                      # rate limiter is the real backstop beyond this.


class DeliveryOutcome(Enum):
    """A DM send's result. Distinct from "should this row be marked sent"
    (SUCCESS and FORBIDDEN both do; TRANSIENT_FAILURE doesn't) and from
    "should the per-user dm_blocked_since flag change" (SUCCESS clears it,
    FORBIDDEN sets it, TRANSIENT_FAILURE touches neither)."""

    SUCCESS = "success"
    FORBIDDEN = "forbidden"
    TRANSIENT_FAILURE = "transient_failure"


async def deliver(bot, item: DueReminder) -> DeliveryOutcome:
    """Send one reminder DM (embed + buttons). Pure Discord I/O -- no
    session access, safe to run concurrently."""
    try:
        user = bot.get_user(item.discord_id) or await bot.fetch_user(item.discord_id)
        embed, view = build_reminder_message(item)
        await user.send(embed=embed, view=view)
        return DeliveryOutcome.SUCCESS
    except discord.Forbidden:
        log.warning(
            "user %s has DMs closed; dropping reminder %s", item.discord_id, item.queue_id
        )
        return DeliveryOutcome.FORBIDDEN  # permanent failure: retrying can never succeed
    except discord.HTTPException as e:
        log.error("transient send failure for queue row %s: %s", item.queue_id, e)
        return DeliveryOutcome.TRANSIENT_FAILURE  # leave unsent; next tick retries


async def _notification_context(session, note):
    """DB-bound prep for one notification's message payload -- reads the
    session, so callers must run this sequentially, never concurrently.
    Dispatches on note.kind since different notice kinds need different
    context shapes (a leg-cancellation notice doesn't need the new-event
    context's subscriber-state fields, and vice versa)."""
    if note.kind == "leg_cancelled":
        return await leg_cancelled_context(session, note.concert_id) if note.concert_id else None
    return await notice_context(session, note.concert_id, note.user_id) if note.concert_id else None


async def _send_notification(bot, note, ctx) -> DeliveryOutcome:
    """Send a notice DM. Structured (ctx set) -> rich embed with the
    state-aware buttons; otherwise the plain-text fallback body. Pure
    Discord I/O -- no session access, safe to run concurrently."""
    try:
        user = bot.get_user(note.user_id) or await bot.fetch_user(note.user_id)
        if ctx is not None and note.kind == "leg_cancelled":
            embed, view = build_leg_cancelled_message(ctx)
            await user.send(embed=embed, view=view)
        elif ctx is not None:
            embed, view = build_new_event_message(ctx)
            await user.send(embed=embed, view=view)
        else:
            await user.send(note.body)
        return DeliveryOutcome.SUCCESS
    except discord.Forbidden:
        log.warning("user %s has DMs closed; dropping notification", note.user_id)
        return DeliveryOutcome.FORBIDDEN
    except discord.HTTPException as e:
        log.error("transient notification failure for user %s: %s", note.user_id, e)
        return DeliveryOutcome.TRANSIENT_FAILURE


async def tick(bot) -> int:
    """One scheduler pass. Returns how many messages were delivered."""
    now = datetime.now(UTC)
    delivered = 0
    sem = asyncio.Semaphore(SEND_CONCURRENCY)

    async def bounded_deliver(item: DueReminder):
        async with sem:
            return item, await deliver(bot, item)

    async def bounded_send_notification(note, ctx):
        async with sem:
            return note, await _send_notification(bot, note, ctx)

    async with SessionMaker() as session:
        items = await due_reminders(session, now)
        for item, outcome in await asyncio.gather(*(bounded_deliver(i) for i in items)):
            if outcome in (DeliveryOutcome.SUCCESS, DeliveryOutcome.FORBIDDEN):
                await mark_sent(session, item.queue_id, now)
                delivered += 1
            if outcome is not DeliveryOutcome.TRANSIENT_FAILURE:
                await record_dm_outcome(
                    session, item.discord_id, blocked=outcome is DeliveryOutcome.FORBIDDEN
                )

        notes = await due_notifications(session)
        # DB-bound prep stays sequential on the one shared session...
        prepared = [(note, await _notification_context(session, note)) for note in notes]
        # ...then the actual Discord sends run concurrently.
        for note, outcome in await asyncio.gather(
            *(bounded_send_notification(note, ctx) for note, ctx in prepared)
        ):
            if outcome in (DeliveryOutcome.SUCCESS, DeliveryOutcome.FORBIDDEN):
                await mark_notification_sent(session, note.id)
                delivered += 1
            if outcome is not DeliveryOutcome.TRANSIENT_FAILURE:
                await record_dm_outcome(
                    session, note.user_id, blocked=outcome is DeliveryOutcome.FORBIDDEN
                )

        await session.commit()
    return delivered


async def reminder_loop(bot) -> None:
    if bot is None:
        log.info("web-only mode: scheduler idle (reminders queue up, nothing sends)")
        while True:
            heartbeat.beat()
            await asyncio.sleep(TICK_SECONDS)

    await bot.wait_until_ready()
    log.info("scheduler running: tick every %ss", TICK_SECONDS)
    while True:
        heartbeat.beat()
        try:
            n = await tick(bot)
            if n:
                log.info("delivered %d reminder(s)", n)
        except Exception:
            log.exception("scheduler tick failed; will retry next tick")
        await asyncio.sleep(TICK_SECONDS)
