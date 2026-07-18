"""Interactive DM buttons (persistent across bot restarts).

Each button is a discord.py DynamicItem: its identity lives entirely in the
custom_id (e.g. "dk:apply:123"), so no state is held in memory and clicks on
messages sent before the last restart still work — the regex re-hydrates the
handler. State is ALWAYS re-checked at click time, never trusted from the
label: a "Set my reminders" button clicked after the user already applied a
preset responds gracefully instead of double-applying.

custom_id namespace:
    dk:apply:{concert_id}     apply the user's default preset
    dk:remove:{concert_id}    remove the user's rules on this concert
    dk:deadlines:{concert_id} reply with the full deadline list
    dk:snooze:{queue_id}      re-arm a delivered reminder for +24h (capped)
    dk:reinstate:{concert_id} re-sync the clicking user's rules on this concert
"""

import re

import discord

from app.db.service import (
    apply_default_preset,
    is_round_cancelled,
    reinstate_user_rules,
    remove_user_rules,
    snooze_reminder,
)
from app.db.session import SessionMaker
from app.domain.timezones import fmt_dual


class ApplyDefaultButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"dk:apply:(?P<cid>\d+)"
):
    def __init__(self, concert_id: int) -> None:
        super().__init__(discord.ui.Button(
            label="Set my reminders",
            style=discord.ButtonStyle.primary,
            custom_id=f"dk:apply:{concert_id}",
        ))
        self.concert_id = concert_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["cid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        async with SessionMaker() as session:
            status, n = await apply_default_preset(
                session, interaction.user.id, self.concert_id
            )
            await session.commit()
        msg = {
            "applied": f"Done — {n} reminder(s) set from your default preset.",
            "already_covered": "You already have reminders on this event.",
            "no_default": "You have no default preset — mark one with ★ in Preferences.",
        }[status]
        await interaction.response.send_message(msg)


class RemoveRemindersButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"dk:remove:(?P<cid>\d+)"
):
    def __init__(self, concert_id: int) -> None:
        super().__init__(discord.ui.Button(
            label="Remove these reminders",
            style=discord.ButtonStyle.secondary,
            custom_id=f"dk:remove:{concert_id}",
        ))
        self.concert_id = concert_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["cid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        async with SessionMaker() as session:
            n = await remove_user_rules(session, interaction.user.id, self.concert_id)
            await session.commit()
        msg = (
            f"Removed {n} reminder(s) for this event — you won't be pinged about it."
            if n else "You had no reminders on this event."
        )
        await interaction.response.send_message(msg)


class ReinstateRemindersButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"dk:reinstate:(?P<cid>\d+)"
):
    def __init__(self, concert_id: int) -> None:
        super().__init__(discord.ui.Button(
            label="Reinstate my reminders",
            style=discord.ButtonStyle.primary,
            custom_id=f"dk:reinstate:{concert_id}",
        ))
        self.concert_id = concert_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["cid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        async with SessionMaker() as session:
            n = await reinstate_user_rules(session, interaction.user.id, self.concert_id)
            await session.commit()
        msg = (
            f"Reinstated {n} reminder(s) — you'll be notified again per your existing "
            "settings for any that are still active."
            if n else "You had no reminders set up on this event."
        )
        await interaction.response.send_message(msg)


class ShowDeadlinesButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"dk:deadlines:(?P<cid>\d+)"
):
    def __init__(self, concert_id: int) -> None:
        super().__init__(discord.ui.Button(
            label="Show all deadlines",
            style=discord.ButtonStyle.secondary,
            custom_id=f"dk:deadlines:{concert_id}",
        ))
        self.concert_id = concert_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["cid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        from app.db.models import Concert, User

        async with SessionMaker() as session:
            concert = await session.get(Concert, self.concert_id)
            if concert is None:
                await interaction.response.send_message("That event no longer exists.")
                return
            await session.refresh(concert, ["rounds", "days"])
            user = await session.get(User, interaction.user.id)
            tz = user.timezone if user else "America/Moncton"

            cancelled_day_ids = {d.id for d in concert.days if d.cancelled}
            lines = []
            for r in concert.rounds:
                bits = []
                if r.opens_at_utc:
                    bits.append(f"opens {fmt_dual(r.opens_at_utc, tz)}")
                if r.closes_at_utc:
                    bits.append(f"closes {fmt_dual(r.closes_at_utc, tz)}")
                if r.results_at_utc:
                    bits.append(f"results {fmt_dual(r.results_at_utc, tz)}")
                if r.payment_deadline_at_utc:
                    bits.append(f"payment due {fmt_dual(r.payment_deadline_at_utc, tz)}")
                suffix = " (cancelled)" if is_round_cancelled(r, cancelled_day_ids) else ""
                lines.append(f"**{r.label}**{suffix} — {' / '.join(bits)}")
            for d in concert.days:
                suffix = " (cancelled)" if d.cancelled else ""
                lines.append(f"🎤 **{d.label}**{suffix} — {fmt_dual(d.starts_at_utc, tz)}")
        await interaction.response.send_message(
            "\n".join(lines) or "No deadlines entered yet."
        )


class SnoozeButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"dk:snooze:(?P<qid>\d+)"
):
    def __init__(self, queue_id: int) -> None:
        super().__init__(discord.ui.Button(
            label="Snooze 1 day",
            style=discord.ButtonStyle.secondary,
            custom_id=f"dk:snooze:{queue_id}",
        ))
        self.queue_id = queue_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["qid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        async with SessionMaker() as session:
            status = await snooze_reminder(session, self.queue_id, interaction.user.id)
            await session.commit()
        msg = {
            "snoozed": "Snoozed — I'll remind you again in 24 hours.",
            "too_close": "Can't snooze — the deadline is less than a day away. ⏳",
            "not_yours": "That reminder isn't yours.",
            "gone": "That reminder no longer exists.",
        }[status]
        await interaction.response.send_message(msg)


DYNAMIC_ITEMS = [
    ApplyDefaultButton, RemoveRemindersButton, ReinstateRemindersButton, ShowDeadlinesButton,
    SnoozeButton,
]
