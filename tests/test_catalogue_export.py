"""The admin catalogue export: complete, personal-data-free, reproducible."""

import io
import time
import zipfile

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.db.models import Concert, Tag
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


# ── the concert draft carries CHARACTER tags (2026-08-01) ────────────────
#
# Until this, `concert_to_yaml` emitted franchises/groups/artists only, so
# `export.zip` was not a faithful backup of an im@s concert: on a restore the
# derived seiyuu came back (she is an ARTIST row and was written as one) and
# the character -- the reason the bill reads the way it does -- was silently
# gone. `import_commit` had accepted `character_tags` since the kind shipped;
# only the FILE could not say one.


def _seed_character_concert(client):
    login_as(client, ADMIN_ID, "reiji")
    client.post("/tags", data={
        "name": "今井麻美", "name_en": "Asami Imai", "name_zh": "今井麻美", "kind": "artist",
    })
    client.post("/tags", data={
        "name": "如月千早", "name_en": "Chihaya Kisaragi", "name_zh": "如月千早",
        "kind": "character", "voiced_by_tag_id": 1,
    })
    r = client.post("/concerts", data={
        "title": "THE IDOLM@STER 10th", "title_en": "THE IDOLM@STER 10th",
        "title_zh": "偶像大师 10th", "event_id": "imas-10th", "character_tags": [2],
    })
    assert r.status_code == 303, r.text


def test_the_concert_draft_carries_characters_by_name_and_handle(client):
    _seed_character_concert(client)
    draft = _entries(client.get("/admin/export.zip").content)["concerts/imas-10th.yaml"]
    data = yaml.safe_load(draft)
    assert data["series"]["characters"] == ["如月千早"]
    assert data["series_handles"]["characters"] == ["chihaya-kisaragi"]
    # Her seiyuu is attached too (attach_tag's chained step) and still exports
    # as the ARTIST she is -- the character is an ADDITION, not a replacement.
    assert data["series"]["artists"] == ["今井麻美"]


async def test_a_character_concert_survives_the_export_import_round_trip(client):
    """The whole promise, for the shape the branch exists for: export, wipe the
    concert, re-import its own draft, and the character is back on it."""
    _seed_character_concert(client)
    draft = _entries(client.get("/admin/export.zip").content)["concerts/imas-10th.yaml"]

    async with client.db() as s:
        concert = (await s.execute(
            select(Concert).where(Concert.event_id == "imas-10th")
        )).scalar_one()
        await s.delete(concert)
        await s.commit()

    # The draft producer and the preview are one path: paste it back, and the
    # preview's own hidden fields are what import_commit receives.
    r = client.post("/concerts/import/draft", data={"draft": draft})
    assert r.status_code == 200, r.text
    assert '"2"' in r.text.split("const INITIAL_SELECTED = ")[1].split(";\n")[0], (
        "the character did not survive the file into the picker's selection"
    )
