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
    subject = item.window_label or item.day_label or "event"
    emoji = KIND_EMOJI.get(item.window_kind or "", "🗓️")
    verb = ANCHOR_VERB[item.anchor]

    lines = [f"{emoji} **{item.concert_title}** — {subject}"]
    if item.anchor_time_utc is not None:
        when = fmt_dual(item.anchor_time_utc, item.user_timezone)
        rel = relative_phrase(item.anchor_time_utc, item.fire_at_utc)
        lines.append(f"{verb} {rel}: {when}")
    if item.url:
        lines.append(item.url)
    return "\n".join(lines)
