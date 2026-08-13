"""The Preferences Following section: pruned counts + review-and-restore.

The rebuild reorganises the page onto a left-rail structure and adds a
Following section that surfaces the deliberately-invisible OPTED_OUT state
(spec decision 1): a "N concerts . M you pruned" count and a restore control
per pruned concert. The restore control is a plain POST to the Task-4
subscription route with state=default, which calls `clear_concert_subscription`
-- the concert returns to the tracked set.

These tests assert the new surface only; the presets / timezone / calendar /
editors tests elsewhere pin that the re-presented functionality still works.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db.models import (
    Concert,
    ConcertDay,
    ConcertSubscription,
    ConcertTag,
    Round,
    Tag,
    TagSubscription,
    User,
)
from app.db.service import tracked_concert_ids
from app.db.session import get_session
from app.domain.types import RoundKind, SubscriptionState, TagKind
from app.web import auth
from app.web.app import create_app

USER_A = 4242


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


async def seed(db, *, opt_out: bool = False) -> SimpleNamespace:
    """One concert matched by a followed ARTIST tag. Optionally pre-prune it
    with an OPTED_OUT override so the Following section has something to show."""
    now = datetime.now(UTC)
    async with db() as s:
        s.add(User(discord_id=USER_A, username="reiji"))
        await s.flush()
        tag = Tag(name="MyArtist", kind=TagKind.ARTIST)
        s.add(tag)
        await s.flush()
        s.add(TagSubscription(user_id=USER_A, tag_id=tag.id))
        concert = Concert(title="Big Show", event_id="big-show", created_by=USER_A)
        s.add(concert)
        await s.flush()
        s.add(ConcertTag(concert_id=concert.id, tag_id=tag.id))
        day = ConcertDay(
            concert_id=concert.id, label="Day 1", starts_at_utc=now + timedelta(days=60)
        )
        s.add(day)
        await s.flush()
        s.add(Round(
            concert_id=concert.id, label="Lottery 1", kind=RoundKind.LOTTERY_ROUND,
            opens_at_utc=now - timedelta(days=1), closes_at_utc=now + timedelta(days=7),
            applies_to=[day.id],
        ))
        if opt_out:
            s.add(ConcertSubscription(
                user_id=USER_A, concert_id=concert.id, state=SubscriptionState.OPTED_OUT
            ))
        await s.commit()
        return SimpleNamespace(concert_id=concert.id, event_id="big-show")


async def is_tracked(db, user_id: int, concert_id: int) -> bool:
    async with db() as s:
        return concert_id in await tracked_concert_ids(s, user_id)


async def test_preferences_renders_for_logged_in_user(client):
    """A missing logged-in GET render test once shipped a 500 (context drift)."""
    await seed(client.db)
    login_as(client, USER_A, "reiji")
    r = client.get("/preferences")
    assert r.status_code == 200
    assert "Following" in r.text


async def test_following_shows_pruned_count(client):
    """An OPTED_OUT row surfaces as a 'you pruned' count and lists the concert."""
    await seed(client.db, opt_out=True)
    login_as(client, USER_A, "reiji")
    r = client.get("/preferences")
    assert r.status_code == 200
    assert "1 skipped" in r.text
    assert "Big Show" in r.text  # the pruned concert appears in the restore list


async def test_no_pruned_when_nothing_opted_out(client):
    """With no override the pruned count is zero and no restore list shows."""
    await seed(client.db)
    login_as(client, USER_A, "reiji")
    r = client.get("/preferences")
    assert r.status_code == 200
    assert "1 skipped" not in r.text


async def test_restore_control_untracks_then_retracks(client):
    """The restore control POSTs to the subscription route with state=default,
    which calls clear_concert_subscription -- the concert returns to tracked."""
    ids = await seed(client.db, opt_out=True)
    login_as(client, USER_A, "reiji")
    assert not await is_tracked(client.db, USER_A, ids.concert_id)

    # the form the Following section renders: state=default -> clear override
    r = client.post(
        f"/concerts/{ids.event_id}/subscription",
        data={"state": "default"},
        headers={"Referer": "/preferences"},
    )
    assert r.status_code in (200, 303)
    assert await is_tracked(client.db, USER_A, ids.concert_id)


async def test_editors_section_hidden_for_non_admin(client):
    """The admin-only Editors section must not render for a plain user."""
    await seed(client.db)
    login_as(client, USER_A, "reiji")
    r = client.get("/preferences")
    assert r.status_code == 200
    assert "Editors" not in r.text


async def test_editors_section_shown_for_admin(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(USER_A))
    await seed(client.db)
    login_as(client, USER_A, "reiji")
    r = client.get("/preferences")
    assert r.status_code == 200
    assert "Editors" in r.text


# ── Per-tag toggle routes (Notify / Auto-apply) -- DELETED, task 4 ──────────
#
# `POST /subscriptions/{id}/notify` and `.../auto-apply` lost their only
# caller when the Following section's per-tag `.subrow`s were deleted (this
# file tested them directly, by POSTing the route with no UI in between --
# there never was a template assertion here to lose). Deleted along with the
# markup rather than left unreachable: `/subscriptions/{id}/settings` already
# writes both fields, together, with an explicit "none" preset choice, which
# is strictly less ambiguous than the NULL "auto-apply off" overload the two
# deleted routes wrote (see routes/preferences.py's module docstring). That
# route's equivalent coverage -- the notify flag, linking/clearing a preset,
# ownership 404 -- lives in test_following_dialog.py's
# "POST /subscriptions/{id}/settings" section; nothing here duplicates it.


async def sub_id_for(db, user_id: int) -> int:
    from sqlalchemy import select
    async with db() as s:
        return (await s.execute(
            select(TagSubscription.id).where(TagSubscription.user_id == user_id)
        )).scalar_one()


async def test_deleted_toggle_routes_are_gone(client):
    """Confirms the deletion decision itself, not just its absence from the
    UI: a POST to either old path answers like any other unmatched route,
    rather than a stub silently left registered and unreferenced.

    Mutation: re-add either route (even as a no-op) and this fails.
    """
    await seed(client.db)
    login_as(client, USER_A, "reiji")
    sid = await sub_id_for(client.db, USER_A)
    assert client.post(f"/subscriptions/{sid}/notify").status_code == 404
    assert client.post(f"/subscriptions/{sid}/auto-apply").status_code == 404
