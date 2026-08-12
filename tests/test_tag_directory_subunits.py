"""On /tags, a subunit's members render under the subunit and nowhere else.

Owner ruling 2026-08-12, THIS PAGE ONLY. The 2026-08-01 spec kept the
repetition on the concert page because a bill must be a truthful lineup and
what it shows depends on which other tags are attached. A catalogue has no
"attached", so that reasoning does not transfer.
"""

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db.models import Tag, TagKind, TagMember
from app.db.service import tag_directory_context
from app.db.session import get_session
from app.web import auth
from app.web.app import create_app


async def _tag(session, name, kind, slug, **kw):
    t = Tag(name=name, name_en=name, kind=kind, slug=slug, **kw)
    session.add(t)
    await session.flush()
    return t


async def test_a_subunit_member_leaves_its_parents_row(session):
    parent = await _tag(session, "765PRO ALLSTARS", TagKind.GROUP, "765pro")
    sub = await _tag(session, "竜宮小町", TagKind.GROUP, "ryuguu", parent_id=parent.id)
    shared = await _tag(session, "秋月律子", TagKind.CHARACTER, "ritsuko")
    only_parent = await _tag(session, "天海春香", TagKind.CHARACTER, "haruka")
    session.add_all([
        TagMember(group_tag_id=parent.id, member_tag_id=shared.id),
        TagMember(group_tag_id=parent.id, member_tag_id=only_parent.id),
        TagMember(group_tag_id=sub.id, member_tag_id=shared.id),
    ])
    await session.flush()

    ctx = await tag_directory_context(session)
    rows = {g.name: [m.name for m in members] for g, members, _d in ctx["no_franchise_groups"]}
    assert rows["竜宮小町"] == ["秋月律子"], "the subunit keeps her"
    assert rows["765PRO ALLSTARS"] == ["天海春香"], (
        "and the parent drops her -- she renders in the subunit and nowhere else"
    )


async def test_a_parent_whose_members_are_all_in_subunits_renders_empty(session):
    """6 live groups become empty rows. They must still RENDER -- the row is
    the group, and the concert page's own ruling is that an empty member area
    shows the label row silently rather than '0 performers'.

    "Silently" is a claim about the CONTEXT only, which is all this test can
    see: the template's own fallback copy is pinned by
    test_an_absorbed_parents_row_does_not_claim_no_members_yet below, because
    a context-level assertion cannot tell an empty row from one captioned
    "no members yet" and that is exactly what shipped.

    Mutation this must fail against: dropping a group whose de-duped member
    list is empty.
    """
    parent = await _tag(session, "SideM", TagKind.GROUP, "sidem")
    sub = await _tag(session, "Jupiter", TagKind.GROUP, "jupiter", parent_id=parent.id)
    m = await _tag(session, "天ヶ瀬冬馬", TagKind.CHARACTER, "touma")
    session.add_all([
        TagMember(group_tag_id=parent.id, member_tag_id=m.id),
        TagMember(group_tag_id=sub.id, member_tag_id=m.id),
    ])
    await session.flush()

    ctx = await tag_directory_context(session)
    rows = {g.name: members for g, members, _d in ctx["no_franchise_groups"]}
    assert "SideM" in rows, "the empty parent still renders"
    assert rows["SideM"] == []
    assert [m.name for m in rows["Jupiter"]] == ["天ヶ瀬冬馬"]


async def test_seiyuu_of_maps_characters_to_their_performer(session):
    """The template needs the seiyuu for split pills, and Tag.voiced_by is NOT
    a loaded relationship -- touching it during async rendering is a
    MissingGreenlet 500. The context resolves it from the loaded tag list.
    """
    seiyuu = await _tag(session, "若林直美", TagKind.ARTIST, "naomi")
    await _tag(session, "秋月律子", TagKind.CHARACTER, "ritsuko",
               voiced_by_tag_id=seiyuu.id)
    await _tag(session, "三浦あずさ", TagKind.CHARACTER, "azusa")

    ctx = await tag_directory_context(session)
    by_name = {t.name: t for t in [seiyuu]}
    got = {cid: (s.name if s else None) for cid, s in ctx["seiyuu_of"].items()}
    assert "若林直美" in got.values()
    assert None in got.values(), "a character with no seiyuu maps to None, not a KeyError"
    assert len(got) == 2, "CHARACTER tags only -- artists and groups are not keys"
    assert by_name  # silence the unused-name linter


# ── the same ruling, at the RENDER level ─────────────────────────────────
#
# The three tests above read the context dict, which is why the copy defect
# below shipped: the context is right (the row is there, its list is empty)
# and the TEMPLATE is what turned that into a lie.

VIEWER_ID = 909


@pytest.fixture()
def client(db, monkeypatch):
    monkeypatch.setattr(settings, "editor_whitelist", "")
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


def _row(body: str, name: str) -> str:
    """The one `.grow2` row whose chip carries `name`. A row holds forms and
    spans but never a nested div, so `</div>` really is its end."""
    blocks = [b.split("</div>", 1)[0] for b in body.split('<div class="grow2')[1:]]
    matches = [b for b in blocks if name in b]
    assert len(matches) == 1, f"expected exactly one row for {name}, got {len(matches)}"
    return matches[0]


async def test_an_absorbed_parents_row_does_not_claim_no_members_yet(client):
    """The defect this fix exists for. Live, SideM's 49 members all live in its
    subunits, so its own de-duped list is empty -- and the macro's `{% else %}`
    captioned that "no members yet" directly above the subunit rows listing
    every one of them. One member stands in for the 49 here; the shape is what
    matters.

    Asserted against rendered HTML, not the context: the context was correct
    the whole time. Mutation this must fail against: reverting the fallback to
    a bare `{% else %}`.
    """
    login_as(client, VIEWER_ID, "viewer")
    async with client.db() as s:
        parent = await _tag(s, "SideM", TagKind.GROUP, "sidem")
        sub = await _tag(s, "Jupiter", TagKind.GROUP, "jupiter", parent_id=parent.id)
        m = await _tag(s, "天ヶ瀬冬馬", TagKind.CHARACTER, "touma")
        # A group with no TagMember rows at all -- the case the copy is FOR.
        await _tag(s, "架空ユニット", TagKind.GROUP, "kakuu")
        s.add_all([
            TagMember(group_tag_id=parent.id, member_tag_id=m.id),
            TagMember(group_tag_id=sub.id, member_tag_id=m.id),
        ])
        await s.commit()

    body = client.get("/tags").text
    assert body.count("no members yet") == 1, (
        "exactly one row on the page is genuinely memberless"
    )
    absorbed = _row(body, "SideM")
    assert "no members yet" not in absorbed, (
        "its whole membership renders in the subunit row below -- saying "
        "'no members yet' here contradicts the very next line"
    )
    assert "天ヶ瀬冬馬" not in absorbed, "and the de-dup itself still holds"
    assert "天ヶ瀬冬馬" in _row(body, "Jupiter")
    assert "no members yet" in _row(body, "架空ユニット"), (
        "a group nobody is attached to still says so"
    )
