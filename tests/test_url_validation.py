"""URL scheme validation: the domain validator plus its route wiring.

`<input type="url">` happily accepts `javascript:alert(1)` -- it is a
syntactically valid absolute URL -- and these values render straight into
`href` attributes, so a stored one executes in-origin for whoever clicks
it. Everything below pins the two halves of the fix: what
domain.urls.clean_url rejects, and that the routes surface a rejection as
422 without half-writing the row.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.db.models import Concert, Tag
from app.db.session import get_session
from app.domain.urls import UnsafeURLError, clean_url
from app.web import auth
from app.web.app import create_app

EDITOR_ID = 42


# ── Unit: the domain validator ───────────────────────────────────────────


REJECTED = [
    "javascript:alert(1)",
    "JaVaScRiPt:alert(1)",
    " javascript:alert(1)",
    "\tjavascript:alert(1)",
    "\x00javascript:alert(1)",
    "java\tscript:alert(1)",
    "java\nscript:alert(1)",
    "jav\x01ascript:alert(1)",
    "%6Aavascript:alert(1)",
    "//evil.com",
    "http:\\\\evil.com",
    "data:text/html,<script>",
    "vbscript:msgbox",
    "file:///etc/passwd",
]


@pytest.mark.parametrize("raw", REJECTED)
def test_rejects_unsafe_url(raw):
    with pytest.raises(UnsafeURLError):
        clean_url(raw)


@pytest.mark.parametrize("raw", [
    "https://example.com/",
    "http://example.com/path?q=1#frag",
    "https://eplus.jp/sf/detail/1234",
    "HTTPS://Example.com/Path",
])
def test_accepts_http_and_https_unchanged(raw):
    assert clean_url(raw) == raw


def test_interior_space_is_preserved():
    # Browsers percent-encode an interior space; they do not delete it.
    # Deleting it would silently turn this into a different URL.
    assert clean_url("https://ex.com/a b") == "https://ex.com/a b"


def test_surrounding_whitespace_is_trimmed():
    assert clean_url("  https://ex.com/x \t\n") == "https://ex.com/x"


@pytest.mark.parametrize("raw", ["", "   ", "\t\n", "\x00", None])
def test_blank_is_none(raw):
    assert clean_url(raw) is None


def test_host_is_required():
    with pytest.raises(UnsafeURLError):
        clean_url("https:///no-host")


# ── Route wiring ─────────────────────────────────────────────────────────


@pytest.fixture()
def client(db, monkeypatch):
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


def login_as(client, discord_id: int, name: str):
    async def fake_identity(token):
        return {"id": str(discord_id), "username": name, "global_name": name, "avatar": None}

    client.monkeypatch.setattr(auth, "fetch_identity", fake_identity)
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    client.get(f"/auth/callback?code=x&state={state}")


def concert_form(**overrides):
    data = {
        "title": "C", "title_en": "C", "title_zh": "C", "event_id": "c",
        "round_label": ["R1"], "round_kind": ["lottery_round"],
        "round_opens_at": [""], "round_closes_at": ["2099-06-25T23:59"],
        "round_results_at": [""], "round_payment_at": [""],
        "round_label_en": ["R1"],
        "round_label_zh": ["R1"], "round_url": [""], "round_notes": [""], "round_leg": [""],
    }
    data.update(overrides)
    return data


async def test_create_concert_rejects_javascript_url(client, db):
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/concerts", data=concert_form(official_url="javascript:alert(1)"))
    assert r.status_code == 422
    # Pin WHY. require_variants runs before form_url in create_concert, so a
    # payload that drifted out of full title/notes coverage would still 422 --
    # for the translation reason -- and the "nothing was written" assertion
    # below cannot tell the two apart, since neither path writes.
    assert "http://" in r.json()["detail"], r.json()
    async with db() as s:
        assert (await s.execute(select(Concert))).scalars().first() is None


async def test_create_concert_rejects_javascript_round_url(client, db):
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/concerts", data=concert_form(round_url=["javascript:alert(1)"]))
    assert r.status_code == 422
    async with db() as s:
        assert (await s.execute(select(Concert))).scalars().first() is None


async def test_edit_concert_rejects_javascript_url_and_keeps_original(client, db):
    login_as(client, EDITOR_ID, "reiji")
    assert client.post("/concerts", data=concert_form(title="Original")).status_code == 303

    # A CHANGED title rides along: if the rejection ever degraded into a
    # partial write, "Changed" would survive and this would catch it.
    r = client.post("/concerts/c/edit", data=concert_form(
        title="Changed", eventernote_url="javascript:alert(1)",
    ))
    assert r.status_code == 422
    async with db() as s:
        concert = (await s.execute(select(Concert))).scalars().one()
        assert concert.title == "Original"
        assert concert.eventernote_url is None


async def test_create_tag_rejects_javascript_location_url(client, db):
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/tags", data={"name_en": "Budokan", "name_zh": "Budokan",
        "name": "Budokan", "kind": "venue", "location_url": "javascript:alert(1)",
    })
    assert r.status_code == 422
    async with db() as s:
        assert (await s.execute(select(Tag))).scalars().first() is None


async def test_edit_tag_rejects_javascript_location_url_and_keeps_original(client, db):
    login_as(client, EDITOR_ID, "reiji")
    assert client.post("/tags", data={"name_en": "Budokan", "name_zh": "Budokan",
        "name": "Budokan", "kind": "venue", "location_url": "https://maps.example/budokan",
    }).status_code == 303
    async with db() as s:
        tag_id = (await s.execute(select(Tag))).scalars().one().id

    r = client.post(f"/tags/{tag_id}/edit", data={
        "name": "Renamed", "location_url": "javascript:alert(1)",
    })
    assert r.status_code == 422
    async with db() as s:
        tag = (await s.execute(select(Tag))).scalars().one()
        assert tag.name == "Budokan"
        assert tag.location_url == "https://maps.example/budokan"


async def test_valid_urls_round_trip(client, db):
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/concerts", data=concert_form(
        official_url="https://example.com/e",
        round_url=["https://eplus.jp/sf/detail/1"],
    ))
    assert r.status_code == 303
    async with db() as s:
        concert = (await s.execute(select(Concert))).scalars().one()
        assert concert.official_url == "https://example.com/e"
