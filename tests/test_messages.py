"""DM formatting tests — what users actually see."""

from datetime import UTC, datetime

from app.bot.messages import build_new_event_message, format_reminder, relative_phrase
from app.db.service import DueReminder, NoticeContext
from app.domain.types import Anchor


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
