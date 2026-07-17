"""Web CRUD tests: authorization, JST parsing, and the edit->re-sync contract.

Test DB isolation: get_session is dependency-overridden with an in-memory
async SQLite, so these tests never touch app.db. Login is simulated by
monkeypatching the auth module's Discord calls (same trick as test_auth).
"""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base, Concert, ReminderQueue, Round
from app.db.session import get_session
from app.domain.timezones import jst_to_utc
from app.web import auth
from app.web.app import create_app

EDITOR_ID, VIEWER_ID = 42, 777


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture()
def client(db, monkeypatch):
    monkeypatch.setattr(settings, "editor_whitelist", str(EDITOR_ID))
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


# ── Authorization boundaries ─────────────────────────────────────────────


def test_anonymous_cannot_view_concert_pages(client):
    assert client.post("/concerts", data={"title": "X"}).status_code == 401
    assert client.get("/concerts/1").status_code == 401


def test_viewer_cannot_create_concert(client):
    login_as(client, VIEWER_ID, "viewer")
    r = client.post("/concerts", data={"title": "Nope"})
    assert r.status_code == 403


def test_editor_creates_concert_and_it_lists(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/concerts", data={"title": "Hasunosora 5th", "franchise": "Hasunosora"})
    assert r.status_code == 303
    r = client.get("/")
    assert "Hasunosora 5th" in r.text


# ── JST datetime contract ────────────────────────────────────────────────


@pytest.mark.anyio
async def anyio_noop():  # keeps pytest-asyncio quiet about the async helper below
    pass


def test_round_datetime_is_parsed_as_jst(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "C"})
    r = client.post(
        "/concerts/1/rounds",
        data={"label": "最速先行", "kind": "lottery_round", "closes_at": "2026-08-01T19:00"},
    )
    assert r.status_code == 200

    import asyncio

    async def check():
        async with client.db() as s:
            round_ = (await s.execute(select(Round))).scalar_one()
            assert round_.closes_at_utc == jst_to_utc(datetime(2026, 8, 1, 19, 0))
            assert round_.closes_at_utc == datetime(2026, 8, 1, 10, 0, tzinfo=UTC)  # JST-9

    asyncio.get_event_loop().run_until_complete(check())


def test_round_needs_at_least_one_bound(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "C"})
    r = client.post("/concerts/1/rounds", data={"label": "empty", "kind": "other"})
    assert r.status_code == 422


# ── The core contract: edits re-sync the queue ───────────────────────────


def test_editing_round_over_http_reschedules_queue(client):
    """User story: staff extends a lottery; every affected reminder moves."""
    import asyncio

    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "C"})
    client.post(
        "/concerts/1/rounds",
        data={"label": "R1", "kind": "lottery_round", "closes_at": "2099-06-25T23:59"},
    )
    client.post("/concerts/1/rules", data={"anchor": "closes", "days_before": 3})

    async def fire_at():
        async with client.db() as s:
            return (await s.execute(select(ReminderQueue))).scalar_one().fire_at_utc

    loop = asyncio.get_event_loop()
    before = loop.run_until_complete(fire_at())
    assert before == jst_to_utc(datetime(2099, 6, 22, 23, 59))

    client.post(
        "/rounds/1/edit",
        data={"label": "R1", "kind": "lottery_round", "closes_at": "2099-06-28T23:59"},
    )
    after = loop.run_until_complete(fire_at())
    assert after == jst_to_utc(datetime(2099, 6, 25, 23, 59))  # moved with the deadline


def test_deleting_rule_removes_queue_rows(client):
    import asyncio

    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "C"})
    client.post(
        "/concerts/1/rounds",
        data={"label": "R1", "kind": "lottery_round", "closes_at": "2099-06-25T23:59"},
    )
    client.post("/concerts/1/rules", data={"anchor": "closes", "days_before": 3})
    client.post("/rules/1/delete")

    async def count():
        async with client.db() as s:
            return len((await s.execute(select(ReminderQueue))).scalars().all())

    assert asyncio.get_event_loop().run_until_complete(count()) == 0


def test_cannot_delete_someone_elses_rule(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "C"})
    client.post(
        "/concerts/1/rounds",
        data={"label": "R1", "kind": "lottery_round", "closes_at": "2099-06-25T23:59"},
    )
    client.post("/concerts/1/rules", data={"anchor": "closes", "days_before": 3})

    login_as(client, VIEWER_ID, "viewer")  # switch identity in the same client
    r = client.post("/rules/1/delete")
    assert r.status_code == 404  # not yours -> as if it doesn't exist


def test_timezone_setting_validates(client):
    login_as(client, EDITOR_ID, "reiji")
    assert client.post("/me/timezone", data={"timezone": "Asia/Tokyo"}).status_code == 303
    assert client.post("/me/timezone", data={"timezone": "Mars/Olympus"}).status_code == 422


def test_delete_concert_cascades_everything(client):
    import asyncio

    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "C"})
    client.post(
        "/concerts/1/rounds",
        data={"label": "R1", "kind": "lottery_round", "closes_at": "2099-06-25T23:59"},
    )
    client.post("/concerts/1/rules", data={"anchor": "closes", "days_before": 3})
    client.post("/concerts/1/delete")

    async def counts():
        async with client.db() as s:
            c = len((await s.execute(select(Concert))).scalars().all())
            r = len((await s.execute(select(Round))).scalars().all())
            q = len((await s.execute(select(ReminderQueue))).scalars().all())
            return c, r, q

    assert asyncio.get_event_loop().run_until_complete(counts()) == (0, 0, 0)


def test_concert_detail_page_renders_for_logged_in_users(client):
    """Regression: the detail page must render with full context (tags fragment
    included) — this exact page 500'd in production because no test loaded it."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "Render Me"})
    client.post(
        "/concerts/1/rounds",
        data={"label": "R1", "kind": "lottery_round", "closes_at": "2099-06-25T23:59"},
    )
    r = client.get("/concerts/1")
    assert r.status_code == 200
    assert "Render Me" in r.text
    assert "Franchises" in r.text  # tags fragment rendered

    login_as(client, VIEWER_ID, "viewer")
    r = client.get("/concerts/1")
    assert r.status_code == 200  # viewers render too (read-only chips)


# ── Concert kind ─────────────────────────────────────────────────────────


async def test_create_concert_with_kind(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "Fest", "kind": "festival"})
    async with client.db() as s:
        concert = (await s.execute(select(Concert))).scalar_one()
    assert concert.kind.value == "festival"


async def test_create_concert_without_kind_leaves_it_unset(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "No kind"})
    async with client.db() as s:
        concert = (await s.execute(select(Concert))).scalar_one()
    assert concert.kind is None


async def test_edit_concert_kind_can_be_set_then_cleared(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "C"})
    client.post("/concerts/1/edit", data={"title": "C", "kind": "tour"})
    async with client.db() as s:
        concert = await s.get(Concert, 1)
        assert concert.kind.value == "tour"

    client.post("/concerts/1/edit", data={"title": "C", "kind": ""})
    async with client.db() as s:
        concert = await s.get(Concert, 1)
        assert concert.kind is None


# ── Round applies_to / leg grouping ──────────────────────────────────────


async def test_round_applies_to_is_stored(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "C"})
    client.post("/concerts/1/days", data={"label": "Day 1", "starts_at": "2099-08-01T18:00"})
    client.post("/concerts/1/days", data={"label": "Day 2", "starts_at": "2099-08-02T18:00"})
    client.post(
        "/concerts/1/rounds",
        data={
            "label": "Day 1 lottery", "kind": "lottery_round",
            "closes_at": "2099-06-25T23:59", "applies_to": ["1"],
        },
    )
    async with client.db() as s:
        round_ = (await s.execute(select(Round))).scalar_one()
    assert round_.applies_to == [1]


async def test_detail_page_groups_rounds_by_leg(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "Two Legs"})
    client.post("/concerts/1/days", data={"label": "Day 1", "starts_at": "2099-08-01T18:00"})
    client.post("/concerts/1/days", data={"label": "Day 2", "starts_at": "2099-08-02T18:00"})
    client.post(
        "/concerts/1/rounds",
        data={"label": "Day 1 round", "kind": "lottery_round",
              "closes_at": "2099-06-25T23:59", "applies_to": ["1"]},
    )
    client.post(
        "/concerts/1/rounds",
        data={"label": "Day 2 round", "kind": "lottery_round",
              "closes_at": "2099-06-26T23:59", "applies_to": ["2"]},
    )
    client.post(
        "/concerts/1/rounds",
        data={"label": "General round", "kind": "general_sale", "closes_at": "2099-06-27T23:59"},
    )
    r = client.get("/concerts/1")
    assert r.status_code == 200
    # Every round's edit form has a day-picker checkbox for every day, so
    # bare "Day 1"/"Day 2" appear many times -- anchor on the actual leg
    # heading markup instead of the label text.
    day1_pos = r.text.index('leg-heading">Day 1<')
    round1_pos = r.text.index("Day 1 round")
    day2_pos = r.text.index('leg-heading">Day 2<')
    round2_pos = r.text.index("Day 2 round")
    general_heading_pos = r.text.index('leg-heading">General<')
    general_round_pos = r.text.index("General round")
    assert day1_pos < round1_pos < day2_pos < round2_pos < general_heading_pos < general_round_pos


async def test_round_with_no_day_association_shown_as_general_only(client):
    """A round with no applies_to shouldn't appear under any day heading."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "C"})
    client.post("/concerts/1/days", data={"label": "Day 1", "starts_at": "2099-08-01T18:00"})
    client.post(
        "/concerts/1/rounds",
        data={"label": "Untied round", "kind": "other", "closes_at": "2099-06-25T23:59"},
    )
    r = client.get("/concerts/1")
    assert "General" in r.text
    assert "Untied round" in r.text


# ── YAML export ───────────────────────────────────────────────────────────


async def test_export_yaml_requires_login(client):
    client.post("/concerts", data={"title": "C"})  # will 401 anyway, no login yet
    assert client.get("/concerts/1/export.yaml").status_code == 401


async def test_export_yaml_shape(client):
    import yaml

    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Hasunosora", "kind": "franchise"})
    client.post(
        "/concerts", data={"title": "Export Me", "kind": "concert", "franchise_tags": ["1"]}
    )
    client.post("/concerts/1/days", data={"label": "Day 1", "starts_at": "2099-08-01T18:00"})
    client.post(
        "/concerts/1/rounds",
        data={
            "label": "R1", "kind": "lottery_round",
            "opens_at": "2099-06-10T00:00", "closes_at": "2099-06-25T23:59",
            "applies_to": ["1"],
        },
    )

    r = client.get("/concerts/1/export.yaml")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/yaml")
    assert "attachment" in r.headers["content-disposition"]
    assert "export-me" in r.headers["content-disposition"]

    data = yaml.safe_load(r.text)
    assert data["title"] == "Export Me"
    assert data["kind"] == "concert"
    assert data["slug"] == "export-me"
    assert data["series"]["franchises"] == ["Hasunosora"]
    assert len(data["performances"]) == 1
    assert data["performances"][0]["label"] == "Day 1"
    assert len(data["rounds"]) == 1
    assert data["rounds"][0]["label"] == "R1"
    assert data["rounds"][0]["applies_to"] == ["Day 1"]
    assert data["rounds"][0]["apply_opens_jst"] == "2099-06-10 00:00"
