"""The shared tag picker's inline <script> must not be breakable by tag names.

Tag names are editor-supplied free text (100 chars, no character
restriction), and they are interpolated into a `<script>` block as JSON.
json.dumps does NOT escape "</", so a tag named "</script>..." used to end
the script element early and turn the rest of the name into live markup --
editor-to-editor stored XSS on every page that renders the picker.

Two things are asserted for each of those pages: that the payload cannot
break out, and that the three constants still deserialize to real
objects (the `| tojson` fix breaks silently if the view hands it an
already-json.dumps-ed string -- the constant then becomes a quoted string
literal and the picker stops working).
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
from app.db.models import Base, Concert, ConcertTag, Tag, TagMember
from app.db.session import get_session
from app.domain.types import TagKind
from app.web import auth
from app.web.app import create_app
from app.web.routes import imports as import_routes

EDITOR_ID = 42
PAYLOAD = "</script><img src=x onerror=alert(1)>"
FIXTURES = Path(__file__).parent / "fixtures"
GRADUATION_URL = "https://ramen.events/hasunosora-103rd-class-graduation-concert/"


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


async def seed(db) -> dict:
    """A GROUP tag whose name is the XSS payload, one member artist, and a
    concert carrying that group so the edit page renders it too."""
    async with db() as s:
        group = Tag(name=PAYLOAD, kind=TagKind.GROUP, created_by=EDITOR_ID)
        member = Tag(name="Member " + PAYLOAD, kind=TagKind.ARTIST, created_by=EDITOR_ID)
        s.add_all([group, member])
        await s.flush()
        s.add(TagMember(group_tag_id=group.id, member_tag_id=member.id))
        concert = Concert(event_id="xss-1", title="Payload concert", created_by=EDITOR_ID)
        s.add(concert)
        await s.flush()
        s.add(ConcertTag(concert_id=concert.id, tag_id=group.id))
        await s.commit()
        return {"group_id": group.id, "member_id": member.id, "event_id": concert.event_id}


def js_const(html: str, name: str):
    """Parse one of the picker's inline JS constants back out of the page."""
    m = re.search(rf"const {name} = (.*);", html)
    assert m, f"{name} not found in rendered page"
    return json.loads(m.group(1))


def render(client, page: str, ids: dict) -> str:
    if page == "new":
        r = client.get("/concerts/new")
    elif page == "edit":
        r = client.get(f"/concerts/{ids['event_id']}/edit")
    else:
        async def fake_fetch(url: str) -> str:
            return (FIXTURES / "ramen_graduation_concert.html").read_text(encoding="utf-8")

        client.monkeypatch.setattr(import_routes, "fetch_ramen_html", fake_fetch)
        r = client.post("/concerts/import/preview", data={"url": GRADUATION_URL})
    assert r.status_code == 200
    return r.text


PAGES = ["new", "edit", "import_preview"]


@pytest.mark.parametrize("page", PAGES)
async def test_tag_name_cannot_break_out_of_the_script_block(client, db, page):
    login_as(client, EDITOR_ID, "reiji")  # also creates the editor's users row
    ids = await seed(db)
    html = render(client, page, ids)

    # The literal breakout sequence must appear nowhere -- not in the JSON
    # constants, not in the chip list (Jinja autoescapes that one already).
    assert "</script><img" not in html
    # ...and the payload must still be in the page, escaped, so we know the
    # tag actually rendered rather than the test passing vacuously.
    assert "\\u003c/script\\u003e" in html
    # Every </script> in the page must close a <script> we actually opened.
    assert html.count("<script") == html.count("</script>")


@pytest.mark.parametrize("page", PAGES)
async def test_picker_constants_are_objects_not_strings(client, db, page):
    """Regression guard for the double-encoding trap: `| tojson` serializes
    the Python object it is given, so a pre-json.dumps-ed string would come
    out as a quoted JS string literal and the picker would silently die."""
    login_as(client, EDITOR_ID, "reiji")
    ids = await seed(db)
    html = render(client, page, ids)

    groups = js_const(html, "NC_GROUPS")
    tag_names = js_const(html, "TAG_NAMES")
    initial = js_const(html, "INITIAL_SELECTED")

    for name, value in [("NC_GROUPS", groups), ("TAG_NAMES", tag_names),
                        ("INITIAL_SELECTED", initial)]:
        assert isinstance(value, dict), f"{name} deserialized to {type(value).__name__}"

    gid = str(ids["group_id"])
    assert groups[gid]["name"] == PAYLOAD
    assert [m["id"] for m in groups[gid]["members"]] == [ids["member_id"]]
    assert tag_names[gid] == PAYLOAD

    if page == "edit":
        assert initial["group"] == [gid]
    else:
        assert initial == {}
