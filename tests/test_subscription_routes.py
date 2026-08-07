"""The concert-page following + per-leg opt-out routes (web/routes/subscriptions.py).

Thin shells over Task 2's writers (`set_concert_subscription`,
`clear_concert_subscription`, `set_leg_opt_out`): the user comes from the
session, never the form; a bad concert/day 404s; the writes go through the
service so the reminder-queue resync (invariant 2) happens in one place.

The heavy WON/PAID confirmation is a client-side gate -- the server still
performs the opt-out and never deletes the RoundOutcome (spec decision 3), so
that is what these assert.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import (
    Concert,
    ConcertDay,
    ConcertSubscription,
    ConcertTag,
    LegOptOut,
    Round,
    RoundOutcome,
    Tag,
    TagSubscription,
    User,
)
from app.db.service import tracked_concert_ids
from app.db.session import get_session
from app.domain.types import LotteryOutcome, RoundKind, SubscriptionState, TagKind
from app.web import auth
from app.web.app import create_app

USER_A, USER_B = 4242, 9999


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


def login_as(client, discord_id: int, name: str):
    async def fake_identity(token):
        return {"id": str(discord_id), "username": name, "global_name": name, "avatar": None}

    client.monkeypatch.setattr(auth, "fetch_identity", fake_identity)
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    client.get(f"/auth/callback?code=x&state={state}")


async def seed(db, *, follow_tag: bool = True) -> SimpleNamespace:
    """One concert tracked (by default) via a followed ARTIST tag, with one
    live leg and one open lottery round covering it. Both users follow the tag,
    so each is independently tracking the concert until they act."""
    now = datetime.now(UTC)
    async with db() as s:
        s.add_all([
            User(discord_id=USER_A, username="reiji"),
            User(discord_id=USER_B, username="someone-else"),
        ])
        await s.flush()
        tag = Tag(name="MyArtist", kind=TagKind.ARTIST)
        s.add(tag)
        await s.flush()
        if follow_tag:
            s.add(TagSubscription(user_id=USER_A, tag_id=tag.id))
            s.add(TagSubscription(user_id=USER_B, tag_id=tag.id))
        concert = Concert(title="Big Show", event_id="big-show", created_by=USER_A)
        s.add(concert)
        await s.flush()
        s.add(ConcertTag(concert_id=concert.id, tag_id=tag.id))
        day = ConcertDay(
            concert_id=concert.id, label="Day 1", starts_at_utc=now + timedelta(days=60)
        )
        s.add(day)
        await s.flush()
        round_ = Round(
            concert_id=concert.id, label="Lottery 1", kind=RoundKind.LOTTERY_ROUND,
            opens_at_utc=now - timedelta(days=1), closes_at_utc=now + timedelta(days=7),
            payment_deadline_at_utc=now + timedelta(days=20), applies_to=[day.id],
        )
        s.add(round_)
        await s.flush()
        await s.commit()
        return SimpleNamespace(
            concert_id=concert.id, event_id="big-show", day_id=day.id, round_id=round_.id
        )


async def sub_state(db, user_id: int, concert_id: int) -> SubscriptionState | None:
    async with db() as s:
        row = (await s.execute(select(ConcertSubscription).where(
            ConcertSubscription.user_id == user_id,
            ConcertSubscription.concert_id == concert_id,
        ))).scalar_one_or_none()
        return row.state if row else None


async def is_tracked(db, user_id: int, concert_id: int) -> bool:
    async with db() as s:
        return concert_id in await tracked_concert_ids(s, user_id)


async def leg_opted_out(db, user_id: int, day_id: int) -> bool:
    async with db() as s:
        row = (await s.execute(select(LegOptOut).where(
            LegOptOut.user_id == user_id, LegOptOut.concert_day_id == day_id
        ))).scalar_one_or_none()
        return row is not None


async def set_outcome(db, user_id: int, round_id: int, outcome: LotteryOutcome):
    async with db() as s:
        s.add(RoundOutcome(user_id=user_id, round_id=round_id, outcome=outcome))
        await s.commit()


async def outcome_for(db, user_id: int, round_id: int) -> LotteryOutcome | None:
    async with db() as s:
        row = (await s.execute(select(RoundOutcome).where(
            RoundOutcome.user_id == user_id, RoundOutcome.round_id == round_id
        ))).scalar_one_or_none()
        return row.outcome if row else None


def post_sub(client, event_id: str, state: str, htmx: bool = True):
    headers = {"HX-Request": "true"} if htmx else {}
    return client.post(
        f"/concerts/{event_id}/subscription", data={"state": state}, headers=headers
    )


def post_leg(client, event_id: str, day_id: int, opted_out: str, htmx: bool = True):
    headers = {"HX-Request": "true"} if htmx else {}
    return client.post(
        f"/concerts/{event_id}/legs/{day_id}/opt-out",
        data={"opted_out": opted_out}, headers=headers,
    )


async def test_toggle_following_off_prunes(client):
    """Off writes an OPTED_OUT override and the concert leaves the tracked set,
    even though a followed tag still matches."""
    ids = await seed(client.db)
    login_as(client, USER_A, "reiji")
    assert await is_tracked(client.db, USER_A, ids.concert_id)

    r = post_sub(client, ids.event_id, "opted_out")
    assert r.status_code == 200
    assert await sub_state(client.db, USER_A, ids.concert_id) is SubscriptionState.OPTED_OUT
    assert not await is_tracked(client.db, USER_A, ids.concert_id)


async def test_toggle_following_on_clears(client):
    """On (from an opted-out state) clears the override -- back to the tag
    default, so the concert is tracked again because the tag still matches."""
    ids = await seed(client.db)
    login_as(client, USER_A, "reiji")
    post_sub(client, ids.event_id, "opted_out")
    assert await sub_state(client.db, USER_A, ids.concert_id) is SubscriptionState.OPTED_OUT

    r = post_sub(client, ids.event_id, "default")
    assert r.status_code == 200
    assert await sub_state(client.db, USER_A, ids.concert_id) is None
    assert await is_tracked(client.db, USER_A, ids.concert_id)


async def test_subscribe_forces_tracking_with_no_tag(client):
    """A SUBSCRIBED override tracks a concert no followed tag matches."""
    ids = await seed(client.db, follow_tag=False)
    login_as(client, USER_A, "reiji")
    assert not await is_tracked(client.db, USER_A, ids.concert_id)

    r = post_sub(client, ids.event_id, "subscribed")
    assert r.status_code == 200
    assert await sub_state(client.db, USER_A, ids.concert_id) is SubscriptionState.SUBSCRIBED
    assert await is_tracked(client.db, USER_A, ids.concert_id)


async def test_per_leg_opt_out_creates_row(client):
    ids = await seed(client.db)
    login_as(client, USER_A, "reiji")

    r = post_leg(client, ids.event_id, ids.day_id, "true")
    assert r.status_code == 200
    assert await leg_opted_out(client.db, USER_A, ids.day_id)

    # Toggling back removes the row.
    assert post_leg(client, ids.event_id, ids.day_id, "false").status_code == 200
    assert not await leg_opted_out(client.db, USER_A, ids.day_id)


async def test_opt_out_of_won_concert_keeps_outcome(client):
    """Opting out of a concert the user has WON succeeds and MUST NOT delete
    the RoundOutcome -- the ticket record survives the prune (spec decision 3).
    The heavy confirmation is a client gate; the server never requires the
    outcome to be cleared first."""
    ids = await seed(client.db)
    await set_outcome(client.db, USER_A, ids.round_id, LotteryOutcome.WON)
    login_as(client, USER_A, "reiji")

    r = post_sub(client, ids.event_id, "opted_out")
    assert r.status_code == 200
    assert await sub_state(client.db, USER_A, ids.concert_id) is SubscriptionState.OPTED_OUT
    assert not await is_tracked(client.db, USER_A, ids.concert_id)
    # The ticket record survives.
    assert await outcome_for(client.db, USER_A, ids.round_id) is LotteryOutcome.WON


async def test_writes_scoped_to_calling_user(client):
    """User A's opt-out leaves user B, who follows the same tag, untouched."""
    ids = await seed(client.db)
    login_as(client, USER_A, "reiji")
    post_sub(client, ids.event_id, "opted_out")
    post_leg(client, ids.event_id, ids.day_id, "true")

    assert await sub_state(client.db, USER_A, ids.concert_id) is SubscriptionState.OPTED_OUT
    assert await sub_state(client.db, USER_B, ids.concert_id) is None
    assert not await is_tracked(client.db, USER_A, ids.concert_id)
    assert await is_tracked(client.db, USER_B, ids.concert_id)
    assert await leg_opted_out(client.db, USER_A, ids.day_id)
    assert not await leg_opted_out(client.db, USER_B, ids.day_id)


async def test_bad_concert_404s(client):
    await seed(client.db)
    login_as(client, USER_A, "reiji")
    assert post_sub(client, "no-such-concert", "opted_out").status_code == 404
    assert post_leg(client, "no-such-concert", 1, "true").status_code == 404


async def test_bad_day_404s(client):
    """A day id that names no leg of this concert 404s."""
    ids = await seed(client.db)
    login_as(client, USER_A, "reiji")
    assert post_leg(client, ids.event_id, 987654, "true").status_code == 404


async def test_requires_login(client):
    ids = await seed(client.db)
    # htmx requests: HX-Redirect home, not a 303 the XHR would follow and swap in.
    r = post_sub(client, ids.event_id, "opted_out")
    assert (r.status_code, r.headers["hx-redirect"]) == (204, "/")
    assert post_leg(client, ids.event_id, ids.day_id, "true").status_code == 204
    assert await sub_state(client.db, USER_A, ids.concert_id) is None


async def test_concert_page_renders_with_toggles(client):
    """The logged-in concert page renders and carries the Following toggle and
    the per-leg opt-out control (template context drift once shipped a 500)."""
    ids = await seed(client.db)
    login_as(client, USER_A, "reiji")
    r = client.get(f"/concerts/{ids.event_id}")
    assert r.status_code == 200
    body = r.text
    assert "/subscription" in body           # the Following toggle form
    assert "/opt-out" in body                 # the per-leg opt-out form
