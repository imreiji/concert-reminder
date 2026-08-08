"""The read endpoints, end to end through the router.

`test_the_list_sort_is_totally_ordered` is the load-bearing paging assertion,
NOT `test_paging_covers_every_row_exactly_once`. The union-of-pages shape is
the right test against a SQL `ORDER BY ... LIMIT ... OFFSET`, but these rows
are sorted in Python, where `list.sort` is stable and deterministic -- three
slices of one list are disjoint whatever the key is, so that assertion cannot
fail here. What a missing tiebreaker actually breaks is the ORDER, and only
when the query's own row order disagrees with it, which `_seed_concerts`
arranges by inserting descending. Both are kept: the union assertion still
states the intent, and would catch a regression if this ever becomes a real
query.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.models import Concert, ConcertDay, Round, Tag
from app.db.service import ensure_user, generate_api_token, record_round_outcome, set_leg_opt_out
from app.db.session import get_session
from app.domain.types import LotteryOutcome, RoundKind, TagKind
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
    """Intent, not the tiebreaker guard -- see the module docstring. Every
    seeded concert shares one leg date, so the sort key ties on all five rows;
    a SQL-side sort over a tie-prone key would repeat or drop one here. This
    sort is Python-side and cannot, so the assertion that bites is
    `test_the_list_sort_is_totally_ordered` below."""
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


async def test_since_and_until_must_be_satisfied_by_ONE_leg(client, db):
    """"What's on next month" is the query an agent actually issues, and two
    independent `any()` passes answer it wrong: a tour with a leg in March and
    a leg in December satisfies `since` on one leg and `until` on the OTHER,
    so it comes back for September while having nothing in September."""
    token = await _mint(db)
    async with db() as s:
        await ensure_user(s, ADMIN, "reiji")
        tour = Concert(title="Tour", event_id="tour", created_by=ADMIN)
        s.add(tour)
        real = Concert(title="September", event_id="september", created_by=ADMIN)
        s.add(real)
        await s.flush()
        for when in (datetime(2026, 3, 1, 3, 0, tzinfo=UTC),
                     datetime(2026, 12, 1, 3, 0, tzinfo=UTC)):
            s.add(ConcertDay(concert_id=tour.id, label="Leg", starts_at_utc=when))
        s.add(ConcertDay(concert_id=real.id, label="Leg",
                         starts_at_utc=datetime(2026, 9, 15, 3, 0, tzinfo=UTC)))
        await s.commit()

    body = client.get(
        "/api/v1/concerts?since=2026-09-01&until=2026-09-30", headers=_auth(token)
    ).json()
    assert [r["event_id"] for r in body["items"]] == ["september"]
    assert body["total"] == 1


async def test_leg_dates_are_JST_not_the_UTC_calendar_date(client, db):
    """domain/timezones.py: "Always JST, like every other date in the app".

    A leg at 00:30 JST on the 2nd is 15:30 UTC on the 1st, and reporting the
    1st puts the API a day off from every other surface -- including
    `db/drafts.py`'s discovery collision hint, which asks this exact question
    through `utc_to_jst(...).date()`."""
    token = await _mint(db)
    just_past_midnight_jst = datetime(2026, 9, 1, 15, 30, tzinfo=UTC)
    async with db() as s:
        await ensure_user(s, ADMIN, "reiji")
        c = Concert(title="Late show", event_id="late", created_by=ADMIN)
        s.add(c)
        await s.flush()
        s.add(ConcertDay(concert_id=c.id, label="Day 1",
                         starts_at_utc=just_past_midnight_jst))
        await s.commit()

    body = client.get("/api/v1/concerts/late", headers=_auth(token)).json()
    assert body["leg_dates"] == ["2026-09-02"]

    # And the FILTER asks the same question, or a row's own date excludes it.
    on_the_day = client.get("/api/v1/concerts?since=2026-09-02&until=2026-09-02",
                            headers=_auth(token)).json()
    assert [r["event_id"] for r in on_the_day["items"]] == ["late"]
    the_day_before = client.get("/api/v1/concerts?since=2026-09-01&until=2026-09-01",
                                headers=_auth(token)).json()
    assert the_day_before["items"] == []


async def test_next_anchor_is_catalogue_level_not_per_viewer(client, db):
    """Two different tokens must report the SAME next_anchor_at for one
    concert. Routed through a per-user helper they would not.

    The per-user state is the whole test. With neither user carrying any
    RoundOutcome or LegOptOut, a per-viewer implementation returns the same
    value for both and this passes while asserting nothing.

    MEASURED, not assumed, which state discriminates: running this seed
    through `concert_round_rows` + `concert_next_moment` (the per-viewer
    path this must not become) answers `closes` for ADMIN and None for the
    opted-out user. The LEG OPT-OUT is what does it -- `_needs_you` vetoes an
    opted-out row. An APPLIED outcome alone does NOT: `_primary_anchor` picks
    the anchor by time, not by outcome, so both viewers still read `closes`.
    Both are seeded anyway, since APPLIED is what a future per-user rule would
    most likely start consulting."""
    admin_token = await _mint(db)
    other_token = await _mint(db, 777, "someone-else")
    soon = datetime.now(UTC).replace(microsecond=0) + timedelta(days=3)
    later = soon + timedelta(days=10)
    async with db() as s:
        c = Concert(title="Live", event_id="live-x", created_by=ADMIN)
        s.add(c)
        await s.flush()
        day = ConcertDay(concert_id=c.id, label="Day 1", starts_at_utc=NOW)
        s.add(day)
        await s.flush()
        r = Round(concert_id=c.id, kind=RoundKind.LOTTERY_ROUND, label="R1",
                  closes_at_utc=soon, results_at_utc=later, applies_to=[day.id])
        s.add(r)
        await s.flush()
        # ADMIN has applied; the other user has not, and has opted out of the
        # only leg. Two viewers as far apart as this app's per-user state goes.
        await record_round_outcome(s, ADMIN, r.id, LotteryOutcome.APPLIED)
        await set_leg_opt_out(s, 777, day.id, True)
        await s.commit()

    mine = client.get("/api/v1/concerts/live-x", headers=_auth(admin_token)).json()
    theirs = client.get("/api/v1/concerts/live-x", headers=_auth(other_token)).json()
    assert mine["next_anchor_at"] == theirs["next_anchor_at"]
    # `closes`, the catalogue fact -- NOT the results moment an APPLIED
    # viewer's own next step would be, and not None for the opted-out one.
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


async def test_a_dead_concert_announces_no_next_anchor(client, db):
    """Invariant 2: a concert whose every leg is cancelled "contributes no
    live rounds anywhere -- including the General rounds `is_round_cancelled`
    rightly exempts".

    `is_round_cancelled` alone cannot see this and must not learn to: a
    General round on a multi-leg concert with ONE dead leg stays live. The
    concert-level question is `all_legs_cancelled`. The LIST endpoint is
    already saved by `discoverable_concert_criterion`; the DETAIL endpoint
    applies no such filter -- deliberately, since a dead concert stays
    reachable at its own URL -- so it is where a live anchor leaks out."""
    token = await _mint(db)
    soon = datetime.now(UTC).replace(microsecond=0) + timedelta(days=3)
    async with db() as s:
        await ensure_user(s, ADMIN, "reiji")
        c = Concert(title="Cancelled", event_id="dead", created_by=ADMIN)
        s.add(c)
        await s.flush()
        s.add(ConcertDay(concert_id=c.id, label="Day 1", starts_at_utc=NOW, cancelled=True))
        # A General round: no applies_to, so is_round_cancelled exempts it.
        s.add(Round(concert_id=c.id, kind=RoundKind.GENERAL_SALE, label="General",
                    closes_at_utc=soon))
        await s.commit()

    listed = client.get("/api/v1/concerts", headers=_auth(token)).json()
    assert listed["items"] == []
    detail = client.get("/api/v1/concerts/dead", headers=_auth(token)).json()
    assert detail["next_anchor_at"] is None
    assert detail["leg_dates"] == []


async def test_a_general_round_survives_one_dead_leg_of_a_live_concert(client, db):
    """The other half of the same rule, and the reason the fix belongs at the
    concert level: with a live leg left, the General round is still live."""
    token = await _mint(db)
    soon = datetime.now(UTC).replace(microsecond=0) + timedelta(days=3)
    async with db() as s:
        await ensure_user(s, ADMIN, "reiji")
        c = Concert(title="Tour", event_id="half-dead", created_by=ADMIN)
        s.add(c)
        await s.flush()
        s.add(ConcertDay(concert_id=c.id, label="Day 1", starts_at_utc=NOW, cancelled=True))
        s.add(ConcertDay(concert_id=c.id, label="Day 2",
                         starts_at_utc=NOW + timedelta(days=1)))
        s.add(Round(concert_id=c.id, kind=RoundKind.GENERAL_SALE, label="General",
                    closes_at_utc=soon))
        await s.commit()

    body = client.get("/api/v1/concerts/half-dead", headers=_auth(token)).json()
    assert body["next_anchor_at"] == soon.isoformat()


async def test_a_cancelled_leg_is_dropped_from_the_row(client, db):
    """A cancelled leg is flagged, never deleted (invariant 2), so every row
    builder has to skip it explicitly -- its date and its venue both."""
    token = await _mint(db)
    async with db() as s:
        await ensure_user(s, ADMIN, "reiji")
        gone = Tag(name="Nakano Sun Plaza", kind=TagKind.VENUE, slug="nakano")
        kept = Tag(name="Zepp Haneda", kind=TagKind.VENUE, slug="zepp-haneda")
        s.add_all([gone, kept])
        await s.flush()
        c = Concert(title="Tour", event_id="tour", created_by=ADMIN)
        s.add(c)
        await s.flush()
        s.add(ConcertDay(concert_id=c.id, label="Day 1", starts_at_utc=NOW,
                         venue_tag_id=gone.id, cancelled=True))
        s.add(ConcertDay(concert_id=c.id, label="Day 2", venue_tag_id=kept.id,
                         starts_at_utc=NOW + timedelta(days=1)))
        await s.commit()

    body = client.get("/api/v1/concerts/tour", headers=_auth(token)).json()
    assert body["leg_dates"] == ["2026-09-02"]
    assert body["venue_handles"] == ["zepp-haneda"]


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
