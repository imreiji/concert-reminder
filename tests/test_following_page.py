"""`GET /following` -- the viewer's tag subscriptions as plain chips.

The page's whole claim is that a chip states how it DIFFERS from the viewer's
defaults and nothing else: a muted bell when `notify` is off, the preset's name
when `preset_id` disagrees with the default preset, and nothing at all when the
subscription conforms. A marker on a conforming chip is as much a failure as a
missing one on a deviating chip -- both make the page unscannable, and neither
raises -- so every marker test here asserts BOTH directions on the same render,
per chip, never "the page contains a bell somewhere".

Fixture shapes follow tests/test_preferences_page.py (the `client`/`login_as`
pair) and tests/test_tags.py (the tag seeding).
"""

import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import ReminderPreset, Tag, TagMember, TagSubscription, User
from app.db.session import get_session
from app.domain.types import TagKind
from app.web import auth
from app.web.app import create_app

USER_A = 6161
USER_B = 6262


@pytest.fixture()
def client(db, monkeypatch):
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


def chip_for(html: str, tag_id: int) -> str:
    """The ONE chip element for `tag_id`, span-balanced.

    Every marker assertion below is scoped to a single chip through this, which
    is what makes "the bell is on the right chip" testable at all -- a page-wide
    `"🔕" in html` passes just as happily when the bell lands on the conforming
    chip beside it. Balanced rather than regex-to-the-first-`</span>` because a
    marker IS a nested span.
    """
    anchor = html.index(f'data-tag-id="{tag_id}"')
    start = html.rindex("<span", 0, anchor)
    depth = 0
    for m in re.finditer(r"<span\b|</span>", html[start:]):
        depth += 1 if m.group().startswith("<span") else -1
        if depth == 0:
            return html[start : start + m.end()]
    raise AssertionError(f"chip for tag {tag_id} never closes")


async def seed(db) -> SimpleNamespace:
    """Two franchises, four followed tags, one unfollowed, two presets.

    `muted` follows with notify off; `other_preset` follows with the non-default
    preset; `plain` and `venue` conform (notify on, default preset). `stranger`
    is followed by nobody.
    """
    async with db() as s:
        s.add_all([User(discord_id=USER_A, username="reiji"),
                   User(discord_id=USER_B, username="someone")])
        await s.flush()
        love = Tag(name="Love Live!", kind=TagKind.FRANCHISE, created_by=USER_A)
        imas = Tag(name="idolm@ster", kind=TagKind.FRANCHISE, created_by=USER_A)
        s.add_all([love, imas])
        await s.flush()
        hasu = Tag(name="Hasunosora", kind=TagKind.GROUP, parent_id=love.id, created_by=USER_A)
        cinderella = Tag(name="Cinderella Girls", kind=TagKind.GROUP,
                         parent_id=imas.id, created_by=USER_A)
        s.add_all([hasu, cinderella])
        await s.flush()
        muted = Tag(name="Kozue Otomune", kind=TagKind.ARTIST, created_by=USER_A)
        other_preset = Tag(name="Kaho Hinoshita", kind=TagKind.ARTIST, created_by=USER_A)
        plain = Tag(name="Rurino Osawa", kind=TagKind.ARTIST, created_by=USER_A)
        stranger = Tag(name="Sayaka Murano", kind=TagKind.ARTIST, created_by=USER_A)
        venue = Tag(name="Nippon Budokan", kind=TagKind.VENUE, created_by=USER_A)
        s.add_all([muted, other_preset, plain, stranger, venue])
        await s.flush()
        s.add_all([
            TagMember(group_tag_id=hasu.id, member_tag_id=muted.id),
            TagMember(group_tag_id=hasu.id, member_tag_id=plain.id),
            TagMember(group_tag_id=cinderella.id, member_tag_id=other_preset.id),
        ])
        standard = ReminderPreset(user_id=USER_A, name="Standard cover", is_default=True)
        heavy = ReminderPreset(user_id=USER_A, name="Everything early")
        s.add_all([standard, heavy])
        await s.flush()
        s.add_all([
            TagSubscription(user_id=USER_A, tag_id=muted.id,
                            preset_id=standard.id, notify=False),
            TagSubscription(user_id=USER_A, tag_id=other_preset.id,
                            preset_id=heavy.id, notify=True),
            TagSubscription(user_id=USER_A, tag_id=plain.id,
                            preset_id=standard.id, notify=True),
            TagSubscription(user_id=USER_A, tag_id=venue.id,
                            preset_id=standard.id, notify=True),
            # Another user's follow of the tag nobody here follows: the page
            # must be scoped to the viewer, not to the table.
            TagSubscription(user_id=USER_B, tag_id=stranger.id, notify=True),
        ])
        await s.commit()
        return SimpleNamespace(
            love=love.id, imas=imas.id, hasu=hasu.id, cinderella=cinderella.id,
            muted=muted.id, other_preset=other_preset.id, plain=plain.id,
            stranger=stranger.id, venue=venue.id,
            standard=standard.id, heavy=heavy.id,
        )


# ── The page ─────────────────────────────────────────────────────────────


async def test_page_renders_and_lists_the_followed_tags(client):
    """The "every page has a logged-in GET render test" rule, plus the listing
    itself. Mutation: a route that reads TagSubscription for nobody (or a
    template that renders no chips) leaves the names off the page.
    """
    ids = await seed(client.db)
    login_as(client, USER_A, "reiji")
    r = client.get("/following")
    assert r.status_code == 200
    html = r.text
    for tag_id in (ids.muted, ids.other_preset, ids.plain, ids.venue):
        assert f'data-tag-id="{tag_id}"' in html
    assert "Kozue Otomune" in html


async def test_a_tag_you_do_not_follow_is_absent(client):
    """Mutation: swapping the subscription query for `select(Tag)` -- the tag
    directory's own listing -- would still render every name above.
    `stranger` is followed by ANOTHER user, so a query that forgets the
    user_id filter fails here too.
    """
    ids = await seed(client.db)
    login_as(client, USER_A, "reiji")
    html = client.get("/following").text
    assert f'data-tag-id="{ids.stranger}"' not in html
    assert "Sayaka Murano" not in html


async def test_the_muted_marker_is_on_the_muted_chip_only(client):
    """Mutation: `not sub.notify` -> `sub.notify` (or -> True/False). Inverting
    it moves the bell onto the three conforming chips and off the muted one;
    both halves of this assertion are needed to catch that -- either alone
    survives the inversion of the other.
    """
    ids = await seed(client.db)
    login_as(client, USER_A, "reiji")
    html = client.get("/following").text
    assert "🔕" in chip_for(html, ids.muted)
    for conforming in (ids.other_preset, ids.plain, ids.venue):
        assert "🔕" not in chip_for(html, conforming)


async def test_a_non_default_preset_names_itself_on_its_chip_only(client):
    """Mutation: `sub.preset_id != default_id` -> `False` (nothing ever
    deviates) or -> `True` (everything does). The chip carrying the non-default
    preset names it; the chips on the default preset stay silent -- and the
    default preset's OWN name must appear on no chip, which is what makes this
    a deviation marker rather than a label.
    """
    ids = await seed(client.db)
    login_as(client, USER_A, "reiji")
    html = client.get("/following").text
    assert "Everything early" in chip_for(html, ids.other_preset)
    for conforming in (ids.plain, ids.venue, ids.muted):
        chip = chip_for(html, conforming)
        assert "Everything early" not in chip
        assert "Standard cover" not in chip


async def test_no_preset_while_a_default_exists_is_a_deviation(client):
    """`preset_id is None` is not "conforming by default" -- with a default
    preset set, holding no preset is exactly the surprise the page exists to
    show. Mutation: comparing only when `sub.preset_id` is truthy (the natural
    `if sub.preset_id and sub.preset_id != default_id`) leaves this chip bare.
    """
    ids = await seed(client.db)
    async with client.db() as s:
        row = (await s.execute(
            select(TagSubscription).where(
                TagSubscription.tag_id == ids.plain, TagSubscription.user_id == USER_A
            )
        )).scalar_one()
        row.preset_id = None
        await s.commit()
    login_as(client, USER_A, "reiji")
    html = client.get("/following").text
    chip = chip_for(html, ids.plain)
    assert "No preset" in chip
    # And the still-conforming chip beside it says nothing.
    assert "No preset" not in chip_for(html, ids.venue)


async def test_a_named_preset_deviates_when_there_is_no_default(client):
    """The other end of the same comparison: with no default preset, ANY linked
    preset is a deviation. Mutation: defaulting the comparison basis to "the
    subscription's own preset" when no default exists (or skipping the marker
    when `default is None`) leaves every chip bare here.
    """
    ids = await seed(client.db)
    async with client.db() as s:
        standard = await s.get(ReminderPreset, ids.standard)
        standard.is_default = False
        await s.commit()
    login_as(client, USER_A, "reiji")
    html = client.get("/following").text
    assert "Standard cover" in chip_for(html, ids.plain)
    assert "Everything early" in chip_for(html, ids.other_preset)


async def test_a_tag_with_no_franchise_ancestry_lands_in_other(client):
    """Grouping is by franchise, and the venue belongs to none. Mutation:
    dropping the "Other" bucket makes an unparented follow vanish from a page
    whose entire job is to list every follow.
    """
    ids = await seed(client.db)
    login_as(client, USER_A, "reiji")
    html = client.get("/following").text
    assert f'data-tag-id="{ids.venue}"' in html
    # The franchise headings and the catch-all all render.
    assert "Love Live!" in html
    assert "idolm@ster" in html
    assert "Other" in html


async def test_search_is_wired_to_this_page_s_scope(client):
    """filterChips hides `[data-name]` elements inside the scope it is given and
    hides `[data-filter-container]` boxes that empty out. Mutation: passing
    '.tags-scope' (the selector /tags uses, copied along with the input) leaves
    a search box that matches nothing on this page -- silent, and invisible to
    any test that only asserts an input exists.
    """
    ids = await seed(client.db)
    login_as(client, USER_A, "reiji")
    html = client.get("/following").text
    assert 'class="following-scope"' in html
    assert "filterChips(this, '.following-scope')" in html
    assert "data-filter-container" in html
    assert 'data-name="kozue otomune"' in html
    assert f'data-tag-id="{ids.muted}"' in html


async def test_empty_state_points_at_the_tag_directory(client):
    """A new user follows nothing, so this is the page's most common first
    render. Mutation: a bare `{% for %}` loop with no `{% else %}` renders a
    blank page with a heading and no way forward.
    """
    async with client.db() as s:
        s.add(User(discord_id=USER_A, username="reiji"))
        await s.commit()
    login_as(client, USER_A, "reiji")
    r = client.get("/following")
    assert r.status_code == 200
    assert 'href="/tags"' in r.text


async def test_signed_out_goes_home_not_to_an_error(client):
    """Invariant 5: being signed out is not an error. A plain GET 303s to `/`
    (never 307, never 403), and an htmx GET gets HX-Redirect + 204 because an
    XHR would follow a 303 and swap the landing page into a fragment hole.
    Mutation: `current_user` in place of `require_user` renders the page for
    nobody instead.
    """
    r = client.get("/following")
    assert r.status_code == 303
    assert r.headers["location"].startswith("/")
    assert "/following" not in r.headers["location"].split("?")[0]
    hx = client.get("/following", headers={"HX-Request": "true"})
    assert (hx.status_code, hx.headers["hx-redirect"].split("?")[0]) == (204, "/")
