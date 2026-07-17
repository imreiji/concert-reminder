"""Web CRUD tests: authorization, JST parsing, the edit->re-sync contract,
event_id URLs, and the rich edit page's id-preserving reconciliation.

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
from app.db.models import Base, Concert, ConcertDay, ReminderQueue, Round
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


def create_with_round(client, title="C", event_id="c", closes_at="2099-06-25T23:59"):
    """The rich creation form is the only way to create a round now (the
    old standalone add_round endpoint is gone) -- one lottery round, no
    days, event_id fixed so callers can build follow-up URLs."""
    return client.post(
        "/concerts",
        data={
            "title": title, "event_id": event_id,
            "round_label": ["R1"], "round_kind": ["lottery_round"],
            "round_opens_at": [""], "round_closes_at": [closes_at],
            "round_results_at": [""], "round_payment_at": [""],
            "round_label_en": [""], "round_url": [""], "round_notes": [""], "round_leg": [""],
        },
    )


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
    r = client.post("/concerts", data={"title": "Hasunosora 5th", "event_id": "hasunosora-5th"})
    assert r.status_code == 303
    r = client.get("/")
    assert "Hasunosora 5th" in r.text


# ── event_id ──────────────────────────────────────────────────────────────


def test_event_id_rejects_bad_characters(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/concerts", data={"title": "X", "event_id": "bad id!"})
    assert r.status_code == 422


def test_event_id_rejects_reserved_words(client):
    login_as(client, EDITOR_ID, "reiji")
    assert client.post("/concerts", data={"title": "X", "event_id": "new"}).status_code == 422
    assert client.post("/concerts", data={"title": "X", "event_id": "Import"}).status_code == 422


def test_event_id_must_be_unique_on_create(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "X", "event_id": "dup"})
    r = client.post("/concerts", data={"title": "Y", "event_id": "dup"})
    assert r.status_code == 409


def test_event_id_must_be_unique_on_edit_but_self_is_exempt(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "X", "event_id": "one"})
    client.post("/concerts", data={"title": "Y", "event_id": "two"})

    # re-submitting the same event_id on its own edit is fine (no-op)
    r = client.post("/concerts/one/edit", data={"title": "X", "event_id": "one"})
    assert r.status_code == 303

    # but stealing another concert's event_id is not
    r = client.post("/concerts/one/edit", data={"title": "X", "event_id": "two"})
    assert r.status_code == 409


def test_get_concert_resolves_by_event_id_and_404s_on_unknown(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "Hasunosora 5th", "event_id": "5"})  # backfill-shaped
    assert client.get("/concerts/5").status_code == 200
    assert client.get("/concerts/does-not-exist").status_code == 404


# ── JST datetime contract ────────────────────────────────────────────────


def test_round_datetime_is_parsed_as_jst(client):
    login_as(client, EDITOR_ID, "reiji")
    create_with_round(client, closes_at="2026-08-01T19:00")

    import asyncio

    async def check():
        async with client.db() as s:
            round_ = (await s.execute(select(Round))).scalar_one()
            assert round_.closes_at_utc == jst_to_utc(datetime(2026, 8, 1, 19, 0))
            assert round_.closes_at_utc == datetime(2026, 8, 1, 10, 0, tzinfo=UTC)  # JST-9

    asyncio.get_event_loop().run_until_complete(check())


def test_round_needs_at_least_one_bound(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.post(
        "/concerts",
        data={
            "title": "C", "event_id": "c",
            "round_label": ["empty"], "round_kind": ["other"],
            "round_opens_at": [""], "round_closes_at": [""],
            "round_results_at": [""], "round_payment_at": [""],
            "round_label_en": [""], "round_url": [""], "round_notes": [""], "round_leg": [""],
        },
    )
    assert r.status_code == 422


# ── The core contract: edits re-sync the queue, ids are preserved ────────


def test_editing_round_over_http_reschedules_queue(client):
    """User story: staff extends a lottery via the edit page; the reminder
    moves. The round keeps its id across the edit -- required so its
    ReminderQueue history isn't lost (see the id-preservation test below)."""
    import asyncio

    login_as(client, EDITOR_ID, "reiji")
    create_with_round(client, closes_at="2099-06-25T23:59")
    client.post("/concerts/c/rules", data={"anchor": "closes", "days_before": 3})

    async def fire_at():
        async with client.db() as s:
            return (await s.execute(select(ReminderQueue))).scalar_one().fire_at_utc

    async def round_id():
        async with client.db() as s:
            return (await s.execute(select(Round))).scalar_one().id

    loop = asyncio.get_event_loop()
    before = loop.run_until_complete(fire_at())
    assert before == jst_to_utc(datetime(2099, 6, 22, 23, 59))
    rid = loop.run_until_complete(round_id())

    client.post(
        "/concerts/c/edit",
        data={
            "title": "C", "event_id": "c",
            "round_id": [str(rid)], "round_label": ["R1"], "round_kind": ["lottery_round"],
            "round_opens_at": [""], "round_closes_at": ["2099-06-28T23:59"],
            "round_results_at": [""], "round_payment_at": [""],
            "round_label_en": [""], "round_url": [""], "round_notes": [""], "round_leg": [""],
        },
    )
    after = loop.run_until_complete(fire_at())
    assert after == jst_to_utc(datetime(2099, 6, 25, 23, 59))  # moved with the deadline
    assert loop.run_until_complete(round_id()) == rid  # same row, not delete+recreate


async def test_edit_reconciliation_preserves_unrelated_round_and_day_ids(client):
    """Editing the concert title alone must not touch any round/day id --
    a naive delete-and-recreate would silently reset ReminderQueue.sent_at
    history for rows that never conceptually changed."""
    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={
            "title": "C", "event_id": "c",
            "day_label": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_city": [""], "day_venue": [""], "day_venue_address": [""], "day_doors_at": [""],
            "round_label": ["R1"], "round_kind": ["lottery_round"],
            "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
            "round_results_at": [""], "round_payment_at": [""],
            "round_label_en": [""], "round_url": [""], "round_notes": [""], "round_leg": [""],
        },
    )
    async with client.db() as s:
        day_id = (await s.execute(select(ConcertDay))).scalar_one().id
        round_id = (await s.execute(select(Round))).scalar_one().id

    client.post(
        "/concerts/c/edit",
        data={
            "title": "C (renamed)", "event_id": "c",
            "day_id": [str(day_id)], "day_label": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_city": [""], "day_venue": [""], "day_venue_address": [""], "day_doors_at": [""],
            "round_id": [str(round_id)], "round_label": ["R1"], "round_kind": ["lottery_round"],
            "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
            "round_results_at": [""], "round_payment_at": [""],
            "round_label_en": [""], "round_url": [""], "round_notes": [""], "round_leg": [""],
        },
    )
    async with client.db() as s:
        assert (await s.execute(select(ConcertDay))).scalar_one().id == day_id
        assert (await s.execute(select(Round))).scalar_one().id == round_id
        assert (await s.get(Concert, 1)).title == "C (renamed)"


async def test_edit_can_remove_a_day_and_add_a_round(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={
            "title": "C", "event_id": "c",
            "day_label": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_city": [""], "day_venue": [""], "day_venue_address": [""], "day_doors_at": [""],
        },
    )
    async with client.db() as s:
        day_id = (await s.execute(select(ConcertDay))).scalar_one().id

    client.post(
        "/concerts/c/edit",
        data={
            "title": "C", "event_id": "c",
            # day rows omitted entirely -> the existing day is dropped
            "round_id": [""], "round_label": ["New round"], "round_kind": ["other"],
            "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
            "round_results_at": [""], "round_payment_at": [""],
            "round_label_en": [""], "round_url": [""], "round_notes": [""], "round_leg": [""],
        },
    )
    async with client.db() as s:
        assert (await s.get(ConcertDay, day_id)) is None
        round_ = (await s.execute(select(Round))).scalar_one()
        assert round_.label == "New round"


def test_deleting_rule_removes_queue_rows(client):
    import asyncio

    login_as(client, EDITOR_ID, "reiji")
    create_with_round(client)
    client.post("/concerts/c/rules", data={"anchor": "closes", "days_before": 3})
    client.post("/rules/1/delete")

    async def count():
        async with client.db() as s:
            return len((await s.execute(select(ReminderQueue))).scalars().all())

    assert asyncio.get_event_loop().run_until_complete(count()) == 0


def test_cannot_delete_someone_elses_rule(client):
    login_as(client, EDITOR_ID, "reiji")
    create_with_round(client)
    client.post("/concerts/c/rules", data={"anchor": "closes", "days_before": 3})

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
    create_with_round(client)
    client.post("/concerts/c/rules", data={"anchor": "closes", "days_before": 3})
    client.post("/concerts/c/delete")

    async def counts():
        async with client.db() as s:
            c = len((await s.execute(select(Concert))).scalars().all())
            r = len((await s.execute(select(Round))).scalars().all())
            q = len((await s.execute(select(ReminderQueue))).scalars().all())
            return c, r, q

    assert asyncio.get_event_loop().run_until_complete(counts()) == (0, 0, 0)


def test_concert_detail_page_renders_for_logged_in_users(client):
    """Regression: the detail page must render with full context -- this
    exact page 500'd in production once because no test loaded it. The
    page is read-only now (no inline edit boxes); editors additionally see
    an Edit Concert link."""
    login_as(client, EDITOR_ID, "reiji")
    create_with_round(client, title="Render Me")
    r = client.get("/concerts/c")
    assert r.status_code == 200
    assert "Render Me" in r.text
    assert "/concerts/c/edit" in r.text  # editor sees the Edit Concert link

    login_as(client, VIEWER_ID, "viewer")
    r = client.get("/concerts/c")
    assert r.status_code == 200  # viewers render too
    assert "/concerts/c/edit" not in r.text  # but get no edit link


# ── Concert kind ─────────────────────────────────────────────────────────


async def test_create_concert_with_kind(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "Fest", "event_id": "fest", "kind": "festival"})
    async with client.db() as s:
        concert = (await s.execute(select(Concert))).scalar_one()
    assert concert.kind.value == "festival"


async def test_create_concert_without_kind_leaves_it_unset(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "No kind", "event_id": "no-kind"})
    async with client.db() as s:
        concert = (await s.execute(select(Concert))).scalar_one()
    assert concert.kind is None


async def test_edit_concert_kind_can_be_set_then_cleared(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "C", "event_id": "c"})
    client.post("/concerts/c/edit", data={"title": "C", "event_id": "c", "kind": "tour"})
    async with client.db() as s:
        concert = await s.get(Concert, 1)
        assert concert.kind.value == "tour"

    client.post("/concerts/c/edit", data={"title": "C", "event_id": "c", "kind": ""})
    async with client.db() as s:
        concert = await s.get(Concert, 1)
        assert concert.kind is None


# ── Round applies_to / leg grouping ──────────────────────────────────────


async def test_round_applies_to_is_stored(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={
            "title": "C", "event_id": "c",
            "day_label": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_city": [""], "day_venue": [""], "day_venue_address": [""], "day_doors_at": [""],
            "round_label": ["Day 1 lottery"], "round_kind": ["lottery_round"],
            "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
            "round_results_at": [""], "round_payment_at": [""], "round_label_en": [""],
            "round_url": [""], "round_notes": [""], "round_leg": ["Day 1"],
        },
    )
    async with client.db() as s:
        day = (await s.execute(select(ConcertDay))).scalar_one()
        round_ = (await s.execute(select(Round))).scalar_one()
    assert round_.applies_to == [day.id]


async def test_detail_page_groups_rounds_by_leg(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={
            "title": "Two Legs", "event_id": "two-legs",
            "day_label": ["Day 1", "Day 2"],
            "day_starts_at": ["2099-08-01T18:00", "2099-08-02T18:00"],
            "day_city": ["", ""], "day_venue": ["", ""],
            "day_venue_address": ["", ""], "day_doors_at": ["", ""],
            "round_label": ["Day 1 round", "Day 2 round", "General round"],
            "round_kind": ["lottery_round", "lottery_round", "general_sale"],
            "round_opens_at": ["", "", ""],
            "round_closes_at": ["2099-06-25T23:59", "2099-06-26T23:59", "2099-06-27T23:59"],
            "round_results_at": ["", "", ""], "round_payment_at": ["", "", ""],
            "round_label_en": ["", "", ""], "round_url": ["", "", ""], "round_notes": ["", "", ""],
            "round_leg": ["Day 1", "Day 2", ""],
        },
    )
    r = client.get("/concerts/two-legs")
    assert r.status_code == 200
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
    client.post(
        "/concerts",
        data={
            "title": "C", "event_id": "c",
            "day_label": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_city": [""], "day_venue": [""], "day_venue_address": [""], "day_doors_at": [""],
            "round_label": ["Untied round"], "round_kind": ["other"],
            "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
            "round_results_at": [""], "round_payment_at": [""], "round_label_en": [""],
            "round_url": [""], "round_notes": [""], "round_leg": [""],
        },
    )
    r = client.get("/concerts/c")
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
        "/concerts",
        data={
            "title": "Export Me", "event_id": "export-me", "kind": "concert",
            "franchise_tags": ["1"],
            "day_label": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_city": [""], "day_venue": [""], "day_venue_address": [""], "day_doors_at": [""],
            "round_label": ["R1"], "round_kind": ["lottery_round"],
            "round_opens_at": ["2099-06-10T00:00"], "round_closes_at": ["2099-06-25T23:59"],
            "round_results_at": [""], "round_payment_at": [""], "round_label_en": [""],
            "round_url": [""], "round_notes": [""], "round_leg": ["Day 1"],
        },
    )

    r = client.get("/concerts/export-me/export.yaml")
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


# ── The rich /concerts/new page and its all-in-one POST /concerts ────────


def test_new_concert_page_is_editor_only(client):
    assert client.get("/concerts/new").status_code == 401  # anonymous

    login_as(client, VIEWER_ID, "viewer")
    assert client.get("/concerts/new").status_code == 403

    login_as(client, EDITOR_ID, "reiji")
    r = client.get("/concerts/new")
    assert r.status_code == 200
    assert "Add an event" in r.text
    assert 'name="event_id"' in r.text  # event id field present
    assert 'name="day_label"' in r.text  # performance row template present
    assert 'name="round_leg"' in r.text  # round row template present


async def test_rich_create_builds_concert_days_and_rounds_atomically(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.post(
        "/concerts",
        data={
            "title": "Rich Concert",
            "event_id": "rich-concert",
            "title_en": "Rich Concert (EN)",
            "kind": "tour",
            "organizer": "LustQueen",
            "categories": "concert, tour",
            "eventernote_url": "https://eventernote.com/x",
            "official_url": "https://official.example/x",
            "source_url": "https://ramen.events/x",
            "performers_text": "Kaho\nSayaka",
            "notes": "Event notes",
            "day_label": ["Day 1", "Day 2"],
            "day_starts_at": ["2099-08-01T18:00", "2099-08-02T18:00"],
            "day_city": ["Kanagawa", "Osaka"],
            "day_venue": ["K Arena Yokohama", ""],
            "day_venue_address": ["", ""],
            "day_doors_at": ["2099-08-01T17:00", ""],
            "round_label": ["Kanagawa lottery", "Whole-tour goods sale"],
            "round_label_en": ["Kanagawa lottery round", ""],
            "round_kind": ["lottery_round", "general_sale"],
            "round_opens_at": ["2099-06-01T00:00", "2099-07-01T00:00"],
            "round_closes_at": ["2099-06-15T23:59", "2099-07-31T23:59"],
            "round_results_at": ["", ""],
            "round_payment_at": ["", ""],
            "round_url": ["", ""],
            "round_notes": ["", ""],
            "round_leg": ["Kanagawa", ""],  # matches day 1's city; blank = whole event
        },
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/concerts/rich-concert"

    async with client.db() as s:
        concert = (await s.execute(
            select(Concert).where(Concert.event_id == "rich-concert")
        )).scalar_one()
        await s.refresh(concert, ["days", "rounds"])

    assert concert.title_en == "Rich Concert (EN)"
    assert concert.organizer == "LustQueen"
    assert concert.categories == "concert, tour"
    assert concert.performers_text == "Kaho\nSayaka"

    days = sorted(concert.days, key=lambda d: d.label)
    assert [d.label for d in days] == ["Day 1", "Day 2"]
    assert days[0].city == "Kanagawa"
    assert days[0].venue == "K Arena Yokohama"
    assert days[0].doors_at_utc is not None

    rounds = sorted(concert.rounds, key=lambda r: r.label)
    kanagawa_round = next(r for r in rounds if r.label == "Kanagawa lottery")
    general_round = next(r for r in rounds if r.label == "Whole-tour goods sale")
    # leg "Kanagawa" matched Day 1's city -> applies_to resolved to its real id.
    assert kanagawa_round.applies_to == [days[0].id]
    # blank leg -> no match -> lands in "General".
    assert general_round.applies_to is None


async def test_rich_create_tolerates_blank_trailing_rows(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.post(
        "/concerts",
        data={
            "title": "Minimal", "event_id": "minimal",
            "day_label": [""], "day_starts_at": [""], "day_city": [""],
            "day_venue": [""], "day_venue_address": [""], "day_doors_at": [""],
            "round_label": [""], "round_label_en": [""], "round_kind": ["other"],
            "round_opens_at": [""], "round_closes_at": [""], "round_results_at": [""],
            "round_payment_at": [""], "round_url": [""], "round_notes": [""], "round_leg": [""],
        },
    )
    assert r.status_code == 303
    async with client.db() as s:
        assert (await s.execute(select(ConcertDay))).scalars().all() == []
        assert (await s.execute(select(Round))).scalars().all() == []


async def test_edit_concert_persists_all_new_fields(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "C", "event_id": "c"})
    client.post(
        "/concerts/c/edit",
        data={
            "title": "C", "event_id": "c", "title_en": "C (EN)", "organizer": "Org",
            "categories": "a, b", "eventernote_url": "https://eventernote.com/x",
            "official_url": "https://official.example/x", "source_url": "https://src.example/x",
            "performers_text": "A\nB", "notes": "notes",
        },
    )
    async with client.db() as s:
        concert = await s.get(Concert, 1)
    assert concert.title_en == "C (EN)"
    assert concert.organizer == "Org"
    assert concert.categories == "a, b"
    assert concert.eventernote_url == "https://eventernote.com/x"
    assert concert.official_url == "https://official.example/x"
    assert concert.source_url == "https://src.example/x"
    assert concert.performers_text == "A\nB"


async def test_edit_page_prefills_every_field(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={
            "title": "C", "event_id": "c", "title_en": "C (EN)", "organizer": "Org",
            "day_label": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_city": [""], "day_venue": [""], "day_venue_address": [""], "day_doors_at": [""],
            "round_label": ["R1"], "round_kind": ["lottery_round"],
            "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
            "round_results_at": [""], "round_payment_at": [""], "round_label_en": [""],
            "round_url": [""], "round_notes": [""], "round_leg": [""],
        },
    )
    r = client.get("/concerts/c/edit")
    assert r.status_code == 200
    assert 'value="C"' in r.text
    assert 'value="c"' in r.text
    assert 'value="C (EN)"' in r.text
    assert 'value="Org"' in r.text
    assert 'value="Day 1"' in r.text
    assert 'value="R1"' in r.text


def test_edit_page_is_editor_only(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "C", "event_id": "c"})

    login_as(client, VIEWER_ID, "viewer")
    assert client.get("/concerts/c/edit").status_code == 403
    assert client.post("/concerts/c/edit", data={"title": "C", "event_id": "c"}).status_code == 403


def test_nav_add_link_shown_only_to_editors(client):
    login_as(client, EDITOR_ID, "reiji")
    assert '/concerts/new">+ Add' in client.get("/").text

    login_as(client, VIEWER_ID, "viewer")
    assert '/concerts/new">+ Add' not in client.get("/").text
