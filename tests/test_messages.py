"""DM formatting tests — what users actually see."""

from datetime import UTC, datetime

from app.bot.messages import (
    build_new_event_message,
    build_reminder_message,
    format_reminder,
    relative_phrase,
)
from app.db.service import DueReminder, NoticeContext
from app.domain.types import Anchor, LotteryOutcome


def dt(month, day, hour=12):
    return datetime(2026, month, day, hour, tzinfo=UTC)


def test_relative_phrase():
    assert relative_phrase(dt(6, 25), dt(6, 22)) == "in 3 days"
    assert relative_phrase(dt(6, 25, 18), dt(6, 25, 12)) == "in 6 hours"
    assert relative_phrase(dt(6, 25), dt(6, 25)) == "now"
    assert relative_phrase(dt(6, 25), dt(6, 27)) == "2 days ago"  # after-offset recap


def test_format_round_reminder():
    item = DueReminder(
        queue_id=1,
        discord_id=42,
        user_timezone="America/Moncton",
        concert_title="Hasunosora 5th",
        anchor=Anchor.CLOSES,
        fire_at_utc=dt(6, 22, 14, ),
        round_label="最速先行 Round 1",
        round_kind="lottery_round",
        anchor_time_utc=dt(6, 25, 14),
        url="https://example.com/tickets",
    )
    msg = format_reminder(item)
    assert "Hasunosora 5th" in msg
    assert "最速先行 Round 1" in msg
    assert "closes in 3 days" in msg
    assert "JST" in msg and "ADT" in msg  # both timezones shown
    assert "https://example.com/tickets" in msg


def test_general_sale_uses_ticket_emoji_not_running_emoji():
    item = DueReminder(
        queue_id=3,
        discord_id=42,
        user_timezone="America/Moncton",
        concert_title="Hasunosora 5th",
        anchor=Anchor.CLOSES,
        fire_at_utc=dt(6, 22, 14),
        round_label="General sale",
        round_kind="general_sale",
        anchor_time_utc=dt(6, 25, 14),
    )
    msg = format_reminder(item)
    assert "🎫" in msg
    assert "🏃" not in msg


def test_fcfs_sale_gets_its_own_emoji():
    item = DueReminder(
        queue_id=4,
        discord_id=42,
        user_timezone="America/Moncton",
        concert_title="Hasunosora 5th",
        anchor=Anchor.OPENS,
        fire_at_utc=dt(6, 22, 14),
        round_label="FCFS sale",
        round_kind="fcfs_sale",
        anchor_time_utc=dt(6, 25, 14),
    )
    msg = format_reminder(item)
    assert "🏁" in msg


def test_tour_package_gets_its_own_emoji():
    item = DueReminder(
        queue_id=5,
        discord_id=42,
        user_timezone="America/Moncton",
        concert_title="Hasunosora 5th",
        anchor=Anchor.CLOSES,
        fire_at_utc=dt(6, 22, 14),
        round_label="Overseas tour package",
        round_kind="tour_package",
        anchor_time_utc=dt(6, 25, 14),
    )
    msg = format_reminder(item)
    assert "✈️" in msg


def test_format_day_reminder_without_round():
    item = DueReminder(
        queue_id=2,
        discord_id=42,
        user_timezone="Asia/Tokyo",
        concert_title="Gakumas 2nd",
        anchor=Anchor.EVENT_START,
        fire_at_utc=dt(7, 25, 9),
        anchor_time_utc=dt(8, 1, 9),
        day_label="Day 1",
    )
    msg = format_reminder(item)
    assert "Gakumas 2nd" in msg and "Day 1" in msg
    assert "starts in 7 days" in msg


def test_new_event_message_links_to_event_id_not_internal_pk():
    """The "Open on dekimasen.app" button must use the URL-facing event_id,
    never the internal Concert.id -- those two diverge as soon as an editor
    picks a custom event_id at creation."""
    ctx = NoticeContext(
        concert_id=999,
        event_id="hasunosora-6th",
        title="Hasunosora 6th",
        tags_line="Hasunosora",
        venue="K Arena Yokohama",
        first_deadline_label="最速先行",
        first_deadline_at=dt(6, 25, 14),
        user_timezone="America/Moncton",
        user_has_rules=False,
        user_has_default_preset=False,
    )
    _, view = build_new_event_message(ctx)
    open_button = next(
        item for item in view.children
        if getattr(item, "label", None) == "Open on dekimasen.app"
    )
    assert open_button.url.endswith("/concerts/hasunosora-6th")
    assert "/concerts/999" not in open_button.url


def test_build_reminder_message_shows_apply_buttons_on_closes_with_no_outcome():
    item = DueReminder(
        queue_id=1, discord_id=42, user_timezone="America/Moncton",
        concert_title="Hasunosora 5th", anchor=Anchor.CLOSES, fire_at_utc=dt(6, 22),
        round_id=7, round_label="最速先行 Round 1", round_kind="lottery_round",
        anchor_time_utc=dt(6, 25), outcome=None,
    )
    _, view = build_reminder_message(item)
    # discord.ui.DynamicItem only proxies custom_id (not .label) -- checking
    # custom_id is also the more precise assertion, since it identifies
    # exactly which button this is, not just its display text.
    custom_ids = [getattr(c, "custom_id", None) for c in view.children]
    assert any(cid and cid.startswith("dk:applied:") for cid in custom_ids)
    assert any(cid and cid.startswith("dk:notapplied:") for cid in custom_ids)
    assert not any(
        cid and (cid.startswith("dk:won:") or cid.startswith("dk:paid:")) for cid in custom_ids
    )


def test_build_reminder_message_shows_won_lost_buttons_on_results_when_applied():
    item = DueReminder(
        queue_id=1, discord_id=42, user_timezone="America/Moncton",
        concert_title="Hasunosora 5th", anchor=Anchor.RESULTS, fire_at_utc=dt(6, 25),
        round_id=7, round_label="最速先行 Round 1", round_kind="lottery_round",
        anchor_time_utc=dt(6, 25), outcome=LotteryOutcome.APPLIED,
    )
    _, view = build_reminder_message(item)
    custom_ids = [getattr(c, "custom_id", None) for c in view.children]
    assert any(cid and cid.startswith("dk:won:") for cid in custom_ids)
    assert any(cid and cid.startswith("dk:lost:") for cid in custom_ids)


def test_build_reminder_message_shows_paid_button_on_payment_when_won():
    item = DueReminder(
        queue_id=1, discord_id=42, user_timezone="America/Moncton",
        concert_title="Hasunosora 5th", anchor=Anchor.PAYMENT, fire_at_utc=dt(6, 28),
        round_id=7, round_label="最速先行 Round 1", round_kind="lottery_round",
        anchor_time_utc=dt(6, 30), outcome=LotteryOutcome.WON,
    )
    _, view = build_reminder_message(item)
    custom_ids = [getattr(c, "custom_id", None) for c in view.children]
    assert any(cid and cid.startswith("dk:paid:") for cid in custom_ids)


def test_build_reminder_message_shows_no_outcome_buttons_on_payment_when_lost():
    item = DueReminder(
        queue_id=1, discord_id=42, user_timezone="America/Moncton",
        concert_title="Hasunosora 5th", anchor=Anchor.PAYMENT, fire_at_utc=dt(6, 28),
        round_id=7, round_label="最速先行 Round 1", round_kind="lottery_round",
        anchor_time_utc=dt(6, 30), outcome=LotteryOutcome.LOST,
    )
    _, view = build_reminder_message(item)
    custom_ids = [getattr(c, "custom_id", None) for c in view.children]
    blocked_prefixes = ("dk:paid:", "dk:won:", "dk:lost:")
    assert not any(cid and cid.startswith(blocked_prefixes) for cid in custom_ids)


def test_build_reminder_message_closes_reminder_uses_remind_later_not_snooze():
    item = DueReminder(
        queue_id=1, discord_id=42, user_timezone="America/Moncton",
        concert_title="Hasunosora 5th", anchor=Anchor.CLOSES, fire_at_utc=dt(6, 22),
        round_id=7, round_label="最速先行 Round 1", round_kind="lottery_round",
        anchor_time_utc=dt(6, 25), url="https://example.com/apply",
    )
    _, view = build_reminder_message(item)
    # discord.ui.DynamicItem only proxies custom_id (not .label) -- checking
    # custom_id is also the more precise assertion, since it identifies
    # exactly which button this is, not just its display text.
    custom_ids = [getattr(c, "custom_id", None) for c in view.children]
    assert any(cid and cid.startswith("dk:remindlater:") for cid in custom_ids)
    assert not any(cid and cid.startswith("dk:snooze:") for cid in custom_ids)
    labels = {getattr(c, "label", None) for c in view.children}
    assert "Apply here" in labels
    assert "Ticket page" not in labels


def test_build_reminder_message_other_anchors_keep_plain_snooze():
    item = DueReminder(
        queue_id=1, discord_id=42, user_timezone="America/Moncton",
        concert_title="Hasunosora 5th", anchor=Anchor.RESULTS, fire_at_utc=dt(6, 25),
        round_id=7, round_label="最速先行 Round 1", round_kind="lottery_round",
        anchor_time_utc=dt(6, 25),
    )
    _, view = build_reminder_message(item)
    custom_ids = [getattr(c, "custom_id", None) for c in view.children]
    assert any(cid and cid.startswith("dk:snooze:") for cid in custom_ids)
    assert not any(cid and cid.startswith("dk:remindlater:") for cid in custom_ids)
