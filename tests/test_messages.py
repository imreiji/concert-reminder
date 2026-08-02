"""DM formatting tests — what users actually see."""

from datetime import UTC, datetime

from app import i18n
from app.bot.messages import (
    build_leg_cancelled_message,
    build_new_event_message,
    build_reminder_message,
    format_reminder,
    relative_phrase,
)
from app.db.service import DueReminder, LegCancelledContext, NoticeContext
from app.domain.types import Anchor, LotteryOutcome


def dt(month, day, hour=12):
    return datetime(2026, month, day, hour, tzinfo=UTC)


def _closes_item_3_days_out(language="en"):
    """A CLOSES-anchor reminder whose deadline is exactly 3 days after fire."""
    return DueReminder(
        queue_id=1,
        discord_id=42,
        user_timezone="America/Moncton",
        user_language=language,
        concert_title="Hasunosora 5th",
        anchor=Anchor.CLOSES,
        fire_at_utc=dt(6, 22, 14),
        round_label="最速先行 Round 1",
        round_kind="lottery_round",
        anchor_time_utc=dt(6, 25, 14),
    )


def test_reminder_message_translates_verb():
    """With a ja catalogue live, the anchor verb and relative phrase localize."""
    i18n._catalog_cache["ja"] = i18n._translations_from_po_text(
        'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=utf-8\\n"\n'
        '"Plural-Forms: nplurals=1; plural=0;\\n"\n\n'
        'msgid "closes"\nmsgstr "締切"\n\n'
        'msgid "in {unit}"\nmsgstr "{unit}後"\n\n'
        'msgid "{n} day"\nmsgid_plural "{n} days"\nmsgstr[0] "{n}日"\n',
        "ja",
    )
    i18n.set_locale("ja")
    try:
        text = format_reminder(_closes_item_3_days_out())
        assert "締切" in text
        assert "3日後" in text
    finally:
        i18n.set_locale("en")
        i18n.reset_catalog_cache()


def test_reminder_message_en_unchanged():
    """EN default stays byte-identical to the pre-i18n output."""
    i18n.set_locale("en")
    text = format_reminder(_closes_item_3_days_out())
    assert "closes in 3 days:" in text


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


def test_upgrade_round_gets_its_own_emoji():
    item = DueReminder(
        queue_id=6,
        discord_id=42,
        user_timezone="America/Moncton",
        concert_title="Hasunosora 5th",
        anchor=Anchor.CLOSES,
        fire_at_utc=dt(6, 22, 14),
        round_label="Upgrade round",
        round_kind="upgrade",
        anchor_time_utc=dt(6, 25, 14),
    )
    msg = format_reminder(item)
    assert "⬆️" in msg


def test_every_round_kind_has_an_emoji():
    from app.bot.messages import KIND_EMOJI
    from app.domain.types import RoundKind

    assert set(KIND_EMOJI) == {k.value for k in RoundKind}


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


def test_leg_cancelled_message_names_the_whole_event_when_it_is_dead():
    """This DM is the only channel telling a reader the show is off entirely.
    "A performance was cancelled" is true of one leg of a tour and an
    understatement of a dead concert, where every reminder is gone -- the
    payment reminder on a won ticket included. It also has to say what
    survives: the outcome record is never deleted (invariant 8)."""
    ctx = LegCancelledContext(
        concert_id=1, event_id="dead-tour", title="Dead Tour", concert_cancelled=True,
    )
    embed, _view = build_leg_cancelled_message(ctx)
    assert "This event is cancelled" in embed.description
    assert "all your reminders" in embed.description
    assert "stays on your record" in embed.description


def test_leg_cancelled_message_is_unchanged_for_one_leg_of_a_tour():
    """One leg down while others stand: the original sentence is exactly
    right, and the reader still has live reminders here."""
    ctx = LegCancelledContext(concert_id=1, event_id="tour", title="Tour")
    embed, _view = build_leg_cancelled_message(ctx)
    assert embed.description == (
        "A performance you had a reminder for was cancelled, and it's been cleared."
    )


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


def test_build_reminder_message_multi_leg_results_offers_per_day_buttons():
    """A round covering two legs can come back won on one and lost on the
    other, so the flat Won/Lost pair would have to lie about one of them.
    `covered_days` (leg PRESENCE, filled by due_reminders) is the switch."""
    item = DueReminder(
        queue_id=1, discord_id=42, user_timezone="America/Moncton",
        concert_title="Hasunosora 5th", anchor=Anchor.RESULTS, fire_at_utc=dt(6, 25),
        round_id=7, round_label="最速先行 Round 1", round_kind="lottery_round",
        anchor_time_utc=dt(6, 25), outcome=LotteryOutcome.APPLIED,
        covered_days=((11, "Day 1"), (12, "Day 2")),
    )
    _, view = build_reminder_message(item)
    custom_ids = [getattr(c, "custom_id", None) for c in view.children]
    assert "dk:wonall:7" in custom_ids
    assert "dk:wonday:7:11" in custom_ids
    assert "dk:wonday:7:12" in custom_ids
    assert "dk:lostall:7" in custom_ids
    # The flat pair would be a second, contradictory way to answer the same
    # question -- one round, one vocabulary.
    assert not any(cid and cid.startswith(("dk:won:", "dk:lost:")) for cid in custom_ids)
    # A DynamicItem proxies custom_id but not label -- the wrapped Button
    # carries it, so the leg's name has to be read one level down.
    labels = {getattr(getattr(c, "item", c), "label", None) for c in view.children}
    assert "Won — Day 1" in labels


def test_build_reminder_message_single_leg_results_keeps_the_flat_pair():
    item = DueReminder(
        queue_id=1, discord_id=42, user_timezone="America/Moncton",
        concert_title="Hasunosora 5th", anchor=Anchor.RESULTS, fire_at_utc=dt(6, 25),
        round_id=7, round_label="最速先行 Round 1", round_kind="lottery_round",
        anchor_time_utc=dt(6, 25), outcome=LotteryOutcome.APPLIED,
        covered_days=(),
    )
    _, view = build_reminder_message(item)
    custom_ids = [getattr(c, "custom_id", None) for c in view.children]
    assert "dk:won:7" in custom_ids and "dk:lost:7" in custom_ids
    assert not any(cid and cid.startswith("dk:wonall:") for cid in custom_ids)


def test_build_reminder_message_caps_the_per_day_buttons():
    """Discord allows 25 components across 5 rows, and the follow-up view
    gives each leg a row of its own -- so the questions are asked a batch at a
    time. Resolving one shrinks the unresolved list, which is what makes the
    remaining legs reachable on the next press."""
    item = DueReminder(
        queue_id=1, discord_id=42, user_timezone="America/Moncton",
        concert_title="Long tour", anchor=Anchor.RESULTS, fire_at_utc=dt(6, 25),
        round_id=7, round_label="全国ツアー", round_kind="lottery_round",
        anchor_time_utc=dt(6, 25), outcome=LotteryOutcome.APPLIED,
        covered_days=tuple((100 + n, f"Day {n}") for n in range(9)),
    )
    _, view = build_reminder_message(item)
    custom_ids = [getattr(c, "custom_id", None) for c in view.children]
    assert len([c for c in custom_ids if c and c.startswith("dk:wonday:")]) == 4
    assert len(view.children) <= 25


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


def test_build_reminder_message_drops_button_for_non_http_url_but_still_sends():
    """Rows predating URL validation on save can still hold a `javascript:`
    link. Discord's API rejects a non-http(s) button URL with an HTTPException
    the scheduler classes as TRANSIENT_FAILURE, so the row would never be
    marked sent and would retry every tick forever. Drop the button, keep the
    DM -- one missing link beats a wedged queue."""
    item = DueReminder(
        queue_id=1, discord_id=42, user_timezone="America/Moncton",
        concert_title="Hasunosora 5th", anchor=Anchor.CLOSES, fire_at_utc=dt(6, 22),
        round_id=7, round_label="最速先行 Round 1", round_kind="lottery_round",
        anchor_time_utc=dt(6, 25), url="javascript:alert(1)",
    )
    embed, view = build_reminder_message(item)
    urls = [getattr(c, "url", None) for c in view.children]
    assert not any(u and u.startswith("javascript:") for u in urls)
    labels = {getattr(c, "label", None) for c in view.children}
    assert "Apply here" not in labels
    # The DM itself still goes out, with everything that doesn't depend on
    # that URL: the embed, the site link, and the outcome/snooze buttons.
    assert embed.title and "Hasunosora 5th" in embed.title
    assert "Open on dekimasen.app" in labels
    custom_ids = [getattr(c, "custom_id", None) for c in view.children]
    assert any(cid and cid.startswith("dk:applied:") for cid in custom_ids)


def test_build_reminder_message_keeps_button_for_ordinary_http_url():
    item = DueReminder(
        queue_id=1, discord_id=42, user_timezone="America/Moncton",
        concert_title="Hasunosora 5th", anchor=Anchor.CLOSES, fire_at_utc=dt(6, 22),
        round_id=7, round_label="最速先行 Round 1", round_kind="lottery_round",
        anchor_time_utc=dt(6, 25), url="https://example.com/apply",
    )
    _, view = build_reminder_message(item)
    urls = [getattr(c, "url", None) for c in view.children]
    assert "https://example.com/apply" in urls


def test_reminder_embed_carries_requires_line():
    """A CLOSES reminder on a round that requires an item sale gets an extra
    "Requires: <label>" line, with the sale's own close time appended while
    it's still open -- the same open-gate rule RoundRow's display carries."""
    item = DueReminder(
        queue_id=1, discord_id=42, user_timezone="America/Moncton",
        concert_title="Hasunosora 5th", anchor=Anchor.CLOSES, fire_at_utc=dt(6, 22),
        round_id=7, round_label="最速先行 Round 1", round_kind="lottery_round",
        anchor_time_utc=dt(6, 25),
        requires_label="グッズ販売", requires_closes_at_utc=dt(6, 20),
    )
    embed, _view = build_reminder_message(item)
    assert "Requires:" in embed.description
    assert "グッズ販売" in embed.description
    assert "sale ends" in embed.description


def test_reminder_embed_drops_requires_close_time_once_sale_over():
    item = DueReminder(
        queue_id=1, discord_id=42, user_timezone="America/Moncton",
        concert_title="Hasunosora 5th", anchor=Anchor.CLOSES, fire_at_utc=dt(6, 22),
        round_id=7, round_label="最速先行 Round 1", round_kind="lottery_round",
        anchor_time_utc=dt(6, 25),
        requires_label="グッズ販売", requires_closes_at_utc=None,
    )
    embed, _view = build_reminder_message(item)
    assert "Requires:" in embed.description
    assert "グッズ販売" in embed.description
    assert "sale ends" not in embed.description


def test_reminder_embed_has_no_requires_line_when_round_names_no_dependency():
    item = _closes_item_3_days_out()
    embed, _view = build_reminder_message(item)
    assert "Requires:" not in embed.description


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
