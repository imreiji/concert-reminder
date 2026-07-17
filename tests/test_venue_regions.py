"""Venue tags (region + location link), the round table's past-marking,
.ics export, and the sidebar's region filter.

Pure-function helpers (find_venue_tag / is_round_past / is_day_past) are
exercised directly against plain constructed ORM objects, no DB needed.
Everything that touches routes/templates goes through the same
client+db+login_as fixture pattern as test_crud.py.
"""

from datetime import UTC, datetime

import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base, ConcertDay, Round, Tag
from app.db.session import get_session
from app.domain.types import RoundKind, TagKind
from app.web import auth
from app.web.app import create_app, region_sidebar_links
from app.web.routes.concerts import find_venue_tag, is_day_past, is_round_past

EDITOR_ID, VIEWER_ID = 42, 777


def dt(month: int, day: int, hour: int = 12) -> datetime:
    return datetime(2026, month, day, hour, tzinfo=UTC)


NOW = dt(6, 15)


# ── find_venue_tag ────────────────────────────────────────────────────────


def test_find_venue_tag_matches_case_insensitively():
    venue = Tag(name="K Arena Yokohama", kind=TagKind.VENUE, created_by=EDITOR_ID)
    assert find_venue_tag([venue], "k arena yokohama") is venue
    assert find_venue_tag([venue], "K ARENA YOKOHAMA") is venue


def test_find_venue_tag_no_match_returns_none():
    venue = Tag(name="K Arena Yokohama", kind=TagKind.VENUE, created_by=EDITOR_ID)
    assert find_venue_tag([venue], "Some Other Hall") is None


def test_find_venue_tag_blank_or_none_returns_none():
    venue = Tag(name="K Arena Yokohama", kind=TagKind.VENUE, created_by=EDITOR_ID)
    assert find_venue_tag([venue], "") is None
    assert find_venue_tag([venue], None) is None


# ── is_round_past / is_day_past ──────────────────────────────────────────


def test_round_with_all_timestamps_past_is_past():
    r = Round(concert_id=1, kind=RoundKind.OTHER, label="R", closes_at_utc=dt(1, 1))
    assert is_round_past(r, NOW) is True


def test_round_with_any_future_timestamp_is_not_past():
    r = Round(
        concert_id=1, kind=RoundKind.OTHER, label="R",
        opens_at_utc=dt(1, 1), closes_at_utc=dt(12, 1),
    )
    assert is_round_past(r, NOW) is False


def test_round_with_no_timestamps_is_not_past():
    r = Round(concert_id=1, kind=RoundKind.OTHER, label="R")
    assert is_round_past(r, NOW) is False


def test_day_past_when_starts_at_before_now():
    d = ConcertDay(concert_id=1, label="Day 1", starts_at_utc=dt(1, 1))
    assert is_day_past(d, NOW) is True
    d2 = ConcertDay(concert_id=1, label="Day 2", starts_at_utc=dt(12, 1))
    assert is_day_past(d2, NOW) is False


# ── region_sidebar_links ─────────────────────────────────────────────────


def test_region_sidebar_links_groups_by_region_with_other_bucket():
    kanto = Tag(id=1, name="K Arena Yokohama", kind=TagKind.VENUE, region="Kanto", created_by=1)
    kansai = Tag(id=2, name="Osaka-jo Hall", kind=TagKind.VENUE, region="Kansai", created_by=1)
    unset = Tag(id=3, name="Mystery Hall", kind=TagKind.VENUE, region=None, created_by=1)
    links = region_sidebar_links([kanto, kansai, unset], [], "event")
    names = [link_["name"] for link_ in links]
    assert "Kanto" in names and "Kansai" in names
    assert names[-1] == "Other"  # unset region sorts last


def test_region_sidebar_links_toggle_selects_and_deselects_whole_group():
    kanto1 = Tag(id=1, name="Hall A", kind=TagKind.VENUE, region="Kanto", created_by=1)
    kanto2 = Tag(id=2, name="Hall B", kind=TagKind.VENUE, region="Kanto", created_by=1)
    links = region_sidebar_links([kanto1, kanto2], [], "event")
    (kanto_link,) = links
    assert kanto_link["active"] is False
    assert "tag=1" in kanto_link["href"] and "tag=2" in kanto_link["href"]

    active_links = region_sidebar_links([kanto1, kanto2], [1, 2], "event")
    (kanto_active,) = active_links
    assert kanto_active["active"] is True
    assert "tag=" not in kanto_active["href"]  # deselect link drops both ids


# ── HTTP-level: tag edit, .ics export, region filter, past-marking ───────


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture()
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


async def test_tag_edit_persists_region_and_location_url(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "K Arena Yokohama", "kind": "venue"})
    r = client.post(
        "/tags/1/edit",
        data={"location_url": "https://maps.example/k-arena", "region": "Kanto"},
    )
    assert r.status_code == 303
    async with client.db() as s:
        tag = await s.get(Tag, 1)
    assert tag.region == "Kanto"
    assert tag.location_url == "https://maps.example/k-arena"


async def test_create_tag_accepts_region_and_location_url(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/tags",
        data={
            "name": "Osaka-jo Hall", "kind": "venue",
            "location_url": "https://maps.example/osaka-jo", "region": "Kansai",
        },
    )
    async with client.db() as s:
        tag = (await s.execute(select(Tag))).scalar_one()
    assert tag.region == "Kansai"
    assert tag.location_url == "https://maps.example/osaka-jo"


async def test_round_ics_downloads_valid_vevent_for_future_round(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "C"})
    client.post(
        "/concerts/1/rounds",
        data={"label": "R1", "kind": "lottery_round", "closes_at": "2099-06-25T23:59"},
    )
    r = client.get("/rounds/1/ics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/calendar")
    assert "attachment" in r.headers["content-disposition"]
    assert "BEGIN:VEVENT" in r.text
    assert "SUMMARY:C — R1" in r.text
    assert "DTSTART:20990625T145900Z" in r.text  # 2099-06-25 23:59 JST -> UTC


async def test_round_ics_requires_login(client):
    client.post("/concerts", data={"title": "C"})
    assert client.get("/rounds/1/ics").status_code == 401


async def test_past_round_has_no_ics_link_and_is_marked_past(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "C"})
    client.post(
        "/concerts/1/rounds",
        data={"label": "Old round", "kind": "lottery_round", "closes_at": "2000-01-01T00:00"},
    )
    client.post(
        "/concerts/1/rounds",
        data={"label": "Future round", "kind": "lottery_round", "closes_at": "2099-06-25T23:59"},
    )
    r = client.get("/concerts/1")
    assert r.status_code == 200
    assert "/rounds/2/ics" in r.text  # future round: exportable
    assert "/rounds/1/ics" not in r.text  # past round: no export link
    assert 'round-row past' in r.text


async def test_past_day_marked_past(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "C"})
    client.post("/concerts/1/days", data={"label": "Old day", "starts_at": "2000-01-01T00:00"})
    r = client.get("/concerts/1")
    assert r.status_code == 200
    assert 'li class="past"' in r.text


async def test_leg_heading_links_to_matching_venue_tag(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/tags",
        data={
            "name": "K Arena Yokohama", "kind": "venue",
            "location_url": "https://maps.example/k-arena", "region": "Kanto",
        },
    )
    client.post("/concerts", data={"title": "C"})
    client.post(
        "/concerts/1/days",
        data={
            "label": "Day 1", "starts_at": "2099-08-01T18:00",
            "venue": "K Arena Yokohama",
        },
    )
    r = client.get("/concerts/1")
    assert r.status_code == 200
    assert 'href="https://maps.example/k-arena"' in r.text
    assert "(Kanto)" in r.text


async def test_region_filter_selects_all_venues_in_region(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/tags",
        data={"name": "Hall A", "kind": "venue", "region": "Kanto"},
    )
    client.post(
        "/tags",
        data={"name": "Hall B", "kind": "venue", "region": "Kanto"},
    )
    client.post("/concerts", data={"title": "At Hall A", "venue_tags": ["1"]})
    client.post("/concerts", data={"title": "At Hall B", "venue_tags": ["2"]})
    client.post("/concerts", data={"title": "Untagged"})

    r = client.get("/")
    assert "Regions" in r.text
    assert "Kanto" in r.text

    r = client.get("/?sort=event&tag=1&tag=2")
    assert "At Hall A" in r.text
    assert "At Hall B" in r.text
    assert "Untagged" not in r.text
