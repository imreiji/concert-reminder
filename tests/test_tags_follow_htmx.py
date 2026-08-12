"""A follow press swaps its own chip instead of reloading /tags.

Owner report, 2026-08-12: "whenever a user clicks a tag to follow, the website
will post the follow and refresh the entire website. This will bring the page
back to top, not where the user is currently scrolled at, and the loading time
after clicking each follow and unfollow is incredibly slow."

Phase 2 shipped the chips as plain forms that POST and 303 back to /tags. At
live scale (735 tags, measured) that re-rendered a 6.8 MB page -- 1.6 s of
server time -- and discarded scroll position, on every single press.

The fix is `_capture_actions.html`'s idiom, not a new one: hx-post/hx-target/
hx-swap BESIDE method/action, so htmx swaps one chip and JS-off still posts and
redirects. These tests pin both halves of that, and pin that the swapped chip is
the SAME chip the page renders -- the failure mode of a hand-written fragment is
a chip that quietly loses `data-name` (unfindable by the search box),
`data-tag-id` (inert in Edit mode) or its `unused` marking.
"""

import re

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db.models import Concert, ConcertTag, Tag
from app.db.session import get_session
from app.domain.types import TagKind
from app.web import auth
from app.web.app import create_app

EDITOR_ID, VIEWER_ID = 42, 777
HX = {"HX-Request": "true"}


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


async def _seed_tag(client, name="Aqours", kind=TagKind.FRANCHISE, **extra) -> int:
    async with client.db() as s:
        t = Tag(name=name, name_en=name, name_zh=name, kind=kind,
                slug=name.lower().replace(" ", "-"), **extra)
        s.add(t)
        await s.commit()
        return t.id


async def _seed_concert_on(client, tag_id: int) -> None:
    """One concert carrying the tag, so its chip's count is 1, not 0."""
    async with client.db() as s:
        c = Concert(event_id="ev1", title="Live", created_by=None)
        s.add(c)
        await s.flush()
        s.add(ConcertTag(concert_id=c.id, tag_id=tag_id))
        await s.commit()


def _form_on(body: str, marker: str, cls: str) -> str:
    """The one `<form class="{cls}">` on the page whose markup contains
    `marker` -- how the page renders that chip right now."""
    open_tag = f'<form class="{cls}"'
    for piece in body.split(open_tag)[1:]:
        html = open_tag + piece.split("</form>", 1)[0] + "</form>"
        if marker in html:
            return html
    raise AssertionError(f"no <form class={cls!r}> containing {marker!r}")


async def test_a_follow_from_htmx_returns_only_the_chip(client):
    """An htmx follow must swap one chip, not re-render 878 of them.

    Mutation this must fail against: the route ignoring HX-Request and
    redirecting, which still "works" and is what shipped in phase 2.
    """
    login_as(client, VIEWER_ID, "viewer")
    tag_id = await _seed_tag(client)

    r = client.post("/subscriptions",
                    data={"tag_id": tag_id, "notify": "true", "next": "/tags",
                          "chip": "count"},
                    headers=HX)
    assert r.status_code == 200, "a fragment, not a 303 the browser would follow"
    assert '<form class="chipform"' in r.text
    # The page shell, in three independent forms -- a whole-page response would
    # carry all of them.
    assert "<!doctype html" not in r.text.lower()
    assert 'class="tag-search"' not in r.text, "the search box is page shell"
    assert "Franchises and groups" not in r.text, "so is the section heading"
    assert len(r.text) < 2000, f"one chip, not a page ({len(r.text)} bytes)"


async def test_a_follow_without_htmx_still_redirects(client):
    """JS off keeps working. Mutation: making the htmx branch unconditional,
    which leaves a non-JS user staring at a bare chip fragment."""
    login_as(client, VIEWER_ID, "viewer")
    tag_id = await _seed_tag(client)

    r = client.post("/subscriptions",
                    data={"tag_id": tag_id, "notify": "true", "next": "/tags",
                          "chip": "count"})
    assert r.status_code == 303
    assert r.headers["location"] == "/tags"

    # ...and the unfollow half of the pair, which has its own branch.
    r = client.post("/subscriptions/unfollow",
                    data={"tag_id": tag_id, "next": "/tags", "chip": "count"})
    assert r.status_code == 303
    assert r.headers["location"] == "/tags"


async def test_the_swapped_chip_carries_the_opposite_action(client):
    """Following returns a chip offering unfollow, and vice versa -- otherwise
    the swapped chip is a dead end until a full reload."""
    login_as(client, VIEWER_ID, "viewer")
    tag_id = await _seed_tag(client)

    r = client.post("/subscriptions",
                    data={"tag_id": tag_id, "notify": "true", "next": "/tags",
                          "chip": "count"},
                    headers=HX)
    assert 'action="/subscriptions/unfollow"' in r.text
    assert 'hx-post="/subscriptions/unfollow"' in r.text
    assert f'name="tag_id" value="{tag_id}"' in r.text, "keyed by tag, not by sub id"
    assert "tchip k-franchise on" in r.text

    r = client.post("/subscriptions/unfollow",
                    data={"tag_id": tag_id, "next": "/tags", "chip": "count"}, headers=HX)
    assert r.status_code == 200
    assert 'action="/subscriptions"' in r.text
    assert 'hx-post="/subscriptions"' in r.text
    assert f'name="tag_id" value="{tag_id}"' in r.text, "and it can be followed again"
    assert "on" not in r.text.split('class="tchip', 1)[1].split(">", 1)[0]


async def test_the_swapped_chip_is_the_chip_the_page_renders(client):
    """Byte-identity, both directions. The fragment is compared against the
    page's own markup for the same tag, so nothing (data-name, data-tag-id,
    the hidden inputs, the unused class) can be dropped from one copy only.

    Mutation this must fail against: a route that hand-writes the chip, or a
    partial that renders a different shape than the page's call site.
    """
    login_as(client, VIEWER_ID, "viewer")
    tag_id = await _seed_tag(client)

    frag = client.post("/subscriptions",
                       data={"tag_id": tag_id, "notify": "true", "next": "/tags",
                             "chip": "count"},
                       headers=HX).text
    page_chip = _form_on(client.get("/tags").text, f'data-tag-id="{tag_id}"', "chipform")
    assert frag.strip() == page_chip, "followed chip must match the page's"
    # search_key joins all three name columns, lowercased -- the seed gives the
    # same name in each, so the hook is that name three times over.
    assert 'data-name="aqours aqours aqours"' in frag, "still findable by search"
    assert f'data-tag-id="{tag_id}"' in frag, "still openable in Edit mode"

    frag = client.post("/subscriptions/unfollow",
                       data={"tag_id": tag_id, "next": "/tags", "chip": "count"},
                       headers=HX).text
    page_chip = _form_on(client.get("/tags").text, f'data-tag-id="{tag_id}"', "chipform")
    assert frag.strip() == page_chip, "unfollowed chip must match the page's"


async def test_the_swapped_chip_speaks_the_readers_language(client):
    """The fragment is rendered off `Template.module`, which Jinja CACHES for
    the process. Everything locale-dependent in the chip -- its title, and
    `loc(t, "name")` -- must still resolve per request, not freeze at whichever
    language happened to press first.

    Mutation this must fail against: resolving `_()`/`loc()` once and reusing
    the result, which would show every reader the first presser's language.
    """
    login_as(client, VIEWER_ID, "viewer")
    tag_id = await _seed_tag(client)

    en = client.post("/subscriptions",
                     data={"tag_id": tag_id, "notify": "true", "next": "/tags",
                           "chip": "count"}, headers=HX)
    assert "Following — click to unfollow" in en.text
    client.post("/subscriptions/unfollow",
                data={"tag_id": tag_id, "next": "/tags", "chip": "count"}, headers=HX)

    client.cookies.set("lang", "ja")
    ja = client.post("/subscriptions",
                     data={"tag_id": tag_id, "notify": "true", "next": "/tags",
                           "chip": "count"}, headers=HX)
    assert "フォロー中（クリックで解除）" in ja.text, "the second press must be read in ja"
    assert "Following — click to unfollow" not in ja.text
    client.cookies.delete("lang")


async def test_the_swapped_chip_keeps_the_unused_marking_and_the_count(client):
    """`.tchip.unused` is the signal that a tag is attached to nothing, and the
    `.n2` count is what a chip in the directory shows. Both are computed from
    the tag's concert count, which the swapping route has to work out for
    itself -- the page's one-pass `tag_directory_context` is not in scope.

    Mutation this must fail against: rendering the swapped chip with no count
    at all, which silently drops BOTH the number and the unused marking.
    """
    login_as(client, VIEWER_ID, "viewer")
    dead = await _seed_tag(client, "Dead")
    used = await _seed_tag(client, "Used")
    await _seed_concert_on(client, used)

    r = client.post("/subscriptions",
                    data={"tag_id": dead, "notify": "true", "next": "/tags",
                          "chip": "count"}, headers=HX)
    assert "unused" in r.text, "zero concerts -> unused, even once followed"
    assert '<span class="n2">0</span>' in r.text

    r = client.post("/subscriptions",
                    data={"tag_id": used, "notify": "true", "next": "/tags",
                          "chip": "count"}, headers=HX)
    assert '<span class="n2">1</span>' in r.text
    assert "unused" not in r.text


async def test_a_member_chip_swaps_without_growing_a_count(client):
    """A member chip renders with `count=None` -- no `.n2`, never `unused`.
    `chip=plain` is what carries that through the swap.

    Mutation this must fail against: the route always looking the count up,
    which puts a number on a chip that has never had one.
    """
    login_as(client, VIEWER_ID, "viewer")
    tag_id = await _seed_tag(client, "Member", kind=TagKind.ARTIST)

    r = client.post("/subscriptions",
                    data={"tag_id": tag_id, "notify": "true", "next": "/tags",
                          "chip": "plain"}, headers=HX)
    assert r.status_code == 200
    assert '<span class="n2">' not in r.text
    assert "unused" not in r.text


async def test_a_split_pill_half_swaps_as_its_own_half(client):
    """A pill half is a follow control too. It must come back as a half -- same
    `.half` form, same `cn`/`cv` styling -- or a pressed seiyuu returns wearing
    the character's look, or worse, as a whole chip inside the pill.

    Mutation this must fail against: the route rendering `tag_chip` for every
    press regardless of which shape asked.
    """
    login_as(client, EDITOR_ID, "editor")
    seiyuu = await _seed_tag(client, "今井麻美", kind=TagKind.ARTIST)
    await _seed_tag(client, "如月千早", kind=TagKind.CHARACTER, voiced_by_tag_id=seiyuu)

    r = client.post("/subscriptions",
                    data={"tag_id": seiyuu, "notify": "true", "next": "/tags",
                          "chip": "cv"}, headers=HX)
    assert r.status_code == 200
    assert r.text.strip().startswith('<form class="half"')
    assert 'class="cv on"' in r.text
    assert 'class="chipform"' not in r.text
    assert '<span class="n2">' not in r.text


async def test_the_page_and_the_partial_agree_on_a_pill_half(client):
    """The same byte-identity check, for the half shape. A group is needed for
    the pill to render at all (member chips are what split)."""
    from app.db.models import TagMember

    login_as(client, EDITOR_ID, "editor")
    seiyuu = await _seed_tag(client, "今井麻美", kind=TagKind.ARTIST)
    chara = await _seed_tag(client, "如月千早", kind=TagKind.CHARACTER,
                            voiced_by_tag_id=seiyuu)
    group = await _seed_tag(client, "765PRO", kind=TagKind.GROUP)
    async with client.db() as s:
        s.add(TagMember(group_tag_id=group, member_tag_id=chara))
        await s.commit()

    frag = client.post("/subscriptions",
                       data={"tag_id": chara, "notify": "true", "next": "/tags",
                             "chip": "cn"}, headers=HX).text
    page_half = _form_on(client.get("/tags").text, f'data-tag-id="{chara}"', "half")
    assert frag.strip() == page_half


async def test_the_same_tag_really_does_render_twice(client):
    """The premise of the test below, pinned separately so it cannot rot into
    a duplicate-free page that passes for the wrong reason. A performer in two
    groups gets a chip in each group's row."""
    from app.db.models import TagMember

    login_as(client, EDITOR_ID, "editor")
    artist = await _seed_tag(client, "中村繪里子", kind=TagKind.ARTIST)
    a = await _seed_tag(client, "765PRO", kind=TagKind.GROUP)
    b = await _seed_tag(client, "竜宮小町", kind=TagKind.GROUP)
    async with client.db() as s:
        s.add(TagMember(group_tag_id=a, member_tag_id=artist))
        s.add(TagMember(group_tag_id=b, member_tag_id=artist))
        await s.commit()

    body = client.get("/tags").text
    assert body.count(f'data-tag-id="{artist}"') == 2


async def test_unfollowing_one_copy_leaves_the_other_copy_pressable(client):
    """THE STALE-COPY BUG (found in review, 2026-08-12). /tags renders the same
    tag more than once -- a performer in two groups, a seiyuu who is both a
    direct member and a pill half. A full-page 303 kept every copy in step; a
    one-chip swap cannot.

    Keyed by SUBSCRIPTION ID, the second copy still showed ✓ pointing at a row
    the first copy had just deleted, so pressing it answered 404 -- and htmx
    does not swap a 4xx, so the reader got NOTHING: a chip that lies and then
    refuses to work until a full reload.

    Keyed by TAG the press is idempotent: already unfollowed means there is
    nothing to delete, and the answer is the follow chip either way.

    Mutation this must fail against: an unfollow that resolves the row by id
    (or one that 404s when no row is found) -- both leave the second copy dead.
    """
    from app.db.models import TagMember

    login_as(client, EDITOR_ID, "editor")
    artist = await _seed_tag(client, "中村繪里子", kind=TagKind.ARTIST)
    a = await _seed_tag(client, "765PRO", kind=TagKind.GROUP)
    b = await _seed_tag(client, "竜宮小町", kind=TagKind.GROUP)
    async with client.db() as s:
        s.add(TagMember(group_tag_id=a, member_tag_id=artist))
        s.add(TagMember(group_tag_id=b, member_tag_id=artist))
        await s.commit()

    follow = {"tag_id": artist, "notify": "true", "next": "/tags", "chip": "plain"}
    unfollow = {"tag_id": artist, "next": "/tags", "chip": "plain"}

    r = client.post("/subscriptions", data=follow, headers=HX)
    assert r.status_code == 200 and "on" in r.text

    # Copy A unfollows.
    r = client.post("/subscriptions/unfollow", data=unfollow, headers=HX)
    assert r.status_code == 200
    assert 'action="/subscriptions"' in r.text

    # Copy B is still showing ✓ from the page load. Pressing it must hand back
    # a usable chip, not a 404 with an empty body.
    r = client.post("/subscriptions/unfollow", data=unfollow, headers=HX)
    assert r.status_code == 200, "the stale copy must not 404 into silence"
    assert r.text.strip(), "and it must not answer with an empty body"
    assert 'action="/subscriptions"' in r.text, "it comes back offering follow"
    assert f'name="tag_id" value="{artist}"' in r.text, "and it is pressable"


async def test_following_from_a_stale_copy_is_idempotent_too(client):
    """The other direction, so the pair behaves alike. Two copies both showing
    "follow": pressing the second after the first already followed must answer
    with the followed chip, not a second row or an error. /subscriptions has
    always upserted by (user, tag); this pins that the CHIP relies on it."""
    login_as(client, VIEWER_ID, "viewer")
    tag_id = await _seed_tag(client)
    follow = {"tag_id": tag_id, "notify": "true", "next": "/tags", "chip": "count"}

    first = client.post("/subscriptions", data=follow, headers=HX)
    second = client.post("/subscriptions", data=follow, headers=HX)
    assert first.status_code == second.status_code == 200
    assert first.text == second.text, "the same chip, whichever copy pressed it"
    assert "tchip k-franchise on" in second.text

    from sqlalchemy import select

    from app.db.models import TagSubscription

    async with client.db() as s:
        rows = (await s.execute(select(TagSubscription))).scalars().all()
    assert len(rows) == 1, "and no duplicate subscription row"


async def test_a_hostile_chip_value_cannot_escape_the_whitelist(client):
    """`chip` arrives from the form, so it is user-controlled. `_PILL_HALVES`
    is the whitelist that decides whether it becomes a CSS class on a pill
    half; everything else must fall to the plain chip and the value must reach
    no attribute at all.

    Mutation this must fail against: turning the whitelist into a blacklist
    (`chip not in {"count", "plain"}` -> render a half), which every other test
    here survives. Autoescape keeps today's blast radius cosmetic, but nothing
    else names the defence.
    """
    login_as(client, VIEWER_ID, "viewer")
    tag_id = await _seed_tag(client)

    r = client.post("/subscriptions",
                    data={"tag_id": tag_id, "notify": "true", "next": "/tags",
                          "chip": 'cn" onclick="x'}, headers=HX)
    assert r.status_code == 200
    assert '<form class="chipform"' in r.text, "an unknown shape is a plain chip"
    assert 'class="half"' not in r.text
    assert "onclick" not in r.text
    assert 'cn"' not in r.text


async def test_every_chip_form_on_the_page_can_swap_itself(client):
    """The whole-page guarantee: no chip is left behind as a full-reload form.

    Mutation this must fail against: wiring only the plain chip and leaving
    `follow_half` posting the old way -- exactly the shape of this regression,
    where one surface was fixed and the other kept reloading the page.

    HALF THE SEEDED TAGS ARE FOLLOWED BEFORE THE PAGE IS FETCHED, and the two
    counts below are asserted, because the FOLLOWED branch is the one that
    carries the risk and this test could not see it. Seeding no subscription
    made every form here the unfollowed branch -- which structurally cannot
    contain `/delete` and always carries `tag_id` -- so the two assertions
    that matter were vacuously true, and reverting either followed branch to
    `action="/subscriptions/{{ sub.id }}/delete"` left this file green (found
    in review, 2026-08-12). A sweep that cannot see the branch it guards is
    worse than no sweep: it reads as coverage.
    """
    from app.db.models import TagMember

    login_as(client, EDITOR_ID, "editor")
    seiyuu = await _seed_tag(client, "今井麻美", kind=TagKind.ARTIST)
    chara = await _seed_tag(client, "如月千早", kind=TagKind.CHARACTER,
                            voiced_by_tag_id=seiyuu)
    group = await _seed_tag(client, "765PRO", kind=TagKind.GROUP)
    async with client.db() as s:
        s.add(TagMember(group_tag_id=group, member_tag_id=chara))
        await s.commit()

    # Follow the character (a pill half) and the group (a plain chip); leave
    # the seiyuu half and the franchise unfollowed. Both shapes then render in
    # both states on one page.
    for tid, shape in ((chara, "cn"), (group, "count")):
        r = client.post("/subscriptions",
                        data={"tag_id": tid, "notify": "true", "next": "/tags",
                              "chip": shape})
        assert r.status_code == 303

    body = client.get("/tags").text
    forms = re.findall(r'<form class="(?:chipform|half)".*?</form>', body, re.DOTALL)
    assert len(forms) >= 4, "franchise + group + both pill halves at least"
    followed = [f for f in forms if 'action="/subscriptions/unfollow"' in f]
    unfollowed = [f for f in forms if 'action="/subscriptions"' in f]
    assert len(followed) >= 2 and len(unfollowed) >= 2, (
        f"the sweep must see BOTH branches, in both shapes -- "
        f"{len(followed)} followed / {len(unfollowed)} unfollowed forms"
    )
    assert any('class="half"' in f for f in followed), "including a followed HALF"
    assert any('class="chipform"' in f for f in followed), "and a followed chip"
    for f in forms:
        assert "hx-post=" in f, f"a chip that still reloads the page: {f[:120]}"
        assert 'hx-target="this"' in f and 'hx-swap="outerHTML"' in f
        assert 'method="post"' in f and "action=" in f, (
            "and it must still be a real form for JS-off following"
        )
        assert 'name="chip"' in f, "the shape the swap comes back as"
        assert "/delete" not in f, (
            "no chip may post to the id-keyed unfollow: this page renders the "
            "same tag more than once, and the second copy's id goes stale the "
            "moment the first is pressed"
        )
        assert 'name="tag_id"' in f, "both directions are keyed by tag"
