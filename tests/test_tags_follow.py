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
    # Scoped to the chip's own <form>...</form> -- the page's language-switch
    # form (base.html) also carries a hidden next=/tags input, and unscoped
    # this assertion would pass against THAT instead of the chip.
    chip_form = r.text.split('action="/subscriptions/1/delete"', 1)[1].split("</form>", 1)[0]
    assert 'name="next" value="/tags"' in chip_form, (
        "the FOLLOWED chip must emit next=/tags too -- the POST below pins the "
        "route's handling of it, not the template's emission of it"
    )

    r = client.post("/subscriptions/1/delete", data={"next": "/tags"})
    assert r.status_code == 303
    # The `location`, not just the 303. The follow half of this test already
    # pins "/tags"; unpinned, the unfollow half passed while the route sent
    # the user to /preferences -- the default `next`. And the assertion above
    # pins the FOLLOWED chip actually emitting the hidden `next` input in the
    # first place -- without it, this POST's explicit `data={"next": "/tags"}`
    # would keep passing even if the template stopped emitting it.
    assert r.headers["location"] == "/tags"

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
    """Just the chips directory -- the per-tag edit dialogs that follow it in
    DOM order still carry `showModal`/dialog markup (the table view itself is
    gone as of Task 5, 2026-08-12, and Task 6 restores an editor mode switch);
    scoping to the chips directory keeps this test about the chip
    specifically, not the rest of the page."""
    after = body.split('<div class="tags-page">', 1)[1]
    return after.split('<dialog id="tag-dialog-', 1)[0]


async def test_editors_also_get_a_follow_form_not_an_edit_dialog_opener(client):
    """The editor's edit-on-click is intentionally taken away here (Task 6
    gives it back as a mode switch). Assert the chip is a follow form for
    editors too, not `onclick="...showModal()"`."""
    login_as(client, EDITOR_ID, "reiji")
    await _seed_tag(client)
    chips = _chips(client.get("/tags").text)
    assert 'action="/subscriptions"' in chips
    assert "showModal" not in chips


async def test_a_followed_chip_keeps_its_unused_marking(client):
    """`.tchip.unused` (opacity .55, dashed border) is the signal that a tag is
    attached to nothing. Following a tag must not hide that -- the two facts
    are independent, and the tags most likely to be dead are the ones an
    editor is watching.

    Mutation this must fail against: dropping `unused` from tag_chip's
    FOLLOWED branch, which is how it shipped in phase 2.
    """
    login_as(client, VIEWER_ID, "viewer")
    tag_id = await _seed_tag(client)  # zero concerts -> unused

    r = client.post("/subscriptions", data={"tag_id": tag_id, "notify": "true", "next": "/tags"})
    assert r.status_code == 303

    r = client.get("/tags")
    # Scoped to the chip's own <button>...</form> -- a page-wide
    # "unused" in r.text would pass off any other unused chip on the page.
    chip_form = r.text.split('action="/subscriptions/1/delete"', 1)[1].split("</form>", 1)[0]
    assert "tchip k-franchise on unused" in chip_form


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
