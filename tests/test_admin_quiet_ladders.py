"""The round-watch worklist: admin-only, and it writes only the recheck stamp."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.db.models import Concert
from app.db.service import ensure_user
from app.db.session import get_session
from app.web import auth
from app.web.app import create_app

ADMIN_ID, EDITOR_ID = 42, 77


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


async def test_the_page_renders_for_an_admin(client):
    """Every page needs a logged-in GET render test: a missing one shipped a
    500 once, from template context drift."""
    async with client.db() as s:
        await ensure_user(s, ADMIN_ID, "reiji")
        s.add(Concert(title="ブシロード20周年", event_id="bushi", created_by=ADMIN_ID))
        await s.commit()

    login_as(client, ADMIN_ID, "reiji")
    r = client.get("/admin/quiet-ladders")
    assert r.status_code == 200
    assert "bushi" in r.text


async def test_an_editor_is_forbidden(client):
    login_as(client, EDITOR_ID, "editor")
    assert client.get("/admin/quiet-ladders").status_code == 403


async def test_signed_out_is_redirected_not_an_error(client):
    r = client.get("/admin/quiet-ladders")
    assert r.status_code == 303


async def test_checked_stamps_and_redirects(client):
    async with client.db() as s:
        await ensure_user(s, ADMIN_ID, "reiji")
        s.add(Concert(title="Quiet", event_id="quiet", created_by=ADMIN_ID))
        await s.commit()

    login_as(client, ADMIN_ID, "reiji")
    r = client.post("/admin/quiet-ladders/quiet/checked")
    assert r.status_code == 303

    async with client.db() as s:
        c = (await s.execute(
            select(Concert).where(Concert.event_id == "quiet")
        )).scalar_one()
        assert c.ladder_rechecked_at_utc is not None


async def test_checking_an_unknown_concert_is_404(client):
    login_as(client, ADMIN_ID, "reiji")
    assert client.post("/admin/quiet-ladders/nope/checked").status_code == 404
