"""The editor's round-to-leg association — `Round.applies_to`.

`applies_to` is a JSON list of `ConcertDay` ids: genuinely a *set*, because a
round can cover several legs of a tour. The editor used to express that set as
one free-text string per round row, matched server-side against each day's
city or label. Matching returned every hit, so a two-leg round could be
*created*; but the edit page pre-filled that text box from `applies_to[0]`
alone, so re-saving the round silently narrowed it to one leg.

These tests pin the replacement: one toggle chip per leg, submitted as the ids
themselves. The encoding is deliberately ONE form field per round row
(`round_legs`, a space-separated id list) rather than one field per selected
id, because the round_* fields are parallel repeatable lists zipped
positionally — a flat repeated field could not say which row an id belonged
to, and a misalignment there would be a quieter version of the very bug this
file exists to prevent.
"""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Concert, ConcertDay, Round, Tag, User
from app.db.service import ensure_user
from app.db.session import get_session
from app.domain.types import RoundKind, TagKind
from app.web import auth
from app.web.app import create_app

EDITOR = 777


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    # Production registers this too; cascades silently do not fire without it.
    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


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


def login(client, discord_id: int = EDITOR, name: str = "editor"):
    async def fake_identity(token):
        return {"id": str(discord_id), "username": name, "global_name": name, "avatar": None}

    client.monkeypatch.setattr(auth, "fetch_identity", fake_identity)
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    client.get(f"/auth/callback?code=x&state={state}")


async def make_editor(db, discord_id: int = EDITOR, name: str = "editor"):
    async with db() as s:
        await ensure_user(s, discord_id, name)
        u = await s.get(User, discord_id)
        u.is_editor = True
        await s.commit()


async def seed(db, *, legs, rounds, event_id="tour"):
    """A concert with `legs` (label, city, cancelled) and `rounds`
    (label, applies_to-index-list) — indices into `legs`, resolved to real
    day ids once the days have been flushed."""
    async with db() as s:
        await ensure_user(s, EDITOR, "editor")
        c = Concert(title="Tour", event_id=event_id, created_by=EDITOR)
        s.add(c)
        await s.flush()
        day_ids = []
        for i, (label, city, cancelled) in enumerate(legs):
            d = ConcertDay(
                concert_id=c.id,
                label=label,
                city=city,
                starts_at_utc=datetime(2099, 8, 1 + i, 9, 0, tzinfo=UTC),
                cancelled=cancelled,
            )
            s.add(d)
            await s.flush()
            day_ids.append(d.id)
        round_ids = []
        for label, leg_indices in rounds:
            r = Round(
                concert_id=c.id,
                label=label,
                kind=RoundKind.LOTTERY_ROUND,
                closes_at_utc=datetime(2099, 6, 25, 14, 59, tzinfo=UTC),
                applies_to=[day_ids[i] for i in leg_indices] or None,
            )
            s.add(r)
            await s.flush()
            round_ids.append(r.id)
        await s.commit()
        return day_ids, round_ids


def resubmit(client, event_id, *, days, rounds, title="Tour", extra=None):
    """POST the edit form back with every field the editor's form carries.

    `days` is a list of (id, label, city, cancelled) with an optional 5th
    element, the row's `day_key`; `rounds` a list of (id, label, legs-string)
    — the last being exactly what the hidden `round_legs` input holds for
    that row.

    A saved leg's key IS its id, which is why it defaults to `id` here: the
    key list only has to carry identity for rows that do not have one yet.
    """
    data = {
            "title": title,
            "event_id": event_id,
            "day_id": [str(d[0]) for d in days],
            "day_key": [str(d[4]) if len(d) > 4 else str(d[0]) for d in days],
            "day_label": [d[1] for d in days],
            "day_label_en": [""] * len(days),
            "day_label_zh": [""] * len(days),
            "day_city": [d[2] for d in days],
            "day_starts_at": [
                f"2099-08-{1 + i:02d}T18:00" for i in range(len(days))
            ],
            "day_venue": [""] * len(days),
            "day_venue_address": [""] * len(days),
            "day_doors_at": [""] * len(days),
            "day_cancelled": ["true" if d[3] else "false" for d in days],
            "round_id": [str(r[0]) for r in rounds],
            "round_label": [r[1] for r in rounds],
            "round_label_en": [""] * len(rounds),
            "round_label_zh": [""] * len(rounds),
            "round_kind": ["lottery_round"] * len(rounds),
            "round_opens_at": [""] * len(rounds),
            "round_closes_at": ["2099-06-25T23:59"] * len(rounds),
            "round_results_at": [""] * len(rounds),
            "round_payment_at": [""] * len(rounds),
            "round_url": [""] * len(rounds),
            "round_notes": [""] * len(rounds),
            "round_legs": [r[2] for r in rounds],
    }
    data.update(extra or {})
    return client.post(f"/concerts/{event_id}/edit", data=data)


async def applies_to(db, round_id):
    async with db() as s:
        return (await s.get(Round, round_id)).applies_to


# ── the regression ───────────────────────────────────────────────────────


async def test_a_two_leg_round_survives_an_edit_round_trip(client, db):
    """The regression the old form could not pass: a round whose applies_to
    covers BOTH legs, opened in the editor and saved without being touched,
    must still cover both. The legs are deliberately named so that no single
    typed string could have matched them both — under the old text matching
    the round could only come back with one id."""
    await make_editor(db)
    login(client)
    (day1, day2), (round_id,) = await seed(
        db,
        legs=[("Day 1", "Kanagawa", False), ("Day 2", "Osaka", False)],
        rounds=[("Both legs lottery", [0, 1])],
    )

    page = client.get("/concerts/tour/edit")
    assert page.status_code == 200

    r = resubmit(
        client,
        "tour",
        days=[(day1, "Day 1", "Kanagawa", False), (day2, "Day 2", "Osaka", False)],
        rounds=[(round_id, "Both legs lottery", f"{day1} {day2}")],
    )
    assert r.status_code == 303
    assert set(await applies_to(db, round_id)) == {day1, day2}


# ── the encoding, under the condition that would expose a misalignment ───


async def test_three_rounds_keep_their_own_leg_selections(client, db):
    """Three rounds, three different selections. A per-row encoding that
    silently shifted ids between rows would be worse than the bug being
    fixed, so this is the test that pins it."""
    await make_editor(db)
    login(client)
    (day1, day2, day3), (r1, r2, r3) = await seed(
        db,
        legs=[
            ("Day 1", "Kanagawa", False),
            ("Day 2", "Osaka", False),
            ("Day 3", "Fukuoka", False),
        ],
        rounds=[
            ("First leg only", [0]),
            ("First and third", [0, 2]),
            ("Second only", [1]),
        ],
    )

    r = resubmit(
        client,
        "tour",
        days=[
            (day1, "Day 1", "Kanagawa", False),
            (day2, "Day 2", "Osaka", False),
            (day3, "Day 3", "Fukuoka", False),
        ],
        rounds=[
            (r1, "First leg only", f"{day1}"),
            (r2, "First and third", f"{day1} {day3}"),
            (r3, "Second only", f"{day2}"),
        ],
    )
    assert r.status_code == 303
    assert set(await applies_to(db, r1)) == {day1}
    assert set(await applies_to(db, r2)) == {day1, day3}
    assert set(await applies_to(db, r3)) == {day2}


async def test_selecting_no_legs_stores_nothing(client, db):
    """An empty selection means "not tied to a specific leg" — stored as
    None (not []), which is what concert_round_rows reads to put the round
    in the all-legs group."""
    await make_editor(db)
    login(client)
    (day1, day2), (round_id,) = await seed(
        db,
        legs=[("Day 1", "Kanagawa", False), ("Day 2", "Osaka", False)],
        rounds=[("Was leg-specific", [0])],
    )

    resubmit(
        client,
        "tour",
        days=[(day1, "Day 1", "Kanagawa", False), (day2, "Day 2", "Osaka", False)],
        rounds=[(round_id, "Was leg-specific", "")],
    )
    assert await applies_to(db, round_id) is None

    # Scoped past "Next for you", which names whichever round wants the reader
    # first and so legitimately mentions this one before its group heading.
    body = client.get("/concerts/tour").text.split("<!-- /standing -->", 1)[-1]
    assert 'leg-heading">All legs<' in body
    assert body.index("All legs") < body.index("Was leg-specific")


async def test_selecting_every_leg_round_trips(client, db):
    await make_editor(db)
    login(client)
    (day1, day2), (round_id,) = await seed(
        db,
        legs=[("Day 1", "Kanagawa", False), ("Day 2", "Osaka", False)],
        rounds=[("Whole tour", [])],
    )

    resubmit(
        client,
        "tour",
        days=[(day1, "Day 1", "Kanagawa", False), (day2, "Day 2", "Osaka", False)],
        rounds=[(round_id, "Whole tour", f"{day1} {day2}")],
    )
    assert set(await applies_to(db, round_id)) == {day1, day2}


# ── the ids the chips cannot show ────────────────────────────────────────


async def test_a_round_keeps_its_cancelled_leg_id_through_a_save(client, db):
    """Chips render for live legs only, but a round may already reference a
    cancelled one (invariant 2: a cancelled ConcertDay is flagged, never
    deleted, precisely because applies_to consumers depend on the row). The
    id rides along in the row's hidden value and must come back intact —
    dropping it would be a second, quieter copy of the bug this task fixes.
    """
    await make_editor(db)
    login(client)
    (day1, day2), (round_id,) = await seed(
        db,
        legs=[("Day 1", "Kanagawa", False), ("Day 2", "Osaka", True)],
        rounds=[("Both legs lottery", [0, 1])],
    )

    resubmit(
        client,
        "tour",
        days=[(day1, "Day 1", "Kanagawa", False), (day2, "Day 2", "Osaka", True)],
        rounds=[(round_id, "Both legs lottery", f"{day1} {day2}")],
    )
    assert set(await applies_to(db, round_id)) == {day1, day2}


async def test_a_deleted_leg_leaves_no_dangling_id(client, db):
    """A leg dropped from the form is really deleted; an id for it must not
    survive in any round's applies_to, however it was submitted."""
    await make_editor(db)
    login(client)
    (day1, day2), (round_id,) = await seed(
        db,
        legs=[("Day 1", "Kanagawa", False), ("Day 2", "Osaka", False)],
        rounds=[("Both legs lottery", [0, 1])],
    )

    resubmit(
        client,
        "tour",
        days=[(day1, "Day 1", "Kanagawa", False)],
        rounds=[(round_id, "Both legs lottery", f"{day1} {day2}")],
    )
    assert await applies_to(db, round_id) == [day1]
    async with db() as s:
        assert (await s.execute(select(ConcertDay))).scalars().all() != []
        assert (await s.get(ConcertDay, day2)) is None


# ── the form itself ──────────────────────────────────────────────────────


async def test_the_edit_page_renders_a_preselected_chip_per_leg(client, db):
    await make_editor(db)
    login(client)
    (day1, day2), (round_id,) = await seed(
        db,
        legs=[("Day 1", "Kanagawa", False), ("Day 2", "Osaka", False)],
        rounds=[("First leg only", [0])],
    )

    body = client.get("/concerts/tour/edit").text
    assert f'data-leg-id="{day1}"' in body
    assert f'data-leg-id="{day2}"' in body
    # the hidden field carries the round's real applies_to, pre-selected
    assert f'name="round_legs" value="{day1}"' in body
    # the free-text leg <select> is gone
    assert 'name="round_leg"' not in body
    assert "syncLegOptions" not in body


async def test_a_concert_with_no_legs_still_edits(client, db):
    """No legs means no chips — the row must still render and save."""
    await make_editor(db)
    login(client)
    _, (round_id,) = await seed(db, legs=[], rounds=[("Only round", [])])

    assert client.get("/concerts/tour/edit").status_code == 200
    r = resubmit(client, "tour", days=[], rounds=[(round_id, "Only round", "")])
    assert r.status_code == 303
    assert await applies_to(db, round_id) is None


# ── day_key: assigning a round to a leg that does not exist yet ──────────
#
# Chips carry ids, and a leg added in the browser has none until it is
# written — which made "add a leg and put a round on it" a two-save
# workflow. `day_key` closes that: every day row submits a stable client-side
# identifier (a saved leg's key IS its id; a new row gets a generated one),
# chips reference keys, and the server resolves keys to real ids after the
# days are flushed.
#
# The danger this introduces is a key sliding between rows, which would
# assign a round to the WRONG leg — silently, which is worse than the
# two-save limitation it replaces. The tests below are sized for that: every
# round picks a different leg, so any shift shows up.


async def _day_id_by_city(db, city):
    async with db() as s:
        res = await s.execute(select(ConcertDay).where(ConcertDay.city == city))
        return res.scalar_one().id


async def test_three_rounds_land_on_three_different_legs_one_of_them_brand_new(
    client, db
):
    """The whole point of day_key, and the shape that would expose a slide.

    Three legs — the third added in this very submit, so it has no id yet —
    and three rounds, each picking exactly one and a different one. A key
    that drifted by a row would still produce three well-formed assignments;
    only checking each round individually catches it.
    """
    await make_editor(db)
    login(client)
    (day1, day2), (r1, r2, r3) = await seed(
        db,
        legs=[("Day 1", "Kanagawa", False), ("Day 2", "Osaka", False)],
        rounds=[("First leg", [0]), ("Second leg", [1]), ("Third leg", [])],
    )

    r = resubmit(
        client,
        "tour",
        days=[
            (day1, "Day 1", "Kanagawa", False),
            (day2, "Day 2", "Osaka", False),
            ("", "Day 3", "Fukuoka", False, "new-2"),  # no id yet
        ],
        rounds=[
            (r1, "First leg", str(day1)),
            (r2, "Second leg", str(day2)),
            (r3, "Third leg", "new-2"),
        ],
    )
    assert r.status_code == 303

    day3 = await _day_id_by_city(db, "Fukuoka")
    assert day3 not in (day1, day2)
    assert await applies_to(db, r1) == [day1]
    assert await applies_to(db, r2) == [day2]
    assert await applies_to(db, r3) == [day3]


async def test_a_new_leg_key_and_a_saved_leg_id_can_share_one_round(client, db):
    """A round spanning an existing leg and one created in the same save —
    the two resolution paths meeting inside a single applies_to."""
    await make_editor(db)
    login(client)
    (day1,), (round_id,) = await seed(
        db,
        legs=[("Day 1", "Kanagawa", False)],
        rounds=[("Both legs", [0])],
    )

    resubmit(
        client,
        "tour",
        days=[
            (day1, "Day 1", "Kanagawa", False),
            ("", "Day 2", "Osaka", False, "new-7"),
        ],
        rounds=[(round_id, "Both legs", f"{day1} new-7")],
    )
    day2 = await _day_id_by_city(db, "Osaka")
    assert set(await applies_to(db, round_id)) == {day1, day2}


async def test_a_key_for_a_row_that_was_removed_resolves_to_nothing(client, db):
    """A leg row deleted client-side after a round was pointed at it leaves
    its key in the round's hidden value. Nothing submitted claims that key,
    so it must resolve to nothing rather than to some other row's leg."""
    await make_editor(db)
    login(client)
    (day1,), (round_id,) = await seed(
        db,
        legs=[("Day 1", "Kanagawa", False)],
        rounds=[("First leg", [0])],
    )

    resubmit(
        client,
        "tour",
        days=[(day1, "Day 1", "Kanagawa", False)],
        rounds=[(round_id, "First leg", f"{day1} new-3")],
    )
    assert await applies_to(db, round_id) == [day1]


async def test_a_blank_leg_row_does_not_consume_the_next_rows_key(client, db):
    """Blank trailing rows are skipped, and the skip must take the row's key
    with it — a key list that kept marching while the day list stopped is
    exactly how one would slide."""
    await make_editor(db)
    login(client)
    (day1,), (round_id,) = await seed(
        db,
        legs=[("Day 1", "Kanagawa", False)],
        rounds=[("Second leg", [])],
    )

    resubmit(
        client,
        "tour",
        days=[
            (day1, "Day 1", "Kanagawa", False),
            ("", "", "", False, "new-blank"),  # entirely blank row: skipped
            ("", "Day 2", "Osaka", False, "new-real"),
        ],
        rounds=[(round_id, "Second leg", "new-real")],
    )
    day2 = await _day_id_by_city(db, "Osaka")
    assert await applies_to(db, round_id) == [day2]


async def test_the_edit_page_gives_every_leg_row_a_key(client, db):
    """A saved leg's key is its own id, so the chips and the day rows agree
    without the two lists having to be read together."""
    await make_editor(db)
    login(client)
    (day1, day2), _ = await seed(
        db,
        legs=[("Day 1", "Kanagawa", False), ("Day 2", "Osaka", False)],
        rounds=[("Only round", [0])],
    )

    body = client.get("/concerts/tour/edit").text
    assert f'name="day_key" value="{day1}"' in body
    assert f'name="day_key" value="{day2}"' in body


async def test_a_duplicate_day_key_gives_the_leg_to_the_first_row(client, db):
    """Two rows claiming one key is the single way the key map could collide.
    First row wins (setdefault), so a later row cannot silently steal a
    reference a round already made."""
    await make_editor(db)
    login(client)
    (day1,), (round_id,) = await seed(
        db,
        legs=[("Day 1", "Kanagawa", False)],
        rounds=[("Keyed round", [])],
    )

    resubmit(
        client,
        "tour",
        days=[
            (day1, "Day 1", "Kanagawa", False),
            ("", "Day 2", "Osaka", False, "dupe"),
            ("", "Day 3", "Fukuoka", False, "dupe"),
        ],
        rounds=[(round_id, "Keyed round", "dupe")],
    )
    assert await applies_to(db, round_id) == [await _day_id_by_city(db, "Osaka")]


# ── round_legs padding: the two halves of a short array ──────────────────


async def test_a_stale_page_omitting_round_legs_leaves_every_round_alone(client, db):
    """The reachable data-loss window this padding rule exists to close.

    An editor whose browser still holds the PRE-deploy edit page submits the
    old `round_leg` free-text field and no `round_legs` at all. FastAPI drops
    the unknown field, so the route sees a wholly-omitted array. Filling it
    with blanks would make one innocent Save wipe `applies_to` on every round
    of the concert — so a whole-array omission must mean "this submitter has
    nothing to say about legs", not "no legs anywhere".
    """
    await make_editor(db)
    login(client)
    (day1, day2), (r1, r2, r3) = await seed(
        db,
        legs=[("Day 1", "Kanagawa", False), ("Day 2", "Osaka", False)],
        rounds=[("First leg", [0]), ("Both legs", [0, 1]), ("All legs", [])],
    )

    r = resubmit(
        client,
        "tour",
        days=[(day1, "Day 1", "Kanagawa", False), (day2, "Day 2", "Osaka", False)],
        rounds=[(r1, "First leg", ""), (r2, "Both legs", ""), (r3, "All legs", "")],
        # An empty list is encoded as no field at all — exactly what the stale
        # page sends. `round_leg` (singular) is what it sends INSTEAD.
        extra={"round_legs": [], "round_leg": ["Kanagawa", "", ""]},
    )
    assert r.status_code == 303
    assert await applies_to(db, r1) == [day1]
    assert set(await applies_to(db, r2)) == {day1, day2}
    assert await applies_to(db, r3) is None


async def test_a_partial_round_legs_array_raises_instead_of_shifting_legs(client, db):
    """The other half. A short-but-nonempty array cannot be end-padded: doing
    so slides every later row's selection by one, silently assigning rounds to
    the wrong legs — the exact failure this whole encoding exists to prevent.
    Loud is better, so the strict zip is allowed to raise, and nothing commits.
    """
    await make_editor(db)
    login(client)
    (day1, day2), (r1, r2, r3) = await seed(
        db,
        legs=[("Day 1", "Kanagawa", False), ("Day 2", "Osaka", False)],
        rounds=[("First leg", [0]), ("Second leg", [1]), ("All legs", [])],
    )

    with pytest.raises(ValueError):
        resubmit(
            client,
            "tour",
            days=[(day1, "Day 1", "Kanagawa", False), (day2, "Day 2", "Osaka", False)],
            rounds=[(r1, "First leg", ""), (r2, "Second leg", ""), (r3, "All legs", "")],
            # Two entries for three round rows.
            extra={"round_legs": [str(day1), str(day2)]},
        )

    # Nothing committed, so the assignments are exactly as seeded — in
    # particular r2 did NOT inherit r1's selection.
    assert await applies_to(db, r1) == [day1]
    assert await applies_to(db, r2) == [day2]
    assert await applies_to(db, r3) is None


# ── the restructured page ────────────────────────────────────────────────


async def test_rounds_come_before_the_folds(client, db):
    """The common edit is "a new round was announced". Adding one must not
    mean scrolling past twenty fields nobody is touching."""
    await make_editor(db)
    login(client)
    await seed(
        db,
        legs=[("Day 1", "Kanagawa", False)],
        rounds=[("Only round", [0])],
    )

    body = client.get("/concerts/tour/edit").text
    assert body.index("Add a round") < body.index("Details and links")
    assert body.index("Add a round") < body.index("Edit history")


async def test_the_folds_render_summaries_of_what_they_hold(client, db):
    """A fold that does not say what is inside it is just a hidden field."""
    await make_editor(db)
    login(client)
    await seed(db, legs=[("Day 1", "Kanagawa", False)], rounds=[("R", [0])])
    # give the concert something for each summary to report
    resubmit(
        client,
        "tour",
        days=[(1, "Day 1", "Kanagawa", False)],
        rounds=[(1, "R", "1")],
        title="Tour renamed",
    )

    body = client.get("/concerts/tour/edit").text
    for summary in ("Details and links", "Tags", "Edit history"):
        assert summary in body
    assert body.count('<details class="fold"') == 3
    # every fold states its contents rather than just naming itself
    assert body.count('class="fold-hint"') == 3
    # the history summary counts the edit the resubmit above just made
    assert "1 change" in body


async def test_the_danger_row_says_what_duplicate_copies(client, db):
    """Invariant 3: duplicate re-attaches the already-pruned tag set with
    expand=False, and copies no rounds or legs. The button has to say so."""
    await make_editor(db)
    login(client)
    await seed(db, legs=[], rounds=[("R", [])])

    body = client.get("/concerts/tour/edit").text
    assert 'class="danger-row"' in body
    assert "/concerts/tour/duplicate" in body
    assert "/concerts/tour/delete" in body
    assert "not rounds or legs" in body


# ── cancelled: a leg toggle, not a buried <select> ───────────────────────


async def test_the_cancelled_toggle_round_trips(client, db):
    await make_editor(db)
    login(client)
    (day1, day2), _ = await seed(
        db,
        legs=[("Day 1", "Kanagawa", False), ("Day 2", "Osaka", False)],
        rounds=[("R", [0])],
    )

    body = client.get("/concerts/tour/edit").text
    # a toggle backed by a hidden field, not a <select> among the day fields
    assert 'name="day_cancelled" value="false"' in body
    assert 'data-cancel-toggle' in body
    assert '<option value="true"' not in body

    days = [(day1, "Day 1", "Kanagawa", False), (day2, "Day 2", "Osaka", True)]
    resubmit(client, "tour", days=days, rounds=[(1, "R", str(day1))])
    async with db() as s:
        assert (await s.get(ConcertDay, day1)).cancelled is False
        assert (await s.get(ConcertDay, day2)).cancelled is True

    # and back again
    days = [(day1, "Day 1", "Kanagawa", False), (day2, "Day 2", "Osaka", False)]
    resubmit(client, "tour", days=days, rounds=[(1, "R", str(day1))])
    async with db() as s:
        assert (await s.get(ConcertDay, day2)).cancelled is False

    body = client.get("/concerts/tour/edit").text
    assert 'aria-pressed="false"' in body


# ── the invariants the restructure must not disturb ──────────────────────


async def test_duplicate_from_the_danger_row_still_copies_tags_unexpanded(client, db):
    """Invariant 3 in the restructured page: the danger row's Duplicate posts
    to the same route, which re-attaches the source's already-pruned tag set
    with expand=False and copies no days or rounds."""
    from app.db.models import Tag
    from app.db.service import attach_tag
    from app.domain.types import TagKind

    await make_editor(db)
    login(client)
    await seed(db, legs=[("Day 1", "Kanagawa", False)], rounds=[("R", [0])])
    async with db() as s:
        group = Tag(name="Aqours", kind=TagKind.GROUP)
        member = Tag(name="伊波杏樹", kind=TagKind.ARTIST)
        pruned = Tag(name="Left the group", kind=TagKind.ARTIST)
        s.add_all([group, member, pruned])
        await s.flush()
        from app.db.models import TagMember

        s.add_all([
            TagMember(group_tag_id=group.id, member_tag_id=member.id),
            TagMember(group_tag_id=group.id, member_tag_id=pruned.id),
        ])
        concert = (await s.execute(select(Concert))).scalar_one()
        await attach_tag(s, concert.id, group, expand=False)
        await attach_tag(s, concert.id, member, expand=False)
        await s.commit()

    r = client.post("/concerts/tour/duplicate")
    assert r.status_code == 303
    async with db() as s:
        clone = (
            await s.execute(select(Concert).where(Concert.event_id != "tour"))
        ).scalar_one()
        await s.refresh(clone, ["tags", "days", "rounds"])
        names = {t.name for t in clone.tags}
        assert "Aqours" in names and "伊波杏樹" in names
        assert "Left the group" not in names  # expand=False, not re-expanded
        assert clone.days == [] and clone.rounds == []


async def test_saving_the_restructured_form_still_records_a_real_diff(client, db):
    """`edit_concert` snapshots BEFORE mutating and records AFTER. Reversed,
    every diff reads as unchanged — and the restructure moved the fields the
    diff is built from into a fold, which is exactly when that would slip."""
    from app.db.models import ConcertAudit

    await make_editor(db)
    login(client)
    (day1,), (round_id,) = await seed(
        db,
        legs=[("Day 1", "Kanagawa", False)],
        rounds=[("R", [0])],
    )

    resubmit(
        client,
        "tour",
        days=[(day1, "Day 1", "Kanagawa", False)],
        rounds=[(round_id, "R", str(day1))],
        title="Tour, renamed",
        extra={"organizer": "Aqours", "notes": "moved to a bigger hall"},
    )

    async with db() as s:
        audits = (await s.execute(select(ConcertAudit))).scalars().all()
    assert len(audits) == 1
    changes = {c["field"]: (c["before"], c["after"]) for c in audits[0].changes}
    assert changes["title"] == ("Tour", "Tour, renamed")
    assert changes["organizer"] == (None, "Aqours")
    assert changes["notes"] == (None, "moved to a bigger hall")


# ── venue_tag_id: the structured venue FK ────────────────────────────────


async def _venue_tag(db, name="Zepp Haneda"):
    async with db() as s:
        t = Tag(name=name, kind=TagKind.VENUE)
        s.add(t)
        await s.commit()
        return t.id


async def test_leg_editor_offers_venue_tags(client, db):
    """The leg's venue is picked from real VENUE tags, not typed free-hand."""
    await make_editor(db)
    login(client)
    await _venue_tag(db)

    resp = client.get("/concerts/new")

    assert resp.status_code == 200
    assert 'name="day_venue_tag_id"' in resp.text
    assert "Zepp Haneda" in resp.text
    # the free-text venue/city inputs are gone from the leg editor
    assert 'name="day_venue"' not in resp.text
    assert 'name="day_city"' not in resp.text


async def test_the_edit_page_preselects_each_legs_venue_tag(client, db):
    await make_editor(db)
    login(client)
    venue_id = await _venue_tag(db)
    other_id = await _venue_tag(db, "Makuhari Messe")
    (day1, day2), _ = await seed(
        db,
        legs=[("Day 1", "Kanagawa", False), ("Day 2", "Osaka", False)],
        rounds=[("R", [0])],
    )
    async with db() as s:
        (await s.get(ConcertDay, day1)).venue_tag_id = venue_id
        await s.commit()

    body = client.get("/concerts/tour/edit").text
    assert f'<option value="{venue_id}" selected>' in body
    # the OTHER leg picked nothing, and neither did the unassigned tag
    assert f'<option value="{other_id}" selected>' not in body


async def test_every_leg_row_emits_exactly_one_venue_tag_field(client, db):
    """`day_venue_tag_id` is padded only when the WHOLE array is omitted; a
    short-but-nonempty array trips the strict zip. So the form must emit one
    value per leg row -- never fewer. Pinned against `day_id`, which already
    has exactly one field per row (including the JS row template; `day_key`
    would over-count, it is also named inside _leg_chips_script.html)."""
    await make_editor(db)
    login(client)
    await _venue_tag(db)
    await seed(
        db,
        legs=[("Day 1", "Kanagawa", False), ("Day 2", "Osaka", False)],
        rounds=[("R", [0])],
    )

    for page in ("/concerts/tour/edit", "/concerts/new"):
        body = client.get(page).text
        assert body.count('name="day_venue_tag_id"') == body.count('name="day_id"'), page


async def test_a_blank_leg_rows_venue_select_defaults_to_no_value(client, db):
    """A blank trailing row must submit an EMPTY venue value.

    The blank-row guard counts the venue pick, so a row carrying only a venue
    and no start time reaches validation and 422s. If the picker ever
    pre-selected a tag on an empty row, every trailing blank row would become
    a 422 and the editor could not save at all. The "no venue" option is
    therefore first, valued "", and nothing else may carry `selected`.
    """
    await make_editor(db)
    login(client)
    venue_id = await _venue_tag(db)
    (day1,), (round_id,) = await seed(
        db,
        legs=[("Day 1", "Kanagawa", False)],
        rounds=[("R", [0])],
    )

    # The JS row template -- the markup a browser clones for a new blank row.
    tpl = client.get("/concerts/tour/edit").text.split('<template id="day-row-template">')[1]
    tpl = tpl.split("</template>")[0]
    picker = tpl.split('name="day_venue_tag_id"')[1].split("</select>")[0]
    assert 'value=""' in picker        # the "no venue" option exists
    assert "selected" not in picker    # and nothing overrides it

    # And end-to-end, both directions. An untouched trailing row -- empty
    # venue included -- is skipped and the save goes through:
    blank_row = [(day1, "Day 1", "Kanagawa", False), ("", "", "", False, "new-blank")]
    r = resubmit(
        client,
        "tour",
        days=blank_row,
        rounds=[(round_id, "R", str(day1))],
        extra={
            "day_venue_tag_id": ["", ""],
            "day_starts_at": ["2099-08-01T18:00", ""],
        },
    )
    assert r.status_code == 303
    async with db() as s:
        assert len((await s.execute(select(ConcertDay))).scalars().all()) == 1

    # ...whereas the SAME row with a venue pre-selected is no longer blank,
    # reaches validation without a start time, and 422s. This is why the
    # picker must never pre-select anything on an empty row.
    r = resubmit(
        client,
        "tour",
        days=blank_row,
        rounds=[(round_id, "R", str(day1))],
        extra={
            "day_venue_tag_id": ["", str(venue_id)],
            "day_starts_at": ["2099-08-01T18:00", ""],
        },
    )
    assert r.status_code == 422


def resubmit_as_the_template_does(client, event_id, *, days, rounds, title="Tour"):
    """POST exactly the field set the CURRENT leg editor emits.

    `resubmit` above still sends `day_city`/`day_venue`/`day_venue_address`,
    which the templates no longer have -- so it cannot see this bug. A real
    browser sends the keys below and nothing else. `days` is
    (id, label, venue_tag_id).
    """
    data = {
        "title": title,
        "event_id": event_id,
        "day_id": [str(d[0]) for d in days],
        "day_key": [str(d[0]) for d in days],
        "day_label": [d[1] for d in days],
        "day_label_en": [""] * len(days),
        "day_label_zh": [""] * len(days),
        "day_venue_tag_id": [str(d[2] or "") for d in days],
        "day_starts_at": [f"2099-08-{1 + i:02d}T18:00" for i in range(len(days))],
        "day_doors_at": [""] * len(days),
        "day_cancelled": ["false"] * len(days),
        "round_id": [str(r[0]) for r in rounds],
        "round_label": [r[1] for r in rounds],
        "round_label_en": [""] * len(rounds),
        "round_label_zh": [""] * len(rounds),
        "round_kind": ["lottery_round"] * len(rounds),
        "round_opens_at": [""] * len(rounds),
        "round_closes_at": ["2099-06-25T23:59"] * len(rounds),
        "round_results_at": [""] * len(rounds),
        "round_payment_at": [""] * len(rounds),
        "round_url": [""] * len(rounds),
        "round_notes": [""] * len(rounds),
        "round_legs": [r[2] for r in rounds],
    }
    return client.post(f"/concerts/{event_id}/edit", data=data)


async def test_an_edit_save_does_not_destroy_a_legs_free_text_venue(client, db):
    """The two-deploy migration's whole safety net.

    `city`/`venue`/`venue_address` outlive this phase so a leg whose venue
    string the backfill could not match to a tag stays recoverable by hand.
    But the inputs are gone from the templates, so those params arrive absent,
    get padded to "", and a plain assignment would write None over the only
    surviving record of that venue -- on the FIRST save of any existing
    concert, silently. Preserve-on-empty is what stops it.
    """
    await make_editor(db)
    login(client)
    (day1,), (round_id,) = await seed(
        db,
        legs=[("Day 1", "Kanagawa", False)],
        rounds=[("R", [0])],
    )
    async with db() as s:
        d = await s.get(ConcertDay, day1)
        d.venue = "Zepp Haneda"
        d.venue_address = "1-4-8 Hanedakuko, Ota-ku, Tokyo"
        await s.commit()

    r = resubmit_as_the_template_does(
        client,
        "tour",
        days=[(day1, "Day 1", None)],
        rounds=[(round_id, "R", str(day1))],
    )
    assert r.status_code == 303

    async with db() as s:
        d = await s.get(ConcertDay, day1)
        assert d.city == "Kanagawa"
        assert d.venue == "Zepp Haneda"
        assert d.venue_address == "1-4-8 Hanedakuko, Ota-ku, Tokyo"


async def test_a_new_leg_with_no_free_text_still_stores_nulls(client, db):
    """Preserve-on-empty must not resurrect anything. A brand-new row starts
    with these columns NULL, so an empty incoming value has to leave them
    NULL -- not invent a "" or carry another row's value in."""
    await make_editor(db)
    login(client)
    venue_id = await _venue_tag(db)
    _, (round_id,) = await seed(db, legs=[], rounds=[("R", [])])

    r = resubmit_as_the_template_does(
        client,
        "tour",
        days=[("", "Day 1", venue_id)],
        rounds=[(round_id, "R", "")],
    )
    assert r.status_code == 303

    async with db() as s:
        d = (await s.execute(select(ConcertDay))).scalar_one()
        assert d.venue_tag_id == venue_id
        assert d.city is None
        assert d.venue is None
        assert d.venue_address is None


async def test_day_venue_tag_fk_sets_null_on_tag_delete(db):
    """A venue tag is shared taxonomy; deleting one must not delete the legs
    that referenced it (mirrors Tag.created_by's SET NULL reasoning)."""
    async with db() as session:
        tag = Tag(name="Zepp Haneda", kind=TagKind.VENUE)
        session.add(tag)
        await session.flush()

        concert = Concert(title="t", event_id="ev1", created_by=None)
        session.add(concert)
        await session.flush()

        day = ConcertDay(
            concert_id=concert.id,
            label="Day 1",
            starts_at_utc=datetime(2026, 8, 1, 9, 0, tzinfo=UTC),
            venue_tag_id=tag.id,
        )
        session.add(day)
        await session.flush()

        await session.delete(tag)
        await session.flush()
        await session.refresh(day)

        assert day.id is not None, "deleting the tag must not delete the leg"
        assert day.venue_tag_id is None


async def test_editor_round_trips_label_variants(client, db):
    """The create form carries all three language variants of a leg label and
    of a round label, and each lands in its own column."""
    await make_editor(db)
    login(client)

    resp = client.post("/concerts", data={
        "title": "T", "event_id": "lv1",
        "day_label": ["2日目"], "day_label_en": ["Day 2"], "day_label_zh": ["第二天"],
        "day_starts_at": ["2026-08-01T18:00"], "day_venue_tag_id": [""],
        "day_doors_at": [""], "day_cancelled": ["false"],
        "round_label": ["1次先行"], "round_label_en": ["1st advance"],
        "round_label_zh": ["第一轮先行"],
        "round_kind": ["lottery_round"], "round_closes_at": ["2026-07-01T18:00"],
        "round_opens_at": [""], "round_results_at": [""], "round_payment_at": [""],
        "round_url": [""], "round_notes": [""], "round_legs": [""],
        "round_qualifiers": [""],
    })
    assert resp.status_code in (200, 303)

    async with db() as session:
        concert = (await session.execute(
            select(Concert).where(Concert.event_id == "lv1")
        )).scalar_one()
        await session.refresh(concert, ["days", "rounds"])
        day, round_ = concert.days[0], concert.rounds[0]

        assert (day.label, day.label_en, day.label_zh) == ("2日目", "Day 2", "第二天")
        assert (round_.label, round_.label_en, round_.label_zh) == (
            "1次先行", "1st advance", "第一轮先行",
        )
