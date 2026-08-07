"""Friendly HTML error pages for full-page navigations.

The whole design turns on ONE distinction, and it is not the status code: a
browser navigation gets HTML, an XHR keeps the JSON body it was already
parsing. `_tag_create_dialog.html` and `_venue_create_dialog.html` both read
`(await resp.json()).detail` off a 409 to offer "that already exists, select
it instead" -- convert every HTTPException to HTML and those silently lose
their message. Same split `LoginRequired`'s handler already makes.
"""

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db.session import get_session
from app.web import auth
from app.web.app import create_app

ADMIN_ID, PLAIN_ID = 42, 777

# A browser navigation. TestClient defaults to `*/*`, which is what a fetch()
# sends, so every HTML expectation here has to say so explicitly.
NAV = {"accept": "text/html,application/xhtml+xml"}


def _build(db, monkeypatch, **kw):
    app = create_app()

    async def override_session():
        async with db() as s:
            yield s

    app.dependency_overrides[get_session] = override_session

    async def fake_exchange(code):
        return "tok"

    monkeypatch.setattr(auth, "exchange_code", fake_exchange)
    c = TestClient(app, follow_redirects=False, **kw)
    c.db = db
    c.app = app
    c.monkeypatch = monkeypatch
    return c


@pytest.fixture()
def client(db, monkeypatch):
    return _build(db, monkeypatch)


def login_as(client, discord_id: int, name: str):
    """Drives the real OAuth callback, which CREATES the user row -- so no test
    here seeds the user itself (that is an IntegrityError, not a shortcut)."""

    async def fake_identity(token):
        return {"id": str(discord_id), "username": name, "global_name": name, "avatar": None}

    client.monkeypatch.setattr(auth, "fetch_identity", fake_identity)
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    client.get(f"/auth/callback?code=x&state={state}")


# ── 404 ──────────────────────────────────────────────────────────────────


def test_404_navigation_renders_a_page_with_a_way_home(client):
    r = client.get("/no-such-page", headers=NAV)
    assert r.status_code == 404
    assert "text/html" in r.headers["content-type"]
    assert 'href="/"' in r.text


def test_404_xhr_still_gets_json(client):
    """A fetch() sends `*/*`. It must keep the JSON body it expects."""
    r = client.get("/no-such-page")
    assert r.status_code == 404
    assert "application/json" in r.headers["content-type"]
    assert r.json()["detail"]


def test_404_htmx_still_gets_json(client):
    r = client.get("/no-such-page", headers={**NAV, "hx-request": "true"})
    assert "application/json" in r.headers["content-type"]


# ── 403 ──────────────────────────────────────────────────────────────────


def test_403_navigation_names_the_account_you_are_signed_in_as(client, monkeypatch):
    """The commonest cause of a 403 here is being on the wrong Discord
    account, and nothing else on the page would tell you which one you are."""
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, PLAIN_ID, "someone")
    r = client.get("/admin/deliveries", headers=NAV)
    assert r.status_code == 403
    assert "text/html" in r.headers["content-type"]
    assert "someone" in r.text
    assert 'href="/"' in r.text


def test_403_xhr_still_gets_json(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, PLAIN_ID, "someone")
    r = client.get("/admin/deliveries")
    assert r.status_code == 403
    assert "application/json" in r.headers["content-type"]


# ── 422 ──────────────────────────────────────────────────────────────────


def test_422_navigation_lists_the_messages_and_offers_to_go_back(client, monkeypatch):
    """422 is almost always a form POST, so an error PAGE throws away what was
    typed. Showing the real messages and offering history.back() -- which
    browsers restore with the field values intact -- is what keeps the page
    from being worse than the JSON blob it replaces."""
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")
    r = client.post(
        "/admin/broadcast/preview",
        data={"mode": "all", "mode_param": "", "body": "   "},
        headers=NAV,
    )
    assert r.status_code == 422
    assert "text/html" in r.headers["content-type"]
    assert "empty" in r.text.lower()
    assert "history.back()" in r.text


def test_422_xhr_still_gets_json(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")
    r = client.post(
        "/admin/broadcast/preview",
        data={"mode": "all", "mode_param": "", "body": "   "},
    )
    assert r.status_code == 422
    assert "application/json" in r.headers["content-type"]


# ── 409: the regression this whole split exists to protect ───────────────


def test_409_from_a_quick_create_dialog_keeps_its_json_detail(client, monkeypatch):
    """THE regression test. _tag_create_dialog.html does
    `(await resp.json()).detail` on a 409 to offer select-existing. If this
    ever returns HTML, that dialog silently degrades to a generic failure."""
    monkeypatch.setattr(settings, "editor_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")
    first = client.post("/tags/quick", data={"name": "Dup", "kind": "artist"})
    assert first.status_code in (200, 201)
    again = client.post("/tags/quick", data={"name": "Dup", "kind": "artist"})
    assert again.status_code == 409
    assert "application/json" in again.headers["content-type"]
    assert isinstance(again.json()["detail"], (str, dict))


# ── 500 ──────────────────────────────────────────────────────────────────


def test_500_navigation_renders_a_page_rather_than_bare_text(db, monkeypatch):
    """Starlette's default 500 is unstyled plain text with no way back -- the
    worst of the four to land on, and the one where a person most needs a
    link home."""
    c = _build(db, monkeypatch, raise_server_exceptions=False)

    @c.app.get("/boom-test")
    async def _boom():
        raise RuntimeError("kaboom")

    r = c.get("/boom-test", headers=NAV)
    assert r.status_code == 500
    assert "text/html" in r.headers["content-type"]
    assert 'href="/"' in r.text


def test_500_still_logs_the_traceback(db, monkeypatch, caplog):
    """A prettier 500 that costs you the stack trace is a bad trade."""
    c = _build(db, monkeypatch, raise_server_exceptions=False)

    @c.app.get("/boom-log")
    async def _boom():
        raise RuntimeError("kaboom-marker")

    with caplog.at_level("ERROR"):
        c.get("/boom-log", headers=NAV)
    assert "kaboom-marker" in caplog.text


# ── Admin tools in Preferences ───────────────────────────────────────────


def test_preferences_shows_admin_tools_to_an_admin(client, monkeypatch):
    """The three admin pages are linked from nowhere else in the site. Before
    this section existed you had to already know the URLs to reach them."""
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    login_as(client, ADMIN_ID, "reiji")
    r = client.get("/preferences", headers=NAV)
    assert r.status_code == 200
    assert 'href="/admin/deliveries"' in r.text
    assert 'href="/admin/broadcast"' in r.text


def test_preferences_hides_admin_tools_from_a_normal_user(client):
    login_as(client, PLAIN_ID, "someone")
    r = client.get("/preferences", headers=NAV)
    assert r.status_code == 200
    assert "/admin/deliveries" not in r.text


def test_the_rehearsal_link_follows_the_flag_that_registers_the_route(client, monkeypatch):
    """Offering a link that 404s is worse than offering none, so the link and
    the route are gated on the same flag."""
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    monkeypatch.setattr(settings, "rehearsal_enabled", False)
    login_as(client, ADMIN_ID, "reiji")
    assert 'href="/admin/rehearsal"' not in client.get("/preferences", headers=NAV).text

    monkeypatch.setattr(settings, "rehearsal_enabled", True)
    assert 'href="/admin/rehearsal"' in client.get("/preferences", headers=NAV).text
