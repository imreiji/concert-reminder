"""The editor's upgrade-round qualifier set -- the "Qualifies" chip row.

An UPGRADE round is a nested second campaign only holders of a qualifying
round's ticket may enter (see domain/upgrades.py). The editor expresses which
rounds qualify as a row of toggle chips, one per OTHER already-saved round on
the concert. On save the selected round ids become that upgrade round's
`round_qualifiers` association rows.

The encoding mirrors branch 2's `round_legs` exactly: ONE hidden field per
round row (`round_qualifiers`, a space-separated id list), positionally aligned
with the parallel round_* lists and parsed by `parse_round_qualifiers`. A round
created in the SAME submit has no id and cannot be a qualifier until the concert
is saved once -- a deliberate simplification stated in the UI copy.
"""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Concert, ConcertDay, Round, RoundQualifier, User
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


async def seed(db, *, rounds, links=(), event_id="tour"):
    """A concert with one leg and `rounds` (label, RoundKind). `links` is a
    list of (upgrade-index, qualifier-index) into `rounds`, seeded as real
    RoundQualifier rows once the rounds have ids. Returns the round ids."""
    async with db() as s:
        await ensure_user(s, EDITOR, "editor")
        c = Concert(title="Tour", event_id=event_id, created_by=EDITOR)
        s.add(c)
        await s.flush()
        day = ConcertDay(
            concert_id=c.id, label="Day 1", city="Kanagawa",
            starts_at_utc=datetime(2099, 8, 1, 9, 0, tzinfo=UTC),
        )
        s.add(day)
        await s.flush()
        round_ids = []
        for label, kind in rounds:
            r = Round(
                concert_id=c.id, label=label, kind=kind,
                closes_at_utc=datetime(2099, 6, 25, 14, 59, tzinfo=UTC),
                applies_to=[day.id],
            )
            s.add(r)
            await s.flush()
            round_ids.append(r.id)
        for up_i, q_i in links:
            s.add(RoundQualifier(
                upgrade_round_id=round_ids[up_i], qualifying_round_id=round_ids[q_i]
            ))
        await s.commit()
        return round_ids


def resubmit(client, event_id, *, rounds, title="Tour", extra=None):
    """POST the edit form back. `rounds` is a list of
    (id, label, kind-value, qualifiers-string) -- the last being exactly what
    the hidden `round_qualifiers` input holds for that row. The concert keeps
    its single saved leg (its id is passed straight back)."""
    leg = client.portal_leg_id
    data = {
        "title": title,
        "event_id": event_id,
        "day_id": [str(leg)],
        "day_key": [str(leg)],
        "day_label": ["Day 1"],
        "day_label_en": [""],
        "day_label_zh": [""],
        "day_starts_at": ["2099-08-01T18:00"],
        "day_doors_at": [""],
        "day_cancelled": ["false"],
        "round_id": [str(r[0]) for r in rounds],
        "round_label": [r[1] for r in rounds],
        "round_label_en": [""] * len(rounds),
        "round_label_zh": [""] * len(rounds),
        "round_kind": [r[2] for r in rounds],
        "round_opens_at": [""] * len(rounds),
        "round_closes_at": ["2099-06-25T23:59"] * len(rounds),
        "round_results_at": [""] * len(rounds),
        "round_payment_at": [""] * len(rounds),
        "round_url": [""] * len(rounds),
        "round_notes": [""] * len(rounds),
        "round_legs": [str(leg)] * len(rounds),
        "round_qualifiers": [r[3] for r in rounds],
    }
    data.update(extra or {})
    return client.post(f"/concerts/{event_id}/edit", data=data)


async def _leg_id(db):
    async with db() as s:
        return (await s.execute(select(ConcertDay))).scalar_one().id


async def qualifiers(db, round_id):
    async with db() as s:
        rows = (await s.execute(
            select(RoundQualifier.qualifying_round_id).where(
                RoundQualifier.upgrade_round_id == round_id
            )
        )).scalars()
        return set(rows)


# ── the round-trip ───────────────────────────────────────────────────────


async def test_a_two_qualifier_upgrade_round_survives_an_edit_round_trip(client, db):
    """Select two qualifying rounds, save, reopen -- both round_qualifiers
    rows exist and both chips come back preselected."""
    await make_editor(db)
    login(client)
    r1, r2, up = await seed(db, rounds=[
        ("最速先行", RoundKind.LOTTERY_ROUND),
        ("先行抽選 R1", RoundKind.LOTTERY_ROUND),
        ("Seat upgrade", RoundKind.UPGRADE),
    ])
    client.portal_leg_id = await _leg_id(db)

    assert client.get("/concerts/tour/edit").status_code == 200
    r = resubmit(client, "tour", rounds=[
        (r1, "最速先行", "lottery_round", ""),
        (r2, "先行抽選 R1", "lottery_round", ""),
        (up, "Seat upgrade", "upgrade", f"{r1} {r2}"),
    ])
    assert r.status_code == 303
    assert await qualifiers(db, up) == {r1, r2}

    # Reopen: both chips preselected (the hidden field carries both ids).
    body = client.get("/concerts/tour/edit").text
    assert f'data-qualifier-id="{r1}"' in body
    assert f'data-qualifier-id="{r2}"' in body
    vals = _qualifier_values(body)
    assert any(str(r1) in v.split() and str(r2) in v.split() for v in vals)


async def test_deselecting_a_qualifier_removes_its_row_on_save(client, db):
    await make_editor(db)
    login(client)
    r1, r2, up = await seed(
        db,
        rounds=[
            ("最速先行", RoundKind.LOTTERY_ROUND),
            ("先行抽選 R1", RoundKind.LOTTERY_ROUND),
            ("Seat upgrade", RoundKind.UPGRADE),
        ],
        links=[(2, 0), (2, 1)],
    )
    client.portal_leg_id = await _leg_id(db)
    assert await qualifiers(db, up) == {r1, r2}

    # Drop r2 from the hidden value -> its row is removed.
    resubmit(client, "tour", rounds=[
        (r1, "最速先行", "lottery_round", ""),
        (r2, "先行抽選 R1", "lottery_round", ""),
        (up, "Seat upgrade", "upgrade", f"{r1}"),
    ])
    assert await qualifiers(db, up) == {r1}


async def test_a_qualifier_whose_round_was_deleted_in_the_same_submit_does_not_persist(
    client, db
):
    """A round dropped from the form is really deleted; its id must not
    survive as a dangling qualifier, however it was submitted."""
    await make_editor(db)
    login(client)
    r1, r2, up = await seed(
        db,
        rounds=[
            ("最速先行", RoundKind.LOTTERY_ROUND),
            ("先行抽選 R1", RoundKind.LOTTERY_ROUND),
            ("Seat upgrade", RoundKind.UPGRADE),
        ],
        links=[(2, 0), (2, 1)],
    )
    client.portal_leg_id = await _leg_id(db)

    # r2 is gone from the rounds list, but up's hidden value still names it.
    resubmit(client, "tour", rounds=[
        (r1, "最速先行", "lottery_round", ""),
        (up, "Seat upgrade", "upgrade", f"{r1} {r2}"),
    ])
    assert await qualifiers(db, up) == {r1}
    async with db() as s:
        assert (await s.get(Round, r2)) is None


async def test_an_empty_selection_stores_no_qualifier_rows(client, db):
    """No qualifiers means "any secured ticket on this concert qualifies"
    (the empty-set convention from domain/upgrades.py) -- zero rows stored."""
    await make_editor(db)
    login(client)
    r1, up = await seed(
        db,
        rounds=[
            ("最速先行", RoundKind.LOTTERY_ROUND),
            ("Seat upgrade", RoundKind.UPGRADE),
        ],
        links=[(1, 0)],
    )
    client.portal_leg_id = await _leg_id(db)
    assert await qualifiers(db, up) == {r1}

    resubmit(client, "tour", rounds=[
        (r1, "最速先行", "lottery_round", ""),
        (up, "Seat upgrade", "upgrade", ""),
    ])
    assert await qualifiers(db, up) == set()


async def test_qualifiers_on_a_non_upgrade_kind_are_discarded(client, db):
    """A non-upgrade round has no qualifier concept; a stale value submitted
    for one is dropped rather than stored."""
    await make_editor(db)
    login(client)
    r1, r2 = await seed(db, rounds=[
        ("最速先行", RoundKind.LOTTERY_ROUND),
        ("先行抽選 R1", RoundKind.LOTTERY_ROUND),
    ])
    client.portal_leg_id = await _leg_id(db)

    resubmit(client, "tour", rounds=[
        (r1, "最速先行", "lottery_round", f"{r2}"),
        (r2, "先行抽選 R1", "lottery_round", ""),
    ])
    assert await qualifiers(db, r1) == set()


async def test_self_reference_is_dropped(client, db):
    """A round cannot qualify itself; its own id in the value is ignored."""
    await make_editor(db)
    login(client)
    r1, up = await seed(db, rounds=[
        ("最速先行", RoundKind.LOTTERY_ROUND),
        ("Seat upgrade", RoundKind.UPGRADE),
    ])
    client.portal_leg_id = await _leg_id(db)

    resubmit(client, "tour", rounds=[
        (r1, "最速先行", "lottery_round", ""),
        (up, "Seat upgrade", "upgrade", f"{up} {r1}"),
    ])
    assert await qualifiers(db, up) == {r1}


async def test_a_cross_concert_round_id_is_dropped(client, db):
    """An id for a round on ANOTHER concert is not a valid qualifier here."""
    await make_editor(db)
    login(client)
    (other_r,) = await seed(
        db, rounds=[("Other", RoundKind.LOTTERY_ROUND)], event_id="other"
    )
    r1, up = await seed(db, rounds=[
        ("最速先行", RoundKind.LOTTERY_ROUND),
        ("Seat upgrade", RoundKind.UPGRADE),
    ])
    client.portal_leg_id = await _leg_id_for(db, "tour")

    resubmit(client, "tour", rounds=[
        (r1, "最速先行", "lottery_round", ""),
        (up, "Seat upgrade", "upgrade", f"{r1} {other_r}"),
    ])
    assert await qualifiers(db, up) == {r1}


# ── the form itself ──────────────────────────────────────────────────────


async def test_a_non_upgrade_round_shows_no_qualifier_chip_row(client, db):
    """The Qualifies box appears only for an UPGRADE round; a concert with
    only a lottery round renders its box hidden."""
    await make_editor(db)
    login(client)
    await seed(db, rounds=[("最速先行", RoundKind.LOTTERY_ROUND)])

    body = client.get("/concerts/tour/edit").text
    # Every rendered box (the saved row and the add-a-round template) is hidden.
    assert "data-qualifier-box hidden" in body
    assert "data-qualifier-box>" not in body


async def test_only_already_saved_rounds_appear_as_chip_options(client, db):
    """The upgrade round's chips list every OTHER saved round -- not itself,
    and (implicitly) not a round yet to be created."""
    await make_editor(db)
    login(client)
    r1, r2, up = await seed(db, rounds=[
        ("最速先行", RoundKind.LOTTERY_ROUND),
        ("先行抽選 R1", RoundKind.LOTTERY_ROUND),
        ("Seat upgrade", RoundKind.UPGRADE),
    ])

    body = client.get("/concerts/tour/edit").text
    # The upgrade round's box is the only visible one; isolate its chip list.
    assert "data-qualifier-box>" in body
    start = body.index("data-qualifier-box>")
    chips_start = body.index("data-qualifier-chips", start)
    up_box = body[chips_start:body.index("</div>", chips_start)]
    # It offers the two other saved rounds and never itself.
    assert f'data-qualifier-id="{r1}"' in up_box
    assert f'data-qualifier-id="{r2}"' in up_box
    assert f'data-qualifier-id="{up}"' not in up_box
    # The save-first limitation is stated in the copy.
    assert "after you save" in body


# ── helpers ──────────────────────────────────────────────────────────────


def _qualifier_values(body):
    import re

    return re.findall(r'name="round_qualifiers" value="([^"]*)"', body)


async def _leg_id_for(db, event_id):
    async with db() as s:
        c = (await s.execute(select(Concert).where(Concert.event_id == event_id))).scalar_one()
        return (await s.execute(
            select(ConcertDay).where(ConcertDay.concert_id == c.id)
        )).scalar_one().id
