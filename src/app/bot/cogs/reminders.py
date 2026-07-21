"""/upcoming and /remindme — the bot's first genuinely useful commands."""

from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from app.db.models import Concert, ReminderRule, User
from app.db.service import ensure_user, sync_rule, upcoming_rounds, user_calendar_events
from app.db.session import SessionMaker
from app.domain.timezones import fmt_dual
from app.domain.types import Anchor
from app.i18n import get_locale, loc_field, set_locale
from app.i18n import gettext as _

ANCHOR_CHOICES = [
    app_commands.Choice(name="before it closes (deadlines)", value=Anchor.CLOSES.value),
    app_commands.Choice(name="before it opens (sales starting)", value=Anchor.OPENS.value),
    app_commands.Choice(name="before the concert day", value=Anchor.EVENT_START.value),
]


class Reminders(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /upcoming ────────────────────────────────────────────────────

    @app_commands.command(description="Deadlines opening or closing soon")
    @app_commands.describe(days="Horizon in days (default 14)")
    async def upcoming(self, interaction: discord.Interaction, days: int = 14) -> None:
        days = max(1, min(days, 90))
        async with SessionMaker() as session:
            user = await ensure_user(session, interaction.user.id, interaction.user.name)
            set_locale(user.language)
            tz = user.timezone
            pairs = await upcoming_rounds(session, horizon_days=days)
            await session.commit()

        loc = get_locale()
        if not pairs:
            none_msg = _("Nothing opens or closes in the next {days} days. 平和ですね。")
            await interaction.response.send_message(
                none_msg.format(days=days), ephemeral=True
            )
            return

        now = datetime.now(UTC)
        lines: list[str] = []
        for concert, round_ in pairs[:20]:
            bits = []
            if round_.opens_at_utc and round_.opens_at_utc > now:
                bits.append(f"{_('opens')} {fmt_dual(round_.opens_at_utc, tz, loc)}")
            if round_.closes_at_utc and round_.closes_at_utc > now:
                bits.append(f"{_('closes')} {fmt_dual(round_.closes_at_utc, tz, loc)}")
            if bits:
                title = loc_field(concert, "title", loc)
                lines.append(f"**{title}** — {round_.label}\n{' / '.join(bits)}")

        embed = discord.Embed(
            title=_("Next {days} days").format(days=days),
            description="\n\n".join(lines) or _("Nothing upcoming."),
            color=0x5865F2,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /mydeadlines ─────────────────────────────────────────────────

    @app_commands.command(description="Your own next deadlines -- only reminders you have set")
    @app_commands.describe(count="How many to show (default 10)")
    async def mydeadlines(self, interaction: discord.Interaction, count: int = 10) -> None:
        """The personalized counterpart to /upcoming: that command lists
        every deadline in the next N days regardless of who's watching it;
        this one lists only the concerts/rounds *this* user has an active
        reminder rule on, sourced from the same reminder_queue-backed
        user_calendar_events() the personal .ics feed uses -- same
        real-deadline timestamps, not the reminder's lead-time-adjusted
        fire time."""
        count = max(1, min(count, 25))
        async with SessionMaker() as session:
            user = await ensure_user(session, interaction.user.id, interaction.user.name)
            set_locale(user.language)
            tz = user.timezone
            events = await user_calendar_events(
                session, interaction.user.id, locale=get_locale()
            )
            await session.commit()

        if not events:
            await interaction.response.send_message(
                _("No upcoming deadlines on your reminders. `/remindme` to add one."),
                ephemeral=True,
            )
            return

        loc = get_locale()
        lines = [
            f"**{e.concert_title}** — {e.label}\n{fmt_dual(e.at_utc, tz, loc)}"
            for e in events[:count]
        ]
        embed = discord.Embed(
            title=_("Your upcoming deadlines"),
            description="\n\n".join(lines),
            color=0x5865F2,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /remindme ────────────────────────────────────────────────────

    @app_commands.command(description="DM me before every deadline in a concert")
    @app_commands.describe(
        concert="Which concert (start typing to search)",
        anchor="What to remind about",
        days_before="How many days before (0 = same day)",
    )
    @app_commands.choices(anchor=ANCHOR_CHOICES)
    async def remindme(
        self,
        interaction: discord.Interaction,
        concert: int,
        anchor: app_commands.Choice[str],
        days_before: app_commands.Range[int, 0, 60],
    ) -> None:
        async with SessionMaker() as session:
            user = await ensure_user(session, interaction.user.id, interaction.user.name)
            set_locale(user.language)
            target = await session.get(Concert, concert)
            if target is None:
                await interaction.response.send_message(
                    _("That concert doesn't exist (deleted?)."), ephemeral=True
                )
                return

            rule = ReminderRule(
                user_id=interaction.user.id,
                concert_id=target.id,
                anchor=Anchor(anchor.value),
                offset_days=-days_before,  # UX asks 'days before'; storage is signed
            )
            session.add(rule)
            await session.flush()
            await sync_rule(session, rule)
            await session.commit()

        when = _("same day") if days_before == 0 else _("{n} day(s) before").format(n=days_before)
        await interaction.response.send_message(
            _("Done — I'll DM you {when} each **{anchor}** for **{title}**.").format(
                when=when, anchor=anchor.name.removeprefix("before "), title=target.title
            ),
            ephemeral=True,
        )

    @remindme.autocomplete("concert")
    async def concert_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        async with SessionMaker() as session:
            stmt = select(Concert).order_by(Concert.created_at.desc()).limit(20)
            if current:
                stmt = stmt.where(Concert.title.ilike(f"%{current}%"))
            res = await session.execute(stmt)
            concerts = list(res.scalars())
        return [app_commands.Choice(name=c.title[:100], value=c.id) for c in concerts[:25]]

    # ── /myreminders ─────────────────────────────────────────────────

    @app_commands.command(description="List my reminder rules")
    async def myreminders(self, interaction: discord.Interaction) -> None:
        async with SessionMaker() as session:
            user = await session.get(User, interaction.user.id)
            set_locale(user.language if user else "en")
            res = await session.execute(
                select(ReminderRule, Concert)
                .outerjoin(Concert, ReminderRule.concert_id == Concert.id)
                .where(ReminderRule.user_id == interaction.user.id)
            )
            rows = res.all()

        if not rows:
            await interaction.response.send_message(
                _("No rules yet — `/remindme` to create one."), ephemeral=True
            )
            return

        lines = []
        for rule, concert in rows:
            scope = concert.title if concert else _("round #{n}").format(n=rule.round_id)
            d = abs(rule.offset_days)
            direction = _("before") if rule.offset_days < 0 else _("after")
            timing = _("same-day") if d == 0 else f"{d}d {direction}"
            lines.append(f"`#{rule.id}` **{scope}** — {timing} {rule.anchor.value}")
        await interaction.response.send_message("\n".join(lines[:25]), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Reminders(bot))
