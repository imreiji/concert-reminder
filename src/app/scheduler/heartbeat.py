"""Scheduler heartbeat: lets /healthz prove the reminder loop is actually alive.

The web server being up says nothing about the scheduler; for a reminder app
the scheduler IS the product. The loop calls beat() every tick; /healthz calls
status() and reports overall health as false if the loop has gone quiet.
An uptime monitor watching for '"ok":true' then catches silent scheduler
death, not just full outages.

Grace period: right after process start (before the first tick) we report
healthy, so restarts don't trigger false alarms.
"""

from datetime import UTC, datetime

MAX_AGE_SECONDS = 180  # 3 missed ticks = unhealthy

_started_at = datetime.now(UTC)
last_tick: datetime | None = None


def beat() -> None:
    global last_tick
    last_tick = datetime.now(UTC)


def status(now: datetime | None = None) -> tuple[bool, str | None]:
    """(scheduler_ok, last_tick_iso). Healthy = ticked recently, or just started."""
    now = now or datetime.now(UTC)
    if last_tick is None:
        return (now - _started_at).total_seconds() < MAX_AGE_SECONDS, None
    return (now - last_tick).total_seconds() < MAX_AGE_SECONDS, last_tick.isoformat()
