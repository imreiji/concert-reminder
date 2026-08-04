"""Auth tests: OAuth flow, DB-backed sessions, revocation, editor gating.

Discord's API is monkeypatched; the database is real (in-memory). The session
lifecycle — including the cookie-replay attack this design exists to stop —
runs against actual rows.
"""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base, User, WebSession
from app.db.service import delete_user, ensure_user
from app.db.session import get_session
from app.web import auth
from app.web.app import create_app


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

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


def test_callback_redirects_new_user_to_welcome(client):
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    r = client.get(f"/auth/callback?code=good-code&state={state}")
    assert r.headers["location"] == "/welcome"


def test_callback_redirects_welcomed_user_to_index(client):
    """The returning-user test, made honest: a row alone is not onboarding,
    so this user has to actually finish the wizard before / is right."""
    do_login(client)  # first login: creates the row
    client.post("/welcome/skip-all")  # ... and finishes onboarding
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    r = client.get(f"/auth/callback?code=good-code&state={state}")
    assert r.headers["location"] == "/"


async def test_bot_first_user_is_still_sent_to_the_wizard(client):
    """The bot's ensure_user creates a bare row; that row's owner has never
    seen onboarding, and 'a row exists' must not read as 'was onboarded'."""
    async with client.db() as s:
        await ensure_user(s, 42, "reiji")  # exactly what a slash command does
        await s.commit()
    assert _login_with_next(client) == "/welcome"  # their first WEB login


def test_returning_unwelcomed_user_is_sent_back_to_the_wizard(client):
    """Logging in twice without finishing the wizard lands on /welcome twice --
    an unfinished onboarding is unfinished, not 'seen it, too late'."""
    do_login(client)
    client.get("/auth/logout")
    assert _login_with_next(client) == "/welcome"


async def test_a_later_login_does_not_reseed_the_language_column(client):
    """The redirect moved to welcomed_at, but seeding stayed keyed on row
    absence, and that difference is deliberate: the column cannot tell
    "defaulted to en" from "chose en", so only the moment before the row
    exists is safe to write from a browser cookie."""
    client.cookies.set("lang", "ja")
    do_login(client)  # creation: the cookie seeds the column
    async with client.db() as s:
        assert (await s.get(User, 42)).language == "ja"

    client.get("/auth/logout")
    # Clear the jar first: the callback set its own lang cookie on the way
    # out, and merely set()ing a second one leaves BOTH in the jar (different
    # domains) -- the request then carries "lang=zh; lang=ja" and the server's
    # own value wins, so this test would pass no matter what the route did.
    client.cookies.clear()
    client.cookies.set("lang", "zh")  # a different browser, or a stale cookie
    do_login(client)
    async with client.db() as s:
        assert (await s.get(User, 42)).language == "ja"  # the account still rules


async def test_deleted_then_recreated_user_is_rewizarded(client):
    """The original repro: erase the account, log in again, get onboarded
    afresh -- the re-created row is a stranger, whatever its discord id."""
    do_login(client)
    client.post("/welcome/skip-all")
    async with client.db() as s:
        assert await delete_user(s, 42) is True
        await s.commit()
    assert _login_with_next(client) == "/welcome"


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
    # Signed out is not an error, it is a wrong turn: 303 home, not 401.
    r = client.post("/concerts", data={"title_en": "X", "title_zh": "X", "title": "X"})
    assert r.status_code == 303
    assert r.headers["location"] == "/"


def test_anonymous_get_carries_the_destination_home(client):
    r = client.get("/preferences")
    assert r.status_code == 303
    assert r.headers["location"] == "/?next=%2Fpreferences"


def test_anonymous_get_preserves_the_query_string(client):
    r = client.get("/setup/applications?from=dm")
    assert r.headers["location"] == "/?next=%2Fsetup%2Fapplications%3Ffrom%3Ddm"


def test_anonymous_post_carries_no_destination(client):
    """A POST body is gone by now, so replaying its URL after login would
    render a form that looks like it submitted and didn't."""
    r = client.post("/concerts", data={"title_en": "X", "title_zh": "X", "title": "X"})
    assert r.headers["location"] == "/"


def test_htmx_returns_to_the_page_not_the_fragment_endpoint(client):
    """The fragment URL is not somewhere you can stand -- HX-Current-URL is."""
    r = client.post(
        "/concerts",
        data={"title_en": "X", "title_zh": "X", "title": "X"},
        headers={"HX-Request": "true", "HX-Current-URL": "http://testserver/discover?tag=3"},
    )
    assert r.status_code == 204
    assert r.headers["hx-redirect"] == "/?next=%2Fdiscover%3Ftag%3D3"


def test_htmx_current_url_origin_cannot_steer_the_redirect(client):
    """Only the PATH of that header survives, so a forged origin goes nowhere."""
    r = client.post(
        "/concerts",
        data={"title_en": "X", "title_zh": "X", "title": "X"},
        headers={"HX-Request": "true", "HX-Current-URL": "https://evil.com/phish"},
    )
    assert r.headers["hx-redirect"] == "/?next=%2Fphish"


def _login_with_next(client, query: str = "") -> str:
    """Run the OAuth round-trip, returning where the callback sent us."""
    r = client.get(f"/auth/login{query}")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    return client.get(f"/auth/callback?code=good-code&state={state}").headers["location"]


def test_next_round_trips_through_oauth_to_the_original_page(client):
    """The whole point: bounced off /preferences, signed in, land there."""
    do_login(client)  # the account exists
    client.post("/welcome/skip-all")  # and is onboarded, so next beats the wizard
    client.get("/auth/logout")
    assert _login_with_next(client, "?next=%2Fpreferences") == "/preferences"


def test_new_user_still_goes_to_the_wizard(client):
    """A brand-new account has not picked a single tag, so the page that
    bounced them is the wrong place to land -- the wizard wins over next."""
    assert _login_with_next(client, "?next=%2Fpreferences") == "/welcome"


def test_hostile_next_is_dropped_at_login(client):
    """safe_next runs on the way IN, so nothing off-origin ever reaches the
    session -- and the callback re-checks on the way out anyway."""
    do_login(client)
    client.post("/welcome/skip-all")  # else the wizard, not next, decides
    client.get("/auth/logout")
    assert _login_with_next(client, "?next=https%3A%2F%2Fevil.com%2Fphish") == "/"


def test_abandoned_next_does_not_outlive_its_login(client):
    """Start a login with a destination, abandon it, start a clean one: the
    stale destination must not be waiting in the session."""
    do_login(client)
    client.post("/welcome/skip-all")  # else the wizard, not next, decides
    client.get("/auth/logout")
    client.get("/auth/login?next=%2Fpreferences")  # abandoned
    assert _login_with_next(client) == "/"


def test_sign_in_cta_preserves_next(client):
    """Every CTA goes through login_url(request), so the destination survives
    the visitor reading the landing page before clicking."""
    r = client.get("/?next=%2Fpreferences")
    assert r.status_code == 200
    assert "/auth/login?next=%2Fpreferences" in r.text
    assert 'href="/auth/login"' not in r.text  # no CTA silently drops it


def test_home_explains_the_bounce(client):
    """Without this the redirect is silent and the click just looks broken."""
    assert "Sign in to continue" in client.get("/?next=%2Fpreferences").text
    assert "Sign in to continue" not in client.get("/").text


def test_anonymous_get_lands_on_a_rendered_home_page(client):
    """The redirect has to go somewhere real: following it renders Home's
    signed-out landing page, which carries the sign-in CTA."""
    r = client.get("/preferences", follow_redirects=True)
    assert r.status_code == 200
    assert "/auth/login" in r.text  # the sign-in CTA the visitor actually needs


def test_anonymous_htmx_request_gets_hx_redirect(client):
    """An htmx XHR would FOLLOW a 303 and swap the landing page into a
    fragment target. HX-Redirect makes the browser navigate instead."""
    r = client.post("/concerts", data={
        "title_en": "X", "title_zh": "X", "title": "X",
    }, headers={"HX-Request": "true"})
    assert r.status_code == 204
    assert r.headers["hx-redirect"] == "/"
    assert r.text == ""  # nothing for htmx to swap


def test_signed_in_but_unauthorized_still_403s(client, monkeypatch):
    """Only ANONYMOUS is a wrong turn. A signed-in non-editor asked for
    something they may not have -- that stays an error, not a redirect."""
    monkeypatch.setattr(settings, "editor_whitelist", "999")
    do_login(client)
    assert client.get("/concerts/new").status_code == 403


def test_non_editor_is_forbidden(client, monkeypatch):
    monkeypatch.setattr(settings, "editor_whitelist", "999")  # 42 not whitelisted
    do_login(client)
    assert client.post("/concerts", data={
        "title_en": "X", "title_zh": "X", "title": "X",
    }).status_code == 403


def test_editor_passes(client, monkeypatch):
    monkeypatch.setattr(settings, "editor_whitelist", "42")
    do_login(client)
    assert client.post("/concerts", data={
        "title_en": "X", "title_zh": "X", "title": "X", "event_id": "x",
    }).status_code == 303


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
    assert client.post("/concerts", data={
        "title_en": "X", "title_zh": "X", "title": "X", "event_id": "x",
    }).status_code == 303


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
    assert client.post("/concerts", data={
        "title_en": "Y", "title_zh": "Y", "title": "Y", "event_id": "y",
    }).status_code == 303


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


# ── Undeliverable-DM banner ──────────────────────────────────────────────


def test_banner_hidden_when_dm_not_blocked(client):
    do_login(client)
    r = client.get("/")
    assert "couldn't be delivered" not in r.text


async def test_banner_shown_when_dm_blocked(client):
    from datetime import UTC, datetime

    do_login(client)
    async with client.db() as s:
        user = await s.get(User, 42)
        user.dm_blocked_since = datetime.now(UTC)
        await s.commit()

    r = client.get("/")
    assert "couldn't be delivered" in r.text
