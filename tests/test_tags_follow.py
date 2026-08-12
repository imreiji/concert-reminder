"""Every chip on /tags follows or unfollows, for everyone, without JS.

Before this, the only follow control on the page was in the table view, and
the chips-vs-table toggle is editor-gated -- so a non-editor was shipped a
hidden table full of follow buttons they had no way to reveal, and the chips
they could see were inert <span>s.
"""

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db.models import Tag, TagSubscription
from app.db.session import get_session
from app.domain.types import TagKind
from app.web import auth
from app.web.app import create_app

EDITOR_ID, VIEWER_ID = 42, 777


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


async def _seed_tag(client) -> int:
    async with client.db() as s:
        s.add(Tag(name="Aqours", name_en="Aqours", name_zh="Aqours",
                   kind=TagKind.FRANCHISE, slug="aqours"))
        await s.commit()
    return 1


async def test_a_non_editor_can_follow_from_the_chips(client):
    """The whole point. VIEWER_ID is not an editor."""
    login_as(client, VIEWER_ID, "viewer")
    await _seed_tag(client)
    r = client.get("/tags")
    assert 'action="/subscriptions"' in r.text, "a real form, not a JS handler"
    assert 'name="next" value="/tags"' in r.text, "and it comes back here"


async def test_following_a_tag_makes_its_chip_offer_unfollow(client):
    """The chip carries state: follow -> unfollow, and back."""
    login_as(client, VIEWER_ID, "viewer")
    tag_id = await _seed_tag(client)

    r = client.post("/subscriptions", data={"tag_id": tag_id, "notify": "true", "next": "/tags"})
    assert r.status_code == 303
    assert r.headers["location"] == "/tags"

    r = client.get("/tags")
    assert "tchip k-franchise on" in r.text
    assert 'action="/subscriptions/1/delete"' in r.text

    r = client.post("/subscriptions/1/delete", data={"next": "/tags"})
    assert r.status_code == 303

    r = client.get("/tags")
    assert "tchip k-franchise on" not in r.text
    assert 'action="/subscriptions"' in r.text


async def test_the_follow_form_needs_no_javascript(client):
    """Mutation this must fail against: a chip rewritten as
    <button onclick=...>, which renders identically and does nothing with JS
    off. Assert the FORM, not the button."""
    login_as(client, VIEWER_ID, "viewer")
    await _seed_tag(client)
    r = client.get("/tags")
    assert '<form class="chipform" method="post" action="/subscriptions"' in r.text
    assert "onclick" not in r.text.split('<form class="chipform"', 2)[1].split("</form>")[0]


def _chips(body: str) -> str:
    """Just the chips directory -- the table view and the per-tag edit
    dialogs both still carry `showModal`/dialog markup after this change (the
    table's own button still opens the dialog, and Task 6 restores an editor
    mode switch); scoping to the chips directory keeps this test about the
    chip specifically, not the rest of the page."""
    after = body.split('<div class="tags-page">', 1)[1]
    return after.split('id="tag-table-wrap"', 1)[0]


async def test_editors_also_get_a_follow_form_not_an_edit_dialog_opener(client):
    """The editor's edit-on-click is intentionally taken away here (Task 6
    gives it back as a mode switch). Assert the chip is a follow form for
    editors too, not `onclick="...showModal()"`."""
    login_as(client, EDITOR_ID, "reiji")
    await _seed_tag(client)
    chips = _chips(client.get("/tags").text)
    assert 'action="/subscriptions"' in chips
    assert "showModal" not in chips


async def test_subscription_row_carries_tag_subscription(client):
    """Sanity: following through the chip's form really writes a
    TagSubscription row, not just a UI flag."""
    login_as(client, VIEWER_ID, "viewer")
    tag_id = await _seed_tag(client)
    client.post("/subscriptions", data={"tag_id": tag_id, "notify": "true", "next": "/tags"})
    async with client.db() as s:
        from sqlalchemy import select

        rows = (await s.execute(select(TagSubscription))).scalars().all()
        assert any(r.user_id == VIEWER_ID and r.tag_id == tag_id for r in rows)
