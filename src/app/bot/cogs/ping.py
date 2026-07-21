"""/ping — proves the gateway connection and slash-command sync work."""

import discord
from discord import app_commands
from discord.ext import commands

from app.config import settings
from app.db.models import User
from app.db.session import SessionMaker
from app.i18n import gettext as _
from app.i18n import set_locale


class Ping(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(description="Check the bot is alive")
    async def ping(self, interaction: discord.Interaction) -> None:
        uid = interaction.user.id
        async with SessionMaker() as session:
            user = await session.get(User, uid)
            set_locale(user.language if user else "en")
        role = (
            _("admin") if settings.is_admin(uid)
            else _("editor") if settings.is_editor(uid)
            else _("viewer")
        )
        await interaction.response.send_message(
            _("pong — latency {ms:.0f}ms — you are a **{role}**").format(
                ms=self.bot.latency * 1000, role=role
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Ping(bot))
