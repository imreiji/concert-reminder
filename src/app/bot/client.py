"""Discord bot client.

Phase 1: connects, syncs slash commands, answers /ping.
Later phases add cogs under bot/cogs/ — each cog is one feature area.
"""

import logging

import discord
from discord.ext import commands

log = logging.getLogger(__name__)

# No privileged intents needed: slash commands + DMs work with defaults.
intents = discord.Intents.default()


class ReminderBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(command_prefix="!", intents=intents)  # prefix unused; slash-only

    async def setup_hook(self) -> None:
        await self.load_extension("app.bot.cogs.ping")
        await self.load_extension("app.bot.cogs.reminders")
        synced = await self.tree.sync()
        log.info("synced %d slash command(s)", len(synced))

    async def on_ready(self) -> None:
        assert self.user is not None
        log.info("bot online as %s (id=%s)", self.user, self.user.id)


bot = ReminderBot()
