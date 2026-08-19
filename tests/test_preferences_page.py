"""Preferences rebuilt on the demo's vocabulary (Task 8).

The page is a left-rail layout (`.plyt`/`.prail`) whose sections carry the
demo's component classes: a fixed-height Following summary (Following rework
phase 4, task 4 -- no more per-tag `.subrow`/`.swb` toggles; the surviving
`.swb` is the skipped-events list's Restore button), `.presetcard`/
`.ruleline` Reminders, a two-select Time block, Delivery status pills, and a
`.danger`-framed Account. These tests pin the *markup surface* renders (the
"every page a logged-in GET render test" rule); the toggle behaviour lives in
test_preferences_following.py, and the shared token CSS in test_theme_and_tokens.
"""

import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.db.models import (
    Concert,
    ConcertDay,
    ConcertTag,
    PresetItem,
    ReminderPreset,
    Round,
    Tag,
    TagSubscription,
    User,
)
from app.db.session import get_session
from app.domain.types import RoundKind, TagKind
from app.web import auth
from app.web.app import create_app

USER_A = 5151


@pytest.fixture()
def client(db, monkeypatch):
    app = create_app()

    async def override_session():
        async with db() as s:
            yield s

    app.dependency_overrides[get_session] = override_session

    async def fake_exchange(code):
        return "tok"

    monkeypatch.setattr(auth, "exchange_code", fake_exchange)
    c = TestClient(app, follow_redirects=False)
    c.db = db
    c.monkeypatch = monkeypatch
    return c


def login_as(client, discord_id: int, name: str):
    async def fake_identity(token):
        return {"id": str(discord_id), "username": name, "global_name": name, "avatar": None}

    client.monkeypatch.setattr(auth, "fetch_identity", fake_identity)
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    client.get(f"/auth/callback?code=x&state={state}")


async def seed(db) -> SimpleNamespace:
    """One followed GROUP tag on one upcoming concert, so the Following
    section's tags-followed and concerts-tracked counts are both non-zero."""
    now = datetime.now(UTC)
    async with db() as s:
        s.add(User(discord_id=USER_A, username="reiji"))
        await s.flush()
        tag = Tag(name="Aqours", kind=TagKind.GROUP)
        s.add(tag)
        await s.flush()
        s.add(TagSubscription(user_id=USER_A, tag_id=tag.id, notify=True))
        concert = Concert(title="Big Show", event_id="big-show", created_by=USER_A)
        s.add(concert)
        await s.flush()
        s.add(ConcertTag(concert_id=concert.id, tag_id=tag.id))
        day = ConcertDay(
            concert_id=concert.id, label="Day 1", starts_at_utc=now + timedelta(days=60)
        )
        s.add(day)
        await s.flush()
        s.add(Round(
            concert_id=concert.id, label="Lottery 1", kind=RoundKind.LOTTERY_ROUND,
            opens_at_utc=now - timedelta(days=1), closes_at_utc=now + timedelta(days=7),
            applies_to=[day.id],
        ))
        await s.commit()
        return SimpleNamespace(tag_id=tag.id, concert_id=concert.id)


async def test_renders_for_logged_in_user(client):
    """The logged-in GET render test every page must keep."""
    await seed(client.db)
    login_as(client, USER_A, "reiji")
    r = client.get("/preferences")
    assert r.status_code == 200
    assert "Preferences" in r.text


async def test_rail_renders_with_active_indicator(client):
    """The left rail is the demo's `.prail`, with the first link server-side
    `.on` (the scrollspy JS then keeps it in step with scroll)."""
    await seed(client.db)
    login_as(client, USER_A, "reiji")
    r = client.get("/preferences")
    assert 'class="prail"' in r.text
    assert 'href="#p-follow"' in r.text
    assert 'class="on"' in r.text  # first rail link starts active


def _p_follow_section(html: str) -> str:
    """The Following section's own markup, and NOTHING else.

    `base.html` renders a Tags nav link and a mobile tab bar on every page,
    and the Reminders section right below Following renders its own "Default"
    pill and "Make default" button carrying similar copy -- an unscoped
    assertion about Following could pass against either. Both boundary
    strings are `id="..."` on the `<section>` tags themselves (not the rail's
    `href="#p-follow"` anchors, which appear earlier in the document and
    would make the slice start too soon).
    """
    start = html.index('id="p-follow"')
    end = html.index('id="p-remind"', start)
    return html[start:end]


async def test_following_section_reduced_to_count_and_manage_link(client):
    """Following shrinks to a fixed-height summary (phase 4 task 4): the
    tags-followed pill plus a "Manage" link to /following, both inside the
    p-follow section.

    Follows TWO tags but tracks only ONE concert (seed()'s GROUP tag, plus a
    second tag matching no concert), so the pill's number and the tracked-
    concert clock's number differ on purpose. With a single followed tag the
    two counts would coincide at 1 regardless of which context variable fed
    which span, and a `followed_count`/`tracked_count` mix-up in the route
    would pass unnoticed -- reviewer finding, fix round 1.

    Mutation: drop the summary pill, or point the link somewhere else --
    either leaves the section without its stated replacement for the deleted
    per-tag rows. Swapping `followed_count` for `tracked_count` in the route
    (or the reverse) also fails now: the pill would read "1 tags followed"
    instead of "2".
    """
    await seed(client.db)  # 1 followed GROUP tag, 1 tracked concert
    async with client.db() as s:
        untracked = Tag(name="Untracked Solo Artist", kind=TagKind.ARTIST)
        s.add(untracked)
        await s.flush()
        s.add(TagSubscription(user_id=USER_A, tag_id=untracked.id))
        await s.commit()
    login_as(client, USER_A, "reiji")
    r = client.get("/preferences")
    section = _p_follow_section(r.text)
    assert 'class="pill p-ok"' in section
    assert "2 tags followed" in section
    assert "1 tracked" in section  # the concert count, deliberately different
    assert 'href="/following"' in section


async def test_following_section_disambiguates_tracked_from_followed(client):
    """The pill counts TAGS ("N tags followed"); the tracked-concert clock
    must use a DIFFERENT word, or the two numbers sit side by side under the
    same word and read as a contradiction -- reviewer finding 1, fix round 1.
    Worse in ja/zh before the fix: both spans rendered the identical
    フォロー中 / 关注.

    Scoped to the specific clock span (the one containing "skipped"), not
    the whole section -- "followed" legitimately appears elsewhere in
    p-follow (the pill itself, and the apply button's "all followed tags"),
    so an unscoped `"followed" not in section` could never pass.

    Mutation: revert the clock span's "tracked" back to "followed" -- the
    captured span then reads "...followed &middot; ... upcoming...", so
    `"followed" not in clock_text` fails.
    """
    await seed(client.db)
    login_as(client, USER_A, "reiji")
    r = client.get("/preferences")
    section = _p_follow_section(r.text)
    m = re.search(r'<span class="clock">(.*?skipped.*?)</span>', section, re.S)
    assert m is not None, "no clock span carries the tracked/upcoming/skipped summary"
    clock_text = m.group(1)
    assert "tracked" in clock_text
    assert "followed" not in clock_text


async def test_following_section_has_no_per_tag_subrow_or_toggle(client):
    """The per-tag `.subrow`s and their Notify / Auto-apply / Unfollow `.swb`
    toggles are gone -- that management moved to /following's per-tag dialog.

    Every one of these strings DOES appear in the pre-change template (see
    test_preferences_page.py history / the phase-4 task-4 report for the
    red-first verification), so this is not a check that could never fail.

    Mutation: restore any one of the deleted `<form>`s (e.g. the Notify
    toggle alone) and this fails, because `aria-pressed` and
    `/subscriptions/{id}/notify` only ever appeared on the deleted markup --
    the surviving "Restore" button in the skipped-events list below carries
    neither.
    """
    await seed(client.db)
    login_as(client, USER_A, "reiji")
    r = client.get("/preferences")
    section = _p_follow_section(r.text)
    assert "aria-pressed" not in section
    assert "/subscriptions/" not in section
    assert "Auto-apply preset" not in section


async def test_following_section_has_no_tag_picker(client):
    """The "Follow another tag" disclosure and its sub-defaults control are
    gone -- following a new tag happens on /tags or /following now.

    Mutation: leave the `<details>` fold in place and this fails, since
    neither string appears anywhere else on the page.
    """
    await seed(client.db)
    login_as(client, USER_A, "reiji")
    r = client.get("/preferences")
    section = _p_follow_section(r.text)
    assert "Follow another tag" not in section
    assert "sub-defaults" not in section


async def test_following_section_shows_default_preset_and_relocated_apply_button(client):
    """The standing default (read-only) and Task 3's fill button now live
    beside each other inside the reduced Following section, not in the
    Reminders bar where the fill button used to render.

    Mutation: leave the fill button only in Reminders (it would still pass a
    page-wide search, which is why this asserts on the SCOPED section); or
    drop the default-preset display entirely.
    """
    await seed(client.db)
    async with client.db() as s:
        s.add(ReminderPreset(user_id=USER_A, name="Standard cover", is_default=True))
        await s.commit()
    login_as(client, USER_A, "reiji")
    r = client.get("/preferences")
    section = _p_follow_section(r.text)
    assert "Standard cover" in section
    assert 'action="/presets/apply-to-following"' in section


async def test_following_section_default_preset_pill_is_absent_with_no_default(client):
    """A user with no default preset sees the no-default state, not a blank
    or a crash.

    Mutation: render nothing in the `{% else %}` branch, or render the
    `{% if default_preset %}` branch unconditionally (a NoneType .name access
    would 500 instead)."""
    await seed(client.db)
    login_as(client, USER_A, "reiji")
    r = client.get("/preferences")
    section = _p_follow_section(r.text)
    assert "No default preset yet" in section


async def test_apply_button_suppressed_with_no_default_preset(client):
    """The fill button is a genuine no-op with no default preset -- the
    route itself would just report "you have no default preset yet" for the
    press. Reviewer finding 2, fix round 1: suppress it rather than offer a
    press that can only ever confirm nothing happened. The row stays
    non-empty: the "No default preset yet" pill still renders alone.

    Mutation: render the button unconditionally, outside the
    `{% if default_preset %}` guard -- this fails because the button would
    then sit beside the "No default preset yet" pill.
    """
    await seed(client.db)  # follows a tag; seed() creates no preset at all
    login_as(client, USER_A, "reiji")
    r = client.get("/preferences")
    section = _p_follow_section(r.text)
    assert "No default preset yet" in section
    assert 'action="/presets/apply-to-following"' not in section


async def test_apply_button_suppressed_with_no_follows(client):
    """The fill button is also a genuine no-op with zero follows -- there is
    no preset-less subscription row it could possibly fill. Reviewer finding
    2, fix round 1. The row stays non-empty: the default-preset pill still
    renders alone.

    Mutation: drop (or invert) the `{% if followed_count %}` guard around the
    button -- dropping it fails because the button renders here despite zero
    follows; inverting it fails the companion test above instead (the button
    would render with no default preset and vanish with a real one).
    """
    async with client.db() as s:
        s.add(User(discord_id=USER_A, username="reiji"))
        await s.flush()
        s.add(ReminderPreset(user_id=USER_A, name="Standard cover", is_default=True))
        await s.commit()
    login_as(client, USER_A, "reiji")
    r = client.get("/preferences")
    section = _p_follow_section(r.text)
    assert "Standard cover" in section
    assert 'action="/presets/apply-to-following"' not in section


async def test_delivery_status_pills(client):
    """Delivery shows a DM status pill and a calendar-feed status pill."""
    await seed(client.db)
    login_as(client, USER_A, "reiji")
    r = client.get("/preferences")
    assert "Discord DMs reaching you" in r.text  # not-blocked pill
    assert "calendar feed" in r.text.lower()  # calendar status pill (active or off)


async def test_reminder_sentence_slot_order_en(client):
    """The reminder rule reads as a sentence built from locale-ordered slots.
    In English the day/hour selects come before the anchor select, and the
    `each` fragment sits between the direction and anchor selects."""
    await seed(client.db)
    login_as(client, USER_A, "reiji")
    r = client.get("/preferences")
    # A preset must exist for a rule row to render; the "New preset" fold
    # always carries a blank sentence_fields even with no presets.
    assert 'name="days"' in r.text and 'name="time"' in r.text and 'name="anchor"' in r.text
    assert r.text.index('name="days"') < r.text.index('name="anchor"')
    assert "each" in r.text  # the EN pattern's between-slots text


async def test_reminder_sentence_slot_order_ja(client):
    """Under ja the pattern reorders the slots -- the anchor select leads and
    the day/hour selects follow -- once the ja msgstr exists.

    This task (minute-offsets Task 4) changed the pattern's msgid to
    "Remind me {days} day(s) {time} {direction} each {anchor}." (days/hours
    collapsed into one h:mm box), which by construction has no ja translation
    yet -- Task 8 re-translates it. Until then gettext falls back to the
    (untranslated) English msgid, so the slots render in SOURCE order rather
    than reordered, and this test only pins that the page still renders and
    the slots still resolve -- not the ja word order, which
    test_i18n_catalogues.py will re-guard once Task 8 lands the msgstr.
    """
    from app import i18n

    i18n.reset_catalog_cache()
    try:
        await seed(client.db)
        login_as(client, USER_A, "reiji")
        client.cookies.set("lang", "ja")
        r = client.get("/preferences")
        assert r.status_code == 200
        assert 'name="days"' in r.text and 'name="anchor"' in r.text and 'name="time"' in r.text
        # No ja msgstr yet for the new pattern -- falls back to source order.
        assert r.text.index('name="days"') < r.text.index('name="anchor"')
    finally:
        i18n.reset_catalog_cache()


async def test_time_has_two_selects(client):
    """The Time section is the demo's two-select layout: zone + detection."""
    await seed(client.db)
    login_as(client, USER_A, "reiji")
    r = client.get("/preferences")
    assert 'name="timezone"' in r.text
    assert "Follow my browser" in r.text  # the detection select's auto option


async def test_account_danger_framing(client):
    """The Account items sit in the danger-framed card (`.banner.dgr` + the
    `.danger-card` anatomy class -- G2's callout grammar)."""
    await seed(client.db)
    login_as(client, USER_A, "reiji")
    r = client.get("/preferences")
    assert 'class="banner dgr danger-card"' in r.text


async def test_editors_hidden_for_non_admin(client):
    """The admin-only Editors section renders nothing for a plain user."""
    await seed(client.db)
    login_as(client, USER_A, "reiji")
    r = client.get("/preferences")
    assert r.status_code == 200
    assert "Editors" not in r.text


async def test_editors_shown_for_admin(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(USER_A))
    await seed(client.db)
    login_as(client, USER_A, "reiji")
    r = client.get("/preferences")
    assert r.status_code == 200
    assert "Editors" in r.text


# ── The h:mm box (minute-offsets, Task 4) ────────────────────────────────


async def test_a_preset_item_round_trips_a_sub_hour_offset(client):
    """Type 0:30, store (0, 0, -30), and read it back out of the box as 0:30."""
    login_as(client, USER_A, "reiji")
    assert client.post("/presets", data={
        "name": "fcfs", "anchor": "opens", "days": "0", "time": "0:30",
        "direction": "before",
    }).status_code == 303

    async with client.db() as s:
        item = (await s.execute(select(PresetItem))).scalar_one()
    assert (item.offset_days, item.offset_hours, item.offset_minutes) == (0, 0, -30)

    page = client.get("/preferences")
    assert 'value="0:30"' in page.text


def test_a_bad_time_value_is_refused_not_rounded(client):
    """0:75 is a typo, and a reminder that silently moved to 1:15 is worse
    than an error page."""
    login_as(client, USER_A, "reiji")
    r = client.post("/presets", data={
        "name": "typo", "anchor": "opens", "days": "0", "time": "0:75",
        "direction": "before",
    })
    assert r.status_code == 422


async def test_editing_an_item_puts_the_same_sign_on_all_three_columns(client):
    login_as(client, USER_A, "reiji")
    client.post("/presets", data={
        "name": "p", "anchor": "closes", "days": "3", "time": "0:00",
        "direction": "before",
    })
    r = client.post("/presets/1/items/1/edit", data={
        "anchor": "closes", "days": "1", "time": "1:15", "direction": "after",
    })
    assert r.status_code == 303

    async with client.db() as s:
        item = (await s.execute(select(PresetItem))).scalar_one()
    assert (item.offset_days, item.offset_hours, item.offset_minutes) == (1, 1, 15)
