"""The reminder scheduler.

Phase 1: a heartbeat that proves the loop coexists with bot + web.
Phase 3 replaces the body with:
    SELECT ... FROM reminder_queue WHERE fire_at_utc <= now AND sent_at_utc IS NULL
    -> send DM (sequentially, small delay: be gentle with Discord rate limits)
    -> stamp sent_at_utc
Crash-safe by design: unsent rows simply fire on the next tick.
"""

import asyncio
import logging
from datetime import UTC, datetime

log = logging.getLogger(__name__)

TICK_SECONDS = 60


async def reminder_loop(bot) -> None:  # bot: ReminderBot | None in web-only mode
    while True:
        now = datetime.now(UTC)
        # Phase 3: drain due reminders here.
        log.debug("scheduler tick at %s", now.isoformat(timespec="seconds"))
        await asyncio.sleep(TICK_SECONDS)
