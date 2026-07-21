"""/promote-editor, /demote-editor, /list-editors — admin-only editor management.

No decorator-based admin gate exists on the bot side (unlike require_admin
for the web routes), so each command checks settings.is_admin() up front.
"""

import discord
from discord import app_commands
from discord.ext import commands

from app.config import settings
from app.db.models import User
from app.db.service import list_editors as db_list_editors
from app.db.service import set_editor
from app.db.session import SessionMaker
from app.i18n import gettext as _
from app.i18n import set_locale


async def _set_operator_locale(user_id: int) -> None:
    """Localize admin replies to the operator's own language preference.

    Admin copy is operator-facing, but it is still app copy, so it honours the
    operator's language. Runs in its own Task with an isolated ContextVar copy.
    """
    async with SessionMaker() as session:
        user = await session.get(User, user_id)
        set_locale(user.language if user else "en")


async def _reject_if_not_admin(interaction: discord.Interaction) -> bool:
    await _set_operator_locale(interaction.user.id)
    if settings.is_admin(interaction.user.id):
        return False
    await interaction.response.send_message(
        _("Admin access required."), ephemeral=True
    )
    return True


class Admin(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="promote-editor", description="[Admin] Grant a user editor access")
    @app_commands.describe(member="Who to promote")
    async def promote_editor(
        self, interaction: discord.Interaction, member: discord.Member
    ) -> None:
        if await _reject_if_not_admin(interaction):
            return
        async with SessionMaker() as session:
            await set_editor(session, member.id, True, member.name)
            await session.commit()
        await interaction.response.send_message(
            _("**{name}** is now an editor.").format(name=member.name), ephemeral=True
        )

    @app_commands.command(name="demote-editor", description="[Admin] Revoke a user's editor access")
    @app_commands.describe(member="Who to demote")
    async def demote_editor(self, interaction: discord.Interaction, member: discord.Member) -> None:
        if await _reject_if_not_admin(interaction):
            return
        if settings.is_editor(member.id):
            await interaction.response.send_message(
                _("**{name}** is env-managed (EDITOR_WHITELIST) — edit `.env` instead.").format(
                    name=member.name
                ),
                ephemeral=True,
            )
            return
        async with SessionMaker() as session:
            await set_editor(session, member.id, False, member.name)
            await session.commit()
        await interaction.response.send_message(
            _("**{name}** is no longer an editor.").format(name=member.name), ephemeral=True
        )

    @app_commands.command(name="list-editors", description="[Admin] List current editors")
    async def list_editors_cmd(self, interaction: discord.Interaction) -> None:
        if await _reject_if_not_admin(interaction):
            return
        async with SessionMaker() as session:
            editors = await db_list_editors(session)
        if not editors:
            await interaction.response.send_message(_("No editors yet."), ephemeral=True)
            return
        env_suffix = f" — {_('env')}"
        lines = [
            f"**{e['username'] or e['id']}** ({e['id']}){env_suffix if e['env'] else ''}"
            for e in editors
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Admin(bot))
