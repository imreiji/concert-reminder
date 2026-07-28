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
from app.db.models import (
    Base,
    Concert,
    ConcertAudit,
    ConcertDay,
    ReminderQueue,
    Round,
    RoundQualifier,
)
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
        data={"title_en": title, "title_zh": title,
            "title": title, "event_id": event_id,
            "round_label": ["R1"], "round_kind": ["lottery_round"],
            "round_opens_at": [""], "round_closes_at": [closes_at],
            "round_results_at": [""], "round_payment_at": [""],
            "round_label_en": ["R1"],
            "round_label_zh": ["R1"], "round_url": [""], "round_notes": [""], "round_leg": [""],
        },
    )


# ── Authorization boundaries ─────────────────────────────────────────────


def test_anonymous_cannot_view_concert_pages(client):
    assert client.post("/concerts", data={
        "title_en": "X", "title_zh": "X", "title": "X",
    }).status_code == 303
    assert client.get("/concerts/1").status_code == 303


def test_viewer_cannot_create_concert(client):
    login_as(client, VIEWER_ID, "viewer")
    r = client.post("/concerts", data={"title_en": "Nope", "title_zh": "Nope", "title": "Nope"})
    assert r.status_code == 403


def test_editor_creates_concert_and_it_lists(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/concerts", data={
        "title_en": "Hasunosora 5th", "title_zh": "Hasunosora 5th", "title": "Hasunosora 5th",
        "event_id": "hasunosora-5th",
    })
    assert r.status_code == 303
    # The catalogue lives on /discover now; / is the personal Home.
    r = client.get("/discover")
    assert "Hasunosora 5th" in r.text


# ── event_id ──────────────────────────────────────────────────────────────


def test_event_id_rejects_bad_characters(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/concerts", data={
        "title_en": "X", "title_zh": "X", "title": "X", "event_id": "bad id!",
    })
    assert r.status_code == 422
    # Pin WHY: require_variants runs before validate_event_id in
    # create_concert, so a payload drifting out of full title coverage would
    # keep this test green for the translation reason instead.
    assert "event id may only contain" in r.json()["detail"], r.json()


def test_event_id_rejects_reserved_words(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/concerts", data={
        "title_en": "X", "title_zh": "X", "title": "X", "event_id": "new",
    })
    assert r.status_code == 422
    assert "is reserved" in r.json()["detail"], r.json()
    r = client.post("/concerts", data={
        "title_en": "X", "title_zh": "X", "title": "X", "event_id": "Import",
    })
    assert r.status_code == 422
    assert "is reserved" in r.json()["detail"], r.json()


def test_event_id_must_be_unique_on_create(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={
        "title_en": "X", "title_zh": "X", "title": "X", "event_id": "dup",
    })
    r = client.post("/concerts", data={
        "title_en": "Y", "title_zh": "Y", "title": "Y", "event_id": "dup",
    })
    assert r.status_code == 409


def test_event_id_must_be_unique_on_edit_but_self_is_exempt(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={
        "title_en": "X", "title_zh": "X", "title": "X", "event_id": "one",
    })
    client.post("/concerts", data={
        "title_en": "Y", "title_zh": "Y", "title": "Y", "event_id": "two",
    })

    # re-submitting the same event_id on its own edit is fine (no-op)
    r = client.post("/concerts/one/edit", data={"title": "X", "event_id": "one"})
    assert r.status_code == 303

    # but stealing another concert's event_id is not
    r = client.post("/concerts/one/edit", data={"title": "X", "event_id": "two"})
    assert r.status_code == 409


def test_get_concert_resolves_by_event_id_and_404s_on_unknown(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={
        "title_en": "Hasunosora 5th", "title_zh": "Hasunosora 5th", "title": "Hasunosora 5th",
        "event_id": "5",
    })  # backfill-shaped
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
        data={"title_en": "C", "title_zh": "C",
            "title": "C", "event_id": "c",
            "round_label": ["empty"], "round_kind": ["other"],
            "round_opens_at": [""], "round_closes_at": [""],
            "round_results_at": [""], "round_payment_at": [""],
            "round_label_en": ["empty"],
            "round_label_zh": ["empty"], "round_url": [""], "round_notes": [""], "round_leg": [""],
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
            "round_label_en": [""],
            "round_label_zh": [""], "round_url": [""], "round_notes": [""], "round_leg": [""],
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
        data={"title_en": "C", "title_zh": "C",
            "title": "C", "event_id": "c",
            "day_label": ["Day 1"],
            "day_label_en": ["Day 1"],
            "day_label_zh": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_doors_at": [""],
            "round_label": ["R1"], "round_kind": ["lottery_round"],
            "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
            "round_results_at": [""], "round_payment_at": [""],
            "round_label_en": ["R1"],
            "round_label_zh": ["R1"], "round_url": [""], "round_notes": [""], "round_leg": [""],
        },
    )
    async with client.db() as s:
        day_id = (await s.execute(select(ConcertDay))).scalar_one().id
        round_id = (await s.execute(select(Round))).scalar_one().id

    client.post(
        "/concerts/c/edit",
        data={
            "title": "C (renamed)", "event_id": "c",
            "day_id": [str(day_id)], "day_label": ["Day 1"],
            "day_label_en": [""],
            "day_label_zh": [""], "day_starts_at": ["2099-08-01T18:00"],
            "day_doors_at": [""],
            "round_id": [str(round_id)], "round_label": ["R1"], "round_kind": ["lottery_round"],
            "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
            "round_results_at": [""], "round_payment_at": [""],
            "round_label_en": [""],
            "round_label_zh": [""], "round_url": [""], "round_notes": [""], "round_leg": [""],
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
        data={"title_en": "C", "title_zh": "C",
            "title": "C", "event_id": "c",
            "day_label": ["Day 1"],
            "day_label_en": ["Day 1"],
            "day_label_zh": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_doors_at": [""],
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
            "round_label_en": [""],
            "round_label_zh": [""], "round_url": [""], "round_notes": [""], "round_leg": [""],
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


async def test_test_dm_succeeds_and_clears_dm_blocked(client, monkeypatch):
    from app.config import settings as app_settings
    from app.db.models import User

    class FakeUser:
        async def send(self, body):
            pass

    class FakeBot:
        def get_user(self, uid):
            return FakeUser()

    # bot_enabled defaults to False in tests (discord_token defaults to "");
    # this route short-circuits on that flag, so it must be turned on here.
    monkeypatch.setattr(app_settings, "discord_token", "fake-token")
    login_as(client, EDITOR_ID, "reiji")
    async with client.db() as s:
        user = await s.get(User, EDITOR_ID)
        user.dm_blocked_since = datetime.now(UTC)
        await s.commit()

    import app.bot.client as bot_client_mod

    client.monkeypatch.setattr(bot_client_mod, "bot", FakeBot())
    r = client.post("/me/test-dm")
    assert r.status_code == 200
    assert "Test DM sent" in r.text

    async with client.db() as s:
        user = await s.get(User, EDITOR_ID)
        assert user.dm_blocked_since is None


async def test_test_dm_forbidden_sets_dm_blocked(client, monkeypatch):
    import discord

    from app.config import settings as app_settings
    from app.db.models import User

    class FakeResponse:
        status = 403
        reason = "Forbidden"

    class FakeUser:
        async def send(self, body):
            raise discord.Forbidden(FakeResponse(), "missing access")

    class FakeBot:
        def get_user(self, uid):
            return FakeUser()

    monkeypatch.setattr(app_settings, "discord_token", "fake-token")
    login_as(client, EDITOR_ID, "reiji")

    import app.bot.client as bot_client_mod

    client.monkeypatch.setattr(bot_client_mod, "bot", FakeBot())
    r = client.post("/me/test-dm")
    assert r.status_code == 200
    assert "Still blocked" in r.text

    async with client.db() as s:
        user = await s.get(User, EDITOR_ID)
        assert user.dm_blocked_since is not None


def test_test_dm_when_bot_disabled(client):
    """discord_token defaults to "" (bot_enabled False) in every test
    environment, so no monkeypatch is needed for this one -- it's the
    default state."""
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/me/test-dm")
    assert r.status_code == 200
    assert "isn't running" in r.text


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
    client.post("/concerts", data={
        "title_en": "Fest", "title_zh": "Fest", "title": "Fest", "event_id": "fest",
        "kind": "festival",
    })
    async with client.db() as s:
        concert = (await s.execute(select(Concert))).scalar_one()
    assert concert.kind.value == "festival"


async def test_create_concert_without_kind_leaves_it_unset(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={
        "title_en": "No kind", "title_zh": "No kind", "title": "No kind", "event_id": "no-kind",
    })
    async with client.db() as s:
        concert = (await s.execute(select(Concert))).scalar_one()
    assert concert.kind is None


async def test_edit_concert_kind_can_be_set_then_cleared(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title_en": "C", "title_zh": "C", "title": "C", "event_id": "c"})
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
        data={"title_en": "C", "title_zh": "C",
            "title": "C", "event_id": "c",
            "day_key": ["new-a"],
            "day_label": ["Day 1"],
            "day_label_en": ["Day 1"],
            "day_label_zh": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_doors_at": [""],
            "round_label": ["Day 1 lottery"], "round_kind": ["lottery_round"],
            "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
            "round_results_at": [""], "round_payment_at": [""], "round_label_en": ["Day 1 lottery"],
            "round_label_zh": ["Day 1 lottery"],
            "round_url": [""], "round_notes": [""], "round_legs": ["new-a"],
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
        data={"title_en": "Two Legs", "title_zh": "Two Legs",
            "title": "Two Legs", "event_id": "two-legs",
            "day_key": ["new-a", "new-b"],
            "day_label": ["Day 1", "Day 2"],
            "day_label_en": ["Day 1", "Day 2"],
            "day_label_zh": ["Day 1", "Day 2"],
            "day_starts_at": ["2099-08-01T18:00", "2099-08-02T18:00"],
            "day_doors_at": ["", ""],
            "round_label": ["Day 1 round", "Day 2 round", "General round"],
            "round_kind": ["lottery_round", "lottery_round", "general_sale"],
            "round_opens_at": ["", "", ""],
            "round_closes_at": ["2099-06-25T23:59", "2099-06-26T23:59", "2099-06-27T23:59"],
            "round_results_at": ["", "", ""], "round_payment_at": ["", "", ""],
            "round_label_en": ["Day 1 round", "Day 2 round", "General round"],
            "round_label_zh": ["Day 1 round", "Day 2 round", "General round"],
            "round_url": ["", "", ""], "round_notes": ["", "", ""],
            "round_legs": ["new-a", "new-b", ""],
        },
    )
    r = client.get("/concerts/two-legs")
    assert r.status_code == 200
    # Scoped past "Next for you", which names whichever round wants the reader
    # first and so legitimately mentions one of these before its leg.
    body = r.text.split("<!-- /standing -->", 1)[-1]
    day1_pos = body.index('leg-heading">Day 1<')
    round1_pos = body.index("Day 1 round")
    day2_pos = body.index('leg-heading">Day 2<')
    round2_pos = body.index("Day 2 round")
    assert day1_pos < round1_pos < day2_pos < round2_pos
    # The untied round is a fact about both legs, so it renders under each of
    # them -- there is no separate all-legs section to cross-reference.
    assert "All legs" not in body
    assert body.count("General round") == 2


async def test_round_with_no_day_association_shows_under_every_leg(client):
    """A round with no applies_to covers every leg, so it renders under each
    of them -- the separate all-legs section is gone (the owner had to
    cross-reference it to read one leg's story)."""
    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={"title_en": "C", "title_zh": "C",
            "title": "C", "event_id": "c",
            "day_label": ["Day 1"],
            "day_label_en": ["Day 1"],
            "day_label_zh": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_doors_at": [""],
            "round_label": ["Untied round"], "round_kind": ["other"],
            "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
            "round_results_at": [""], "round_payment_at": [""], "round_label_en": ["Untied round"],
            "round_label_zh": ["Untied round"],
            "round_url": [""], "round_notes": [""], "round_leg": [""],
        },
    )
    r = client.get("/concerts/c")
    assert "All legs" not in r.text
    # It lands in Day 1's own section, the only leg there is.
    day1 = r.text.split('leg-heading">Day 1<', 1)[1].split("<h3", 1)[0]
    assert "Untied round" in day1


async def test_detail_page_nests_performances_and_their_rounds_together(client):
    """Each leg gets one integrated section (heading + venue/time info + its
    rounds right underneath), including a leg with no rounds yet at all --
    previously that day was entirely absent from the separate rounds
    section."""
    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={"title_en": "C", "title_zh": "C",
            "title": "C", "event_id": "c",
            "day_key": ["new-a", "new-b"],
            "day_label": ["Day 1", "Day 2"],
            "day_label_en": ["Day 1", "Day 2"],
            "day_label_zh": ["Day 1", "Day 2"],
            "day_starts_at": ["2099-08-01T18:00", "2099-08-02T18:00"],
            "day_doors_at": ["", ""],
            "round_label": ["Day 1 round"], "round_kind": ["lottery_round"],
            "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
            "round_results_at": [""], "round_payment_at": [""], "round_label_en": ["Day 1 round"],
            "round_label_zh": ["Day 1 round"],
            "round_url": [""], "round_notes": [""], "round_legs": ["new-a"],
        },
    )
    r = client.get("/concerts/c")
    assert r.status_code == 200
    # both legs get their own integrated section...
    assert r.text.count('class="leg-heading') == 2
    # ...Day 1's includes its rounds...
    day1_section = r.text[r.text.index('leg-heading">Day 1<'):r.text.index('leg-heading">Day 2<')]
    assert "Day 1 round" in day1_section
    # ...Day 2 has none yet, but still gets its section with a placeholder
    day2_section = r.text[r.text.index('leg-heading">Day 2<'):]
    assert "No rounds yet for this leg." in day2_section


# ── YAML export ───────────────────────────────────────────────────────────


async def test_export_yaml_requires_login(client):
    client.post("/concerts", data={
        "title_en": "C", "title_zh": "C", "title": "C",
    })  # goes home anyway, no login yet
    assert client.get("/concerts/1/export.yaml").status_code == 303


async def test_export_yaml_shape(client):
    import yaml

    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={
        "name_en": "Hasunosora", "name_zh": "Hasunosora", "name": "Hasunosora", "kind": "franchise",
    })
    client.post(
        "/concerts",
        data={"title_en": "Export Me", "title_zh": "Export Me",
            "title": "Export Me", "event_id": "export-me", "kind": "concert",
            "franchise_tags": ["1"],
            "day_key": ["new-a"],
            "day_label": ["Day 1"],
            "day_label_en": ["Day 1"],
            "day_label_zh": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_doors_at": [""],
            "round_label": ["R1"], "round_kind": ["lottery_round"],
            "round_opens_at": ["2099-06-10T00:00"], "round_closes_at": ["2099-06-25T23:59"],
            "round_results_at": [""], "round_payment_at": [""], "round_label_en": ["R1"],
            "round_label_zh": ["R1"],
            "round_url": [""], "round_notes": [""], "round_legs": ["new-a"],
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


async def test_export_yaml_reads_venue_from_the_tag(client):
    """A leg's venue is a VENUE tag now, and the editor's free-text venue
    inputs are gone -- so an export still reading d.city/d.venue/
    d.venue_address would silently emit nulls for every newly entered
    concert. The export uses the tag's CANONICAL columns, not loc(): an
    export is data, not a viewer-facing render."""
    import yaml

    from app.db.models import Tag
    from app.domain.types import TagKind

    login_as(client, EDITOR_ID, "reiji")
    async with client.db() as s:
        tag = Tag(
            name="K Arena Yokohama", name_en="K Arena EN", kind=TagKind.VENUE,
            city="Yokohama", city_en="Yokohama EN", address="Yokohama, Japan",
        )
        s.add(tag)
        await s.commit()
        venue_tag_id = tag.id

    client.post(
        "/concerts",
        data={"title_en": "Venue Export", "title_zh": "Venue Export",
            "title": "Venue Export", "event_id": "venue-export", "kind": "concert",
            "day_key": ["new-a"],
            "day_label": ["Day 1"],
            "day_label_en": ["Day 1"],
            "day_label_zh": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_venue_tag_id": [str(venue_tag_id)], "day_doors_at": [""],
        },
    )

    r = client.get("/concerts/venue-export/export.yaml")
    assert r.status_code == 200
    day = yaml.safe_load(r.text)["performances"][0]
    assert day["venue"] == "K Arena Yokohama"
    assert day["city"] == "Yokohama"
    assert day["venue_address"] == "Yokohama, Japan"


async def test_edit_page_renders_a_blank_label_legs_venue_chip(client):
    """The leg chip's label chain is `label or venue tag name or date`, so
    d.venue_tag is only ever evaluated for a leg with NO label -- which makes
    this the one render that actually exercises the eager load.
    ConcertDay.venue_tag is lazy="raise", so a missing one is a
    MissingGreenlet 500 here, not a quiet fallback."""
    from app.db.models import Tag
    from app.domain.types import TagKind

    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={
        "title_en": "C", "title_zh": "C", "title": "C", "event_id": "chip",
    })
    async with client.db() as s:
        tag = Tag(name="Zepp Haneda", kind=TagKind.VENUE)
        s.add(tag)
        concert = (await s.execute(select(Concert).where(Concert.event_id == "chip"))).scalar_one()
        await s.flush()
        s.add(ConcertDay(
            concert_id=concert.id, label="", venue_tag_id=tag.id,
            starts_at_utc=datetime(2099, 8, 1, 9, tzinfo=UTC),
        ))
        await s.commit()

    r = client.get("/concerts/chip/edit")
    assert r.status_code == 200
    assert "Zepp Haneda" in r.text  # the chip fell back to the venue tag's name


# ── The rich /concerts/new page and its all-in-one POST /concerts ────────


def test_new_concert_page_is_editor_only(client):
    assert client.get("/concerts/new").status_code == 303  # anonymous: home

    login_as(client, VIEWER_ID, "viewer")
    assert client.get("/concerts/new").status_code == 403

    login_as(client, EDITOR_ID, "reiji")
    r = client.get("/concerts/new")
    assert r.status_code == 200
    assert "Add an event" in r.text
    assert 'name="event_id"' in r.text  # event id field present
    assert 'name="day_label"' in r.text  # performance row template present
    # Rounds bind to legs by chip now, exactly as the editor does -- the old
    # free-text <select> + syncLegOptions matcher is gone.
    assert "data-leg-chips" in r.text
    assert 'name="round_legs"' in r.text
    assert 'name="round_leg"' not in r.text
    assert "syncLegOptions" not in r.text
    # the identity spine stays open; the extras fold below it
    assert 'class="fold"' in r.text
    # conveniences that must survive the rebuild
    assert "/concerts/import" in r.text  # import link
    assert 'id="ec-title"' in r.text and 'id="ec-event-id"' in r.text  # id suggestion JS hooks


def test_new_concert_page_shows_new_round_kind_labels(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.get("/concerts/new")
    assert "First come, first served" in r.text
    assert "Overseas tour package" in r.text


async def test_rich_create_builds_concert_days_and_rounds_atomically(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.post(
        "/concerts",
        data={
            "title_zh": "Rich Concert",
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
            "notes_en": "Event notes",
            "notes_zh": "Event notes",
            "day_key": ["new-a", "new-b"],
            "day_label": ["Day 1", "Day 2"],
            "day_label_en": ["Day 1", "Day 2"],
            "day_label_zh": ["Day 1", "Day 2"],
            "day_starts_at": ["2099-08-01T18:00", "2099-08-02T18:00"],
            "day_doors_at": ["2099-08-01T17:00", ""],
            "round_label": ["Kanagawa lottery", "Whole-tour goods sale"],
            "round_label_en": ["Kanagawa lottery round", "Whole-tour goods sale"],
            "round_label_zh": ["Kanagawa lottery", "Whole-tour goods sale"],
            "round_kind": ["lottery_round", "general_sale"],
            "round_opens_at": ["2099-06-01T00:00", "2099-07-01T00:00"],
            "round_closes_at": ["2099-06-15T23:59", "2099-07-31T23:59"],
            "round_results_at": ["", ""],
            "round_payment_at": ["", ""],
            "round_url": ["", ""],
            "round_notes": ["", ""],
            "round_legs": ["new-a", ""],  # first round -> day 1's chip; blank = whole event
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
        data={"title_en": "Minimal", "title_zh": "Minimal",
            "title": "Minimal", "event_id": "minimal",
            "day_label": [""],
            "day_label_en": [""],
            "day_label_zh": [""], "day_starts_at": [""],
            "day_doors_at": [""],
            "round_label": [""], "round_label_en": [""],
            "round_label_zh": [""], "round_kind": ["other"],
            "round_opens_at": [""], "round_closes_at": [""], "round_results_at": [""],
            "round_payment_at": [""], "round_url": [""], "round_notes": [""], "round_leg": [""],
        },
    )
    assert r.status_code == 303
    async with client.db() as s:
        assert (await s.execute(select(ConcertDay))).scalars().all() == []
        assert (await s.execute(select(Round))).scalars().all() == []


async def test_create_binds_a_round_to_two_brand_new_legs(client):
    """The regression the old create form could not pass: a round whose chips
    select BOTH performances must save with an applies_to holding both day
    ids. Every leg is brand-new at creation, so each row carries a client-side
    `day_key` (new-a / new-b) that its round references -- the same key-mapped
    resolution the editor proved. The old single-value `round_leg` <select>
    could only ever store one leg."""
    login_as(client, EDITOR_ID, "reiji")
    r = client.post(
        "/concerts",
        data={"title_en": "Two Legs", "title_zh": "Two Legs",
            "title": "Two Legs", "event_id": "two-legs",
            "day_key": ["new-a", "new-b"],
            "day_label": ["Day 1", "Day 2"],
            "day_label_en": ["Day 1", "Day 2"],
            "day_label_zh": ["Day 1", "Day 2"],
            "day_starts_at": ["2099-08-01T18:00", "2099-08-02T18:00"],
            "day_doors_at": ["", ""],
            "round_label": ["Both legs lottery"], "round_kind": ["lottery_round"],
            "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
            "round_results_at": [""], "round_payment_at": [""],
            "round_label_en": ["Both legs lottery"],
            "round_label_zh": ["Both legs lottery"],
            "round_url": [""], "round_notes": [""],
            "round_legs": ["new-a new-b"],
        },
    )
    assert r.status_code == 303
    async with client.db() as s:
        days = (await s.execute(select(ConcertDay))).scalars().all()
        round_ = (await s.execute(select(Round))).scalar_one()
    day_ids = {d.id for d in days}
    assert len(day_ids) == 2
    assert set(round_.applies_to) == day_ids  # BOTH legs, not just the first


async def test_create_round_with_no_legs_stores_none(client):
    """An empty selection means "not tied to a specific leg" -- stored as
    None (not []), the all-legs group convention concert_round_rows reads."""
    login_as(client, EDITOR_ID, "reiji")
    r = client.post(
        "/concerts",
        data={"title_en": "No Leg", "title_zh": "No Leg",
            "title": "No Leg", "event_id": "no-leg",
            "day_key": ["new-a"],
            "day_label": ["Day 1"],
            "day_label_en": ["Day 1"],
            "day_label_zh": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_doors_at": [""],
            "round_label": ["Whole event"], "round_kind": ["general_sale"],
            "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
            "round_results_at": [""], "round_payment_at": [""], "round_label_en": ["Whole event"],
            "round_label_zh": ["Whole event"],
            "round_url": [""], "round_notes": [""],
            "round_legs": [""],
        },
    )
    assert r.status_code == 303
    async with client.db() as s:
        round_ = (await s.execute(select(Round))).scalar_one()
    assert round_.applies_to is None


async def test_create_upgrade_round_stores_a_same_submit_qualifier(client):
    """An upgrade round can qualify off another round created in the SAME
    submit: rounds get their ids at the flush, so parse_round_qualifiers
    resolves the reference afterwards (this fresh DB assigns the base round
    id 1, exactly what the qualifier names). The UI cannot pre-select this --
    the chips only offer already-saved rounds -- but the route mechanism does,
    matching the editor's post-flush resolution."""
    login_as(client, EDITOR_ID, "reiji")
    r = client.post(
        "/concerts",
        data={"title_en": "Upgrade", "title_zh": "Upgrade",
            "title": "Upgrade", "event_id": "upgrade",
            "day_key": ["new-a"],
            "day_label": ["Day 1"],
            "day_label_en": ["Day 1"],
            "day_label_zh": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_doors_at": [""],
            "round_label": ["Base lottery", "VIP upgrade"],
            "round_kind": ["lottery_round", "upgrade"],
            "round_opens_at": ["", ""], "round_closes_at": ["2099-06-25T23:59", "2099-07-01T23:59"],
            "round_results_at": ["", ""], "round_payment_at": ["", ""],
            "round_label_en": ["Base lottery", "VIP upgrade"],
            "round_label_zh": ["Base lottery", "VIP upgrade"], "round_url": ["", ""],
            "round_notes": ["", ""],
            "round_legs": ["", ""],
            # Base round is the first created -> id 1 in this fresh DB. The
            # upgrade round names it as its qualifier.
            "round_qualifiers": ["", "1"],
        },
    )
    assert r.status_code == 303
    async with client.db() as s:
        base = (await s.execute(
            select(Round).where(Round.label == "Base lottery")
        )).scalar_one()
        upgrade = (await s.execute(
            select(Round).where(Round.label == "VIP upgrade")
        )).scalar_one()
        quals = (await s.execute(
            select(RoundQualifier).where(RoundQualifier.upgrade_round_id == upgrade.id)
        )).scalars().all()
    assert [q.qualifying_round_id for q in quals] == [base.id]


async def test_edit_concert_persists_all_new_fields(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title_en": "C", "title_zh": "C", "title": "C", "event_id": "c"})
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
        data={"title_zh": "C",
            "title": "C", "event_id": "c", "title_en": "C (EN)", "organizer": "Org",
            "day_label": ["Day 1"],
            "day_label_en": ["Day 1"],
            "day_label_zh": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_doors_at": [""],
            "round_label": ["R1"], "round_kind": ["lottery_round"],
            "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
            "round_results_at": [""], "round_payment_at": [""], "round_label_en": ["R1"],
            "round_label_zh": ["R1"],
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


async def test_edit_page_shows_new_round_kind_labels(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title_en": "C", "title_zh": "C", "title": "C", "event_id": "c"})
    r = client.get("/concerts/c/edit")
    assert "First come, first served" in r.text
    assert "Overseas tour package" in r.text


async def test_edit_page_preselects_the_rounds_real_leg_ids(client):
    """The leg field is a toggle chip per leg over a hidden id list, filled
    from the round's real applies_to -- no text matching in either
    direction, so a round covering several legs cannot collapse to one on
    save. The full contract lives in tests/test_editor_legs.py."""
    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={"title_en": "C", "title_zh": "C",
            "title": "C", "event_id": "c",
            "day_key": ["new-a"],
            "day_label": ["Day 1"],
            "day_label_en": ["Day 1"],
            "day_label_zh": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_doors_at": [""],
            "round_label": ["R1"], "round_kind": ["lottery_round"],
            "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
            "round_results_at": [""], "round_payment_at": [""], "round_label_en": ["R1"],
            "round_label_zh": ["R1"],
            "round_url": [""], "round_notes": [""], "round_legs": ["new-a"],
        },
    )
    async with client.db() as s:
        day_id = (await s.execute(select(ConcertDay))).scalar_one().id
    r = client.get("/concerts/c/edit")
    assert r.status_code == 200
    assert f'name="round_legs" value="{day_id}"' in r.text
    assert f'data-leg-id="{day_id}"' in r.text


def test_edit_page_is_editor_only(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title_en": "C", "title_zh": "C", "title": "C", "event_id": "c"})

    login_as(client, VIEWER_ID, "viewer")
    assert client.get("/concerts/c/edit").status_code == 403
    assert client.post("/concerts/c/edit", data={"title": "C", "event_id": "c"}).status_code == 403


def test_nav_add_link_shown_only_to_editors(client):
    login_as(client, EDITOR_ID, "reiji")
    assert '/concerts/new">+ Add' in client.get("/").text

    login_as(client, VIEWER_ID, "viewer")
    assert '/concerts/new">+ Add' not in client.get("/").text


# ── Duplicate concert as template ────────────────────────────────────────


async def test_duplicate_clones_scalars_and_tags_but_not_days_rounds_or_venues(client):
    """The clone carries the scalars and the source's already-pruned
    franchise/group/artist tags, but not its days, rounds, or VENUE tags:
    a venue is derived from the legs, and a clone has none."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={
        "name_en": "Hasunosora", "name_zh": "Hasunosora", "name": "Hasunosora", "kind": "franchise",
    })
    client.post("/tags", data={
        "name_en": "K Arena", "name_zh": "K Arena", "name": "K Arena", "kind": "venue",
    })
    client.post(
        "/concerts",
        data={"title_en": "Hasunosora 5th", "title_zh": "Hasunosora 5th",
            "title": "Hasunosora 5th", "event_id": "hasunosora-5th",
            "kind": "concert", "organizer": "LustQueen", "categories": "concert, tour",
            "franchise_tags": ["1"], "venue_tags": ["2"],
            "day_label": ["Day 1"],
            "day_label_en": ["Day 1"],
            "day_label_zh": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_doors_at": [""],
            # The venue now lives on the leg: the concert's VENUE tags are
            # rolled up from its legs, so a concert-level venue_tags value no
            # leg backs would be derived straight back off again.
            "day_venue_tag_id": ["2"],
            "round_label": ["R1"], "round_kind": ["lottery_round"],
            "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
            "round_results_at": [""], "round_payment_at": [""], "round_label_en": ["R1"],
            "round_label_zh": ["R1"],
            "round_url": [""], "round_notes": [""], "round_leg": [""],
        },
    )
    r = client.post("/concerts/hasunosora-5th/duplicate")
    assert r.status_code == 303
    location = r.headers["location"]
    assert location.endswith("/edit")
    new_event_id = location.removeprefix("/concerts/").removesuffix("/edit")
    assert new_event_id != "hasunosora-5th"

    async with client.db() as s:
        clone = (await s.execute(
            select(Concert).where(Concert.event_id == new_event_id)
        )).scalar_one()
        await s.refresh(clone, ["days", "rounds", "tags"])

    assert clone.title == "Hasunosora 5th (copy)"
    assert clone.kind.value == "concert"
    assert clone.organizer == "LustQueen"
    assert clone.categories == "concert, tour"
    # The source's VENUE tag stays behind: it is derived from legs the clone
    # does not have. Keeping it would contradict the empty leg set from birth,
    # and the editor's first save (duplicate lands on /edit) would strip it.
    assert {t.name for t in clone.tags} == {"Hasunosora"}
    assert clone.days == []
    assert clone.rounds == []


async def test_duplicate_slugs_the_new_event_id_from_the_english_title(client):
    """The §A slug change reaches the duplicate route too, and this is the one
    place it is observable end to end: the source's Japanese title alone
    slugifies to the "concert" fallback, so a clone of a JP-titled concert
    used to land on /concerts/concert. It now lands on the English one."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={
        "title": "蓮ノ空女学院スクールアイドルクラブ 6thライブ",
        "title_en": "Hasunosora 6th Live",
        "title_zh": "莲之空6th演唱会",
        "event_id": "hasunosora-6th",
    })
    r = client.post("/concerts/hasunosora-6th/duplicate")
    assert r.status_code == 303
    assert r.headers["location"] == "/concerts/hasunosora-6th-live-copy/edit"


async def test_duplicate_ignores_a_whitespace_only_english_title(client):
    """The route decided "has a title_en" by truthiness while
    generate_event_id decides it by .strip(), so a whitespace title_en sent
    "   copy" down -- which strips back to "copy" and minted /concerts/copy
    instead of falling back to the Japanese title. The two now agree.

    Written straight to the DB: require_variants makes the trio mandatory at
    every create boundary, and the duplicate route exists precisely to clone
    rows that predate that rule."""
    login_as(client, EDITOR_ID, "reiji")
    async with client.db() as s:
        s.add(Concert(
            title="Hasunosora 6th Live", title_en="   ",
            event_id="legacy", created_by=EDITOR_ID,
        ))
        await s.commit()

    r = client.post("/concerts/legacy/duplicate")
    assert r.status_code == 303
    # "   copy" strips to "copy" -- the whole slug the reader would have got.
    assert r.headers["location"] == "/concerts/hasunosora-6th-live-copy/edit"


def test_duplicate_requires_editor(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title_en": "C", "title_zh": "C", "title": "C", "event_id": "c"})

    login_as(client, VIEWER_ID, "viewer")
    assert client.post("/concerts/c/duplicate").status_code == 403


async def test_duplicate_pruned_group_members_stay_pruned(client):
    """A GROUP tag whose non-performing members were pruned on the source
    concert must carry over the same pruned artist set, not re-expand to
    the group's current (possibly larger) membership."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={
        "name_en": "Liella", "name_zh": "Liella", "name": "Liella", "kind": "group",
    })
    client.post("/tags", data={
        "name_en": "Kaho", "name_zh": "Kaho", "name": "Kaho", "kind": "artist",
    })
    client.post("/tags", data={
        "name_en": "Sayaka", "name_zh": "Sayaka", "name": "Sayaka", "kind": "artist",
    })
    client.post("/tags/1/members", data={"member_tag_id": "2"})
    client.post("/tags/1/members", data={"member_tag_id": "3"})
    client.post(
        "/concerts",
        data={"title_en": "C", "title_zh": "C",
            "title": "C", "event_id": "c",
            "group_tags": ["1"], "artist_tags": ["2"],  # Sayaka pruned
        },
    )
    r = client.post("/concerts/c/duplicate")
    location = r.headers["location"]
    new_event_id = location.removeprefix("/concerts/").removesuffix("/edit")

    async with client.db() as s:
        clone = (await s.execute(
            select(Concert).where(Concert.event_id == new_event_id)
        )).scalar_one()
        await s.refresh(clone, ["tags"])
    assert {t.name for t in clone.tags} == {"Liella", "Kaho"}  # Sayaka stays pruned


async def test_duplicate_generates_a_fresh_unique_event_id_on_repeat(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title_en": "C", "title_zh": "C", "title": "C", "event_id": "c"})
    r1 = client.post("/concerts/c/duplicate")
    r2 = client.post("/concerts/c/duplicate")
    id1 = r1.headers["location"].removeprefix("/concerts/").removesuffix("/edit")
    id2 = r2.headers["location"].removeprefix("/concerts/").removesuffix("/edit")
    assert id1 != id2


def test_duplicate_button_shown_on_edit_page(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title_en": "C", "title_zh": "C", "title": "C", "event_id": "c"})
    r = client.get("/concerts/c/edit")
    assert '/concerts/c/duplicate' in r.text


# ── Concert edit history ─────────────────────────────────────────────────


async def test_editing_concert_over_http_records_an_audit_row(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={
        "title_en": "C", "title_zh": "C", "title": "C", "event_id": "c", "organizer": "Old Org",
    })
    client.post(
        "/concerts/c/edit",
        data={
            "title": "C (renamed)", "title_en": "C", "title_zh": "C",
            "event_id": "c", "organizer": "New Org",
        },
    )
    async with client.db() as s:
        audits = (await s.execute(select(ConcertAudit))).scalars().all()
    assert len(audits) == 1
    fields = {c["field"] for c in audits[0].changes}
    assert fields == {"title", "organizer"}
    assert audits[0].edited_by == EDITOR_ID


async def test_resubmitting_the_edit_form_unchanged_records_nothing(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title_en": "C", "title_zh": "C", "title": "C", "event_id": "c"})
    client.post("/concerts/c/edit", data={  # no-op resubmit
        "title": "C", "title_en": "C", "title_zh": "C", "event_id": "c",
    })
    async with client.db() as s:
        assert (await s.execute(select(ConcertAudit))).scalars().all() == []


def test_edit_history_shown_to_editors_not_viewers(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title_en": "C", "title_zh": "C", "title": "C", "event_id": "c"})
    client.post("/concerts/c/edit", data={"title": "C (renamed)", "event_id": "c"})

    r = client.get("/concerts/c")
    assert "Edit history" in r.text
    assert "C (renamed)" in r.text  # the new title appears in the diff line

    login_as(client, VIEWER_ID, "viewer")
    r = client.get("/concerts/c")
    assert "Edit history" not in r.text


def test_edit_history_absent_when_concert_never_edited(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title_en": "C", "title_zh": "C", "title": "C", "event_id": "c"})
    r = client.get("/concerts/c")
    assert "Edit history" not in r.text


async def test_deleting_concert_cascades_its_audit_log_over_http(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title_en": "C", "title_zh": "C", "title": "C", "event_id": "c"})
    client.post("/concerts/c/edit", data={"title": "C (renamed)", "event_id": "c"})
    client.post("/concerts/c/delete")
    async with client.db() as s:
        assert (await s.execute(select(ConcertAudit))).scalars().all() == []


# ── Cancelled legs ────────────────────────────────────────────────────────


async def test_edit_page_can_mark_a_day_cancelled(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={"title_en": "C", "title_zh": "C",
            "title": "C", "event_id": "c",
            "day_label": ["Day 1"],
            "day_label_en": ["Day 1"],
            "day_label_zh": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_doors_at": [""],
        },
    )
    async with client.db() as s:
        day_id = (await s.execute(select(ConcertDay))).scalar_one().id

    r = client.post(
        "/concerts/c/edit",
        data={
            "title": "C", "event_id": "c",
            "day_id": [str(day_id)], "day_label": ["Day 1"],
            "day_label_en": [""],
            "day_label_zh": [""], "day_starts_at": ["2099-08-01T18:00"],
            "day_doors_at": [""],
            "day_cancelled": ["true"],
        },
    )
    assert r.status_code == 303
    async with client.db() as s:
        day = await s.get(ConcertDay, day_id)
        assert day.cancelled is True

    # flipping it back to Scheduled un-cancels it
    client.post(
        "/concerts/c/edit",
        data={
            "title": "C", "event_id": "c",
            "day_id": [str(day_id)], "day_label": ["Day 1"],
            "day_label_en": [""],
            "day_label_zh": [""], "day_starts_at": ["2099-08-01T18:00"],
            "day_doors_at": [""],
            "day_cancelled": ["false"],
        },
    )
    async with client.db() as s:
        day = await s.get(ConcertDay, day_id)
        assert day.cancelled is False


async def test_edit_page_defaults_new_day_rows_to_not_cancelled(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={"title_en": "C", "title_zh": "C",
            "title": "C", "event_id": "c",
            "day_label": ["Day 1"],
            "day_label_en": ["Day 1"],
            "day_label_zh": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_doors_at": [""],
            "day_cancelled": ["false"],
        },
    )
    async with client.db() as s:
        day = (await s.execute(select(ConcertDay))).scalar_one()
        assert day.cancelled is False


async def test_edit_page_prefills_the_cancelled_toggle(client):
    """Cancelled is a toggle on the leg itself, not a <select> among its
    fields; the hidden input behind it carries the state."""
    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={"title_en": "C", "title_zh": "C",
            "title": "C", "event_id": "c",
            "day_label": ["Day 1"],
            "day_label_en": ["Day 1"],
            "day_label_zh": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_doors_at": [""],
        },
    )
    async with client.db() as s:
        day_id = (await s.execute(select(ConcertDay))).scalar_one().id
    client.post(
        "/concerts/c/edit",
        data={
            "title": "C", "event_id": "c",
            "day_id": [str(day_id)], "day_label": ["Day 1"],
            "day_label_en": [""],
            "day_label_zh": [""], "day_starts_at": ["2099-08-01T18:00"],
            "day_doors_at": [""],
            "day_cancelled": ["true"],
        },
    )
    r = client.get("/concerts/c/edit")
    assert r.status_code == 200
    assert 'name="day_cancelled" value="true"' in r.text
    assert 'data-cancel-toggle' in r.text
    assert 'aria-pressed="true"' in r.text


async def test_cancelling_the_only_leg_clears_its_reminders_and_notifies(client):
    from app.db.models import Notification

    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={"title_en": "C", "title_zh": "C",
            "title": "C", "event_id": "c",
            "day_label": ["Day 1"],
            "day_label_en": ["Day 1"],
            "day_label_zh": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_doors_at": [""],
        },
    )
    client.post("/concerts/c/rules", data={"anchor": "event_start", "days_before": 7})
    async with client.db() as s:
        assert len((await s.execute(select(ReminderQueue))).scalars().all()) == 1
        day_id = (await s.execute(select(ConcertDay))).scalar_one().id

    client.post(
        "/concerts/c/edit",
        data={
            "title": "C", "event_id": "c",
            "day_id": [str(day_id)], "day_label": ["Day 1"],
            "day_label_en": [""],
            "day_label_zh": [""], "day_starts_at": ["2099-08-01T18:00"],
            "day_doors_at": [""],
            "day_cancelled": ["true"],
        },
    )
    async with client.db() as s:
        assert (await s.execute(select(ReminderQueue))).scalars().all() == []
        notes = (await s.execute(select(Notification))).scalars().all()
        assert len(notes) == 1
        assert notes[0].kind == "leg_cancelled"
        assert notes[0].user_id == EDITOR_ID


async def test_resubmitting_edit_with_same_cancelled_state_does_not_renotify(client):
    from app.db.models import Notification

    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={"title_en": "C", "title_zh": "C",
            "title": "C", "event_id": "c",
            "day_label": ["Day 1"],
            "day_label_en": ["Day 1"],
            "day_label_zh": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_doors_at": [""],
            "day_cancelled": ["true"],
        },
    )
    async with client.db() as s:
        day_id = (await s.execute(select(ConcertDay))).scalar_one().id
    client.post(
        "/concerts/c/edit",
        data={
            "title": "C", "event_id": "c",
            "day_id": [str(day_id)], "day_label": ["Day 1"],
            "day_label_en": [""],
            "day_label_zh": [""], "day_starts_at": ["2099-08-01T18:00"],
            "day_doors_at": [""],
            "day_cancelled": ["true"],  # already cancelled -- resubmitting the same state
        },
    )
    async with client.db() as s:
        assert (await s.execute(select(Notification))).scalars().all() == []


# ── the tag pipeline sees the legs THIS submit writes ────────────────────
#
# `handle_newly_tagged` asks all_legs_cancelled (cleanup batch §C, owner
# ruling 1): a dead concert notifies nobody and applies nothing. The edit
# route attaches its tags near the top and reconciles its LEGS further down,
# so asking the question at the attach site answers it about the concert as
# it arrived, not as it is being saved -- wrong in both directions, and the
# notice has no re-announce path, so a suppressed one is lost for good.

FOLLOWER_ID = 9100


async def _follow(client, tag_id: int, user_id: int = FOLLOWER_ID):
    """A notify-only follower: NO preset, so handle_newly_tagged's "already
    has rules on this concert" guard can never mask a missing notice."""
    from app.db.models import TagSubscription, User

    async with client.db() as s:
        s.add(User(discord_id=user_id, username="fan"))
        await s.flush()
        s.add(TagSubscription(user_id=user_id, tag_id=tag_id, notify=True))
        await s.commit()


async def _notices(client, user_id: int = FOLLOWER_ID):
    from app.db.models import Notification

    async with client.db() as s:
        return (await s.execute(
            select(Notification).where(Notification.user_id == user_id)
        )).scalars().all()


def _create_one_leg(client, *, cancelled: str):
    return client.post(
        "/concerts",
        data={"title_en": "C", "title_zh": "C",
            "title": "C", "event_id": "c",
            "day_label": ["Day 1"],
            "day_label_en": ["Day 1"],
            "day_label_zh": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_doors_at": [""], "day_cancelled": [cancelled],
        },
    )


def _edit_one_leg(client, day_id: int, *, cancelled: str, artist_tags: list[str]):
    return client.post(
        "/concerts/c/edit",
        data={
            "title": "C", "event_id": "c",
            "day_id": [str(day_id)], "day_label": ["Day 1"],
            "day_label_en": [""],
            "day_label_zh": [""], "day_starts_at": ["2099-08-01T18:00"],
            "day_doors_at": [""], "day_cancelled": [cancelled],
            "artist_tags": artist_tags,
        },
    )


async def test_uncancelling_a_leg_while_adding_a_tag_still_announces(client):
    """The revival case. The concert arrives dead and leaves alive, so the
    notice is owed -- and there is no re-announce path, so asking before the
    legs are written loses it permanently."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={
        "name": "Hasunosora", "name_en": "Hasunosora", "name_zh": "Hasunosora",
        "kind": "artist",
    })  # id 1
    _create_one_leg(client, cancelled="true")
    await _follow(client, 1)
    async with client.db() as s:
        day_id = (await s.execute(select(ConcertDay))).scalar_one().id

    assert _edit_one_leg(
        client, day_id, cancelled="false", artist_tags=["1"]
    ).status_code == 303

    notes = await _notices(client)
    assert [n.kind for n in notes] == ["new_event"]


async def test_cancelling_the_last_leg_while_adding_a_tag_announces_nothing(client):
    """The mirror, and the half the ruling is about: the concert arrives alive
    and leaves dead, so nobody is owed a 🆕 notice with an "Apply here" button
    on a show that is not happening."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={
        "name": "Hasunosora", "name_en": "Hasunosora", "name_zh": "Hasunosora",
        "kind": "artist",
    })  # id 1
    _create_one_leg(client, cancelled="false")
    await _follow(client, 1)
    async with client.db() as s:
        day_id = (await s.execute(select(ConcertDay))).scalar_one().id

    assert _edit_one_leg(
        client, day_id, cancelled="true", artist_tags=["1"]
    ).status_code == 303

    assert await _notices(client) == []


async def test_detail_page_shows_cancelled_badge_on_cancelled_leg_only(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={"title_en": "C", "title_zh": "C",
            "title": "C", "event_id": "c",
            "day_label": ["Day 1", "Day 2"],
            "day_label_en": ["Day 1", "Day 2"],
            "day_label_zh": ["Day 1", "Day 2"],
            "day_starts_at": ["2099-08-01T18:00", "2099-08-02T18:00"],
            "day_doors_at": ["", ""],
        },
    )
    async with client.db() as s:
        days = sorted(
            (await s.execute(select(ConcertDay))).scalars(), key=lambda d: d.label
        )
        day1_id, day2_id = days[0].id, days[1].id

    client.post(
        "/concerts/c/edit",
        data={
            "title": "C", "event_id": "c",
            "day_id": [str(day1_id), str(day2_id)],
            "day_label": ["Day 1", "Day 2"],
            "day_label_en": ["", ""],
            "day_label_zh": ["", ""],
            "day_starts_at": ["2099-08-01T18:00", "2099-08-02T18:00"],
            "day_doors_at": ["", ""],
            "day_cancelled": ["true", "false"],
        },
    )
    r = client.get("/concerts/c")
    assert r.status_code == 200
    day1_section = r.text[r.text.index('leg-heading">Day 1'):r.text.index('leg-heading">Day 2')]
    day2_section = r.text[r.text.index('leg-heading">Day 2'):]
    assert "Cancelled" in day1_section
    assert "Cancelled" not in day2_section


async def test_round_tied_to_cancelled_day_still_renders_not_vanishes(client):
    """Regression guard for the exact bug this whole design was built to
    avoid (see the spec's "bug this design has to avoid" section):
    concert_round_rows() (db/service.py) groups round.applies_to ids against
    concert.days. If a cancelled day were ever DELETED rather than just
    flagged, a round referencing it would silently disappear from the page
    entirely -- not fall back to "General", just vanish. Marking (not
    deleting) the day keeps it in concert.days, so the round still resolves
    correctly under that day's now-visibly-cancelled heading."""
    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={"title_en": "C", "title_zh": "C",
            "title": "C", "event_id": "c",
            "day_key": ["new-a"],
            "day_label": ["Day 1"],
            "day_label_en": ["Day 1"],
            "day_label_zh": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_doors_at": [""],
            "round_label": ["R1"], "round_kind": ["lottery_round"],
            "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
            "round_results_at": [""], "round_payment_at": [""], "round_label_en": ["R1"],
            "round_label_zh": ["R1"],
            "round_url": [""], "round_notes": [""], "round_legs": ["new-a"],
        },
    )
    async with client.db() as s:
        day_id = (await s.execute(select(ConcertDay))).scalar_one().id
        round_id = (await s.execute(select(Round))).scalar_one().id

    client.post(
        "/concerts/c/edit",
        data={
            "title": "C", "event_id": "c",
            "day_id": [str(day_id)], "day_label": ["Day 1"],
            "day_label_en": [""],
            "day_label_zh": [""], "day_starts_at": ["2099-08-01T18:00"],
            "day_doors_at": [""],
            "day_cancelled": ["true"],
            "round_id": [str(round_id)], "round_label": ["R1"], "round_kind": ["lottery_round"],
            "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
            "round_results_at": [""], "round_payment_at": [""], "round_label_en": [""],
            "round_label_zh": [""],
            "round_url": [""], "round_notes": [""], "round_legs": [str(day_id)],
        },
    )
    r = client.get("/concerts/c")
    assert r.status_code == 200
    assert "R1" in r.text  # still rendered -- not silently dropped
    assert "No rounds yet for this performance." not in r.text


# `test_concert_date_range_excludes_cancelled_legs` lived here. It pinned the
# concert header's single date-range summary, which is gone: a tour's legs
# have different dates and different cities, so ANY single header summary
# disagrees with the sections under it. Dates now live on the leg, cancelled
# legs included (dimmed and badged, never hidden -- invariant 2). The
# replacement contract is in tests/test_concert_page.py:
# `test_the_header_carries_no_date_range_and_no_single_venue` and
# `test_a_cancelled_leg_is_dimmed_but_keeps_its_own_date_and_rounds`.
