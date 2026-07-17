"""Discord bot client.

Phase 1: connects, syncs slash commands, answers /ping.
Later phases add cogs under bot/cogs/ — each cog is one feature area.
"""

import logging

import discord
from discord.ext import commands

from app.config import settings

log = logging.getLogger(__name__)

# No privileged intents needed: slash commands + DMs work with defaults.
intents = discord.Intents.default()


class ReminderBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(command_prefix="!", intents=intents)  # prefix unused; slash-only

    async def setup_hook(self) -> None:
        from app.bot.views import DYNAMIC_ITEMS

        self.add_dynamic_items(*DYNAMIC_ITEMS)  # buttons survive restarts
        await self.load_extension("app.bot.cogs.ping")
        await self.load_extension("app.bot.cogs.reminders")
        await self.load_extension("app.bot.cogs.admin")

        if settings.dev_guild_id:
            # Guild-scoped sync propagates in seconds instead of up to an
            # hour -- set DEV_GUILD_ID for fast local iteration.
            guild = discord.Object(id=settings.dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info(
                "synced %d slash command(s) to dev guild %s", len(synced), settings.dev_guild_id
            )
        else:
            synced = await self.tree.sync()
            log.info("synced %d slash command(s) globally", len(synced))

    async def on_ready(self) -> None:
        assert self.user is not None
        log.info("bot online as %s (id=%s)", self.user, self.user.id)


bot = ReminderBot()
