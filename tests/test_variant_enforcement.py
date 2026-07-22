"""The create-boundary backstop: a half-translated field is a 422.

The browser-side check normally catches this and paints an inline error
without submitting, so these routes are the backstop -- and for a caller
with JS disabled the 422 detail string is the ENTIRE error UX, which is
why every test here asserts on the message and not just the status code.

The deliberate asymmetry -- the EDIT routes stay open, so a legacy
half-translated record can still be saved -- is pinned at the bottom.
"""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base
from app.db.session import get_session
from app.web import auth
from app.web.app import create_app

EDITOR_ID = 42


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
    """Signed-in-editor TestClient -- same fixture shape as
    tests/test_round_phrases.py's `editor_client`."""
    monkeypatch.setattr(settings, "editor_whitelist", str(EDITOR_ID))
    app = create_app()

    async def override_session():
        async with db() as s:
            yield s

    app.dependency_overrides[get_session] = override_session

    async def fake_exchange(code):
        return "tok"

    async def fake_identity(token):
        return {"id": str(EDITOR_ID), "username": "ed", "global_name": "ed", "avatar": None}

    monkeypatch.setattr(auth, "exchange_code", fake_exchange)
    monkeypatch.setattr(auth, "fetch_identity", fake_identity)

    c = TestClient(app, follow_redirects=False)
    r = c.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    c.get(f"/auth/callback?code=x&state={state}")
    c.db = db
    return c


def _minimal_concert(event_id: str, *, full_title: bool = False) -> dict:
    """The smallest payload `POST /concerts` accepts -- no legs, no rounds."""
    data: dict = {"event_id": event_id, "title": "ラブライブ"}
    if full_title:
        data |= {"title_en": "Love Live", "title_zh": "LoveLive"}
    return data


def _leg(label: str, label_en: str, label_zh: str) -> dict:
    """One fully-specified leg row (every array create_concert zips strictly)."""
    return {
        "day_label": [label], "day_label_en": [label_en], "day_label_zh": [label_zh],
        "day_starts_at": ["2026-09-01T18:00"], "day_doors_at": [""],
    }


def _round(label: str, label_en: str, label_zh: str) -> dict:
    """One fully-specified round row."""
    return {
        "round_label": [label], "round_label_en": [label_en], "round_label_zh": [label_zh],
        "round_kind": ["lottery_round"], "round_closes_at": ["2026-08-01T18:00"],
        "round_opens_at": [""], "round_results_at": [""], "round_payment_at": [""],
        "round_url": [""], "round_notes": [""],
    }


# --- concert create -------------------------------------------------------

async def test_creating_a_concert_with_a_half_translated_title_is_422(client):
    r = client.post("/concerts", data={
        **_minimal_concert("half-title"),
        "title_en": "Love Live", "title_zh": "",
    })
    assert r.status_code == 422
    assert "中文" in r.json()["detail"], r.json()
    assert "title" in r.json()["detail"].lower(), r.json()


async def test_creating_a_concert_with_no_title_translations_is_422(client):
    """title is mandatory: blank in all three is not the escape hatch it is
    for an optional field."""
    r = client.post("/concerts", data={
        **_minimal_concert("no-title-tr"), "title_en": "", "title_zh": "",
    })
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "English" in detail and "中文" in detail, detail


async def test_creating_a_concert_with_an_untouched_optional_field_is_fine(client):
    """notes left blank in all three is not an error."""
    r = client.post("/concerts", data={
        **_minimal_concert("blank-notes", full_title=True),
        "notes": "", "notes_en": "", "notes_zh": "",
    })
    assert r.status_code in (200, 303), r.text


async def test_a_half_translated_notes_field_is_422(client):
    r = client.post("/concerts", data={
        **_minimal_concert("half-notes", full_title=True),
        "notes": "備考", "notes_en": "", "notes_zh": "",
    })
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "notes" in detail.lower() and "English" in detail and "中文" in detail, detail


async def test_a_half_translated_leg_label_is_422(client):
    r = client.post("/concerts", data={
        **_minimal_concert("half-leg", full_title=True),
        **_leg("1日目", "Day 1", ""),
    })
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "中文" in detail, detail
    assert "1" in detail, f"the message must name the row: {detail}"


async def test_a_half_translated_round_label_is_422(client):
    r = client.post("/concerts", data={
        **_minimal_concert("half-round", full_title=True),
        **_leg("1日目", "Day 1", "第一天"),
        **_round("1次先行抽選", "", "第一轮先行"),
    })
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "English" in detail, detail
    assert "round" in detail.lower(), detail


async def test_a_fully_translated_concert_with_legs_and_rounds_saves(client):
    r = client.post("/concerts", data={
        **_minimal_concert("all-good", full_title=True),
        **_leg("1日目", "Day 1", "第一天"),
        **_round("1次先行抽選", "1st-round lottery", "第一轮先行"),
    })
    assert r.status_code in (200, 303), r.text


async def test_blank_labels_everywhere_are_still_fine(client):
    """Labels are optional -- a leg with no label at all still saves."""
    r = client.post("/concerts", data={
        **_minimal_concert("blank-labels", full_title=True),
        **_leg("", "", ""),
        **_round("", "", ""),
    })
    assert r.status_code in (200, 303), r.text


# --- tag create -----------------------------------------------------------

async def test_creating_a_tag_requires_all_three_names(client):
    r = client.post("/tags", data={
        "name": "蓮ノ空", "name_en": "Hasunosora", "name_zh": "", "kind": "group",
    })
    assert r.status_code == 422
    assert "中文" in r.json()["detail"], r.json()


async def test_creating_a_fully_translated_tag_is_fine(client):
    r = client.post("/tags", data={
        "name": "蓮ノ空", "name_en": "Hasunosora", "name_zh": "莲之空", "kind": "group",
    })
    assert r.status_code in (200, 303), r.text


async def test_a_quick_venue_with_a_half_translated_city_is_422(client):
    r = client.post("/tags/venue/quick", data={
        "name": "Zepp羽田", "name_en": "Zepp Haneda", "name_zh": "Zepp羽田",
        "city": "東京", "city_en": "Tokyo", "city_zh": "",
    })
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "city" in detail.lower() and "中文" in detail, detail


async def test_a_quick_venue_with_no_city_at_all_is_fine(client):
    """city is all-or-nothing, not mandatory."""
    r = client.post("/tags/venue/quick", data={
        "name": "Zepp羽田", "name_en": "Zepp Haneda", "name_zh": "Zepp羽田",
        "city": "", "city_en": "", "city_zh": "",
    })
    assert r.status_code == 200, r.text


# --- the deliberate asymmetry --------------------------------------------

async def test_editing_a_legacy_half_translated_record_is_still_allowed(client):
    """The EDIT routes deliberately do NOT enforce this.

    The phase ships without a backfill, so most existing rows are
    half-translated or untranslated. Enforcing on edit would wall the owner
    out of their own data -- a later task surfaces the gap on the page
    instead. If this test ever fails because someone "helpfully" added
    require_variants to edit_concert, that is the regression, not this test.
    """
    created = client.post("/concerts", data=_minimal_concert("legacy", full_title=True))
    assert created.status_code in (200, 303), created.text

    r = client.post("/concerts/legacy/edit", data={
        "event_id": "legacy",
        "title": "ラブライブ", "title_en": "Love Live", "title_zh": "",
        "notes": "備考", "notes_en": "", "notes_zh": "",
    })
    assert r.status_code in (200, 303), r.text
