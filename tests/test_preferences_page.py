"""Preferences rebuilt on the demo's vocabulary (Task 8).

The page is a left-rail layout (`.plyt`/`.prail`) whose sections carry the
demo's component classes: `.subrow`/`.swb` Following toggles, `.presetcard`/
`.ruleline` Reminders, a two-select Time block, Delivery status pills, and a
`.danger`-framed Account. These tests pin the *markup surface* renders (the
"every page a logged-in GET render test" rule); the toggle behaviour lives in
test_preferences_following.py, and the shared token CSS in test_theme_and_tokens.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db.models import (
    Concert,
    ConcertDay,
    ConcertTag,
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
    section has a subrow with a real concert count."""
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

    Mutation: drop the summary pill, or point the link somewhere else --
    either leaves the section without its stated replacement for the
    deleted per-tag rows.
    """
    await seed(client.db)
    login_as(client, USER_A, "reiji")
    r = client.get("/preferences")
    section = _p_follow_section(r.text)
    assert 'class="pill p-ok"' in section
    assert "tags followed" in section
    assert 'href="/following"' in section


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
    from app.db.models import ReminderPreset
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
    assert 'name="days"' in r.text and 'name="anchor"' in r.text
    assert r.text.index('name="days"') < r.text.index('name="anchor"')
    assert "each" in r.text  # the EN pattern's between-slots text


async def test_reminder_sentence_slot_order_ja(client):
    """Under ja the pattern reorders the slots -- the anchor select leads and
    the day/hour selects follow -- and the translated pattern text renders."""
    from app import i18n

    i18n.reset_catalog_cache()
    try:
        await seed(client.db)
        login_as(client, USER_A, "reiji")
        client.cookies.set("lang", "ja")
        r = client.get("/preferences")
        # ja: 各{anchor}の{days}日{hours}時間{direction}に通知。 -> anchor before days.
        assert r.text.index('name="anchor"') < r.text.index('name="days"')
        # Translated pattern fragments appear (proves it went through slots).
        assert "に通知" in r.text
        assert "日" in r.text
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
