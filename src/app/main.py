"""Entrypoint: one process, one event loop, three concurrent tasks.

    python -m app.main

  * Discord bot  (skipped when DISCORD_TOKEN is empty -> web-only dev mode)
  * Web UI       (uvicorn, bound to localhost; Caddy fronts it in production)
  * Scheduler    (60s tick draining the reminder queue — Phase 3)

If any task dies, the whole process exits nonzero and systemd restarts it.
That is deliberate: a half-alive app (web up, bot silently dead) is worse
than a visible restart.
"""

import asyncio
import logging
import sys

import uvicorn

from app.config import settings
from app.scheduler.loop import reminder_loop
from app.web.app import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("app.main")


async def run() -> None:
    tasks: list[asyncio.Task] = []

    bot = None
    if settings.bot_enabled:
        from app.bot.client import bot as _bot  # import here: web-only mode skips discord setup

        bot = _bot
        tasks.append(asyncio.create_task(bot.start(settings.discord_token), name="bot"))
    else:
        log.warning("DISCORD_TOKEN empty -> web-only dev mode (bot disabled)")

    web = uvicorn.Server(
        uvicorn.Config(
            create_app(),
            host=settings.web_host,
            port=settings.web_port,
            log_level="info",
        )
    )
    tasks.append(asyncio.create_task(web.serve(), name="web"))
    tasks.append(asyncio.create_task(reminder_loop(bot), name="scheduler"))

    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

    # One task ended (crash or shutdown) -> take everything down cleanly.
    for t in pending:
        t.cancel()
    await asyncio.gather(*pending, return_exceptions=True)

    for t in done:
        if t.exception():
            log.error("task %r crashed", t.get_name(), exc_info=t.exception())
            sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
