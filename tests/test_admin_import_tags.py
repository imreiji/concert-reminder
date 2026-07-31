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
  - {handle: kozue, name: "乙宗梢", name_en: Kozue Otomune, kind: artist}
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


async def test_a_good_file_is_previewed_then_applied(client):
    """REWRITTEN 2026-07-31: the route was one step and now it is two. Looking
    is no longer doing, which is the whole point of the conflict flow."""
    login_as(client, ADMIN_ID, "reiji")
    preview = client.post("/admin/import/tags", data={"text": FILE}).text
    assert "love-live" in preview and "kozue" in preview
    async with client.db() as s:
        assert (await s.execute(select(Tag))).scalars().all() == [], "preview writes nothing"

    body = client.post("/admin/import/tags/apply", data={"text": FILE}).text
    assert "2" in body
    async with client.db() as s:
        assert sorted(t.slug for t in (await s.execute(select(Tag))).scalars()) == [
            "kozue", "love-live",
        ]


async def test_a_second_import_has_nothing_to_do(client):
    """REWRITTEN 2026-07-31 alongside the two-step flow."""
    login_as(client, ADMIN_ID, "reiji")
    client.post("/admin/import/tags/apply", data={"text": FILE})
    body = client.post("/admin/import/tags/apply", data={"text": FILE}).text
    assert "unchanged" in body.lower()
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


# ── The two-step flow: preview, choose, apply ─────────────────────────────


async def test_a_conflict_is_shown_with_both_values(client):
    login_as(client, ADMIN_ID, "reiji")
    client.post("/admin/import/tags/apply", data={"text": FILE})
    body = client.post("/admin/import/tags", data={
        "text": 'tags:\n  - {handle: kozue, name: "乙宗梢", name_en: Renamed, kind: artist}\n',
    }).text
    assert "Kozue Otomune" in body, "the catalogue's value"
    assert "Renamed" in body, "the file's value"
    assert 'name="conflict__kozue__name_en"' in body


async def test_apply_honours_a_theirs_choice(client):
    login_as(client, ADMIN_ID, "reiji")
    client.post("/admin/import/tags/apply", data={"text": FILE})
    newer = 'tags:\n  - {handle: kozue, name: "乙宗梢", name_en: Renamed, kind: artist}\n'
    client.post("/admin/import/tags/apply", data={
        "text": newer, "conflict__kozue__name_en": "theirs",
    })
    async with client.db() as s:
        row = (await s.execute(select(Tag).where(Tag.slug == "kozue"))).scalar_one()
        assert row.name_en == "Renamed"


async def test_apply_with_no_choices_changes_nothing_it_was_not_asked_to(client):
    login_as(client, ADMIN_ID, "reiji")
    client.post("/admin/import/tags/apply", data={"text": FILE})
    newer = 'tags:\n  - {handle: kozue, name: "乙宗梢", name_en: Renamed, kind: artist}\n'
    client.post("/admin/import/tags/apply", data={"text": newer})
    async with client.db() as s:
        row = (await s.execute(select(Tag).where(Tag.slug == "kozue"))).scalar_one()
        assert row.name_en == "Kozue Otomune"


async def test_a_forged_choice_value_is_refused(client):
    """Only the literal strings are accepted, and the VALUE always comes from
    the re-parsed file -- so there is nothing to inject.

    Asserts the raw tag rather than the substring "alert(1)": base.html contains
    that exact string inside its own comment about invariant 7, so the naive
    check matches the codebase explaining the attack rather than suffering it.
    """
    login_as(client, ADMIN_ID, "reiji")
    payload = "<script>alert(1)</script>"
    r = client.post("/admin/import/tags/apply", data={
        "text": FILE, "conflict__kozue__name_en": payload,
    })
    assert r.status_code == 200
    assert payload not in r.text, "the forged value must never be echoed unescaped"

    async with client.db() as s:
        row = (await s.execute(select(Tag).where(Tag.slug == "kozue"))).scalar_one()
        assert row.name_en == "Kozue Otomune", "and it must never be WRITTEN"


def test_an_editor_cannot_reach_apply(client):
    login_as(client, EDITOR_ID, "editor")
    assert client.post("/admin/import/tags/apply", data={"text": FILE}).status_code == 403
