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
    dk:applied:{round_id}     mark this round as applied to
    dk:notapplied:{round_id}  mark this round as not applied to
    dk:won:{round_id}         mark this round as won
    dk:lost:{round_id}        mark this round as lost
    dk:paid:{round_id}        mark this round's payment as done
    dk:remindlater:{queue_id} open a modal asking how many days to snooze
"""

import re

import discord

from app.db.models import User
from app.db.service import (
    apply_default_preset,
    is_round_cancelled,
    record_round_outcome,
    reinstate_user_rules,
    remove_user_rules,
    snooze_reminder,
)
from app.db.session import SessionMaker
from app.domain.timezones import fmt_dual
from app.domain.types import LotteryOutcome
from app.i18n import N_, get_locale, set_locale
from app.i18n import gettext as _


async def _apply_locale(session, discord_id: int) -> None:
    """Set the active locale to the clicking user's DM language.

    Called after opening the handler's session and before composing any
    reply. Each interaction callback runs in its own asyncio Task with an
    isolated ContextVar copy, so this never races another click's locale.
    """
    user = await session.get(User, discord_id)
    set_locale(user.language if user else "en")


class ApplyDefaultButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"dk:apply:(?P<cid>\d+)"
):
    def __init__(self, concert_id: int) -> None:
        super().__init__(discord.ui.Button(
            label=_("Set my reminders"),
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
            await _apply_locale(session, interaction.user.id)
            await session.commit()
        msg = {
            "applied": _("Done — {n} reminder(s) set from your default preset.").format(n=n),
            "already_covered": _("You already have reminders on this event."),
            "no_default": _("You have no default preset — mark one with ★ in Preferences."),
        }[status]
        await interaction.response.send_message(msg)


class RemoveRemindersButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"dk:remove:(?P<cid>\d+)"
):
    def __init__(self, concert_id: int) -> None:
        super().__init__(discord.ui.Button(
            label=_("Remove these reminders"),
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
            await _apply_locale(session, interaction.user.id)
            await session.commit()
        msg = (
            _("Removed {n} reminder(s) for this event — you won't be pinged about it.").format(n=n)
            if n else _("You had no reminders on this event.")
        )
        await interaction.response.send_message(msg)


class ReinstateRemindersButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"dk:reinstate:(?P<cid>\d+)"
):
    def __init__(self, concert_id: int) -> None:
        super().__init__(discord.ui.Button(
            label=_("Reinstate my reminders"),
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
            await _apply_locale(session, interaction.user.id)
            await session.commit()
        msg = (
            _("Reinstated {n} reminder(s) — you'll be notified again per your existing "
              "settings for any that are still active.").format(n=n)
            if n else _("You had no reminders set up on this event.")
        )
        await interaction.response.send_message(msg)


class ShowDeadlinesButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"dk:deadlines:(?P<cid>\d+)"
):
    def __init__(self, concert_id: int) -> None:
        super().__init__(discord.ui.Button(
            label=_("Show all deadlines"),
            style=discord.ButtonStyle.secondary,
            custom_id=f"dk:deadlines:{concert_id}",
        ))
        self.concert_id = concert_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["cid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        from app.db.models import Concert

        async with SessionMaker() as session:
            user = await session.get(User, interaction.user.id)
            set_locale(user.language if user else "en")
            concert = await session.get(Concert, self.concert_id)
            if concert is None:
                await interaction.response.send_message(_("That event no longer exists."))
                return
            await session.refresh(concert, ["rounds", "days"])
            tz = user.timezone if user else "America/Moncton"
            loc = get_locale()

            cancelled_day_ids = {d.id for d in concert.days if d.cancelled}
            cancelled_suffix = f" ({_('cancelled')})"
            lines = []
            for r in concert.rounds:
                bits = []
                if r.opens_at_utc:
                    bits.append(f"{_('opens')} {fmt_dual(r.opens_at_utc, tz, loc)}")
                if r.closes_at_utc:
                    bits.append(f"{_('closes')} {fmt_dual(r.closes_at_utc, tz, loc)}")
                if r.results_at_utc:
                    bits.append(f"{_('results')} {fmt_dual(r.results_at_utc, tz, loc)}")
                if r.payment_deadline_at_utc:
                    bits.append(
                        f"{_('payment due')} {fmt_dual(r.payment_deadline_at_utc, tz, loc)}"
                    )
                suffix = cancelled_suffix if is_round_cancelled(r, cancelled_day_ids) else ""
                lines.append(f"**{r.label}**{suffix} — {' / '.join(bits)}")
            for d in concert.days:
                suffix = cancelled_suffix if d.cancelled else ""
                lines.append(f"🎤 **{d.label}**{suffix} — {fmt_dual(d.starts_at_utc, tz, loc)}")
        await interaction.response.send_message(
            "\n".join(lines) or _("No deadlines entered yet.")
        )


class SnoozeButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"dk:snooze:(?P<qid>\d+)"
):
    def __init__(self, queue_id: int) -> None:
        super().__init__(discord.ui.Button(
            label=_("Snooze 1 day"),
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
            await _apply_locale(session, interaction.user.id)
            await session.commit()
        msg = {
            "snoozed": _("Snoozed — I'll remind you again in 24 hours."),
            "too_close": _("Can't snooze — the deadline is less than a day away. ⏳"),
            "not_yours": _("That reminder isn't yours."),
            "gone": _("That reminder no longer exists."),
        }[status]
        await interaction.response.send_message(msg)


async def _handle_outcome_click(
    interaction: discord.Interaction, round_id: int, outcome: LotteryOutcome, success_msg: str
) -> None:
    """`success_msg` arrives as a raw English msgid (an N_() marker at the call
    site), translated here AFTER the locale is set from the clicking user."""
    async with SessionMaker() as session:
        await record_round_outcome(session, interaction.user.id, round_id, outcome)
        await _apply_locale(session, interaction.user.id)
        await session.commit()
    await interaction.response.send_message(_(success_msg))


class AppliedButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"dk:applied:(?P<rid>\d+)"
):
    def __init__(self, round_id: int) -> None:
        super().__init__(discord.ui.Button(
            label=_("I applied"), style=discord.ButtonStyle.primary,
            custom_id=f"dk:applied:{round_id}",
        ))
        self.round_id = round_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["rid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await _handle_outcome_click(
            interaction, self.round_id, LotteryOutcome.APPLIED, N_("Got it — marked as applied!")
        )


class NotAppliedButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"dk:notapplied:(?P<rid>\d+)"
):
    def __init__(self, round_id: int) -> None:
        super().__init__(discord.ui.Button(
            label=_("Didn't apply"), style=discord.ButtonStyle.secondary,
            custom_id=f"dk:notapplied:{round_id}",
        ))
        self.round_id = round_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["rid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await _handle_outcome_click(
            interaction, self.round_id, LotteryOutcome.NOT_APPLIED,
            N_("No worries — you won't get results/payment reminders for this one."),
        )


class WonButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"dk:won:(?P<rid>\d+)"
):
    def __init__(self, round_id: int) -> None:
        super().__init__(discord.ui.Button(
            label=_("Won"), style=discord.ButtonStyle.success,
            custom_id=f"dk:won:{round_id}",
        ))
        self.round_id = round_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["rid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await _handle_outcome_click(
            interaction, self.round_id, LotteryOutcome.WON,
            N_("Congrats! I'll remind you when payment is due."),
        )


class LostButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"dk:lost:(?P<rid>\d+)"
):
    def __init__(self, round_id: int) -> None:
        super().__init__(discord.ui.Button(
            label=_("Lost"), style=discord.ButtonStyle.secondary,
            custom_id=f"dk:lost:{round_id}",
        ))
        self.round_id = round_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["rid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await _handle_outcome_click(
            interaction, self.round_id, LotteryOutcome.LOST,
            N_("Sorry to hear it — no payment reminder needed, and I'll let you know "
               "when the next round opens if there is one."),
        )


class PaidButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"dk:paid:(?P<rid>\d+)"
):
    def __init__(self, round_id: int) -> None:
        super().__init__(discord.ui.Button(
            label=_("Paid"), style=discord.ButtonStyle.success,
            custom_id=f"dk:paid:{round_id}",
        ))
        self.round_id = round_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["rid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await _handle_outcome_click(
            interaction, self.round_id, LotteryOutcome.PAID, N_("Marked as paid — all set!")
        )


class RemindLaterModal(discord.ui.Modal):
    def __init__(self, queue_id: int) -> None:
        # Title + input are built per-instance (not as shared class attributes)
        # so their labels localize under the locale the click handler set for
        # this user, with no cross-instance mutation race.
        super().__init__(title=_("Remind me later"))
        self.days: discord.ui.TextInput = discord.ui.TextInput(
            label=_("How many days?"), placeholder="e.g. 3", max_length=3
        )
        self.add_item(self.days)
        self.queue_id = queue_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        async with SessionMaker() as session:
            await _apply_locale(session, interaction.user.id)
            try:
                n = int(str(self.days))
                if n <= 0:
                    raise ValueError
            except ValueError:
                await interaction.response.send_message(
                    _("Enter a whole number of days greater than 0.")
                )
                return
            status = await snooze_reminder(session, self.queue_id, interaction.user.id, days=n)
            await session.commit()
        msg = {
            "snoozed": _("Got it — I'll remind you again in {n} day(s).").format(n=n),
            "too_close": _("Can't snooze that far — the deadline is too close. ⏳"),
            "not_yours": _("That reminder isn't yours."),
            "gone": _("That reminder no longer exists."),
        }[status]
        await interaction.response.send_message(msg)


class RemindLaterButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"dk:remindlater:(?P<qid>\d+)"
):
    def __init__(self, queue_id: int) -> None:
        super().__init__(discord.ui.Button(
            label=_("Remind me later"), style=discord.ButtonStyle.secondary,
            custom_id=f"dk:remindlater:{queue_id}",
        ))
        self.queue_id = queue_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["qid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        # Set the locale before building the modal so its title/label localize.
        async with SessionMaker() as session:
            await _apply_locale(session, interaction.user.id)
        await interaction.response.send_modal(RemindLaterModal(self.queue_id))


DYNAMIC_ITEMS = [
    ApplyDefaultButton, RemoveRemindersButton, ReinstateRemindersButton, ShowDeadlinesButton,
    SnoozeButton, AppliedButton, NotAppliedButton, WonButton, LostButton, PaidButton,
    RemindLaterButton,
]
