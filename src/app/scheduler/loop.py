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

import discord

from app import i18n
from app.bot.messages import (
    build_leg_cancelled_message,
    build_new_event_message,
    build_reminder_message,
)
from app.db.service import (
    DueReminder,
    due_notifications,
    due_reminders,
    evaluate_and_alert,
    leg_cancelled_context,
    mark_notification_sent,
    mark_sent,
    notice_context,
    prune_delivery_log,
    queue_delivery_digest,
    record_deliveries,
    record_dm_outcome,
)
from app.db.session import SessionMaker
from app.domain.types import DeliveryOutcome
from app.ops import run_checks
from app.scheduler import heartbeat

log = logging.getLogger(__name__)

TICK_SECONDS = 60
SEND_CONCURRENCY = 5  # bounded in-flight Discord calls; discord.py's own
                      # rate limiter is the real backstop beyond this.
HEALTH_EVERY_N_TICKS = 5
_tick_count = 0


async def deliver(bot, item: DueReminder) -> DeliveryOutcome:
    """Send one reminder DM (embed + buttons). Pure Discord I/O -- no
    session access, safe to run concurrently.

    The recipient's locale is set here, not in tick(): message construction
    (build_reminder_message) happens in this coroutine, and each concurrent
    send runs in its own asyncio Task with an isolated ContextVar copy, so
    setting the locale per task is what keeps two recipients' languages from
    racing. Reset to en in a finally so the task's context never leaks a
    stale locale to anything that reuses it.
    """
    try:
        i18n.set_locale(item.user_language)
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
    finally:
        i18n.set_locale("en")


async def _notification_context(session, note):
    """DB-bound prep for one notification's message payload -- reads the
    session, so callers must run this sequentially, never concurrently.
    Dispatches on note.kind since different notice kinds need different
    context shapes (a leg-cancellation notice doesn't need the new-event
    context's subscriber-state fields, and vice versa)."""
    if note.kind == "leg_cancelled":
        return await leg_cancelled_context(
            session, note.concert_id, note.user_id
        ) if note.concert_id else None
    return await notice_context(session, note.concert_id, note.user_id) if note.concert_id else None


async def _send_notification(bot, note, ctx) -> DeliveryOutcome:
    """Send a notice DM. Structured (ctx set) -> rich embed with the
    state-aware buttons; otherwise the plain-text fallback body. Pure
    Discord I/O -- no session access, safe to run concurrently."""
    try:
        # Same per-Task locale discipline as deliver(). Both NoticeContext and
        # LegCancelledContext carry the recipient's language; getattr's "en"
        # default only matters when ctx is None, in which case the plain-text
        # fallback body was already composed at enqueue time and locale is moot.
        i18n.set_locale(getattr(ctx, "user_language", "en"))
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
    finally:
        i18n.set_locale("en")


async def tick(bot) -> int:
    """One scheduler pass. Returns how many messages were delivered."""
    # Counted BEFORE any delivery work, not after it. reminder_loop catches
    # tick exceptions and retries forever, so a tick that raises every minute
    # (a DB lock, a due_reminders regression, an HTTPException escaping a send)
    # would otherwise leave the counter frozen and health evaluation would never
    # run again -- silence in exactly the scenario that most needs an alert.
    # heartbeat.beat() fires before tick(), so scheduler_ok stays true too and
    # the uptime monitor would not notice either.
    global _tick_count
    _tick_count += 1

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
        reminder_results = await asyncio.gather(*(bounded_deliver(i) for i in items))
        for item, outcome in reminder_results:
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
        notification_results = await asyncio.gather(
            *(bounded_send_notification(note, ctx) for note, ctx in prepared)
        )
        for note, outcome in notification_results:
            if outcome in (DeliveryOutcome.SUCCESS, DeliveryOutcome.FORBIDDEN):
                await mark_notification_sent(session, note.id)
                delivered += 1
            if outcome is not DeliveryOutcome.TRANSIENT_FAILURE:
                await record_dm_outcome(
                    session, note.user_id, blocked=outcome is DeliveryOutcome.FORBIDDEN
                )

        await session.commit()

        # Delivery log: its own try/except and its own commit, for the same
        # reason the health block below has them. By this point the DMs are on
        # the wire and recorded as sent; an exception that rolled that back
        # would make the next tick re-send every one of them. An observability
        # feature must never be able to cause a duplicate reminder.
        if reminder_results or notification_results:
            try:
                rows = await record_deliveries(
                    session, now, list(reminder_results), list(notification_results)
                )
                # The rows just written, not a re-SELECT of the batch: the
                # digest describes exactly what was logged, and an empty list
                # (a tick that delivered nothing but unreported kinds -- the
                # digest's own delivery, for one) queues no digest at all,
                # which is what keeps this from feeding itself.
                if rows:
                    await queue_delivery_digest(session, now, rows)
                await session.commit()
            except Exception:
                log.exception("delivery logging failed; delivery itself was unaffected")
                await session.rollback()

        # Health evaluation runs AFTER the delivery commit and swallows its own
        # errors. Both halves matter. By this point the tick has already put
        # DMs on the wire -- an irreversible side effect -- and recorded them
        # as sent; an exception raised before the commit would roll that
        # bookkeeping back while the messages stayed delivered, and the next
        # tick would send every one of them again. Monitoring must never be
        # able to cause duplicate reminders, so it commits separately and its
        # failures are logged, not raised.
        #
        # Every 5th tick (~5 min): disk stats and file reads do not need
        # per-minute resolution, and the slower cadence damps flapping.
        if _tick_count % HEALTH_EVERY_N_TICKS == 0:
            try:
                await evaluate_and_alert(session, await run_checks(session), now)
                await session.commit()
            except Exception:
                log.exception("health evaluation failed; reminder delivery was unaffected")
                await session.rollback()

            # Same 5-minute cadence, but its OWN try/except and its OWN commit
            # -- and that separation is the whole point. Sharing the health
            # block's commit (how this first shipped) meant a prune that raised
            # rolled the OpsCheckState writes back with it, so the least
            # important operation in the tick could silently suppress the most
            # important one: the pass that decides whether to page an admin.
            # A delivery_log table that grows for another five minutes is not
            # an incident; a swallowed health transition is. Same
            # least-important-last, commit-between-stages shape the rest of
            # this function already uses.
            try:
                await prune_delivery_log(session, now)
                await session.commit()
            except Exception:
                log.exception("delivery log prune failed; nothing else was affected")
                await session.rollback()
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
