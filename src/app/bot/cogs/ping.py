"""/ping — proves the gateway connection and slash-command sync work."""

import discord
from discord import app_commands
from discord.ext import commands

from app.config import settings


class Ping(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(description="Check the bot is alive")
    async def ping(self, interaction: discord.Interaction) -> None:
        role = "editor" if settings.is_editor(interaction.user.id) else "viewer"
        await interaction.response.send_message(
            f"pong — latency {self.bot.latency * 1000:.0f}ms — you are a **{role}**",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Ping(bot))
