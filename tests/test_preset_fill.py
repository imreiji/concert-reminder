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

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import ReminderPreset, Tag, TagSubscription, User
from app.db.session import get_session
from app.domain.types import TagKind
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
        subs = {
            "blank_one": TagSubscription(user_id=USER_A, tag_id=blank_one.id, preset_id=None),
            "blank_two": TagSubscription(user_id=USER_A, tag_id=blank_two.id, preset_id=None),
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
        )


async def preset_of(db, sub_id: int) -> int | None:
    async with db() as s:
        return await s.scalar(
            select(TagSubscription.preset_id).where(TagSubscription.id == sub_id)
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


# ── The button ───────────────────────────────────────────────────────────


async def test_preferences_offers_the_button(client):
    """The route needs a way in. Scoped to the form's action rather than its
    label: the page is full of preset forms and the words "default preset"
    already appear on it, so a label-substring assertion would survive the
    button being deleted.

    Mutation: remove the form from preferences.html.
    """
    await seed(client.db)
    login_as(client, USER_A, "reiji")
    html = client.get("/preferences").text
    assert 'action="/presets/apply-to-following"' in html
