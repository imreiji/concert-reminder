"""The approval screen: admin-only, English-only, and one decision per host.

Same fixture shape as tests/test_draft_completion_web.py -- this suite has no
shared conftest fixture for an HTTP client, so each file that needs one builds
its own db/client/admin_client/editor_client/session fixtures from the same
TestClient + OAuth-stub pattern.
"""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from app.config import settings
from app.db.models import FetchDomain
from app.db.service import note_fetch_domain
from app.db.session import get_session
from app.web import auth
from app.web.app import create_app

ADMIN_ID, EDITOR_ID = 42, 77
NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


@pytest.fixture()
def client(db, monkeypatch):
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
    monkeypatch.setattr(settings, "editor_whitelist", str(EDITOR_ID))
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


def login_as(client, discord_id, name):
    async def fake_identity(token):
        return {"id": str(discord_id), "username": name, "global_name": name, "avatar": None}

    client.monkeypatch.setattr(auth, "fetch_identity", fake_identity)
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    client.get(f"/auth/callback?code=x&state={state}")


@pytest.fixture()
def admin_client(client):
    login_as(client, ADMIN_ID, "admin")
    return client


@pytest.fixture()
def editor_client(client):
    login_as(client, EDITOR_ID, "editor")
    return client


@pytest_asyncio.fixture()
async def session(db):
    async with db() as s:
        yield s


def test_a_non_admin_editor_is_refused(editor_client):
    assert editor_client.get("/admin/fetch-domains").status_code == 403


@pytest.mark.asyncio
async def test_the_page_lists_a_waiting_host_and_what_wanted_it(admin_client, session):
    await note_fetch_domain(session, "eplus.jp", "https://eplus.jp/sf/detail/1", NOW)
    await session.commit()
    body = admin_client.get("/admin/fetch-domains").text
    assert "eplus.jp" in body
    assert "https://eplus.jp/sf/detail/1" in body


@pytest.mark.asyncio
async def test_approving_makes_the_host_fetchable(admin_client, session):
    row = await note_fetch_domain(session, "eplus.jp", "https://eplus.jp/a", NOW)
    await session.commit()
    # Captured BEFORE expire_all() below: that call expires every attribute of
    # every object in this session, PRIMARY KEY INCLUDED (the same aiosqlite
    # gotcha test_draft_completion_preview.py documents) -- row.id read AFTER
    # expiry would trigger a lazy load outside the greenlet SQLAlchemy's async
    # path requires, which raises MissingGreenlet rather than a value.
    row_id = row.id
    r = admin_client.post(f"/admin/fetch-domains/{row_id}/approve", follow_redirects=False)
    assert r.status_code == 303
    session.expire_all()
    assert (await session.get(FetchDomain, row_id)).approved_at is not None


@pytest.mark.asyncio
async def test_declining_sticks(admin_client, session):
    row = await note_fetch_domain(session, "spam.example", "https://spam.example/a", NOW)
    await session.commit()
    row_id = row.id  # captured before expire_all(), see the comment above
    admin_client.post(f"/admin/fetch-domains/{row_id}/decline")
    # A second, contradictory press must not flip a decision already made.
    admin_client.post(f"/admin/fetch-domains/{row_id}/approve")
    session.expire_all()
    refreshed = await session.get(FetchDomain, row_id)
    assert refreshed.declined_at is not None and refreshed.approved_at is None


def test_an_unknown_id_404s(admin_client):
    assert admin_client.post("/admin/fetch-domains/999/approve").status_code == 404


def test_the_page_is_linked_from_preferences(admin_client):
    assert "/admin/fetch-domains" in admin_client.get("/preferences").text
