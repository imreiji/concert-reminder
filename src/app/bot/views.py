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

    -- progressive capture, for a round covering two or more legs:
    dk:wonall:{round_id}          every leg of this round was won
    dk:lostall:{round_id}         every leg of this round was lost
    dk:wonday:{round_id}:{day_id}   this ONE leg was won
    dk:lostday:{round_id}:{day_id}  this ONE leg was lost
    dk:skipday:{round_id}:{day_id}  not going to this leg (a leg opt-out)
    dk:lostrest:{round_id}        every leg still unanswered was lost
    dk:paidnow:{round_id}         the payment for it is done
    dk:paylater:{round_id}        not paid yet (records nothing)
"""

import re

import discord

from app.db.models import ConcertDay, Round, User
from app.db.service import (
    apply_default_preset,
    has_day_results,
    is_round_cancelled,
    record_remaining_days_lost,
    record_round_day_result,
    record_round_outcome,
    reinstate_user_rules,
    remove_user_rules,
    round_result_state,
    set_leg_opt_out,
    snooze_reminder,
)
from app.db.session import SessionMaker
from app.domain.timezones import fmt_dual
from app.domain.types import LegResult, LotteryOutcome
from app.i18n import N_, get_locale, loc_field, ngettext, set_locale
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
            label=_("Turn my reminders back on"),
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
            ngettext(
                "Turned {n} reminder back on. You'll be notified again for any "
                "active ones using your existing settings.",
                "Turned {n} reminders back on. You'll be notified again for any "
                "active ones using your existing settings.",
                n,
            ).format(n=n)
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
                lines.append(f"**{loc_field(r, 'label', loc)}**{suffix} — {' / '.join(bits)}")
            for d in concert.days:
                suffix = cancelled_suffix if d.cancelled else ""
                lines.append(
                    f"🎤 **{loc_field(d, 'label', loc)}**{suffix} — "
                    f"{fmt_dual(d.starts_at_utc, tz, loc)}"
                )
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


# ── Progressive per-day result capture ───────────────────────────────────
#
# A round covering two or more legs can come back won on one night and lost on
# another, so the flat Won/Lost pair above would have to lie about one of them.
# These buttons ask leg by leg instead, and each press EDITS the DM it was
# pressed on: the question changes as legs get answered, and a thread of
# replies would leave the stale question pressable above the current one.
#
# Nothing about the flow is stored. Every press re-derives its whole reply from
# `round_result_state` -- these buttons are persistent, so the same message can
# be pressed months later against a round already resolved on the site, and the
# only honest answer to that is what is true now.
#
# A stale press costs a re-render and never a wrong write, which takes guards
# in two places. The per-day writers bring their own (a won leg never demotes a
# PAID round; "lost the rest" writes nothing on a round with nothing
# unresolved). The two ALL-legs shortcuts do not -- `record_round_outcome`
# accepts WON and LOST from any state by design, since the web's own buttons
# are the correction path -- so `_apply_press` guards them here, against the
# state read before the write.

# Discord allows 25 components across 5 rows, and the follow-up view gives each
# leg a row of its own -- so a long tour is asked about a batch at a time.
# Answering one shrinks the unresolved list, which is what brings the next legs
# into reach on the following press.
MAX_DAY_BUTTONS = 4

# Discord rejects a button label over 80 characters with an HTTPException,
# which the scheduler classes as a transient failure and retries every tick
# forever -- the same trap `safe_button_url` guards. Leg labels are
# editor-supplied text, so trim rather than risk wedging the queue.
_LABEL_MAX = 80


def _fit_label(text: str) -> str:
    return text if len(text) <= _LABEL_MAX else text[: _LABEL_MAX - 1] + "…"


async def _result_state(session, user_id: int, round_):
    """`(unresolved [(day_id, label), ...], any_won, outcome)` under the locale
    already set for the clicking user.

    Deliberately NOT derived from the DM's own buttons or from
    `DueReminder.covered_days`: that tuple is leg PRESENCE at compose time and
    knows nothing about what has been recorded or opted out of since."""
    return await round_result_state(session, user_id, round_, get_locale())


async def _leg_label(session, round_, day_id: int) -> str | None:
    """This leg's label in the clicking user's language, or None when the id
    names no leg of THIS round's concert.

    Day ids arrive inside a custom_id, so they are re-validated server side
    exactly as `web/routes/outcomes.py` re-validates its form-supplied ones:
    `record_round_day_result` refuses an uncovered day itself, but
    `set_leg_opt_out` validates nothing and would raise an FK error at commit
    on a made-up id. One class of input, one answer -- write nothing."""
    day = await session.get(ConcertDay, day_id)
    if day is None or day.concert_id != round_.concert_id:
        return None
    return loc_field(day, "label", get_locale())


async def _apply_press(
    session, user_id: int, round_, action: str, day_id: int | None, before,
) -> None:
    """What each button MEANS, in one table. Every branch is an existing
    service writer -- the bot adds no write path of its own, or the reminder
    queue would desync (invariant 2).

    `before` is the state snapshot taken BEFORE the write, which is what the
    two all-legs shortcuts need: unlike the per-day writers, they would
    otherwise happily overwrite a round somebody has already settled."""
    _unresolved_before, _any_won_before, outcome_before = before
    if action in ("wonall", "lostall"):
        # The round-level twin of the guard inside record_round_day_result.
        # This DM can be pressed long after the round was secured (and paid
        # for) on the site, and both of these would overwrite that: WON
        # demotes PAID and re-arms its payment reminder, LOST wipes the ticket
        # outright. A settled win is not something a stale press may undo --
        # the site owns corrections.
        if outcome_before in (LotteryOutcome.WON, LotteryOutcome.PAID):
            return
        if action == "lostall":
            await record_round_outcome(session, user_id, round_.id, LotteryOutcome.LOST)
            return
        await record_round_outcome(session, user_id, round_.id, LotteryOutcome.WON)
        # A round nobody has answered leg by leg is fully described by that
        # one write (no-rows-means-all). Once ANY day row exists the fallback
        # is off, so stopping here would leave a WON round without a single
        # WON leg -- a contradiction whose own follow-ups can settle it back
        # to LOST. Spell the still-open legs out instead: the losses already
        # recorded stand ("all" means all the ones still open), and each
        # per-day write self-skips the round-level write, since the round now
        # reads WON.
        if await has_day_results(session, user_id, round_.id):
            for open_day_id, _label in _unresolved_before:
                await record_round_day_result(
                    session, user_id, round_.id, open_day_id, LegResult.WON
                )
        return
    if action == "paid":
        await record_round_outcome(session, user_id, round_.id, LotteryOutcome.PAID)
    elif action == "lostrest":
        await record_remaining_days_lost(session, user_id, round_.id)
    elif action == "skip":
        # "Not going" is a leg opt-out, never a RoundOutcomeDay row: the reader
        # is declining the night, not reporting how the draw went. No resync
        # call here -- set_leg_opt_out re-plans this user's rules itself
        # (invariant 8), so no caller can forget it.
        await set_leg_opt_out(session, user_id, day_id, True)
    else:
        await record_round_day_result(session, user_id, round_.id, day_id, LegResult(action))


def _progress_reply(round_id: int, unresolved, any_won: bool, outcome, day_label: str | None):
    """`(content, view)` for the state a press left behind."""
    if unresolved:
        content = (
            _("{day} recorded — and the other days?").format(day=day_label)
            if day_label else _("Got it — and the other days?")
        )
        return content, build_result_followup_view(round_id, unresolved, any_won)
    if any_won and outcome is not LotteryOutcome.PAID:
        return _("All recorded — have you paid for it yet?"), build_payment_view(round_id)
    if any_won:
        return _("Marked as paid — all set!"), None
    if outcome is LotteryOutcome.LOST:
        return _("Sorry to hear it — no payment reminder needed, and I'll let you know "
                 "when the next round opens if there is one."), None
    # Every leg answered, none secured, and the round itself not settled as
    # lost: the reader skipped the ones they were not going to. Nothing was
    # lost, so neither the congratulations nor the condolences are true.
    return _("Nothing left to record for this one."), None


async def _progressive_click(
    interaction: discord.Interaction, round_id: int, action: str, day_id: int | None = None,
) -> None:
    """The shared body of every progressive button: set the locale, validate
    the ids, apply this button's meaning, then re-render from the state that
    leaves rather than from the message that was pressed."""
    async with SessionMaker() as session:
        await _apply_locale(session, interaction.user.id)
        round_ = await session.get(Round, round_id)
        if round_ is None:
            await interaction.response.edit_message(
                content=_("That round no longer exists."), view=None
            )
            return
        day_label = await _leg_label(session, round_, day_id) if day_id is not None else None
        if day_id is None or day_label is not None:
            # The state BEFORE the write decides what a whole-round press may
            # do; the state after it decides what the reply says.
            before = await _result_state(session, interaction.user.id, round_)
            await _apply_press(session, interaction.user.id, round_, action, day_id, before)
        unresolved, any_won, outcome = await _result_state(
            session, interaction.user.id, round_
        )
        await session.commit()
    content, view = _progress_reply(round_id, unresolved, any_won, outcome, day_label)
    await interaction.response.edit_message(content=content, view=view)


class WonAllButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"dk:wonall:(?P<rid>\d+)"
):
    def __init__(self, round_id: int, row: int | None = None) -> None:
        super().__init__(discord.ui.Button(
            label=_("Won (all)"), style=discord.ButtonStyle.success,
            custom_id=f"dk:wonall:{round_id}",
        ), row=row)
        self.round_id = round_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["rid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await _progressive_click(interaction, self.round_id, "wonall")


class LostAllButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"dk:lostall:(?P<rid>\d+)"
):
    def __init__(self, round_id: int, row: int | None = None) -> None:
        super().__init__(discord.ui.Button(
            label=_("Lost (all)"), style=discord.ButtonStyle.secondary,
            custom_id=f"dk:lostall:{round_id}",
        ), row=row)
        self.round_id = round_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["rid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await _progressive_click(interaction, self.round_id, "lostall")


class LostRestButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"dk:lostrest:(?P<rid>\d+)"
):
    """The all-legs shortcut once SOMETHING is secured: "Lost (all)" there
    would throw away a ticket the reader has already recorded."""

    def __init__(self, round_id: int, row: int | None = None) -> None:
        super().__init__(discord.ui.Button(
            label=_("Lost the rest"), style=discord.ButtonStyle.secondary,
            custom_id=f"dk:lostrest:{round_id}",
        ), row=row)
        self.round_id = round_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["rid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await _progressive_click(interaction, self.round_id, "lostrest")


class _DayButtonMixin:
    """Shared shape of the three per-leg buttons (a mixin, so each concrete
    class still declares its own `template` to discord.py).

    Two ids in one custom_id: the round the press is about and the leg it
    answers for.

    `day_label` is the LEG's name, not the finished button text: the class
    composes "Won — {day}" itself, so the two callers (the reminder DM and the
    follow-up view) cannot drift on wording or on the 80-character trim. It is
    optional because `from_custom_id` re-hydrates a press with no DB access in
    hand -- and the label is never displayed in that direction anyway, since
    the message already carries the text it was sent with."""

    ACTION: str = ""
    STYLE: discord.ButtonStyle = discord.ButtonStyle.secondary

    def __init__(
        self, round_id: int, day_id: int, day_label: str | None = None,
        row: int | None = None,
    ) -> None:
        super().__init__(discord.ui.Button(
            label=_fit_label(self.compose_label(day_label)), style=self.STYLE,
            custom_id=f"dk:{self.ACTION}day:{round_id}:{day_id}",
        ), row=row)
        self.round_id = round_id
        self.day_id = day_id

    @classmethod
    def compose_label(cls, day_label: str | None) -> str:
        raise NotImplementedError

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["rid"]), int(match["did"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await _progressive_click(interaction, self.round_id, self.ACTION, self.day_id)


class WonDayButton(
    _DayButtonMixin, discord.ui.DynamicItem[discord.ui.Button],
    template=r"dk:wonday:(?P<rid>\d+):(?P<did>\d+)",
):
    ACTION = "won"
    STYLE = discord.ButtonStyle.success

    @classmethod
    def compose_label(cls, day_label: str | None) -> str:
        return _("Won — {day}").format(day=day_label) if day_label else _("Won")


class LostDayButton(
    _DayButtonMixin, discord.ui.DynamicItem[discord.ui.Button],
    template=r"dk:lostday:(?P<rid>\d+):(?P<did>\d+)",
):
    ACTION = "lost"

    @classmethod
    def compose_label(cls, day_label: str | None) -> str:
        return _("Lost — {day}").format(day=day_label) if day_label else _("Lost")


class SkipDayButton(
    _DayButtonMixin, discord.ui.DynamicItem[discord.ui.Button],
    template=r"dk:skipday:(?P<rid>\d+):(?P<did>\d+)",
):
    ACTION = "skip"

    @classmethod
    def compose_label(cls, day_label: str | None) -> str:
        return _("Not going — {day}").format(day=day_label) if day_label else _("Not going")


class PaidNowButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"dk:paidnow:(?P<rid>\d+)"
):
    def __init__(self, round_id: int, row: int | None = None) -> None:
        super().__init__(discord.ui.Button(
            label=_("Paid already"), style=discord.ButtonStyle.success,
            custom_id=f"dk:paidnow:{round_id}",
        ), row=row)
        self.round_id = round_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["rid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await _progressive_click(interaction, self.round_id, "paid")


class PayLaterButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"dk:paylater:(?P<rid>\d+)"
):
    """"Not yet" is an answer, not a write: the round stays WON, which is
    exactly the state its payment reminder is armed from."""

    def __init__(self, round_id: int, row: int | None = None) -> None:
        super().__init__(discord.ui.Button(
            label=_("Not yet"), style=discord.ButtonStyle.secondary,
            custom_id=f"dk:paylater:{round_id}",
        ), row=row)
        self.round_id = round_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["rid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        async with SessionMaker() as session:
            await _apply_locale(session, interaction.user.id)
        await interaction.response.edit_message(
            content=_("No rush — mark it paid whenever you're done."), view=None
        )


def build_result_followup_view(round_id: int, unresolved, any_won: bool) -> discord.ui.View:
    """The per-leg question: one row per unanswered leg, then the all-legs
    shortcut. Callers pass the CURRENT unresolved list, so the view shrinks by
    one row with every press."""
    view = discord.ui.View(timeout=None)
    for row, (day_id, label) in enumerate(unresolved[:MAX_DAY_BUTTONS]):
        view.add_item(WonDayButton(round_id, day_id, label, row=row))
        view.add_item(LostDayButton(round_id, day_id, label, row=row))
        view.add_item(SkipDayButton(round_id, day_id, label, row=row))
    shortcut_row = min(len(unresolved), MAX_DAY_BUTTONS)
    if any_won:
        view.add_item(LostRestButton(round_id, row=shortcut_row))
    else:
        view.add_item(LostAllButton(round_id, row=shortcut_row))
    return view


def build_payment_view(round_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(PaidNowButton(round_id))
    view.add_item(PayLaterButton(round_id))
    return view


DYNAMIC_ITEMS = [
    ApplyDefaultButton, RemoveRemindersButton, ReinstateRemindersButton, ShowDeadlinesButton,
    SnoozeButton, AppliedButton, NotAppliedButton, WonButton, LostButton, PaidButton,
    RemindLaterButton,
    WonAllButton, LostAllButton, LostRestButton, WonDayButton, LostDayButton, SkipDayButton,
    PaidNowButton, PayLaterButton,
]
