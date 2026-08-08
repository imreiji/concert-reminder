"""The read endpoints, end to end through the router.

The paging assertion is the load-bearing one and is deliberately not "limit=N
returns N rows": that passes with a non-deterministic sort. Asserting that the
UNION of the pages equals the whole set with no repeats is what catches a
missing tiebreaker.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.models import Concert, ConcertDay, Round, Tag
from app.db.service import ensure_user, generate_api_token
from app.db.session import get_session
from app.domain.types import RoundKind, TagKind
from app.web.app import create_app

ADMIN = 4242
NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


@pytest.fixture()
def client(db):
    app = create_app()

    async def override_session():
        async with db() as s:
            yield s

    app.dependency_overrides[get_session] = override_session
    return TestClient(app, follow_redirects=False)


async def _mint(db, discord_id=ADMIN, name="reiji") -> str:
    async with db() as s:
        await ensure_user(s, discord_id, name)
        token = await generate_api_token(s, discord_id)
        await s.commit()
        return token


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _seed_concerts(db, n: int) -> None:
    """Every concert on ONE leg date, inserted in DESCENDING event_id order.

    Both halves are load-bearing. One date makes the primary sort key tie on
    every row, so only the tiebreaker can separate them. Reverse insertion is
    what makes that checkable: the rows are sorted in Python, and Python's sort
    is stable, so a key that stops at the leg date preserves whatever order the
    query returned -- which, seeded ascending, is already the right answer, and
    the assertion passes while testing nothing (verified: removing `event_id`
    from the sort key left every test in this file green until this was
    reversed).
    """
    async with db() as s:
        await ensure_user(s, ADMIN, "reiji")
        for i in reversed(range(n)):
            c = Concert(title=f"Live {i}", title_en=f"Live {i}",
                        event_id=f"live-{i}", created_by=ADMIN)
            s.add(c)
            await s.flush()
            s.add(ConcertDay(concert_id=c.id, label="Day 1", starts_at_utc=NOW))
        await s.commit()


async def test_concerts_list_returns_an_envelope(client, db):
    token = await _mint(db)
    await _seed_concerts(db, 3)
    body = client.get("/api/v1/concerts", headers=_auth(token)).json()
    assert body["total"] == 3
    assert body["limit"] == 200
    assert body["offset"] == 0
    assert {r["event_id"] for r in body["items"]} == {"live-0", "live-1", "live-2"}


async def test_paging_covers_every_row_exactly_once(client, db):
    """The assertion that catches a missing tiebreaker. Every seeded concert
    shares one leg date, so the sort key ties on all five rows -- without a
    unique tiebreaker the pages may overlap or drop a row."""
    token = await _mint(db)
    await _seed_concerts(db, 5)

    first = client.get("/api/v1/concerts?limit=2&offset=0", headers=_auth(token)).json()
    second = client.get("/api/v1/concerts?limit=2&offset=2", headers=_auth(token)).json()
    third = client.get("/api/v1/concerts?limit=2&offset=4", headers=_auth(token)).json()

    seen = [r["event_id"] for r in first["items"] + second["items"] + third["items"]]
    assert len(seen) == 5
    assert len(set(seen)) == 5, f"pages overlapped or dropped rows: {seen}"
    assert first["total"] == 5


async def test_the_list_sort_is_totally_ordered(client, db):
    """THE test that actually catches a missing tiebreaker on this stack.

    The union-of-pages assertion above cannot: rows are sorted in Python, so
    the order is deterministic within a process whatever the key is, and both
    pages therefore stay disjoint even with a tie-prone key. What breaks is the
    ORDER, and only when the underlying query's order disagrees with the
    tiebreaker -- which `_seed_concerts` arranges by inserting descending."""
    token = await _mint(db)
    await _seed_concerts(db, 5)
    body = client.get("/api/v1/concerts", headers=_auth(token)).json()
    ids = [r["event_id"] for r in body["items"]]
    assert ids == sorted(ids)


async def test_limit_over_the_cap_is_422(client, db):
    token = await _mint(db)
    assert client.get("/api/v1/concerts?limit=501", headers=_auth(token)).status_code == 422


async def test_search_matches_title(client, db):
    token = await _mint(db)
    await _seed_concerts(db, 3)
    body = client.get("/api/v1/concerts?q=live%202", headers=_auth(token)).json()
    assert [r["event_id"] for r in body["items"]] == ["live-2"]
    assert body["total"] == 1


async def _tag_one(db, event_id: str, **tag_fields) -> None:
    """Attach a freshly built tag to one seeded concert."""
    async with db() as s:
        tag = Tag(**tag_fields)
        s.add(tag)
        concert = (await s.execute(
            select(Concert).where(Concert.event_id == event_id).options(selectinload(Concert.tags))
        )).scalar_one()
        concert.tags.append(tag)
        await s.commit()


async def test_search_matches_a_tag_name(client, db):
    """The reason the haystack is `concert_search_text` and not a title LIKE:
    a search in any language must reach a tag's localized names."""
    token = await _mint(db)
    await _seed_concerts(db, 2)
    await _tag_one(db, "live-1", name="ラブライブ", name_en="Love Live",
                   kind=TagKind.FRANCHISE, slug="lovelive")
    body = client.get("/api/v1/concerts?q=love%20live", headers=_auth(token)).json()
    assert [r["event_id"] for r in body["items"]] == ["live-1"]
    assert body["items"][0]["tag_handles"] == ["lovelive"]


async def test_tag_filter_matches_handles_not_names(client, db):
    token = await _mint(db)
    await _seed_concerts(db, 2)
    await _tag_one(db, "live-0", name="ラブライブ", name_en="Love Live",
                   kind=TagKind.FRANCHISE, slug="lovelive")
    body = client.get("/api/v1/concerts?tag=lovelive", headers=_auth(token)).json()
    assert [r["event_id"] for r in body["items"]] == ["live-0"]
    # A NAME is never a handle: invariant 3, names are not unique.
    empty = client.get("/api/v1/concerts?tag=Love%20Live", headers=_auth(token)).json()
    assert empty["items"] == []


async def test_since_and_until_filter_on_leg_dates(client, db):
    token = await _mint(db)
    async with db() as s:
        await ensure_user(s, ADMIN, "reiji")
        for i, when in enumerate([
            datetime(2026, 5, 1, 3, 0, tzinfo=UTC),
            datetime(2026, 9, 1, 3, 0, tzinfo=UTC),
        ]):
            c = Concert(title=f"Live {i}", event_id=f"live-{i}", created_by=ADMIN)
            s.add(c)
            await s.flush()
            s.add(ConcertDay(concert_id=c.id, label="Day 1", starts_at_utc=when))
        await s.commit()

    later = client.get("/api/v1/concerts?since=2026-07-01", headers=_auth(token)).json()
    assert [r["event_id"] for r in later["items"]] == ["live-1"]
    earlier = client.get("/api/v1/concerts?until=2026-07-01", headers=_auth(token)).json()
    assert [r["event_id"] for r in earlier["items"]] == ["live-0"]


async def test_next_anchor_is_catalogue_level_not_per_viewer(client, db):
    """Two different tokens must report the SAME next_anchor_at for one
    concert. Routed through a per-user helper they would not."""
    admin_token = await _mint(db)
    other_token = await _mint(db, 777, "someone-else")
    soon = datetime.now(UTC).replace(microsecond=0) + timedelta(days=3)
    async with db() as s:
        c = Concert(title="Live", event_id="live-x", created_by=ADMIN)
        s.add(c)
        await s.flush()
        s.add(ConcertDay(concert_id=c.id, label="Day 1", starts_at_utc=NOW))
        s.add(Round(concert_id=c.id, kind=RoundKind.LOTTERY_ROUND, label="R1", closes_at_utc=soon))
        await s.commit()

    mine = client.get("/api/v1/concerts/live-x", headers=_auth(admin_token)).json()
    theirs = client.get("/api/v1/concerts/live-x", headers=_auth(other_token)).json()
    assert mine["next_anchor_at"] == theirs["next_anchor_at"]
    assert mine["next_anchor_at"] == soon.isoformat()
    assert mine["round_count"] == 1


async def test_next_anchor_is_none_when_every_moment_is_past(client, db):
    token = await _mint(db)
    past = datetime(2020, 1, 1, tzinfo=UTC)
    async with db() as s:
        c = Concert(title="Live", event_id="live-old", created_by=ADMIN)
        s.add(c)
        await s.flush()
        s.add(ConcertDay(concert_id=c.id, label="Day 1", starts_at_utc=NOW))
        s.add(Round(concert_id=c.id, kind=RoundKind.LOTTERY_ROUND, label="R1", closes_at_utc=past))
        await s.commit()
    body = client.get("/api/v1/concerts/live-old", headers=_auth(token)).json()
    assert body["next_anchor_at"] is None


async def test_a_row_carries_its_leg_venue_handles(client, db):
    """`venue_handle` reads `ConcertDay.venue_tag`, which is lazy="raise" --
    a missing selectinload is a MissingGreenlet 500, not a slow response."""
    token = await _mint(db)
    async with db() as s:
        await ensure_user(s, ADMIN, "reiji")
        venue = Tag(name="Zepp Haneda", kind=TagKind.VENUE, slug="zepp-haneda")
        s.add(venue)
        await s.flush()
        c = Concert(title="Live", event_id="live-v", created_by=ADMIN)
        s.add(c)
        await s.flush()
        s.add(ConcertDay(concert_id=c.id, label="Day 1", starts_at_utc=NOW,
                         venue_tag_id=venue.id))
        await s.commit()
    body = client.get("/api/v1/concerts", headers=_auth(token)).json()
    assert body["items"][0]["venue_handles"] == ["zepp-haneda"]
    assert body["items"][0]["leg_dates"] == ["2026-09-01"]


async def test_concert_detail_carries_the_draft_yaml(client, db):
    token = await _mint(db)
    await _seed_concerts(db, 1)
    body = client.get("/api/v1/concerts/live-0", headers=_auth(token)).json()
    assert body["event_id"] == "live-0"
    assert "draft_yaml" in body
    assert "title" in body["draft_yaml"]


async def test_unknown_event_id_is_404_json(client, db):
    token = await _mint(db)
    r = client.get("/api/v1/concerts/nope", headers=_auth(token))
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")


async def test_concerts_require_a_token(client, db):
    await _seed_concerts(db, 1)
    assert client.get("/api/v1/concerts").status_code == 401
    assert client.get("/api/v1/concerts/live-0").status_code == 401
