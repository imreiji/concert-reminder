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

from app.db.models import Base, Concert, ConcertDay, Round, User
from app.db.service import ensure_user
from app.db.session import get_session
from app.domain.types import RoundKind
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


def resubmit(client, event_id, *, days, rounds):
    """POST the edit form back with every field the editor's form carries.

    `days` is a list of (id, label, city, cancelled); `rounds` a list of
    (id, label, legs-string) — the last being exactly what the hidden
    `round_legs` input holds for that row.
    """
    return client.post(
        f"/concerts/{event_id}/edit",
        data={
            "title": "Tour",
            "event_id": event_id,
            "day_id": [str(d[0]) for d in days],
            "day_label": [d[1] for d in days],
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
            "round_kind": ["lottery_round"] * len(rounds),
            "round_opens_at": [""] * len(rounds),
            "round_closes_at": ["2099-06-25T23:59"] * len(rounds),
            "round_results_at": [""] * len(rounds),
            "round_payment_at": [""] * len(rounds),
            "round_url": [""] * len(rounds),
            "round_notes": [""] * len(rounds),
            "round_legs": [r[2] for r in rounds],
        },
    )


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
