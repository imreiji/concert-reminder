"""Reminder message formatting. Pure functions -> easy tests, no Discord needed.

The format contract:
    ⏰ **Hasunosora 5th** — 最速先行 Round 1
    closes in 3 days: Thu 2026-06-25 23:59 JST (11:59 ADT)
    <url if present>
"""

from datetime import datetime

from app.db.service import DueReminder
from app.domain.timezones import fmt_dual
from app.domain.types import Anchor, LotteryOutcome
from app.domain.urls import UnsafeURLError, clean_url
from app.i18n import N_, get_locale, ngettext
from app.i18n import gettext as _

KIND_EMOJI = {
    "lottery_round": "🎟️",
    "eligibility_item_sale": "💿",
    "stream_ticket_sale": "📺",
    "general_sale": "🎫",
    "result_announcement": "📣",
    "payment_deadline": "💴",
    "fcfs_sale": "🏁",
    "tour_package": "✈️",
    "goods_sale": "🛍️",
    "upgrade": "⬆️",
    "other": "📌",
}

ANCHOR_VERB = {
    Anchor.OPENS: N_("opens"),
    Anchor.CLOSES: N_("closes"),
    Anchor.RESULTS: N_("results announced"),
    Anchor.PAYMENT: N_("payment due"),
    Anchor.EVENT_START: N_("starts"),
}


def relative_phrase(anchor_time: datetime, fire_at: datetime) -> str:
    """'in 3 days' / 'in 5 hours' / 'now' / '2 days ago' (for after-offsets)."""
    delta = anchor_time - fire_at
    seconds = int(delta.total_seconds())
    if abs(seconds) < 3600:
        return _("now")
    hours = abs(seconds) // 3600
    if hours < 48:
        unit = ngettext("{n} hour", "{n} hours", hours).format(n=hours)
    else:
        days = round(hours / 24)
        unit = ngettext("{n} day", "{n} days", days).format(n=days)
    return _("in {unit}").format(unit=unit) if seconds > 0 else _("{unit} ago").format(unit=unit)


def safe_button_url(raw: str | None) -> str | None:
    """An http(s) URL fit for a Discord link button, or None to omit it.

    URLs are validated on save now, but rows written before that landed can
    still hold e.g. `javascript:...`. Discord's API rejects a non-http(s)
    button URL with an HTTPException, which the scheduler classes as a
    TRANSIENT_FAILURE -- so the queue row would never be marked sent and
    would retry every tick forever. Losing one button beats wedging the
    queue, so a bad URL just drops the button and the DM still goes out.

    domain/urls raises rather than returning a flag, and web/forms' 422
    wrapper is the wrong translation here (and would drag FastAPI into the
    bot layer), so this is the bot's own boundary translation.
    """
    try:
        return clean_url(raw)
    except UnsafeURLError:
        return None


def format_reminder(item: DueReminder) -> str:
    subject = item.round_label or item.day_label or _("event")
    emoji = KIND_EMOJI.get(item.round_kind or "", "🗓️")
    verb = _(ANCHOR_VERB[item.anchor])

    lines = [f"{emoji} **{item.concert_title}** — {subject}"]
    if item.anchor_time_utc is not None:
        when = fmt_dual(item.anchor_time_utc, item.user_timezone, get_locale())
        rel = relative_phrase(item.anchor_time_utc, item.fire_at_utc)
        lines.append(f"{verb} {rel}: {when}")
    if item.url:
        lines.append(item.url)
    return "\n".join(lines)


# ── Rich embeds + button views (Phase 12) ────────────────────────────────


def build_new_event_message(ctx) -> tuple:
    """(embed, view) for the new-event notice. ctx: service.NoticeContext."""
    import discord

    from app.bot.views import (
        ApplyDefaultButton,
        RemoveRemindersButton,
        ShowDeadlinesButton,
    )
    from app.config import settings

    embed = discord.Embed(
        title=f"🆕 {ctx.title}",
        description=ctx.tags_line or None,
        color=0x4F46B8,
    )
    if ctx.venue:
        embed.add_field(name=_("Venue"), value=f"📍 {ctx.venue}", inline=True)
    if ctx.first_deadline_at is not None:
        embed.add_field(
            name=_("First deadline"),
            value=(f"{ctx.first_deadline_label}\n"
                   f"{fmt_dual(ctx.first_deadline_at, ctx.user_timezone, get_locale())}"),
            inline=False,
        )

    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label=_("Open on dekimasen.app"),
        url=f"{settings.base_url}/concerts/{ctx.event_id}",
    ))
    # State-aware: auto-applied subscribers get the undo; others get the apply.
    if ctx.user_has_rules:
        view.add_item(RemoveRemindersButton(ctx.concert_id))
    else:
        view.add_item(ApplyDefaultButton(ctx.concert_id))
    view.add_item(ShowDeadlinesButton(ctx.concert_id))
    return embed, view


def build_leg_cancelled_message(ctx) -> tuple:
    """(embed, view) for a leg-cancellation notice. ctx: service.LegCancelledContext."""
    import discord

    from app.bot.views import ReinstateRemindersButton
    from app.config import settings

    # One leg of a tour going down and the whole show being called off are not
    # the same news, and this DM is the ONLY channel that carries either. On a
    # dead concert every reminder here is gone -- the payment reminder on a won
    # ticket included, which is the one a reader owed a refund most needs to
    # know about -- so say that, and say what survives it.
    embed = discord.Embed(
        title=f"🚫 {ctx.title}",
        description=(
            _("This event is cancelled — every performance is off, and all your reminders "
              "for it have been cleared. Anything you already recorded, like a won ticket, "
              "stays on your record.")
            if getattr(ctx, "concert_cancelled", False)
            else _("A performance you had a reminder for was cancelled, and it's been cleared.")
        ),
        color=0xB3261E,
    )
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label=_("Open on dekimasen.app"),
        url=f"{settings.base_url}/concerts/{ctx.event_id}",
    ))
    view.add_item(ReinstateRemindersButton(ctx.concert_id))
    return embed, view


def build_reminder_message(item: DueReminder) -> tuple:
    """(embed, view) for a deadline reminder DM."""
    import discord

    from app.bot.views import (
        MAX_DAY_BUTTONS,
        AppliedButton,
        LostAllButton,
        LostButton,
        NotAppliedButton,
        PaidButton,
        RemindLaterButton,
        SnoozeButton,
        WonAllButton,
        WonButton,
        WonDayButton,
    )
    from app.config import settings

    subject = item.round_label or item.day_label or _("event")
    emoji = KIND_EMOJI.get(item.round_kind or "", "🗓️")
    verb = _(ANCHOR_VERB[item.anchor])

    embed = discord.Embed(title=f"{emoji} {item.concert_title}", color=0x1A7F4E)
    if item.anchor_time_utc is not None:
        rel = relative_phrase(item.anchor_time_utc, item.fire_at_utc)
        embed.description = (
            f"**{subject}** {verb} {rel}\n"
            f"{fmt_dual(item.anchor_time_utc, item.user_timezone, get_locale())}"
        )
    else:
        embed.description = f"**{subject}**"

    if item.requires_label:
        line = "🛍️ " + _("Requires: {name}").format(name=item.requires_label)
        if item.requires_closes_at_utc is not None:
            line += " — {} {}".format(
                _("sale ends"),
                fmt_dual(item.requires_closes_at_utc, item.user_timezone, get_locale()),
            )
        embed.description = f"{embed.description}\n{line}"

    view = discord.ui.View(timeout=None)
    ticket_url = safe_button_url(item.url)
    if ticket_url:
        link_label = _("Apply here") if item.anchor is Anchor.CLOSES else _("Ticket page")
        view.add_item(discord.ui.Button(label=link_label, url=ticket_url))
    view.add_item(discord.ui.Button(
        label=_("Open on dekimasen.app"), url=f"{settings.base_url}"
    ))

    if item.round_id is not None:
        if item.anchor is Anchor.CLOSES and item.outcome is None:
            view.add_item(AppliedButton(item.round_id))
            view.add_item(NotAppliedButton(item.round_id))
        elif item.anchor is Anchor.RESULTS and item.outcome in (None, LotteryOutcome.APPLIED):
            # A round covering two or more legs (covered_days is filled for
            # exactly those, and only on a RESULTS row) asks leg by leg
            # instead: it can come back won on one night and lost on another,
            # so the flat pair would have to lie about one of them. The DM
            # opens with the wins -- "Won — Day 2" and the two all-legs
            # shortcuts -- and the per-leg lost/not-going questions arrive in
            # the follow-up view the first press edits in, which keeps the
            # reminder itself readable. Same vocabulary as the web's
            # _capture_actions.html macro.
            if len(item.covered_days) >= 2:
                view.add_item(WonAllButton(item.round_id))
                for day_id, day_label in item.covered_days[:MAX_DAY_BUTTONS]:
                    view.add_item(WonDayButton(item.round_id, day_id, day_label))
                view.add_item(LostAllButton(item.round_id))
            else:
                view.add_item(WonButton(item.round_id))
                view.add_item(LostButton(item.round_id))
        elif item.anchor is Anchor.PAYMENT and item.outcome is LotteryOutcome.WON:
            view.add_item(PaidButton(item.round_id))

    if item.anchor is Anchor.CLOSES:
        view.add_item(RemindLaterButton(item.queue_id))
    else:
        view.add_item(SnoozeButton(item.queue_id))
    return embed, view
