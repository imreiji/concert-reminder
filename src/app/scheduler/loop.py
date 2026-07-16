"""The reminder scheduler: every 60s, drain due reminders into Discord DMs.

Failure philosophy (each case is deliberate):
  * Bot not ready / web-only mode  -> skip the tick, rows stay queued.
  * DM delivered                   -> mark sent. Only success marks sent.
  * Forbidden (user blocks DMs)    -> mark sent anyway, log a warning.
    Retrying forever would spam the log and never succeed; the row is dead.
  * Any other error (network...)   -> leave unsent; next tick retries.
  * Whole-tick exception           -> logged, loop survives. The loop dying
    silently is the one unacceptable outcome for a reminder app.

Rate limits: sends are sequential with a 1s gap. discord.py also enforces
per-route limits internally; the gap just keeps us far from the cliff when
many rules fire in the same minute.
"""

import asyncio
import logging
from datetime import UTC, datetime

import discord

from app.bot.messages import format_reminder
from app.db.service import (
    DueReminder,
    due_notifications,
    due_reminders,
    mark_notification_sent,
    mark_sent,
)
from app.db.session import SessionMaker
from app.scheduler import heartbeat

log = logging.getLogger(__name__)

TICK_SECONDS = 60
SEND_GAP_SECONDS = 1.0


async def deliver(bot, item: DueReminder) -> bool:
    """Send one reminder DM. Returns True if the row should be marked sent."""
    try:
        user = bot.get_user(item.discord_id) or await bot.fetch_user(item.discord_id)
        await user.send(format_reminder(item))
        return True
    except discord.Forbidden:
        log.warning(
            "user %s has DMs closed; dropping reminder %s", item.discord_id, item.queue_id
        )
        return True  # permanent failure: retrying can never succeed
    except discord.HTTPException as e:
        log.error("transient send failure for queue row %s: %s", item.queue_id, e)
        return False  # leave unsent; next tick retries


async def deliver_text(bot, user_id: int, body: str) -> bool:
    """Send a plain DM. Same policy as deliver(): only success or a permanent
    failure (DMs closed) clears the row; transient errors retry next tick."""
    try:
        user = bot.get_user(user_id) or await bot.fetch_user(user_id)
        await user.send(body)
        return True
    except discord.Forbidden:
        log.warning("user %s has DMs closed; dropping notification", user_id)
        return True
    except discord.HTTPException as e:
        log.error("transient notification failure for user %s: %s", user_id, e)
        return False


async def tick(bot) -> int:
    """One scheduler pass. Returns how many messages were delivered."""
    now = datetime.now(UTC)
    delivered = 0
    async with SessionMaker() as session:
        for item in await due_reminders(session, now):
            if await deliver(bot, item):
                await mark_sent(session, item.queue_id, now)
                delivered += 1
            await asyncio.sleep(SEND_GAP_SECONDS)
        for note in await due_notifications(session):
            if await deliver_text(bot, note.user_id, note.body):
                await mark_notification_sent(session, note.id)
                delivered += 1
            await asyncio.sleep(SEND_GAP_SECONDS)
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
