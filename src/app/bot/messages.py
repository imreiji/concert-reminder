"""Reminder message formatting. Pure functions -> easy tests, no Discord needed.

The format contract:
    ⏰ **Hasunosora 5th** — 最速先行 Round 1
    closes in 3 days: Thu 2026-06-25 23:59 JST (11:59 ADT)
    <url if present>
"""

from datetime import datetime

from app.db.service import DueReminder
from app.domain.timezones import fmt_dual
from app.domain.types import Anchor

KIND_EMOJI = {
    "lottery_round": "🎟️",
    "eligibility_item_sale": "💿",
    "stream_ticket_sale": "📺",
    "general_sale": "🏃",
    "result_announcement": "📣",
    "payment_deadline": "💴",
    "other": "📌",
}

ANCHOR_VERB = {
    Anchor.OPENS: "opens",
    Anchor.CLOSES: "closes",
    Anchor.EVENT_START: "starts",
}


def relative_phrase(anchor_time: datetime, fire_at: datetime) -> str:
    """'in 3 days' / 'in 5 hours' / 'now' / '2 days ago' (for after-offsets)."""
    delta = anchor_time - fire_at
    seconds = int(delta.total_seconds())
    if abs(seconds) < 3600:
        return "now"
    hours = abs(seconds) // 3600
    if hours < 48:
        unit = f"{hours} hour{'s' if hours != 1 else ''}"
    else:
        days = round(hours / 24)
        unit = f"{days} day{'s' if days != 1 else ''}"
    return f"in {unit}" if seconds > 0 else f"{unit} ago"


def format_reminder(item: DueReminder) -> str:
    subject = item.round_label or item.day_label or "event"
    emoji = KIND_EMOJI.get(item.round_kind or "", "🗓️")
    verb = ANCHOR_VERB[item.anchor]

    lines = [f"{emoji} **{item.concert_title}** — {subject}"]
    if item.anchor_time_utc is not None:
        when = fmt_dual(item.anchor_time_utc, item.user_timezone)
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
        embed.add_field(name="Venue", value=f"📍 {ctx.venue}", inline=True)
    if ctx.first_deadline_at is not None:
        embed.add_field(
            name="First deadline",
            value=(f"{ctx.first_deadline_label}\n"
                   f"{fmt_dual(ctx.first_deadline_at, ctx.user_timezone)}"),
            inline=False,
        )

    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label="Open on dekimasen.app",
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

    embed = discord.Embed(
        title=f"🚫 {ctx.title}",
        description="A performance you had a reminder for was cancelled, and it's been cleared.",
        color=0xB3261E,
    )
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label="Open on dekimasen.app",
        url=f"{settings.base_url}/concerts/{ctx.event_id}",
    ))
    view.add_item(ReinstateRemindersButton(ctx.concert_id))
    return embed, view


def build_reminder_message(item: DueReminder) -> tuple:
    """(embed, view) for a deadline reminder DM."""
    import discord

    from app.bot.views import SnoozeButton
    from app.config import settings

    subject = item.round_label or item.day_label or "event"
    emoji = KIND_EMOJI.get(item.round_kind or "", "🗓️")
    verb = ANCHOR_VERB[item.anchor]

    embed = discord.Embed(title=f"{emoji} {item.concert_title}", color=0x1A7F4E)
    if item.anchor_time_utc is not None:
        rel = relative_phrase(item.anchor_time_utc, item.fire_at_utc)
        embed.description = (
            f"**{subject}** {verb} {rel}\n{fmt_dual(item.anchor_time_utc, item.user_timezone)}"
        )
    else:
        embed.description = f"**{subject}**"

    view = discord.ui.View(timeout=None)
    if item.url:
        view.add_item(discord.ui.Button(label="Ticket page", url=item.url))
    view.add_item(discord.ui.Button(
        label="Open on dekimasen.app", url=f"{settings.base_url}"
    ))
    view.add_item(SnoozeButton(item.queue_id))
    return embed, view
