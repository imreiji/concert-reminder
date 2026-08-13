"""`POST /presets/apply-to-following` -- fill the standing default into the
follows that carry no preset, and leave the tuned ones alone.

The property this file exists to defend is the one whose failure looks exactly
like success: the route must fill NULLs and NOTHING else. A blanket `UPDATE`
raises nothing, renders a cheerful count, and silently retimes reminders on
every tag the reader had configured by hand, with no undo and no audit row.
So `test_a_follow_with_its_own_preset_is_left_exactly_as_it_was` is the load-
bearing test here, and the seed below is built backwards from it.

Three fixture decisions, each closing a way a test could pass while the feature
is wrong:

* USER_A owns TWO presets -- `standard` (the default) and `tight` (their own
  tuning). With one preset, "the default" and "the only preset" are the same
  id and a route that ignored `is_default` entirely would pass.
* The seed makes `filled` (2) and `kept` (1) DIFFERENT numbers, and adds a
  fourth follow already sitting on the default so that "kept" cannot be read
  as "every non-blank row" (that would be 2). Equal numbers would let the two
  halves of the report stand in for each other.
* USER_B owns follows in BOTH shapes -- one blank, one on a preset of their
  own -- so dropping `TagSubscription.user_id ==` from either statement is
  visible: from the UPDATE it fills a stranger's row, from the COUNT it
  inflates the number the presser is shown.
"""

import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import (
    Concert,
    PresetItem,
    ReminderPreset,
    ReminderRule,
    Tag,
    TagSubscription,
    User,
)
from app.db.service import attach_tag, handle_newly_tagged
from app.db.session import get_session
from app.domain.types import Anchor, TagKind
from app.web import auth
from app.web.app import create_app

USER_A = 7171  # the presser
USER_B = 7272  # a bystander with rows of both shapes
USER_C = 7373  # follows things, owns no preset at all


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
    """Three users, six follows, five presets. See the module docstring."""
    async with db() as s:
        s.add_all([
            User(discord_id=USER_A, username="reiji"),
            User(discord_id=USER_B, username="bystander"),
            User(discord_id=USER_C, username="presetless"),
        ])
        await s.flush()
        blank_one = Tag(name="Kaho Hinoshita", kind=TagKind.ARTIST, created_by=USER_A)
        blank_two = Tag(name="Sayaka Murano", kind=TagKind.ARTIST, created_by=USER_A)
        tuned = Tag(name="Kozue Otomune", kind=TagKind.ARTIST, created_by=USER_A)
        conforming = Tag(name="Rurino Osawa", kind=TagKind.ARTIST, created_by=USER_A)
        b_blank = Tag(name="Nippon Budokan", kind=TagKind.VENUE, created_by=USER_B)
        b_tuned = Tag(name="Tokyo Dome", kind=TagKind.VENUE, created_by=USER_B)
        c_blank = Tag(name="Saitama Super Arena", kind=TagKind.VENUE, created_by=USER_C)
        s.add_all([blank_one, blank_two, tuned, conforming, b_blank, b_tuned, c_blank])
        await s.flush()
        standard = ReminderPreset(user_id=USER_A, name="Standard cover", is_default=True)
        tight_preset = ReminderPreset(user_id=USER_A, name="Everything early")
        b_default = ReminderPreset(user_id=USER_B, name="B standard", is_default=True)
        b_tight = ReminderPreset(user_id=USER_B, name="B early")
        s.add_all([standard, tight_preset, b_default, b_tight])
        await s.flush()
        # Distinguishable offsets: which preset actually fired on a new concert
        # is otherwise unanswerable, and "a rule was created" is true either way.
        s.add_all([
            PresetItem(preset_id=standard.id, anchor=Anchor.CLOSES, offset_days=-3),
            PresetItem(preset_id=tight_preset.id, anchor=Anchor.CLOSES, offset_days=-1),
        ])
        # `notify` differs between the two blank rows on purpose: it is the only
        # other column on the row the bulk UPDATE could reach, and with both
        # rows on the same value a stray `notify=...` in `.values()` would be
        # invisible in one direction. See test_the_fill_touches_only_the_preset.
        subs = {
            "blank_one": TagSubscription(
                user_id=USER_A, tag_id=blank_one.id, preset_id=None, notify=True
            ),
            "blank_two": TagSubscription(
                user_id=USER_A, tag_id=blank_two.id, preset_id=None, notify=False
            ),
            "tuned": TagSubscription(
                user_id=USER_A, tag_id=tuned.id, preset_id=tight_preset.id
            ),
            "conforming": TagSubscription(
                user_id=USER_A, tag_id=conforming.id, preset_id=standard.id
            ),
            "b_blank": TagSubscription(user_id=USER_B, tag_id=b_blank.id, preset_id=None),
            "b_tuned": TagSubscription(user_id=USER_B, tag_id=b_tuned.id, preset_id=b_tight.id),
            "c_blank": TagSubscription(user_id=USER_C, tag_id=c_blank.id, preset_id=None),
        }
        s.add_all(list(subs.values()))
        await s.commit()
        return SimpleNamespace(
            standard=standard.id, tight=tight_preset.id,
            b_default=b_default.id, b_tight=b_tight.id,
            sub={name: sub.id for name, sub in subs.items()},
            tag={"blank_one": blank_one.id, "tuned": tuned.id},
        )


async def preset_of(db, sub_id: int) -> int | None:
    async with db() as s:
        return await s.scalar(
            select(TagSubscription.preset_id).where(TagSubscription.id == sub_id)
        )


async def notify_of(db, sub_id: int) -> bool:
    async with db() as s:
        return await s.scalar(
            select(TagSubscription.notify).where(TagSubscription.id == sub_id)
        )


# ── The write ────────────────────────────────────────────────────────────


async def test_a_preset_less_follow_is_filled_with_the_default(client):
    """The point of the action. BOTH blank follows, so a fill that stopped at
    the first row is caught too.

    Mutation: dropping the UPDATE (or pointing `.values()` at anything but
    `get_default_preset`'s id -- `tight` is the other preset this user owns and
    would be indistinguishable if the seed had only one).
    """
    ids = await seed(client.db)
    login_as(client, USER_A, "reiji")
    r = client.post("/presets/apply-to-following")
    assert r.status_code == 303
    assert await preset_of(client.db, ids.sub["blank_one"]) == ids.standard
    assert await preset_of(client.db, ids.sub["blank_two"]) == ids.standard


async def test_a_follow_with_its_own_preset_is_left_exactly_as_it_was(client):
    """THE test of this file. `tuned` carries `tight`, deliberately set through
    /following's dialog, and must still carry it afterwards.

    Mutation: drop `TagSubscription.preset_id.is_(None)` from the UPDATE's
    WHERE -- the blanket overwrite. Nothing raises, the report still renders,
    and this assertion is the only thing between that edit and a user's
    per-tag tuning being destroyed with no undo.
    """
    ids = await seed(client.db)
    login_as(client, USER_A, "reiji")
    client.post("/presets/apply-to-following")
    assert await preset_of(client.db, ids.sub["tuned"]) == ids.tight


async def test_another_users_follows_are_untouched(client):
    """USER_A presses; USER_B and USER_C must be exactly where they were.

    Mutation: drop `TagSubscription.user_id == user.id` from the UPDATE. A
    bulk statement without it rewrites the whole table, and both blank rows
    below would come back holding USER_A's preset -- which is not even a
    preset those users own. The `b_tuned` half additionally pins that a
    widened statement cannot overwrite a stranger's tuning either.
    """
    ids = await seed(client.db)
    login_as(client, USER_A, "reiji")
    client.post("/presets/apply-to-following")
    assert await preset_of(client.db, ids.sub["b_blank"]) is None
    assert await preset_of(client.db, ids.sub["c_blank"]) is None
    assert await preset_of(client.db, ids.sub["b_tuned"]) == ids.b_tight


async def test_the_fill_touches_only_the_preset(client):
    """The UPDATE's `.values()` reaches one column and must keep reaching one.

    Mutation: add `notify=True` (or `False`) to `.values()`. Nothing raises,
    every other test here stays green, and the notify flag of every preset-less
    follow is silently rewritten -- which is a DM setting, so the user finds out
    from Discord. The two blank rows disagree on `notify` in the seed, so both
    directions of that mutation are caught.
    """
    ids = await seed(client.db)
    login_as(client, USER_A, "reiji")
    client.post("/presets/apply-to-following")
    assert await notify_of(client.db, ids.sub["blank_one"]) is True
    assert await notify_of(client.db, ids.sub["blank_two"]) is False


async def test_a_second_press_fills_nothing(client):
    """Idempotence. The first press leaves every blank row on the default, so
    the second has nothing to fill and must say so -- 0 filled, the same 1 kept,
    and NO auto-apply warning, because nothing was switched back on.

    Mutations: a fill that re-writes rows it already wrote would report a
    non-zero count here; a warning shown unconditionally rather than gated on
    `filled` would claim a switch was flipped when none was.
    """
    ids = await seed(client.db)
    login_as(client, USER_A, "reiji")
    client.post("/presets/apply-to-following")
    client.get("/preferences")  # pop the first report
    client.post("/presets/apply-to-following")
    html = client.get("/preferences").text
    assert "Applied your default preset to 0 followed tags." in html
    assert "1 tag kept the preset you set for it." in html
    assert "Auto-apply is back on for those tags" not in html
    assert await preset_of(client.db, ids.sub["tuned"]) == ids.tight


# ── The report ───────────────────────────────────────────────────────────


async def test_the_report_names_both_counts(client):
    """Both numbers, and they are different (2 filled, 1 kept) so neither can
    stand in for the other.

    Mutations: reporting only the filled count (the owner asked for both --
    without the second number the action is indistinguishable from one that
    overwrote everything); counting "kept" as every non-blank row, which would
    say 2 because `conforming` already sits on the default and was neither
    filled nor overruled; dropping `user_id` from the count query, which would
    say 2 by pulling in USER_B's tuned follow.
    """
    await seed(client.db)
    login_as(client, USER_A, "reiji")
    client.post("/presets/apply-to-following")
    html = client.get("/preferences").text
    assert "Applied your default preset to 2 followed tags." in html
    assert "1 tag kept the preset you set for it." in html


async def test_the_report_admits_it_re_arms_auto_apply(client):
    """`preset_id IS NULL` means BOTH "never configured" and "I switched
    auto-apply off for this tag" -- `toggle_subscription_autoapply` and
    /following's dialog "none" option both write NULL. The fill cannot tell
    them apart and turns the second back on, so the owner's condition for
    filling them at all (2026-08-13) is that the report says so.

    Mutation: delete that sentence from the banner. Everything else about the
    action still reads as correct, and a user who had deliberately switched a
    tag off is never told it came back on.
    """
    await seed(client.db)
    login_as(client, USER_A, "reiji")
    client.post("/presets/apply-to-following")
    html = client.get("/preferences").text
    assert "Auto-apply is back on for those tags" in html
    assert "which the app stores the same way as never having set a preset" in html


async def test_the_report_is_shown_once(client):
    """A one-shot flash, like the API token mint beside it: the report is about
    ONE press, so a refresh or a second tab must not repeat it.

    Mutation: `request.session.get` instead of `.pop` in GET /preferences.
    """
    await seed(client.db)
    login_as(client, USER_A, "reiji")
    client.post("/presets/apply-to-following")
    assert "Applied your default preset to" in client.get("/preferences").text
    assert "Applied your default preset to" not in client.get("/preferences").text


async def test_the_press_lands_back_on_preferences(client):
    """Pinned as a location, not just a 303: /preferences is the only page that
    pops the flash, so landing anywhere else swallows the report entirely and
    the action reads as silent.

    Mutation: any other redirect target, including one carrying a fragment or
    a query the flash-popping GET would still serve.
    """
    await seed(client.db)
    login_as(client, USER_A, "reiji")
    r = client.post("/presets/apply-to-following")
    assert r.status_code == 303
    assert r.headers["location"] == "/preferences"


# ── No default preset ────────────────────────────────────────────────────


async def test_no_default_preset_is_a_reported_no_op(client):
    """USER_C follows a tag and owns no preset at all. That is a normal state
    (nobody has to make a preset), so it must be a no-op with an explanation,
    not a crash and not a write.

    Mutations: dropping the `default is None` branch -- `default.id` on None is
    an AttributeError 500; or falling through to the UPDATE with `preset_id=
    None`, which writes NULL over NULL and then reports a filled count for
    work that did not happen.
    """
    ids = await seed(client.db)
    login_as(client, USER_C, "presetless")
    r = client.post("/presets/apply-to-following")
    assert r.status_code == 303
    assert r.headers["location"] == "/preferences"
    assert await preset_of(client.db, ids.sub["c_blank"]) is None
    html = client.get("/preferences").text
    assert "you have no default preset yet" in html
    assert "Applied your default preset to" not in html


# ── What the fill does to a FUTURE concert ───────────────────────────────


async def test_a_tuned_tag_outranks_the_filled_default_on_a_new_concert(client):
    """The fill must not make a hand-tuned subscription stop applying.

    `handle_newly_tagged` picks ONE preset when several of a user's followed
    tags land on the same concert -- the common case, since invariant 3
    attaches a group and its members together. The rule was earliest-created
    wins, which was arbitrary but harmless while a preset only got onto a row
    by being picked. This fill writes the default into the blank rows, and the
    blanks are the OLDEST ones, so earliest-wins handed the concert to the
    blanket default and the tuned row stopped mattering -- while sitting there
    byte-identical, which is why nothing surfaced it. Owner ruling,
    2026-08-13: the tuned tag wins.

    Mutation: revert `handle_newly_tagged` to plain first-with-a-preset. The
    rules come back at -3 (the default's offset) instead of -1.

    The id ordering is asserted rather than assumed: with the tuned row FIRST
    this test would pass under the old rule too, for the wrong reason.
    """
    ids = await seed(client.db)
    assert ids.sub["tuned"] > ids.sub["blank_one"], "seed no longer exercises the ordering"
    login_as(client, USER_A, "reiji")
    client.post("/presets/apply-to-following")

    async with client.db() as s:
        concert = Concert(title="6th anniversary", event_id="6th", created_by=USER_A)
        s.add(concert)
        await s.flush()
        added = []
        for tag_id in (ids.tag["blank_one"], ids.tag["tuned"]):
            added += await attach_tag(s, concert.id, await s.get(Tag, tag_id))
        assert await handle_newly_tagged(s, concert, added) == 1
        await s.commit()

    async with client.db() as s:
        rules = list((await s.execute(
            select(ReminderRule).where(ReminderRule.user_id == USER_A)
        )).scalars())
    assert [r.offset_days for r in rules] == [-1]


# ── The button ───────────────────────────────────────────────────────────


async def test_preferences_offers_the_button(client):
    """The route needs a way in: a POST form carrying a submit control.

    Scoped INSIDE the form rather than page-wide, because the page is full of
    preset forms and the words "default preset" already appear on it -- a bare
    label assertion would pass with this form deleted. And the action alone is
    not enough either: deleting the `<button>` and leaving the `<form>` would
    keep the action in the markup while making the feature unreachable.

    Mutations: remove the form (the action match fails); remove the button
    inside it (the button match fails); change the method to GET.
    """
    await seed(client.db)
    login_as(client, USER_A, "reiji")
    html = client.get("/preferences").text
    form = re.search(
        r'<form[^>]*action="/presets/apply-to-following"[^>]*>(.*?)</form>', html, re.S
    )
    assert form is not None, "no form posts to /presets/apply-to-following"
    assert 'method="post"' in form.group(0)
    assert "<button" in form.group(1)
    assert "Apply my default preset to all followed tags" in form.group(1)
