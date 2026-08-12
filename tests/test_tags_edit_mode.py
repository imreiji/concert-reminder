"""The editor's Follow ⇄ Edit mode switch on /tags (Task 6).

Task 2 made every chip a follow form for everyone, which took the editor's
edit-on-click away. This gives it back as a MODE rather than as a per-role
branch, so a chip still means one thing at a time for everybody looking at it.

Three properties this file exists to hold:

  * the mode is editor-only -- a non-editor gets no toggle, no strip, no
    class hook and no script, because there are no tag dialogs on their page
    to open;
  * it does not persist, matching the Chips/Table toggle it replaces, so a
    forgotten Edit mode expires on reload;
  * in Edit the chips stop claiming "following" -- the tick lives in its own
    element precisely so CSS can drop it, and the followed ground is undone.
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db.models import Tag
from app.db.session import get_session
from app.domain.types import TagKind
from app.web import auth
from app.web.app import create_app

EDITOR_ID, VIEWER_ID = 42, 777

CSS = Path("src/app/web/static/style.css").read_text(encoding="utf-8")


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


async def _seed(client) -> tuple[int, int, int]:
    """A franchise, a group under it, and a character with a seiyuu -- so the
    page renders a plain chip, a kinded chip and a split pill."""
    async with client.db() as s:
        s.add(Tag(name="Gakumas", name_en="Gakumas", name_zh="Gakumas",
                  kind=TagKind.FRANCHISE, slug="gakumas"))
        await s.flush()
        s.add(Tag(name="初星学園", name_en="Hatsuboshi", name_zh="初星学园",
                  kind=TagKind.GROUP, slug="hatsuboshi", parent_id=1))
        s.add(Tag(name="花海咲季", name_en="Saki", name_zh="花海咲季",
                  kind=TagKind.CHARACTER, slug="saki", parent_id=1))
        s.add(Tag(name="長月達平", name_en="Tappei", name_zh="长月达平",
                  kind=TagKind.ARTIST, slug="tappei"))
        await s.commit()
    return 1, 2, 3


def _mode_script(body: str) -> str:
    """The mode switch's own <script>, isolated by its element id."""
    for block in re.findall(r"<script>(.*?)</script>", body, re.S):
        if "tagModeToggle" in block:
            return block
    return ""


async def test_a_non_editor_sees_no_mode_switch_and_no_strip(client):
    """A mode nobody but an editor can enter must not appear for anyone else.

    Mutation this must fail against: rendering the viewbar (or the strip, or
    the script) unconditionally instead of inside `{% if user.is_editor %}`.
    Each of the four hooks is asserted separately, because moving any ONE of
    them out of the editor branch is a real regression the others would hide.
    """
    login_as(client, VIEWER_ID, "viewer")
    await _seed(client)
    body = client.get("/tags").text
    assert 'id="tagModeToggle"' not in body
    assert 'id="tagEditStrip"' not in body
    assert 'data-mode="edit"' not in body
    assert "tagModeToggle" not in body, "not even in a script a non-editor could run"


async def test_an_editor_gets_the_switch_and_the_strip_markup(client):
    """The switch reuses the .viewtoggle aria-pressed vocabulary Task 5
    emptied out, and Follow is the pressed default."""
    login_as(client, EDITOR_ID, "reiji")
    await _seed(client)
    body = client.get("/tags").text

    bar = body.split('<div class="viewbar">', 1)[1].split("</div>", 2)[0]
    assert 'id="tagModeToggle"' in bar or 'id="tagModeToggle"' in body
    assert 'data-mode="follow" aria-pressed="true"' in body, "Follow is the default"
    assert 'data-mode="edit" aria-pressed="false"' in body

    # .edgecard, not .banner: this is ongoing state, not something asking for
    # attention -- the callout grammar has exactly two shapes.
    strip = re.search(r'<div class="([^"]*)" id="tagEditStrip"([^>]*)>', body)
    assert strip, "the strip must render (hidden) for an editor"
    assert "edgecard" in strip.group(1)
    assert "banner" not in strip.group(1)
    assert "hidden" in strip.group(2), "hidden until Edit is switched on"


async def test_edit_mode_is_not_persisted(client):
    """Matching the Chips/Table toggle it replaces: no localStorage, so a
    forgotten Edit mode expires on reload.

    Mutation this must fail against: "helpfully" remembering the mode in
    localStorage/sessionStorage/a cookie, which would leave an editor's next
    visit silently unable to follow anything by clicking.
    """
    login_as(client, EDITOR_ID, "reiji")
    await _seed(client)
    js = _mode_script(client.get("/tags").text)
    assert js, "the mode script must be found for this test to mean anything"
    # Comments out: the script's own comment explains that it does NOT use
    # localStorage, and a substring check cannot tell the two apart.
    js = "\n".join(line for line in js.splitlines() if not line.strip().startswith("//"))
    assert "localStorage" not in js
    assert "sessionStorage" not in js
    assert "document.cookie" not in js
    # And the server never renders a pressed Edit button, which would be the
    # other way to make the mode survive a reload.
    assert 'data-mode="edit" aria-pressed="true"' not in client.get("/tags").text


async def test_the_tag_id_reaches_the_handler_through_a_data_attribute(client):
    """Invariant 7: the browser HTML-decodes an inline on* attribute before
    parsing it as JS, so Jinja escaping does not protect it. The id must ride
    on a data- attribute read via dataset.

    Mutation this must fail against: `onclick="...showModal()"` on the chip,
    which is exactly the shape Task 2 removed and the shape a hurried Task 6
    would put back.
    """
    login_as(client, EDITOR_ID, "reiji")
    await _seed(client)
    body = client.get("/tags").text
    chips = body.split('<div class="tags-page">', 1)[1].split('<dialog id="tag-dialog-', 1)[0]
    assert "data-tag-chip" in chips
    assert "data-tag-id=" in chips
    assert "onclick" not in chips, "no inline handler anywhere in the chips directory"

    js = _mode_script(body)
    assert "dataset.tagId" in js, "read through dataset, not interpolated"


async def test_every_chip_button_including_a_split_half_carries_its_tag_id(client):
    """A split pill's two halves are two separate tags. If only the plain
    chip macro got data-tag-id, clicking a character (or her seiyuu) in Edit
    mode would silently do nothing at all -- the failure the whole mode is
    supposed to prevent."""
    login_as(client, EDITOR_ID, "reiji")
    await _seed(client)
    body = client.get("/tags").text
    chips = body.split('<div class="tags-page">', 1)[1].split('<dialog id="tag-dialog-', 1)[0]
    buttons = re.findall(r"<button[^>]*data-tag-chip[^>]*>", chips)
    assert buttons, "there are chips to check"
    missing = [b for b in buttons if "data-tag-id=" not in b]
    assert not missing, missing


async def test_a_followed_chip_puts_its_tick_in_its_own_element(client):
    """Edit mode drops the tick, and CSS cannot delete a bare text node. The
    tick therefore has to be its own element.

    Mutation this must fail against: going back to a literal `name ✓` text
    node, which leaves Edit mode showing a tick on a chip whose click opens
    an editor -- the exact lie this mode exists to avoid.
    """
    login_as(client, EDITOR_ID, "reiji")
    tag_id, _g, _c = await _seed(client)
    client.post("/subscriptions", data={"tag_id": tag_id, "notify": "true", "next": "/tags"})
    body = client.get("/tags").text
    assert '<span class="tick">✓</span>' in body
    assert re.search(r"</span>\s*✓", body) is None, "no stray bare tick left behind"
    assert ".editing .tick" in CSS.replace("\n", " "), "and CSS hides it in Edit mode"


def test_edit_mode_chips_are_dashed_accent_and_lose_their_followed_ground():
    """The dashed accent border is the whole signal."""
    assert ".tags-scope.editing .tchip {" in CSS
    rule = CSS.split(".tags-scope.editing .tchip {", 1)[1].split("}", 1)[0]
    assert "border-style: dashed" in rule
    assert "var(--accent)" in rule

    on_rule = CSS.split(".tags-scope.editing .tchip.on {", 1)[1].split("}", 1)[0]
    assert "font-weight: 400" in on_rule, "a followed chip stops shouting in Edit mode"


def test_edit_mode_never_paints_a_chip_with_the_franchise_ground():
    """--accent-wash IS .tchip.k-franchise's own background (see the rule
    right above these). Handing it to every chip in Edit mode would make the
    whole page read as franchises.

    Mutation this must fail against: `background: var(--accent-wash)` on any
    .tags-scope.editing chip rule.
    """
    rules = re.findall(r"(\.tags-scope\.editing[^{]*)\{([^}]*)\}", CSS)
    assert rules, "the edit-mode block must exist"
    # k-franchise's own rule is allowed to restore its own ground; nothing else
    # in Edit mode may reach for that token.
    offenders = [
        sel.strip() for sel, body in rules
        if "--accent-wash" in body and "k-franchise" not in sel
    ]
    assert not offenders, offenders


def test_the_edit_mode_rules_add_no_new_top_level_media_block():
    """test_theme_and_tokens.py pins the count at 6; this states the reason
    here too, where a future edit-mode phone rule would be written."""
    assert len(re.findall(r"@media \(max-width: \d+px\) \{", CSS)) == 6
