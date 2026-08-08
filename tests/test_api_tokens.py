"""Agent API tokens: minted once, stored only as a hash.

Same shape as the calendar feed token, which invariant 5 names as the pattern
every future personal-secret-link feature should reuse. The properties worth
pinning are the ones that make a leak survivable: the raw value is never
persisted, and minting again invalidates whatever was issued before.
"""

import pytest
from fastapi.testclient import TestClient

from app.db.models import User
from app.db.service import (
    ensure_user,
    generate_api_token,
    get_user_by_api_token,
    hash_token,
)
from app.db.session import get_session
from app.web import auth
from app.web.app import create_app

USER = 4242


async def test_mint_stores_only_the_hash(session):
    await ensure_user(session, USER, "reiji")
    token = await generate_api_token(session, USER)

    row = await session.get(User, USER)
    assert row.api_token_hash == hash_token(token)
    assert row.api_token_hash != token
    assert token not in (row.api_token_hash or "")


async def test_lookup_finds_the_user(session):
    await ensure_user(session, USER, "reiji")
    token = await generate_api_token(session, USER)
    found = await get_user_by_api_token(session, token)
    assert found is not None
    assert found.discord_id == USER


async def test_unknown_token_finds_nobody(session):
    await ensure_user(session, USER, "reiji")
    await generate_api_token(session, USER)
    assert await get_user_by_api_token(session, "not-a-real-token") is None


async def test_minting_again_invalidates_the_old_token(session):
    """Recovery is 'generate a new one', never 'look up the old one' -- so the
    previous value must stop matching the moment a new one is issued."""
    await ensure_user(session, USER, "reiji")
    first = await generate_api_token(session, USER)
    second = await generate_api_token(session, USER)

    assert first != second
    assert await get_user_by_api_token(session, first) is None
    assert (await get_user_by_api_token(session, second)).discord_id == USER


async def test_tokens_are_long_enough_to_be_unguessable(session):
    await ensure_user(session, USER, "reiji")
    token = await generate_api_token(session, USER)
    assert len(token) >= 32


async def test_the_calendar_feed_uses_the_same_hash(session):
    """calendar_feed.py must not keep a second hash implementation -- two would
    be one refactor away from disagreeing, and the failure is silent (a token
    that simply never matches)."""
    from app.db import calendar_feed

    assert not hasattr(calendar_feed, "_hash_token")
    assert calendar_feed.hash_token is hash_token


# ── POST /me/api-token (Task 8: minting from Preferences) ──────────────────
#
# Same client + login_as pattern as tests/test_preferences_page.py, copied
# rather than shared -- that file's `client` fixture is scoped to its own
# module.


@pytest.fixture()
def client_pref(db, monkeypatch):
    app = create_app()

    async def override_session():
        async with db() as s:
            yield s

    app.dependency_overrides[get_session] = override_session

    async def fake_exchange(code):
        return "tok"

    monkeypatch.setattr(auth, "exchange_code", fake_exchange)

    async def fake_identity(token):
        return {"id": str(USER), "username": "reiji", "global_name": "reiji", "avatar": None}

    monkeypatch.setattr(auth, "fetch_identity", fake_identity)

    c = TestClient(app, follow_redirects=False)
    r = c.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    c.get(f"/auth/callback?code=x&state={state}")
    return c


@pytest.fixture()
def client_pref_anon(db):
    app = create_app()

    async def override_session():
        async with db() as s:
            yield s

    app.dependency_overrides[get_session] = override_session
    return TestClient(app, follow_redirects=False)


async def test_mint_route_shows_the_token_once(client_pref, db):
    """The raw value is displayed exactly once and is unrecoverable after.
    Recovery is 'mint a new one', which is the whole point of storing a hash."""
    r = client_pref.post("/me/api-token", data={})
    assert r.status_code in (200, 303)
    if r.status_code == 303:
        location = r.headers["location"]
        assert "api_token=" in location
        token = location.split("api_token=")[1].split("&")[0]
        reveal = client_pref.get(location).text
        assert token in reveal
    page = client_pref.get("/preferences").text
    assert "api_token=" not in page  # never sticky in the URL or re-rendered


async def test_mint_route_requires_a_session(client_pref_anon):
    r = client_pref_anon.post("/me/api-token", data={})
    assert r.status_code in (303, 401, 403)
