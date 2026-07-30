"""The admin tags-import surface: gate, report, and the failure copy."""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base, Tag
from app.db.session import get_session
from app.web import auth
from app.web.app import create_app

ADMIN_ID, EDITOR_ID = 42, 77

FILE = """
tags:
  - {handle: love-live, name: "ラブライブ！", name_en: Love Live!, kind: franchise}
  - {handle: kozue, name: "乙宗梢", kind: artist}
"""


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
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


def test_an_editor_cannot_reach_either_half(client):
    login_as(client, EDITOR_ID, "editor")
    assert client.get("/admin/import/tags").status_code == 403
    assert client.post("/admin/import/tags", data={"text": FILE}).status_code == 403


def test_the_admin_gets_a_paste_form(client):
    login_as(client, ADMIN_ID, "reiji")
    body = client.get("/admin/import/tags").text
    assert 'name="text"' in body
    assert "tags.yaml" in body


async def test_a_good_file_creates_the_tags_and_reports(client):
    login_as(client, ADMIN_ID, "reiji")
    body = client.post("/admin/import/tags", data={"text": FILE}).text
    assert "love-live" in body and "kozue" in body
    assert "Created 2" in body

    async with client.db() as s:
        assert sorted(t.slug for t in (await s.execute(select(Tag))).scalars()) == [
            "kozue", "love-live",
        ]


async def test_a_second_import_reports_skips_and_writes_nothing(client):
    login_as(client, ADMIN_ID, "reiji")
    client.post("/admin/import/tags", data={"text": FILE})
    body = client.post("/admin/import/tags", data={"text": FILE}).text
    assert "Created 0" in body
    assert "Skipped 2" in body
    async with client.db() as s:
        assert len((await s.execute(select(Tag))).scalars().all()) == 2


async def test_an_unparseable_file_reports_and_writes_nothing(client):
    login_as(client, ADMIN_ID, "reiji")
    body = client.post("/admin/import/tags", data={"text": "{["}).text
    assert "does not parse as YAML" in body
    async with client.db() as s:
        assert (await s.execute(select(Tag))).scalars().all() == []


def test_warnings_are_shown_not_swallowed(client):
    login_as(client, ADMIN_ID, "reiji")
    body = client.post("/admin/import/tags", data={
        "text": "tags:\n  - {name: no handle, kind: artist}\n",
    }).text
    assert "handle" in body


def test_an_oversized_paste_is_refused(client):
    login_as(client, ADMIN_ID, "reiji")
    body = client.post("/admin/import/tags", data={"text": "x" * 200_001}).text
    assert "200k" in body


def test_preferences_links_the_importer_for_an_admin(client):
    login_as(client, ADMIN_ID, "reiji")
    assert "/admin/import/tags" in client.get("/preferences").text
