"""Venue tags (region + location link), the round table's past-marking,
and the sidebar's region filter.

The pure-function helper (region_sidebar_links) is exercised directly
against plain constructed ORM objects, no DB needed.
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
from app.db.models import Base, Tag
from app.db.session import get_session
from app.domain.types import TagKind
from app.web import auth
from app.web.app import create_app
from app.web.routes.discover import region_sidebar_links

EDITOR_ID, VIEWER_ID = 42, 777


def dt(month: int, day: int, hour: int = 12) -> datetime:
    return datetime(2026, month, day, hour, tzinfo=UTC)


NOW = dt(6, 15)


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


# ── HTTP-level: tag edit, region filter, past-marking ─────────────────────


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
    client.post("/tags", data={
        "name_en": "K Arena Yokohama", "name_zh": "K Arena Yokohama", "name": "K Arena Yokohama",
        "kind": "venue",
    })
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
        data={"name_en": "Osaka-jo Hall", "name_zh": "Osaka-jo Hall",
            "name": "Osaka-jo Hall", "kind": "venue",
            "location_url": "https://maps.example/osaka-jo", "region": "Kansai",
        },
    )
    async with client.db() as s:
        tag = (await s.execute(select(Tag))).scalar_one()
    assert tag.region == "Kansai"
    assert tag.location_url == "https://maps.example/osaka-jo"


async def test_past_round_is_marked_past(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={"title_en": "C", "title_zh": "C",
            "title": "C", "event_id": "c",
            "round_label": ["Old round", "Future round"],
            "round_kind": ["lottery_round", "lottery_round"],
            "round_opens_at": ["", ""],
            "round_closes_at": ["2000-01-01T00:00", "2099-06-25T23:59"],
            "round_results_at": ["", ""], "round_payment_at": ["", ""],
            "round_label_en": ["Old round", "Future round"],
            "round_label_zh": ["Old round", "Future round"], "round_url": ["", ""],
            "round_notes": ["", ""],
            "round_leg": ["", ""],
        },
    )
    r = client.get("/concerts/c")
    assert r.status_code == 200
    assert 'rnd2 past' in r.text  # the round row, dimmed rather than hidden


async def test_past_day_marked_past(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/concerts",
        data={"title_en": "C", "title_zh": "C",
            "title": "C", "event_id": "c",
            "day_label": ["Old day"],
            "day_label_en": ["Old day"],
            "day_label_zh": ["Old day"], "day_starts_at": ["2000-01-01T00:00"],
            "day_doors_at": [""],
        },
    )
    r = client.get("/concerts/c")
    assert r.status_code == 200
    assert 'leg-heading past">Old day<' in r.text


async def test_leg_heading_links_to_its_venue_tag(client):
    """The leg's venue is a real FK (day_venue_tag_id), not free text matched
    by name -- so the heading's location link follows whichever tag the leg
    actually points at."""
    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/tags",
        data={"name_en": "K Arena Yokohama", "name_zh": "K Arena Yokohama",
            "name": "K Arena Yokohama", "kind": "venue",
            "location_url": "https://maps.example/k-arena", "region": "Kanto",
        },
    )
    client.post(
        "/concerts",
        data={"title_en": "C", "title_zh": "C",
            "title": "C", "event_id": "c",
            "day_label": ["Day 1"],
            "day_label_en": ["Day 1"],
            "day_label_zh": ["Day 1"], "day_starts_at": ["2099-08-01T18:00"],
            "day_venue_tag_id": ["1"], "day_doors_at": [""],
        },
    )
    r = client.get("/concerts/c")
    assert r.status_code == 200
    assert 'href="https://maps.example/k-arena"' in r.text
    assert "· Kanto" in r.text


async def test_region_filter_selects_all_venues_in_region(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post(
        "/tags",
        data={
            "name_en": "Hall A", "name_zh": "Hall A", "name": "Hall A", "kind": "venue",
            "region": "Kanto",
        },
    )
    client.post(
        "/tags",
        data={
            "name_en": "Hall B", "name_zh": "Hall B", "name": "Hall B", "kind": "venue",
            "region": "Kanto",
        },
    )
    client.post(
        "/concerts", data={
            "title_en": "At Hall A", "title_zh": "At Hall A", "title": "At Hall A",
            "event_id": "at-hall-a", "venue_tags": ["1"],
        }
    )
    client.post(
        "/concerts", data={
            "title_en": "At Hall B", "title_zh": "At Hall B", "title": "At Hall B",
            "event_id": "at-hall-b", "venue_tags": ["2"],
        }
    )
    client.post("/concerts", data={
        "title_en": "Untagged", "title_zh": "Untagged", "title": "Untagged", "event_id": "untagged",
    })

    r = client.get("/discover")
    assert "Regions" in r.text
    assert "Kanto" in r.text

    r = client.get("/discover?sort=event&tag=1&tag=2")
    assert "At Hall A" in r.text
    assert "At Hall B" in r.text
    assert "Untagged" in r.text  # still in the DOM for client-side filtering...
    untagged_tile = r.text[r.text.rindex('<a class="tile"', 0, r.text.index("Untagged")):]
    assert 'style="display:none"' in untagged_tile.split("</a>", 1)[0]  # ...just hidden
