"""The admin catalogue export: complete, personal-data-free, reproducible."""

import io
import time
import zipfile

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


def _entries(payload: bytes) -> dict[str, str]:
    """EXTRACT every entry. Searching the raw zip bytes would pass vacuously --
    entries are DEFLATE-compressed, so a string is not there to find even when
    it is in the data."""
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        return {n: zf.read(n).decode("utf-8") for n in zf.namelist()}


def _seed(client):
    login_as(client, ADMIN_ID, "reiji")
    client.post("/tags", data={
        "name": "ラブライブ！", "name_en": "Love Live!", "name_zh": "LL", "kind": "franchise",
    })
    client.post("/tags", data={
        "name": "乙宗梢", "name_en": "Kozue Otomune", "name_zh": "乙宗梢", "kind": "artist",
    })
    client.post("/concerts", data={
        "title": "蓮ノ空 6th", "title_en": "Hasunosora 6th", "title_zh": "6th",
        "event_id": "hasunosora-6th", "franchise_tags": [1],
    })


def test_an_editor_cannot_download_it(client):
    login_as(client, EDITOR_ID, "editor")
    assert client.get("/admin/export.zip").status_code == 403


def test_the_zip_contains_tags_concerts_and_restore_notes(client):
    _seed(client)
    entries = _entries(client.get("/admin/export.zip").content)
    assert "tags.yaml" in entries
    assert "concerts/hasunosora-6th.yaml" in entries
    assert "RESTORE.txt" in entries
    assert "tags.yaml" in entries["RESTORE.txt"], "the restore ORDER is the point"


def test_tags_yaml_carries_handles_and_every_field(client):
    _seed(client)
    tags_yaml = _entries(client.get("/admin/export.zip").content)["tags.yaml"]
    assert "handle: love-live" in tags_yaml
    assert "name_en: Love Live!" in tags_yaml
    assert "kind: franchise" in tags_yaml


def test_the_concert_draft_carries_its_event_id_and_handles(client):
    _seed(client)
    draft = _entries(client.get("/admin/export.zip").content)["concerts/hasunosora-6th.yaml"]
    assert "event_id: hasunosora-6th" in draft
    assert "series_handles" in draft
    assert "love-live" in draft
    assert "slug:" not in draft


def test_no_personal_data_anywhere(client):
    """By construction, not by filter: the queries never reach a user table."""
    _seed(client)
    for name, text in _entries(client.get("/admin/export.zip").content).items():
        assert "created_by" not in text, name
        assert "reiji" not in text, name


def test_two_exports_are_byte_identical(client):
    """A backup you cannot diff is worth much less. ZipFile.writestr stamps
    every entry with the current time and zip timestamps have TWO-SECOND
    resolution, so the sleep here has to cross a bucket -- a 1s sleep passes on
    luck and proves nothing."""
    _seed(client)
    first = client.get("/admin/export.zip").content
    time.sleep(2.5)
    assert client.get("/admin/export.zip").content == first


def test_preferences_links_the_export_for_an_admin(client):
    login_as(client, ADMIN_ID, "reiji")
    assert "/admin/export.zip" in client.get("/preferences").text


async def test_the_export_round_trips_through_the_importer(client):
    """The end-to-end promise: export, drop every tag, import tags.yaml, and the
    taxonomy is back -- field for field, parents and memberships included."""
    _seed(client)
    client.post("/tags/1/members", data={"member_tag_id": 2})
    entries = _entries(client.get("/admin/export.zip").content)

    def snapshot(tags):
        return {
            t.slug: (t.name, t.name_en, t.name_zh, t.kind, t.region, t.city, t.address)
            for t in tags
        }

    async with client.db() as s:
        before = snapshot((await s.execute(select(Tag))).scalars())
        for tag in (await s.execute(select(Tag))).scalars().all():
            await s.delete(tag)
        await s.commit()

    # /apply, not /tags: since 2026-07-31 the latter only PREVIEWS. Nothing to
    # resolve here -- restoring into an emptied catalogue is all creates.
    client.post("/admin/import/tags/apply", data={"text": entries["tags.yaml"]})

    async with client.db() as s:
        after = snapshot((await s.execute(select(Tag))).scalars())
    assert after == before
