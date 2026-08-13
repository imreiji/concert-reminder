"""`/following`'s per-subscription config dialog, and the route it posts to.

The dialog holds the three things a subscription has -- which preset it links,
whether it DMs about new events, and whether it exists at all -- so these tests
are about a WRITE surface, not a read one. Two failure modes shape them:

1. `_safe_next` is a CLOSED allowlist. A path missing from it does not error;
   it silently redirects the reader to /preferences instead of back to the page
   they were on. That exact bug shipped with /tags. So the redirect assertions
   below pin `location` EXACTLY, never just the 303 -- a status-only assertion
   passes with `/following` deleted from `_ALLOWED_NEXT`.
2. A 404 from an ownership check and a 404 from a route that does not exist are
   byte-identical. So the ownership test asserts the OWNER's identical request
   succeeds in the same breath; without that half it passes with the whole
   route removed.

Fixtures mirror tests/test_following_page.py, which this page's read half uses.
"""

import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import (
    Concert,
    ConcertDay,
    ConcertTag,
    ReminderPreset,
    ReminderQueue,
    ReminderRule,
    Round,
    Tag,
    TagSubscription,
    User,
)
from app.db.service import sync_rule
from app.db.session import get_session
from app.domain.types import Anchor, RoundKind, TagKind
from app.web import auth
from app.web.app import create_app

USER_A = 7171
USER_B = 7272


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


def dialog_for(html: str, sub_id: int) -> str:
    """The ONE <dialog> element for `sub_id`.

    Every assertion below is scoped through this rather than run against the
    page: with N followed tags the page holds N dialogs, all built from the same
    macro, so "the page contains an <option selected>" is true whenever ANY
    subscription selected anything -- including the one the test is not looking
    at. Nesting is impossible (a <dialog> inside a <dialog> is not something
    this macro can emit), so the first `</dialog>` after the id closes it.
    """
    start = html.index(f'id="follow-dialog-{sub_id}"')
    start = html.rindex("<dialog", 0, start)
    end = html.index("</dialog>", start)
    return html[start : end + len("</dialog>")]


def selected_option(dialog_html: str) -> str:
    """The `value` of the dialog's selected preset option ("0" is the explicit
    "none"), or "" if the select carries no selection at all -- which is itself
    a bug worth telling apart from "none", since a select with nothing selected
    silently submits its first option."""
    hits = re.findall(r'<option value="(\d+)" selected>', dialog_html)
    assert len(hits) <= 1, f"more than one option selected: {hits}"
    return hits[0] if hits else ""


def chip_for(html: str, tag_id: int) -> str:
    """The ONE chip element for `tag_id`, span-balanced -- the same helper
    tests/test_following_page.py uses, and for the same reason: every dialog on
    the page lists every preset by name, so an unscoped "the name is gone"
    assertion is answered by the dialogs and can never fail."""
    anchor = html.index(f'data-tag-id="{tag_id}"')
    start = html.rindex("<span", 0, anchor)
    depth = 0
    for m in re.finditer(r"<span\b|</span>", html[start:]):
        depth += 1 if m.group().startswith("<span") else -1
        if depth == 0:
            return html[start : start + m.end()]
    raise AssertionError(f"chip for tag {tag_id} never closes")


async def seed(db) -> SimpleNamespace:
    """Two viewers, three of USER_A's follows, two of A's presets, one of B's.

    `linked` follows on the non-default preset with notify ON, `bare` follows on
    no preset with notify OFF, and `stranger_sub` belongs to USER_B. Every pair
    of values differs from every other, so no field can stand in for another:
    a route that writes notify into preset_id, or reads B's row for A, has
    nowhere to hide.
    """
    async with db() as s:
        s.add_all([User(discord_id=USER_A, username="reiji"),
                   User(discord_id=USER_B, username="someone")])
        await s.flush()
        love = Tag(name="Love Live!", kind=TagKind.FRANCHISE, created_by=USER_A)
        s.add(love)
        await s.flush()
        linked_tag = Tag(name="Hasunosora", kind=TagKind.GROUP,
                         parent_id=love.id, created_by=USER_A)
        bare_tag = Tag(name="Nippon Budokan", kind=TagKind.VENUE, created_by=USER_A)
        stranger_tag = Tag(name="Aqours", kind=TagKind.GROUP, created_by=USER_A)
        s.add_all([linked_tag, bare_tag, stranger_tag])
        await s.flush()
        standard = ReminderPreset(user_id=USER_A, name="Standard cover", is_default=True)
        heavy = ReminderPreset(user_id=USER_A, name="Everything early")
        theirs = ReminderPreset(user_id=USER_B, name="Not yours")
        s.add_all([standard, heavy, theirs])
        await s.flush()
        linked = TagSubscription(user_id=USER_A, tag_id=linked_tag.id,
                                 preset_id=heavy.id, notify=True)
        bare = TagSubscription(user_id=USER_A, tag_id=bare_tag.id,
                               preset_id=None, notify=False)
        stranger_sub = TagSubscription(user_id=USER_B, tag_id=stranger_tag.id,
                                       preset_id=theirs.id, notify=True)
        s.add_all([linked, bare, stranger_sub])
        # TWO concerts on `linked_tag`, one past and one future, so its pair of
        # counts is ASYMMETRIC -- 2 events, 1 upcoming. With a symmetric (1, 1)
        # the dialog's two numbers can be swapped for each other in the template
        # with nothing failing (found in review, 2026-08-12, by swapping them).
        upcoming = Concert(title="Big show", event_id="big-show", created_by=USER_A)
        past = Concert(title="Old show", event_id="old-show", created_by=USER_A)
        s.add_all([upcoming, past])
        await s.flush()
        round_ = Round(concert_id=upcoming.id, kind=RoundKind.LOTTERY_ROUND, label="Round 1",
                       closes_at_utc=datetime.now(UTC) + timedelta(days=30))
        s.add(round_)
        s.add_all([
            ConcertTag(concert_id=upcoming.id, tag_id=linked_tag.id),
            ConcertTag(concert_id=past.id, tag_id=linked_tag.id),
            ConcertDay(concert_id=upcoming.id, label="Day 1",
                       starts_at_utc=datetime.now(UTC) + timedelta(days=60)),
            ConcertDay(concert_id=past.id, label="Day 1",
                       starts_at_utc=datetime.now(UTC) - timedelta(days=60)),
        ])
        await s.commit()
        return SimpleNamespace(
            linked_tag=linked_tag.id, bare_tag=bare_tag.id,
            linked=linked.id, bare=bare.id, stranger_sub=stranger_sub.id,
            standard=standard.id, heavy=heavy.id, theirs=theirs.id,
            concert=upcoming.id, round=round_.id,
        )


async def sub_row(db, sub_id: int) -> TagSubscription:
    async with db() as s:
        return await s.get(TagSubscription, sub_id)


# ── The dialog ───────────────────────────────────────────────────────────


async def test_every_followed_tag_gets_its_own_dialog(client):
    """One dialog per subscription, keyed by SUBSCRIPTION id -- which is what
    the chip's data-sub-id resolves to and what the settings route takes.

    Mutation: keying the dialog (or the chip) by `t.id`, the tag id, which is
    the other integer in scope and the one the chip already carried. The seed's
    subscription ids and tag ids are minted from different sequences, so a tag
    id here would not resolve; asserted BOTH ways round so a template that
    emitted both attributes with the same value could not pass.
    """
    ids = await seed(client.db)
    login_as(client, USER_A, "reiji")
    html = client.get("/following").text
    for sub_id in (ids.linked, ids.bare):
        assert f'id="follow-dialog-{sub_id}"' in html
    assert f'data-sub-id="{ids.linked}"' in html
    # The stranger's subscription belongs to USER_B and must not be rendered at
    # all -- not even as an empty shell.
    assert f'id="follow-dialog-{ids.stranger_sub}"' not in html


async def test_the_dialog_offers_my_presets_with_mine_selected(client):
    """The select lists the viewer's presets and pre-selects THIS
    subscription's. Two subscriptions on different presets are asserted in the
    same render, so a template hard-coding the default preset (or the first
    option, or the last one it looped over) fails on one of them.

    Mutation: `f.preset_id == p.id` -> `default_preset.id == p.id` selects
    "Standard cover" on the linked row; -> always-false leaves "— none —"
    selected on both.
    """
    ids = await seed(client.db)
    login_as(client, USER_A, "reiji")
    html = client.get("/following").text

    linked = dialog_for(html, ids.linked)
    assert "Everything early" in linked and "Standard cover" in linked
    assert selected_option(linked) == str(ids.heavy)

    # No preset: the explicit "none" option carries the selection, so a Save
    # that touches nothing writes back the NULL that was already there.
    assert selected_option(dialog_for(html, ids.bare)) == "0"


async def test_the_dialog_never_offers_another_user_s_preset(client):
    """`presets` is the viewer's own list. Mutation: `select(ReminderPreset)`
    with the user filter dropped puts USER_B's preset in USER_A's select --
    which the route would then refuse, so the only symptom is a menu entry that
    404s on Save.
    """
    ids = await seed(client.db)
    login_as(client, USER_A, "reiji")
    dlg = dialog_for(client.get("/following").text, ids.linked)
    assert "Not yours" not in dlg
    assert f'value="{ids.theirs}"' not in dlg


async def test_the_notify_box_reflects_the_stored_flag(client):
    """Both directions in one render: `linked` has notify ON and `bare` OFF, so
    a checkbox that is always checked (or never) fails on one of them.

    Mutation: `{% if not f.muted %}checked{% endif %}` -> `{% if f.muted %}`,
    which inverts the control -- opening the dialog and pressing Save would
    then flip the flag the reader never touched.
    """
    ids = await seed(client.db)
    login_as(client, USER_A, "reiji")
    html = client.get("/following").text
    assert 'name="notify" value="true" checked' in dialog_for(html, ids.linked)
    assert "checked" not in dialog_for(html, ids.bare)


async def test_the_dialog_states_this_tag_s_event_counts(client):
    """The context line, so the decision needs no second page. `linked_tag`
    carries TWO concerts of which ONE is still upcoming; `bare_tag` carries
    none.

    The asymmetry is the test. Mutations: passing the whole map's totals (or
    the head's `followed_count`) instead of `tag_counts.get(t.id)` gives every
    dialog the same pair -- and SWAPPING `counts[0]` with `counts[1]` in the
    template, which a symmetric (1, 1) seed could not see at all (found in
    review, 2026-08-12: the swap left 30 tests green).
    """
    ids = await seed(client.db)
    login_as(client, USER_A, "reiji")
    html = client.get("/following").text
    linked = dialog_for(html, ids.linked)
    assert "<b>2</b> events" in linked
    assert "1 upcoming" in linked
    # And not the other way round, which is the whole point of seeding 2 vs 1.
    assert "<b>1</b> event<" not in linked
    assert "2 upcoming" not in linked
    bare = dialog_for(html, ids.bare)
    assert "<b>0</b> events" in bare
    assert "0 upcoming" in bare


async def test_a_character_s_dialog_names_her_seiyuu(client):
    """The pair sentence, and it points the RIGHT WAY on each half: following
    the performer reaches strictly more than following the character
    (invariant 3 attaches the seiyuu alongside the character), so the character
    is told to follow the performer, and the performer is told she is already
    covered.

    Mutation: one sentence for both halves, or `voiced_by` alone -- either
    leaves the performer's dialog with no sentence at all, which is the half
    the naive `Tag.voiced_by_tag_id` read misses (it has no reverse).
    """
    async with client.db() as s:
        s.add(User(discord_id=USER_A, username="reiji"))
        await s.flush()
        seiyuu = Tag(name="Sachika Misawa", kind=TagKind.ARTIST, created_by=USER_A)
        s.add(seiyuu)
        await s.flush()
        chara = Tag(name="Kozue Otomune", kind=TagKind.CHARACTER,
                    voiced_by_tag_id=seiyuu.id, created_by=USER_A)
        s.add(chara)
        await s.flush()
        chara_sub = TagSubscription(user_id=USER_A, tag_id=chara.id, notify=True)
        seiyuu_sub = TagSubscription(user_id=USER_A, tag_id=seiyuu.id, notify=True)
        s.add_all([chara_sub, seiyuu_sub])
        await s.commit()
        chara_sub_id, seiyuu_sub_id = chara_sub.id, seiyuu_sub.id

    login_as(client, USER_A, "reiji")
    html = client.get("/following").text

    on_character = dialog_for(html, chara_sub_id)
    assert "Voiced by Sachika Misawa" in on_character
    assert "Performs" not in on_character

    on_seiyuu = dialog_for(html, seiyuu_sub_id)
    assert "Performs Kozue Otomune" in on_seiyuu
    assert "Voiced by" not in on_seiyuu


async def test_a_tag_in_no_pair_gets_no_sentence(client):
    """The sentence is a fact about the tag, not furniture. Mutation: rendering
    it unconditionally puts "Voiced by" (with an empty name) on a venue.
    """
    ids = await seed(client.db)
    login_as(client, USER_A, "reiji")
    dlg = dialog_for(client.get("/following").text, ids.bare)
    assert "Voiced by" not in dlg
    assert "Performs" not in dlg


async def test_the_chip_opens_its_dialog_through_dataset(client):
    """Invariant 7: nothing user-controlled goes into an inline on* handler --
    the browser HTML-decodes the attribute before parsing it as JS, so Jinja's
    escaping does not protect it. The subscription id crosses through
    `data-sub-id`, read via `dataset`.

    Mutation: `onclick="document.getElementById('follow-dialog-{{ f.sub_id }}')
    .showModal()"` on the chip -- which works perfectly and is exactly the
    pattern this codebase forbids. There is no repo-wide inline-on* sweep
    (test_xss_escaping.py covers only the picker's `| tojson`), so this test is
    the only guard on this page.

    It scopes to the WHOLE chip and the WHOLE dialog, and matches any `on*`
    attribute rather than the string "onclick". The first version sliced only
    the attributes BEFORE `data-tag-id` and never looked at the dialog at all:
    review put an `onclick` one attribute later and all 18 tests passed. Both
    halves are needed -- the dialog's own buttons carry the same id and are
    exactly where an interpolated handler would be reached for next.
    """
    ids = await seed(client.db)
    login_as(client, USER_A, "reiji")
    html = client.get("/following").text
    chip = chip_for(html, ids.linked_tag)
    assert re.search(r"\son[a-z]+=", chip) is None, chip
    assert f'data-sub-id="{ids.linked}"' in chip
    dlg = dialog_for(html, ids.linked)
    assert re.search(r"\son[a-z]+=", dlg) is None, dlg
    # The close controls really are there -- so "no on* attribute" is not
    # passing because the buttons are missing.
    assert dlg.count(f'data-close="follow-dialog-{ids.linked}"') == 2


async def test_the_chip_is_reachable_and_activatable_by_keyboard(client):
    """A `<span>` is not focusable and has no role, so the page's keydown
    handler -- which listens for Enter and Space on `[data-sub-id]` -- can
    never fire on one. `role="button"` and `tabindex="0"` are what put the chip
    in the tab order and tell a screen reader it does something; the handler is
    what makes the two true rather than a lie.

    Mutation: dropping both attributes from `follow_chip`. Measured
    (2026-08-12): every other test over this page stayed green, 30 of them
    across this file and test_following_page.py -- the page renders
    identically, the mouse still works, and a keyboard reader gets a chip that
    is either unreachable (no tabindex) or reachable and announced as nothing
    (no role).

    Asserted on the ONE chip rather than on the page, this file's habit
    throughout: both attributes must be on the element the handler matches, not
    merely somewhere in the document.
    """
    ids = await seed(client.db)
    login_as(client, USER_A, "reiji")
    chip = chip_for(client.get("/following").text, ids.linked_tag)
    assert 'role="button"' in chip and 'tabindex="0"' in chip, chip


# ── POST /subscriptions/{id}/settings ────────────────────────────────────


async def test_saving_writes_both_the_preset_and_the_notify_flag(client):
    """One submit, both fields, and both CHANGED from what the row held -- a
    route that writes only one of them, or writes the request's value into the
    wrong column, leaves the other at its seeded value.

    Mutation: dropping `sub.preset_id = preset_id or None` leaves `heavy`
    linked; dropping `sub.notify = notify` leaves it True.
    """
    ids = await seed(client.db)
    login_as(client, USER_A, "reiji")
    r = client.post(f"/subscriptions/{ids.linked}/settings",
                    data={"preset_id": ids.standard, "next": "/following"})
    assert r.status_code == 303
    row = await sub_row(client.db, ids.linked)
    assert row.preset_id == ids.standard
    # `notify` was not sent, and a checkbox that is not sent is off.
    assert row.notify is False


async def test_preset_zero_clears_the_link(client):
    """"— none —" posts 0, and 0 must reach the DB as NULL rather than as a
    foreign key to preset 0. Mutation: `sub.preset_id = preset_id` stores 0 and
    the row then names a preset that does not exist (SQLite will take it; the
    chip then renders no name and no deviation).
    """
    ids = await seed(client.db)
    login_as(client, USER_A, "reiji")
    client.post(f"/subscriptions/{ids.linked}/settings",
                data={"preset_id": 0, "notify": "true", "next": "/following"})
    row = await sub_row(client.db, ids.linked)
    assert row.preset_id is None
    assert row.notify is True


async def test_a_preset_the_viewer_does_not_own_is_refused(client):
    """`owned_preset` 404s, and -- the part that matters -- the row is left
    exactly as it was. Mutation: dropping the `owned_preset` call links USER_B's
    preset to USER_A's subscription, which no later render would question.

    The owned preset is posted straight after, for the reason the module
    docstring gives: a 404 from an ownership check and a 404 from a route that
    does not exist are byte-identical, so the refusal alone proves only that
    SOMETHING said no.
    """
    ids = await seed(client.db)
    login_as(client, USER_A, "reiji")
    r = client.post(f"/subscriptions/{ids.linked}/settings",
                    data={"preset_id": ids.theirs, "next": "/following"})
    assert r.status_code == 404
    row = await sub_row(client.db, ids.linked)
    assert row.preset_id == ids.heavy

    mine = client.post(f"/subscriptions/{ids.linked}/settings",
                       data={"preset_id": ids.standard, "next": "/following"})
    assert mine.status_code == 303
    assert (await sub_row(client.db, ids.linked)).preset_id == ids.standard


async def test_another_user_s_subscription_404s_and_is_untouched(client):
    """Invariant 5: ownership 404s rather than admitting the row exists.

    The second half is the point. A 404 from `_owned_subscription` and a 404
    from a route that was never registered are byte-identical, so a test that
    only asserts 404 on the foreign id passes with the entire feature deleted --
    which happened on this branch already. USER_A's own identical request must
    succeed in the same test, and USER_B's row must still hold its own values.

    Mutation: `raise HTTPException(403)` (wrong status, the invariant), or
    `session.get(TagSubscription, sub_id)` with the user_id check dropped
    (USER_A rewrites USER_B's subscription and gets a 303).
    """
    ids = await seed(client.db)
    login_as(client, USER_A, "reiji")

    foreign = client.post(f"/subscriptions/{ids.stranger_sub}/settings",
                          data={"preset_id": 0, "next": "/following"})
    assert foreign.status_code == 404
    theirs = await sub_row(client.db, ids.stranger_sub)
    assert (theirs.preset_id, theirs.notify) == (ids.theirs, True)

    mine = client.post(f"/subscriptions/{ids.linked}/settings",
                       data={"preset_id": 0, "next": "/following"})
    assert mine.status_code == 303


async def test_saving_returns_to_following_not_to_preferences(client):
    """THE TRAP. `_safe_next` is a closed allowlist and its default is
    "/preferences", so a `next` that is not IN `_ALLOWED_NEXT` does not error --
    it quietly lands the reader on another page. That shipped with /tags and
    lived until it was measured.

    Mutation: delete "/following" from `_ALLOWED_NEXT`
    (routes/preferences.py). The status stays 303 and only this equality
    fails -- which is why it is an equality and not `r.status_code == 303`.
    """
    ids = await seed(client.db)
    login_as(client, USER_A, "reiji")
    r = client.post(f"/subscriptions/{ids.linked}/settings",
                    data={"preset_id": 0, "next": "/following"})
    assert r.headers["location"] == "/following"


async def test_unfollowing_from_the_dialog_deletes_the_row_and_comes_back(client):
    """The footer's destructive action posts the existing id-keyed delete with
    next=/following -- so it exercises the SAME allowlist entry, and the same
    mutation breaks it. Asserted together because a delete that lands on
    /preferences and a delete that does not happen look equally like "it
    worked" from the chip's absence on a page you were bounced off.

    Mutation: dropping the hidden `next` from the dialog's unfollow form -- the
    route's own default is "/preferences" and the row still goes.
    """
    ids = await seed(client.db)
    login_as(client, USER_A, "reiji")
    r = client.post(f"/subscriptions/{ids.linked}/delete", data={"next": "/following"})
    assert r.headers["location"] == "/following"
    assert await sub_row(client.db, ids.linked) is None
    # The other subscription is untouched -- a delete keyed by user rather than
    # by row would take both.
    assert await sub_row(client.db, ids.bare) is not None


async def test_the_unfollow_form_in_the_dialog_carries_that_next(client):
    """The markup half of the test above: it is the DIALOG that must send
    next=/following, and a route test cannot see a template that forgot to.

    Scoped to the unfollow form inside one dialog, not to the page -- the
    settings form beside it sends the same hidden value, so a page-wide
    assertion passes with this form's input deleted.
    """
    ids = await seed(client.db)
    login_as(client, USER_A, "reiji")
    dlg = dialog_for(client.get("/following").text, ids.linked)
    start = dlg.index(f'action="/subscriptions/{ids.linked}/delete"')
    form = dlg[start : dlg.index("</form>", start)]
    assert 'name="next" value="/following"' in form


async def test_signed_out_cannot_write_a_subscription(client):
    """`require_user`, not `current_user`: being signed out is not an error, so
    the write 303s to `/` rather than 403ing -- but it must not perform the
    write. Mutation: no dependency at all, and an anonymous POST edits whichever
    row id it names.
    """
    ids = await seed(client.db)
    r = client.post(f"/subscriptions/{ids.linked}/settings",
                    data={"preset_id": ids.standard, "next": "/following"})
    assert r.status_code == 303
    assert r.headers["location"].split("?")[0] == "/"
    row = await sub_row(client.db, ids.linked)
    assert row.preset_id == ids.heavy


async def test_a_save_shows_up_on_the_chip(client):
    """End to end, through the read surface the write exists to change: the
    linked subscription deviates (non-default preset) before the save and
    conforms after it, so its chip loses the preset name it was carrying.

    Mutation: committing nothing (dropping `await session.commit()`) -- every
    assertion above that re-reads the row through a fresh session would catch
    it, but this is the one that says the READER sees it.
    """
    ids = await seed(client.db)
    login_as(client, USER_A, "reiji")
    before = client.get("/following").text
    assert "Everything early" in chip_for(before, ids.linked_tag)

    client.post(f"/subscriptions/{ids.linked}/settings",
                data={"preset_id": ids.standard, "notify": "true", "next": "/following"})

    after = client.get("/following").text
    chip = chip_for(after, ids.linked_tag)
    assert "Everything early" not in chip
    # Conforming now means NO marker at all, not the default preset's name.
    assert "Standard cover" not in chip


async def test_the_settings_route_leaves_the_reminder_queue_alone(client):
    """No resync, deliberately: `notify` is only the new-event DM notice and
    `preset_id` is what future matching events get -- neither is a rule already
    planned (invariant 2). This pins that the route does not quietly grow one,
    which would make a preset change rewrite reminders already in the outbox.

    It asserts on `reminder_queue`, and it asserts on a queue that is actually
    POPULATED -- both learned in review, 2026-08-12. The first version counted
    `ReminderRule` rows, but `reinstate_user_rules` documents that it never
    deletes or recreates a rule, so the very mutation this test names was
    invisible to it by construction: a body that deleted every one of this
    user's queue rows left all 18 tests green. It was worse than that -- the
    rule was inserted raw, without `sync_rule`, so the fixture held ZERO queue
    rows and a correctly-aimed assertion would still have been vacuous.
    """
    ids = await seed(client.db)
    async with client.db() as s:
        rule = ReminderRule(user_id=USER_A, concert_id=ids.concert,
                            anchor=Anchor.CLOSES, offset_days=-3, offset_hours=0)
        s.add(rule)
        await s.flush()
        await sync_rule(s, rule)
        await s.commit()

    async def queue_snapshot():
        async with client.db() as s:
            return sorted(
                (q.id, q.rule_id, q.round_id, q.fire_at_utc, q.sent_at_utc)
                for q in (await s.execute(select(ReminderQueue))).scalars()
            )

    before = await queue_snapshot()
    assert before, "fixture planned no reminders -- the assertion below is vacuous"

    login_as(client, USER_A, "reiji")
    r = client.post(f"/subscriptions/{ids.linked}/settings",
                    data={"preset_id": ids.standard, "next": "/following"})
    assert r.status_code == 303
    assert await queue_snapshot() == before
