"""XSS regression tests for editor-authored strings rendered into HTML.

Tag names are editor-authored free text and are rendered in a hostile
position: inside a <script> block (the shared tag picker's JS constants),
where plain HTML-escaping is NOT sufficient, so the picker gets dedicated
tests here rather than relying on the feature tests that only cover the
happy path.
"""

import json
import re
from pathlib import Path

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
from app.web.routes import imports as import_routes

EDITOR_ID = 42
FIXTURES = Path(__file__).parent / "fixtures"
GRADUATION_URL = "https://ramen.events/hasunosora-103rd-class-graduation-concert/"

# Breaks out of a <script> block: the HTML tokenizer ends the script at the
# literal "</" regardless of JS string quoting, so json.dumps alone is not enough.
SCRIPT_PAYLOAD = "</script><img src=x onerror=alert(1)>"


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


# ── Helpers ──────────────────────────────────────────────────────────────

_CONST_RE = re.compile(r"const (NC_GROUPS|TAG_NAMES|INITIAL_SELECTED) = (.*);")


def js_constants(html: str) -> dict:
    """Parse the picker's three JS constants back out of the rendered page."""
    return {m.group(1): json.loads(m.group(2)) for m in _CONST_RE.finditer(html)}


def assert_script_block_intact(html: str) -> None:
    assert SCRIPT_PAYLOAD not in html, "payload rendered literally: script block broken out of"
    assert html.count("<script") == html.count("</script"), "unbalanced script tags"


def seed_payload_group(client) -> None:
    """A GROUP tag lands in both NC_GROUPS and TAG_NAMES, covering both blobs."""
    r = client.post("/tags", data={
        "name_en": SCRIPT_PAYLOAD, "name_zh": SCRIPT_PAYLOAD, "name": SCRIPT_PAYLOAD,
        "kind": "group",
    })
    assert r.status_code == 303


def picker_pages(client) -> dict[str, str]:
    """Every page that includes _tag_picker_script.html, rendered."""
    client.post("/concerts", data={"title_en": "C", "title_zh": "C", "title": "C", "event_id": "c"})
    new = client.get("/concerts/new")
    edit = client.get("/concerts/c/edit")

    async def fake_fetch(url: str) -> str:
        return (FIXTURES / "ramen_graduation_concert.html").read_text(encoding="utf-8")

    client.monkeypatch.setattr(import_routes, "fetch_ramen_html", fake_fetch)
    preview = client.post("/concerts/import/preview", data={"url": GRADUATION_URL})
    for r in (new, edit, preview):
        assert r.status_code == 200
    return {"/concerts/new": new.text, "/concerts/c/edit": edit.text, "preview": preview.text}


# ── The tag picker's JS constants ────────────────────────────────────────


def test_tag_picker_constants_escape_script_terminator(client):
    login_as(client, EDITOR_ID, "reiji")
    seed_payload_group(client)
    for page, html in picker_pages(client).items():
        assert_script_block_intact(html)
        # The name must still be THERE, just inert -- an escaping fix that
        # silently dropped the tag would otherwise pass the check above.
        assert "alert(1)" in html, f"{page}: payload vanished entirely"
        assert "\\u003c/script\\u003e" in html, f"{page}: '<' not escaped"


def test_tag_picker_constants_are_objects_not_strings(client):
    """Regression guard for the double-encoding trap: `| tojson` serializes the
    Python object, so the producers must pass raw dicts. Pre-serializing with
    json.dumps first yields a JS *string* -- still perfectly escaped, so a
    pure escaping test cannot catch it, but the picker is silently dead."""
    login_as(client, EDITOR_ID, "reiji")
    seed_payload_group(client)
    for page, html in picker_pages(client).items():
        consts = js_constants(html)
        assert set(consts) == {"NC_GROUPS", "TAG_NAMES", "INITIAL_SELECTED"}, page
        for name, value in consts.items():
            assert isinstance(value, dict), f"{page}: {name} deserialized to {type(value)}"


def test_tag_picker_group_members_still_populate(client):
    """The picker's whole job: NC_GROUPS maps group id -> members."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={
        "name_en": "Hasunosora", "name_zh": "Hasunosora", "name": "Hasunosora", "kind": "group",
    })
    client.post("/tags", data={
        "name_en": "Kozue Otomune", "name_zh": "Kozue Otomune", "name": "Kozue Otomune",
        "kind": "artist",
    })
    client.post("/tags/1/members", data={"member_tag_id": 2})
    consts = js_constants(client.get("/concerts/new").text)
    assert consts["NC_GROUPS"]["1"]["members"] == [{"id": 2, "name": "Kozue Otomune"}]
    assert consts["TAG_NAMES"]["1"] == "Hasunosora"
