"""Auth flow tests: OAuth redirect, state verification, session, editor gating.

Discord's API is monkeypatched — no network. What we're actually testing is
OUR logic: state handling, session contents, and the authorization model.
"""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.config import settings
from app.web import auth
from app.web.app import create_app


@pytest.fixture()
def client(monkeypatch):
    async def fake_exchange(code: str) -> str:
        assert code == "good-code"
        return "fake-token"

    async def fake_identity(token: str) -> dict:
        assert token == "fake-token"
        return {"id": "42", "username": "reiji", "global_name": "Reiji", "avatar": "abc123"}

    async def fake_persist(discord_id: int, username: str) -> None:
        fake_persist.calls.append((discord_id, username))

    fake_persist.calls = []
    monkeypatch.setattr(auth, "exchange_code", fake_exchange)
    monkeypatch.setattr(auth, "fetch_identity", fake_identity)
    monkeypatch.setattr(auth, "persist_user", fake_persist)
    c = TestClient(create_app(), follow_redirects=False)
    c.fake_persist = fake_persist
    return c


def do_login(client) -> None:
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    r = client.get(f"/auth/callback?code=good-code&state={state}")
    assert r.status_code in (302, 307)


def test_login_redirects_to_discord_with_state(client):
    r = client.get("/auth/login")
    assert r.status_code in (302, 307)
    loc = r.headers["location"]
    assert loc.startswith("https://discord.com/oauth2/authorize?")
    assert "scope=identify" in loc
    assert "state=" in loc


def test_callback_rejects_bad_state(client):
    client.get("/auth/login")  # sets a state in the session
    r = client.get("/auth/callback?code=good-code&state=WRONG")
    assert r.status_code == 400


def test_callback_rejects_missing_state_entirely(client):
    # no login first — no state in session at all
    r = client.get("/auth/callback?code=good-code&state=whatever")
    assert r.status_code == 400


def test_full_login_sets_session_and_persists_user(client):
    do_login(client)
    assert client.fake_persist.calls == [(42, "Reiji")]
    r = client.get("/")  # session cookie carries over
    assert "Welcome, Reiji" in r.text
    assert "log out" in r.text


def test_logout_clears_session(client):
    do_login(client)
    client.get("/auth/logout")
    r = client.get("/")
    assert "Sign in with Discord" in r.text


def test_state_is_single_use(client):
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    client.get(f"/auth/callback?code=good-code&state={state}")
    # replaying the same state must fail: it was popped on first use
    r = client.get(f"/auth/callback?code=good-code&state={state}")
    assert r.status_code == 400


# ── Editor gating (unit-level, no HTTP needed) ───────────────────────────


def make_request(session: dict) -> Request:
    return Request(scope={"type": "http", "session": session, "headers": []})


def test_require_user_rejects_anonymous():
    with pytest.raises(HTTPException) as e:
        auth.require_user(make_request({}))
    assert e.value.status_code == 401


def test_require_editor_rejects_non_whitelisted(monkeypatch):
    monkeypatch.setattr(settings, "editor_whitelist", "999")
    req = make_request({"user": {"id": 42, "username": "reiji", "avatar": None}})
    with pytest.raises(HTTPException) as e:
        auth.require_editor(req)
    assert e.value.status_code == 403


def test_require_editor_accepts_whitelisted(monkeypatch):
    monkeypatch.setattr(settings, "editor_whitelist", "42")
    req = make_request({"user": {"id": 42, "username": "reiji", "avatar": None}})
    user = auth.require_editor(req)
    assert user.is_editor
