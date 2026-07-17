"""Auth tests: OAuth flow, DB-backed sessions, revocation, editor gating.

Discord's API is monkeypatched; the database is real (in-memory). The session
lifecycle — including the cookie-replay attack this design exists to stop —
runs against actual rows.
"""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base, User, WebSession
from app.db.session import get_session
from app.web import auth
from app.web.app import create_app


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture()
def client(db, monkeypatch):
    async def fake_exchange(code: str) -> str:
        assert code == "good-code"
        return "fake-token"

    async def fake_identity(token: str) -> dict:
        return {"id": "42", "username": "reiji", "global_name": "Reiji", "avatar": "abc123"}

    monkeypatch.setattr(auth, "exchange_code", fake_exchange)
    monkeypatch.setattr(auth, "fetch_identity", fake_identity)

    app = create_app()

    async def override_session():
        async with db() as s:
            yield s

    app.dependency_overrides[get_session] = override_session
    c = TestClient(app, follow_redirects=False)
    c.db = db
    return c


def do_login(client) -> None:
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    r = client.get(f"/auth/callback?code=good-code&state={state}")
    assert r.status_code in (302, 307)


async def _count(db, model) -> int:
    async with db() as s:
        return len((await s.execute(select(model))).scalars().all())


# ── OAuth flow ───────────────────────────────────────────────────────────


def test_login_redirects_to_discord_with_state(client):
    r = client.get("/auth/login")
    assert r.status_code in (302, 307)
    loc = r.headers["location"]
    assert loc.startswith("https://discord.com/oauth2/authorize?")
    assert "scope=identify" in loc and "state=" in loc


def test_callback_rejects_bad_state(client):
    client.get("/auth/login")
    assert client.get("/auth/callback?code=good-code&state=WRONG").status_code == 400


def test_callback_rejects_missing_state_entirely(client):
    assert client.get("/auth/callback?code=good-code&state=x").status_code == 400


def test_state_is_single_use(client):
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    client.get(f"/auth/callback?code=good-code&state={state}")
    assert client.get(f"/auth/callback?code=good-code&state={state}").status_code == 400


# ── DB-backed sessions ───────────────────────────────────────────────────


async def test_login_creates_user_and_session_rows(client):
    do_login(client)
    assert await _count(client.db, User) == 1
    assert await _count(client.db, WebSession) == 1
    r = client.get("/")
    assert "Reiji" in r.text and "Log out" in r.text


async def test_session_rows_store_only_hashes(client):
    do_login(client)
    raw_cookie = "; ".join(f"{k}={v}" for k, v in client.cookies.items())
    async with client.db() as s:
        row = (await s.execute(select(WebSession))).scalar_one()
    assert len(row.token_hash) == 64  # sha256 hex
    assert row.token_hash not in raw_cookie  # cookie carries token, DB carries hash


async def test_logout_revokes_server_side(client):
    do_login(client)
    client.get("/auth/logout")
    async with client.db() as s:
        row = (await s.execute(select(WebSession))).scalar_one()
    assert row.revoked_at is not None
    assert "Sign in with Discord" in client.get("/").text


def test_replayed_cookie_after_logout_is_dead(client):
    """THE regression test: a stolen cookie must die when the user logs out."""
    do_login(client)
    stolen = dict(client.cookies)  # attacker copies the cookie jar
    client.get("/auth/logout")

    client.cookies.clear()
    for k, v in stolen.items():
        client.cookies.set(k, v)  # attacker replays it
    r = client.get("/")
    assert "Sign in with Discord" in r.text  # anonymous: revoked server-side


def test_each_login_rotates_the_token(client):
    do_login(client)
    first = dict(client.cookies)
    do_login(client)
    assert dict(client.cookies) != first


# ── Editor gating ────────────────────────────────────────────────────────


def test_anonymous_is_rejected_by_protected_routes(client):
    assert client.post("/concerts", data={"title": "X"}).status_code == 401


def test_non_editor_is_forbidden(client, monkeypatch):
    monkeypatch.setattr(settings, "editor_whitelist", "999")  # 42 not whitelisted
    do_login(client)
    assert client.post("/concerts", data={"title": "X"}).status_code == 403


def test_editor_passes(client, monkeypatch):
    monkeypatch.setattr(settings, "editor_whitelist", "42")
    do_login(client)
    assert client.post("/concerts", data={"title": "X"}).status_code == 303


# ── Admin gating ─────────────────────────────────────────────────────────


def test_non_admin_is_forbidden_from_admin_routes(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", "999")  # 42 not whitelisted
    do_login(client)
    assert client.post("/admin/editors", data={"discord_id": 777}).status_code == 403


def test_admin_passes_admin_routes(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", "42")
    do_login(client)
    assert client.post("/admin/editors", data={"discord_id": 777}).status_code == 303


def test_admin_implicitly_passes_editor_routes(client, monkeypatch):
    """Admins can create/edit concerts even with no editor_whitelist/DB flag."""
    monkeypatch.setattr(settings, "admin_whitelist", "42")
    do_login(client)
    assert client.post("/concerts", data={"title": "X"}).status_code == 303


async def test_promote_then_demote_round_trip(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", "42")
    do_login(client)  # logged in as admin 42

    # Promote 999 to editor.
    r = client.post("/admin/editors", data={"discord_id": 999})
    assert r.status_code == 303

    async with client.db() as s:
        row = await s.get(User, 999)
        assert row is not None and row.is_editor is True

    # Demote 999 again.
    r = client.post("/admin/editors/999/remove")
    assert r.status_code == 303
    async with client.db() as s:
        row = await s.get(User, 999)
        assert row is not None and row.is_editor is False


def test_cannot_remove_env_whitelisted_editor(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", "42")
    monkeypatch.setattr(settings, "editor_whitelist", "999")
    do_login(client)
    r = client.post("/admin/editors/999/remove")
    assert r.status_code == 400


def test_promoted_editor_gains_access(client, monkeypatch):
    """The whole point: an admin-promoted editor (no env entry, no restart)
    can now hit editor-gated routes."""
    monkeypatch.setattr(settings, "admin_whitelist", "42")
    do_login(client)  # admin 42 promotes 999
    client.post("/admin/editors", data={"discord_id": 999})

    # Log in as 999 and confirm editor access, purely from the DB flag.
    async def fake_identity(token: str) -> dict:
        return {"id": "999", "username": "newbie", "global_name": "Newbie", "avatar": None}

    monkeypatch.setattr(auth, "fetch_identity", fake_identity)
    do_login(client)
    assert client.post("/concerts", data={"title": "Y"}).status_code == 303


def test_admin_sees_editors_panel_on_preferences_page(client, monkeypatch):
    """Logged-in GET render test for the new admin section."""
    monkeypatch.setattr(settings, "admin_whitelist", "42")
    do_login(client)
    r = client.get("/preferences")
    assert r.status_code == 200
    assert "Editors" in r.text


def test_non_admin_does_not_see_editors_panel(client):
    do_login(client)
    r = client.get("/preferences")
    assert r.status_code == 200
    assert "Editors" not in r.text
