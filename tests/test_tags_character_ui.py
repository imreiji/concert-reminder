"""The Tags page's character surface: create with a seiyuu, recast, browse."""

import pytest
from fastapi.testclient import TestClient

from app.config import settings
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


async def _tag(client, name, kind, **extra):
    """Create a tag through the real route and hand back the row.

    The name trio is filled on every create because `create_tag` calls
    `require_variants(..., mandatory=True)`: a tag posted with `name` alone
    422s and is never written, so a helper omitting them would make every
    assertion below fail on a missing row for a reason that has nothing to do
    with characters.
    """
    from sqlalchemy import select

    from app.db.models import Tag
    r = client.post("/tags", data={
        "name": name, "name_en": name, "name_zh": name, "kind": kind, **extra,
    })
    async with client.db() as s:
        row = (await s.execute(select(Tag).where(Tag.name == name))).scalar_one_or_none()
    return r, row


async def _artist(client, name="今井麻美"):
    _, row = await _tag(client, name, "artist")
    return row


async def test_an_editor_creates_a_character_with_a_seiyuu(client):
    login_as(client, EDITOR_ID, "editor")
    imai = await _artist(client)
    r, row = await _tag(client, "如月千早", "character", voiced_by_tag_id=imai.id)
    assert r.status_code in (200, 303)
    assert row.voiced_by_tag_id == imai.id


async def test_a_recast_repoints_the_link(client):
    """The whole of recast handling: one value changes."""
    login_as(client, EDITOR_ID, "editor")
    old = await _artist(client, "今井麻美")
    new = await _artist(client, "別の声優")
    _, chihaya = await _tag(client, "如月千早", "character", voiced_by_tag_id=old.id)
    client.post(f"/tags/{chihaya.id}/edit", data={"voiced_by_tag_id": new.id})
    async with client.db() as s:
        from app.db.models import Tag
        assert (await s.get(Tag, chihaya.id)).voiced_by_tag_id == new.id


async def test_the_page_shows_a_character_with_her_seiyuu(client):
    login_as(client, EDITOR_ID, "editor")
    imai = await _artist(client)
    await _tag(client, "如月千早", "character", voiced_by_tag_id=imai.id)
    body = client.get("/tags").text
    assert "如月千早" in body and "今井麻美" in body


async def test_the_character_row_names_her_seiyuu(client):
    """The pairing, not merely both names on the page.

    A seiyuu is an ARTIST in no group, so she renders in "Performers with no
    group" whether or not anything links her to the character -- which makes
    the whole-page assertion above true even with the character section
    deleted. This one reads the character's own row.
    """
    login_as(client, EDITOR_ID, "editor")
    imai = await _artist(client)
    _, chihaya = await _tag(client, "如月千早", "character", voiced_by_tag_id=imai.id)
    body = client.get("/tags").text
    row = body.split(f'data-character-id="{chihaya.id}"', 1)[1].split("</div>", 1)[0]
    assert "如月千早" in row
    assert "今井麻美" in row


async def test_a_character_with_no_seiyuu_says_so_in_her_row(client):
    login_as(client, EDITOR_ID, "editor")
    _, chihaya = await _tag(client, "如月千早", "character")
    assert chihaya.voiced_by_tag_id is None
    body = client.get("/tags").text
    row = body.split(f'data-character-id="{chihaya.id}"', 1)[1].split("</div>", 1)[0]
    assert "如月千早" in row
    assert "no performer set" in row


async def test_only_an_artist_may_voice_a_character(client):
    """The editor surface must not be more permissive than the file format,
    which refuses a non-ARTIST voiced_by outright. A VENUE here would be
    materialised onto a concert by attach_tag and render as a performer."""
    login_as(client, EDITOR_ID, "editor")
    _, hall = await _tag(client, "K Arena", "venue")
    r, row = await _tag(client, "如月千早", "character", voiced_by_tag_id=hall.id)
    assert r.status_code == 422
    assert row is None


async def test_an_unknown_seiyuu_id_is_refused(client):
    login_as(client, EDITOR_ID, "editor")
    r, row = await _tag(client, "如月千早", "character", voiced_by_tag_id=99999)
    assert r.status_code == 422
    assert row is None


async def test_only_a_character_may_carry_a_seiyuu(client):
    """Nothing reads voiced_by_tag_id off another kind, so accepting one would
    store a value that renders nowhere."""
    login_as(client, EDITOR_ID, "editor")
    imai = await _artist(client)
    r, row = await _tag(client, "水瀬いのり", "artist", voiced_by_tag_id=imai.id)
    assert r.status_code == 422
    assert row is None


async def test_an_edit_that_omits_the_field_leaves_her_seiyuu_alone(client):
    """edit_tag's standing rule: callers predate several fields, so an omitted
    one is never a blanking. Every non-character dialog omits this field."""
    login_as(client, EDITOR_ID, "editor")
    imai = await _artist(client)
    _, chihaya = await _tag(client, "如月千早", "character", voiced_by_tag_id=imai.id)
    r = client.post(f"/tags/{chihaya.id}/edit", data={"name": "如月千早"})
    assert r.status_code == 303
    async with client.db() as s:
        from app.db.models import Tag
        assert (await s.get(Tag, chihaya.id)).voiced_by_tag_id == imai.id


async def test_choosing_none_clears_her_seiyuu(client):
    """The select's "— none —" posts 0, which is the explicit clear -- the one
    thing an omitted field can no longer express."""
    login_as(client, EDITOR_ID, "editor")
    imai = await _artist(client)
    _, chihaya = await _tag(client, "如月千早", "character", voiced_by_tag_id=imai.id)
    r = client.post(f"/tags/{chihaya.id}/edit", data={"voiced_by_tag_id": 0})
    assert r.status_code == 303
    async with client.db() as s:
        from app.db.models import Tag
        assert (await s.get(Tag, chihaya.id)).voiced_by_tag_id is None


async def test_an_edit_may_not_point_her_at_a_non_artist(client):
    login_as(client, EDITOR_ID, "editor")
    imai = await _artist(client)
    _, hall = await _tag(client, "K Arena", "venue")
    _, chihaya = await _tag(client, "如月千早", "character", voiced_by_tag_id=imai.id)
    r = client.post(f"/tags/{chihaya.id}/edit", data={"voiced_by_tag_id": hall.id})
    assert r.status_code == 422
    async with client.db() as s:
        from app.db.models import Tag
        assert (await s.get(Tag, chihaya.id)).voiced_by_tag_id == imai.id


async def test_the_dialogs_offer_the_character_kind_and_her_seiyuu(client):
    """The editor surface itself: a character cannot be created or recast
    without a kind chip to pick and an artist-scoped select to pick from."""
    login_as(client, EDITOR_ID, "editor")
    imai = await _artist(client)
    _, chihaya = await _tag(client, "如月千早", "character", voiced_by_tag_id=imai.id)
    body = client.get("/tags").text
    assert 'data-kind="character"' in body
    # The create dialog's field, scoped to the character kind so setTagKind
    # disables it for every other one.
    create = body.split('id="new-tag-dialog"', 1)[1]
    assert 'kf k-character' in create
    assert 'name="voiced_by_tag_id"' in create
    # The edit dialog's, pre-selected on the seiyuu she already has.
    dialog = body.split(f'id="tag-dialog-{chihaya.id}"', 1)[1].split("</dialog>", 1)[0]
    assert 'name="voiced_by_tag_id"' in dialog
    assert f'<option value="{imai.id}" selected>' in dialog


async def test_a_character_may_not_be_parented_to_an_artist(client):
    """parent_id means 'the broader thing I belong to'. A seiyuu is not that,
    which is exactly why voiced_by_tag_id is a separate column."""
    login_as(client, EDITOR_ID, "editor")
    imai = await _artist(client)
    r, row = await _tag(client, "如月千早", "character", parent_id=imai.id)
    assert r.status_code == 422
    assert row is None


async def test_a_group_may_be_parented_to_a_group(client):
    login_as(client, EDITOR_ID, "editor")
    _, parent = await _tag(client, "765PRO ALLSTARS", "group")
    r, sub = await _tag(client, "竜宮小町", "group", parent_id=parent.id)
    assert r.status_code in (200, 303)
    assert sub.parent_id == parent.id


# ── The quick-create dialog (POST /tags/quick), mid-import ────────────────


def test_quick_create_makes_a_character(client):
    """Until now the dialog offered franchise/group/artist only, so an
    unmatched name the editor re-kinds as a character had no one-click
    create."""
    login_as(client, EDITOR_ID, "editor")
    r = client.post("/tags/quick", data={"name": "如月千早", "kind": "character"})
    assert r.status_code == 200
    assert r.json()["kind"] == "character"


def test_quick_create_reads_the_shared_parent_table(client):
    """It kept a narrower inline rule ("only group tags take a franchise
    parent") after ALLOWED_PARENT_KINDS widened. Two of these three were
    refused by that rule."""
    login_as(client, EDITOR_ID, "editor")
    franchise = client.post(
        "/tags/quick", data={"name": "THE IDOLM@STER", "kind": "franchise"}
    ).json()
    group = client.post("/tags/quick", data={
        "name": "765PRO ALLSTARS", "kind": "group", "parent_id": franchise["id"],
    }).json()
    # CHARACTER -> FRANCHISE, and GROUP -> GROUP: both allowed by the table.
    character = client.post("/tags/quick", data={
        "name": "如月千早", "kind": "character", "parent_id": franchise["id"],
    })
    assert character.status_code == 200
    assert character.json()["parent_id"] == franchise["id"]
    subunit = client.post("/tags/quick", data={
        "name": "竜宮小町", "kind": "group", "parent_id": group["id"],
    })
    assert subunit.status_code == 200
    assert subunit.json()["parent_id"] == group["id"]


def test_quick_create_still_refuses_a_parent_the_table_forbids(client):
    login_as(client, EDITOR_ID, "editor")
    group = client.post("/tags/quick", data={"name": "765PRO", "kind": "group"}).json()
    # A character belongs to a franchise, never to a group (nor to the artist
    # who voices her -- that is voiced_by_tag_id's whole reason to exist).
    assert client.post("/tags/quick", data={
        "name": "如月千早", "kind": "character", "parent_id": group["id"],
    }).status_code == 422
    # And an artist takes no parent at all: absent from the table means none.
    assert client.post("/tags/quick", data={
        "name": "今井麻美", "kind": "artist", "parent_id": group["id"],
    }).status_code == 422


def test_the_import_dialog_offers_the_character_kind(client):
    """The dialog rides on the import preview; the kind chip is what makes the
    route's new kind reachable from there."""
    login_as(client, EDITOR_ID, "editor")
    r = client.post("/concerts/import/draft", data={"draft": "title: 千早ソロライブ"})
    assert r.status_code == 200
    picker = r.text.split('id="tc-kindpick"', 1)[1].split("</div>", 1)[0]
    assert 'data-kind="character"' in picker


# ── a character's own eventernote URL ────────────────────────────────────
#
# The spec's "discovery is nearly free" payoff: 如月千早 has an actor page of
# her own, and `discovery.py`'s sweep is kind-blind, so the ONLY thing standing
# between a character and a daily sweep is whether the Tags page will let an
# editor put a URL on her. Three template gates said no, and `edit_tag` then
# wiped one the catalogue import had set.

EVENTERNOTE = "https://www.eventernote.com/actors/1234"


def _dialog(html: str, tag_id: int) -> str:
    """One tag's edit dialog, not the whole page -- the field is rendered for
    OTHER kinds, so a page-wide `in` check passes with the character gate
    still shut."""
    return html.split(f'id="tag-dialog-{tag_id}"', 1)[1].split("</dialog>", 1)[0]


async def test_the_edit_dialog_offers_a_character_an_eventernote_url(client):
    login_as(client, EDITOR_ID, "editor")
    _, chihaya = await _tag(client, "如月千早", "character")
    assert 'name="eventernote_url"' in _dialog(client.get("/tags").text, chihaya.id)


async def test_the_create_dialog_offers_a_character_an_eventernote_url(client):
    """`setTagKind` shows and ENABLES only the fields carrying `k-<kind>`, and
    hides-and-disables the rest -- so the class list IS the gate, not styling.

    Scoped to the new-tag dialog: the per-tag edit dialogs render the same
    field name, so a page-wide search would answer a different question.
    """
    login_as(client, EDITOR_ID, "editor")
    dialog = client.get("/tags").text.split('id="new-tag-dialog"', 1)[1]
    field = dialog.split('name="eventernote_url"', 1)[0].rsplit("<div ", 1)[1]
    assert "k-character" in field, "the character kind cannot reach the field"
    assert "k-venue" not in field, "a venue has no actor page"


async def test_a_rename_does_not_wipe_a_characters_discovery_link(client):
    """The erasure, and the reason the three template gates were the fix.

    A browser posts every field the dialog RENDERS. Her dialog rendered no
    eventernote box, so a rename carried no value, `Form("")` wrote None
    straight over the URL the catalogue import had set, and the sweep stopped
    reading her page. The test therefore submits what the page shows -- read
    the box back out of her own dialog and post it -- which is the whole claim:
    a value that renders is a value that round-trips.
    """
    login_as(client, EDITOR_ID, "editor")
    _, chihaya = await _tag(client, "如月千早", "character", eventernote_url=EVENTERNOTE)
    assert chihaya.eventernote_url == EVENTERNOTE, "precondition: the URL was stored"

    field = _dialog(client.get("/tags").text, chihaya.id).split(
        'name="eventernote_url"', 1
    )[1].split(">", 1)[0]
    assert f'value="{EVENTERNOTE}"' in field, "the box renders but is not prefilled"

    r = client.post(f"/tags/{chihaya.id}/edit", data={
        "name": "如月千早 (recast)", "eventernote_url": EVENTERNOTE,
    })
    assert r.status_code == 303
    async with client.db() as s:
        from app.db.models import Tag
        assert (await s.get(Tag, chihaya.id)).eventernote_url == EVENTERNOTE


async def test_a_franchise_keeps_its_link_through_an_edit(client):
    """The other half, and NOT a character special case: for a kind whose
    dialog does not render the box, the route must not write the field at all.

    Pre-existing -- a franchise's URL has had this exposure since the column
    shipped -- and unfixable by an `is None` default, because FastAPI folds an
    empty form value into the default and "" therefore cannot mean "absent".
    EVENTERNOTE_KINDS is what says whether this submit had a box.
    """
    login_as(client, EDITOR_ID, "editor")
    _, imas = await _tag(
        client, "THE IDOLM@STER", "franchise", eventernote_url=EVENTERNOTE
    )
    r = client.post(f"/tags/{imas.id}/edit", data={"name": "アイドルマスター"})
    assert r.status_code == 303
    async with client.db() as s:
        from app.db.models import Tag
        assert (await s.get(Tag, imas.id)).eventernote_url == EVENTERNOTE


async def test_an_empty_box_still_clears_the_link(client):
    """The other half: omitted means "leave alone", but an editor emptying a
    box they can SEE still means "clear it". Without this the new rule would
    make the field write-once."""
    login_as(client, EDITOR_ID, "editor")
    _, chihaya = await _tag(client, "如月千早", "character", eventernote_url=EVENTERNOTE)
    r = client.post(
        f"/tags/{chihaya.id}/edit", data={"name": "如月千早", "eventernote_url": ""}
    )
    assert r.status_code == 303
    async with client.db() as s:
        from app.db.models import Tag
        assert (await s.get(Tag, chihaya.id)).eventernote_url is None


async def test_an_admin_can_sweep_a_characters_page_from_her_dialog(client):
    """The button is admin-only and needs a URL; a character now satisfies the
    kind condition too, and `sweep_one_tag` was always kind-blind."""
    login_as(client, ADMIN_ID, "reiji")
    _, chihaya = await _tag(client, "如月千早", "character", eventernote_url=EVENTERNOTE)
    assert f'action="/admin/discoveries/sweep/{chihaya.id}"' in _dialog(
        client.get("/tags").text, chihaya.id
    )
