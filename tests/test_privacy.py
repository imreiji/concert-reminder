"""The public privacy policy page.

The important property here is that GET /privacy renders for an ANONYMOUS
visitor: every other page in this app sits behind require_user, but a
privacy policy nobody can read without logging in is useless (and Discord's
app reviewers read it signed out).

The contact details are deployment config, never committed -- so the page
must also render correctly with both set, one set, or neither set.
"""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base
from app.db.session import get_session
from app.web import auth
from app.web.app import create_app

FAN_ID = 4242


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
    # Default to an unconfigured deploy; individual tests set what they need.
    monkeypatch.setattr(settings, "privacy_contact_discord", "")
    monkeypatch.setattr(settings, "privacy_contact_email", "")
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


# ── reachability ─────────────────────────────────────────────────────────


def test_privacy_renders_for_anonymous_visitor(client):
    """The whole point: no login, no redirect, no 401."""
    r = client.get("/privacy")
    assert r.status_code == 200
    assert "Privacy policy" in r.text
    # rendered through base.html in its logged-out state
    assert "Sign in with Discord" in r.text


def test_privacy_renders_for_logged_in_user(client):
    login_as(client, FAN_ID, "fan")
    r = client.get("/privacy")
    assert r.status_code == 200
    assert "Privacy policy" in r.text
    assert "fan" in r.text  # header shows the session user


def test_privacy_is_linked_from_the_footer(client):
    r = client.get("/")
    assert r.status_code == 200
    assert 'href="/privacy"' in r.text


def test_privacy_uses_the_legal_frame(client):
    """Long-form prose gets the design system's .legal wrapper (constrained
    measure, heading hierarchy) -- ported from the onboarding demo, not a
    bespoke one-off. Checked signed out since that's the page's main
    audience (Discord reviewers, prospective users)."""
    r = client.get("/privacy")
    assert r.status_code == 200
    assert 'class="legal"' in r.text


# ── contact configuration ────────────────────────────────────────────────


def test_both_contacts_render(client):
    client.monkeypatch.setattr(settings, "privacy_contact_discord", "someone_here")
    client.monkeypatch.setattr(settings, "privacy_contact_email", "someone@example.test")
    r = client.get("/privacy")
    assert r.status_code == 200
    assert "someone_here" in r.text
    assert "someone@example.test" in r.text
    assert "mailto:someone@example.test" in r.text


def test_only_email_set_omits_the_discord_line(client):
    client.monkeypatch.setattr(settings, "privacy_contact_email", "someone@example.test")
    r = client.get("/privacy")
    assert r.status_code == 200
    assert "someone@example.test" in r.text
    assert "Discord:" not in r.text
    assert "None" not in r.text


def test_only_discord_set_omits_the_email_line(client):
    client.monkeypatch.setattr(settings, "privacy_contact_discord", "someone_here")
    r = client.get("/privacy")
    assert r.status_code == 200
    assert "someone_here" in r.text
    assert "mailto:" not in r.text
    assert "None" not in r.text


def test_no_contacts_still_renders_a_neutral_fallback(client):
    """An unconfigured deploy must not 500 on a public page, and must not
    show an empty bullet or the word "None"."""
    r = client.get("/privacy")
    assert r.status_code == 200
    assert "Privacy policy" in r.text
    assert "None" not in r.text
    assert "mailto:" not in r.text
    assert "Discord:" not in r.text
    # a human-readable fallback stands in for the missing contacts
    assert "contact details" in r.text


def test_contact_values_are_escaped_not_injected(client):
    """Config is owner-controlled, but it still lands in HTML -- it must go
    through Jinja's autoescaping like everything else."""
    client.monkeypatch.setattr(settings, "privacy_contact_discord", "<script>alert(1)</script>")
    r = client.get("/privacy")
    assert r.status_code == 200
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text


def test_malformed_email_is_not_turned_into_a_mailto_link(client):
    """A value that isn't an email address is shown as plain text rather
    than becoming a broken (or scheme-smuggling) href."""
    client.monkeypatch.setattr(settings, "privacy_contact_email", "javascript:alert(1)")
    r = client.get("/privacy")
    assert r.status_code == 200
    assert "mailto:" not in r.text
    assert "href=\"javascript:" not in r.text


# ── content accuracy ─────────────────────────────────────────────────────


def test_policy_covers_the_required_sections(client):
    r = client.get("/privacy")
    body = r.text
    for heading in (
        "What we collect",
        "What we do not collect",
        "Why we collect it",
        "Sharing",
        "How long we keep it",
        "Cookies",
        "Your rights",
        "Changes to this policy",
    ):
        assert heading in body, heading
    assert "2026-07-19" in body  # last updated
