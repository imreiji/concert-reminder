"""The concert editor's CHARACTER path -- resolution, detach/attach order, and
the owner ruling that a DERIVED SEIYUU IS NEVER OFFERED AS A CHOICE.

Two holes, both found by review rather than by the plan, both silent:

1. `resolve_tags` was called for FRANCHISE / GROUP / ARTIST / VENUE only, so a
   character could not be put on a concert through the UI at all.
2. `edit_concert` diffed `desired_tags` (those four kinds) against `before_ids`
   (every attached non-VENUE tag), so any attached CHARACTER landed in
   `before_ids - after_ids` and was detached on save -- and, with the prune
   rule live, took her seiyuu with her. The routine edit of an im@s concert
   stripped exactly the performer this feature exists to reach.

The ordering is the subtle half and survives fixing (1): the detach loop runs
BEFORE the attach loop, so unticking a character while leaving her seiyuu
ticked detaches the character, cascades the seiyuu away, and then skips her in
the attach loop because she is in `keep_ids & before_ids` and therefore in
neither diff. First save loses her; a second identical save puts her back,
which makes it a save-twice recovery rather than a visible error.

THE RULING (2026-08-01) then changed what "ticked" means. For an event where a
seiyuu represents a character, ONLY the character is added; the artist is
auto-correlated and displayed as `cv. xxx`. An artist added by herself
correlates with no character. So a derived seiyuu is not pre-ticked, the form
does not submit her, and the desired set expands each character to her seiyuu
before the detach diff instead. The two changes are opposite sides of one idea
and only work together: change one alone leaves her offered as a choice she is
not, change two alone leaves the prune rule unreachable from this editor.

Every test here goes through the real HTTP route and re-reads from a FRESH
session, because the bug is entirely about what is left in `concert_tags`
after the request commits -- an assertion against the request's own identity
map would have believed the wrong thing. The tests that turn on the ruling go
one step further and submit WHAT THE BROWSER WOULD (see `form_selection`): a
hand-written form can tick a seiyuu the real page never offers, which is
exactly how a test about pre-ticking ends up not depending on pre-ticking.
"""

import json
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import (
    Base,
    Concert,
    ConcertDay,
    ConcertTag,
    Notification,
    Tag,
    TagMember,
    TagSubscription,
    User,
)
from app.db.service import attach_tag, ensure_user
from app.db.session import get_session
from app.domain.types import TagKind
from app.web import auth
from app.web.app import create_app

EDITOR = 777
FOLLOWER = 888


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    # Production registers this too; cascades silently do not fire without it.
    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


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


def login(client, discord_id: int = EDITOR, name: str = "editor"):
    async def fake_identity(token):
        return {"id": str(discord_id), "username": name, "global_name": name, "avatar": None}

    client.monkeypatch.setattr(auth, "fetch_identity", fake_identity)
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    client.get(f"/auth/callback?code=x&state={state}")


async def make_editor(db, discord_id: int = EDITOR, name: str = "editor"):
    async with db() as s:
        await ensure_user(s, discord_id, name)
        u = await s.get(User, discord_id)
        u.is_editor = True
        await s.commit()


async def seed_imas(db, *, event_id="imas-1", second_role=False):
    """An im@s concert credited to 如月千早, whose seiyuu 今井麻美 is pulled in
    by `attach_tag`'s chaining -- exactly how the production path builds one.

    Returns (concert_id, chihaya_id, imai_id, other_role_id | None, day_id).
    """
    async with db() as s:
        await ensure_user(s, EDITOR, "editor")
        c = Concert(title="THE IDOLM@STER", event_id=event_id, created_by=EDITOR)
        imai = Tag(name="今井麻美", kind=TagKind.ARTIST, slug="asami-imai")
        s.add_all([c, imai])
        await s.flush()
        chihaya = Tag(name="如月千早", kind=TagKind.CHARACTER, slug="chihaya",
                      voiced_by_tag_id=imai.id)
        s.add(chihaya)
        other = None
        if second_role:
            other = Tag(name="別の役", kind=TagKind.CHARACTER, slug="other-role",
                        voiced_by_tag_id=imai.id)
            s.add(other)
        day = ConcertDay(
            concert_id=c.id, label="Day 1",
            starts_at_utc=datetime(2099, 8, 1, 9, 0, tzinfo=UTC),
        )
        s.add(day)
        await s.flush()
        await attach_tag(s, c.id, chihaya)
        if other is not None:
            await attach_tag(s, c.id, other)
        await s.commit()
        return c.id, chihaya.id, imai.id, (other.id if other else None), day.id


async def attached(db, concert_id) -> set[int]:
    """Read from a FRESH session -- the whole point of these tests."""
    async with db() as s:
        return set((await s.execute(
            select(ConcertTag.tag_id).where(ConcertTag.concert_id == concert_id)
        )).scalars())


def resubmit(client, event_id, day_id, *, extra=None, title="THE IDOLM@STER"):
    """POST the edit form back with the leg intact and no rounds -- the
    minimum a real save carries. `extra` supplies the tag fields."""
    data = {
        "title": title,
        "event_id": event_id,
        "day_id": [str(day_id)],
        "day_key": [str(day_id)],
        "day_label": ["Day 1"],
        "day_label_en": [""],
        "day_label_zh": [""],
        "day_starts_at": ["2099-08-01T18:00"],
        "day_doors_at": [""],
        "day_cancelled": ["false"],
    }
    data.update(extra or {})
    return client.post(f"/concerts/{event_id}/edit", data=data)


def form_selection(client, event_id) -> dict[str, list[str]]:
    """The RAW `INITIAL_SELECTED` the edit page hands the picker.

    Use this only to assert what the page pre-ticks (the ruling's "she is not
    offered as a tick"). For what the browser would POST, use
    `picker_payload` -- two of the four rows are derived, so these are not the
    same question.
    """
    return _js_const(_edit_page(client, event_id), "INITIAL_SELECTED")


def _edit_page(client, event_id) -> str:
    page = client.get(f"/concerts/{event_id}/edit")
    assert page.status_code == 200, page.text
    return page.text


def _js_const(html: str, name: str):
    return json.loads(html.split(f"const {name} = ")[1].split(";\n")[0])


def picker_payload(html: str, *, tick_groups=()) -> dict[str, list[str]]:
    """What `syncHidden()` in _tag_picker_script.html would POST for this page.

    A PORT of that script's `recomputeCharacters`/`recomputeArtists`, not a
    paraphrase of `INITIAL_SELECTED`. Two of the picker's four rows are
    DERIVED -- a selected group expands to its ARTIST members and its CHARACTER
    members, minus each row's excluded set, plus each row's manual set, and a
    seiyuu implied by a selected character is removed from the artist row. A
    test that posts `INITIAL_SELECTED` directly asks a question the browser
    never asks, and is exactly why "a group whose members are characters" went
    unnoticed: the ids the page would have posted were never computed.

    `tick_groups` is the one gesture a rendered page cannot show on its own --
    an editor ticking a group on a blank creation form.
    """
    groups = _js_const(html, "NC_GROUPS")
    seiyuu = _js_const(html, "CHAR_SEIYUU")
    initial = _js_const(html, "INITIAL_SELECTED")

    sel_group = set(initial.get("group") or []) | {str(g) for g in tick_groups}

    def auto(key: str) -> set[str]:
        ids: set[str] = set()
        for g in sel_group:
            for m in (groups.get(g) or {}).get(key) or []:
                ids.add(str(m["id"]))
        return ids

    characters = auto("character_members") - set(initial.get("character_excluded") or [])
    characters |= set(initial.get("character") or [])
    artists = auto("members")
    for c in characters:                      # autoArtists()'s derived-seiyuu step
        if seiyuu.get(c) is not None:
            artists.discard(str(seiyuu[c]))
    artists -= set(initial.get("artist_excluded") or [])
    artists |= set(initial.get("artist") or [])
    return {
        "franchise_tags": sorted(initial.get("franchise") or []),
        "group_tags": sorted(sel_group),
        "character_tags": sorted(characters),
        "artist_tags": sorted(artists),
    }


def resubmit_as_rendered(client, event_id, day_id, *, drop_characters=(), **kw):
    """Press save on the edit page, optionally unticking some characters
    first -- the two gestures an editor actually has.

    Posts ALL FOUR tag rows the picker emits, not just the two this file cares
    about: dropping franchise_tags/group_tags would make every save here read
    as "the editor also removed the group", which is not the gesture under
    test and would hide a group-shaped regression behind a character-shaped
    one.
    """
    dropped = {str(i) for i in drop_characters}
    payload = picker_payload(_edit_page(client, event_id))
    payload["character_tags"] = [i for i in payload["character_tags"] if i not in dropped]
    return resubmit(client, event_id, day_id, extra=payload, **kw)


# ── the regression this task exists for ──────────────────────────────────


async def test_a_character_survives_an_edit_save(client, db):
    """The regression the review found. edit_concert diffs desired_tags
    against every attached non-venue tag, so a kind it does not resolve is
    detached on save. Combined with the prune rule that would strip the
    seiyuu too, silently undoing the whole feature on a routine edit."""
    await make_editor(db)
    login(client)
    cid, chihaya, imai, _o, day_id = await seed_imas(db)
    assert await attached(db, cid) == {chihaya, imai}, "seed built the wrong shape"

    r = resubmit_as_rendered(client, "imas-1", day_id)
    assert r.status_code == 303, r.text

    got = await attached(db, cid)
    assert chihaya in got, "the character was detached by a save that submitted her"
    assert imai in got, "the prune rule cascaded her seiyuu away with her"


async def test_an_explicitly_ticked_seiyuu_survives_unticking_her_character(client, db):
    """The ORDERING half, and the case that keeps `keep_tag_ids` load-bearing.

    Under the ruling this is now a DELIBERATE gesture rather than an artefact
    of pre-ticking: 今井麻美 is not offered as a tick while 如月千早 is
    attached, so a submit that carries her is an editor saying "credit the
    performer, not the character". Untick the character, tick the artist.

    Detach-before-attach cascades the seiyuu off with the character, and the
    attach loop then skips her because she is in `keep_ids & before_ids` -- in
    neither diff. She must stay: the editor said so on this very submit.
    """
    await make_editor(db)
    login(client)
    cid, chihaya, imai, _o, day_id = await seed_imas(db)

    r = resubmit(client, "imas-1", day_id, extra={"artist_tags": [str(imai)]})
    assert r.status_code == 303, r.text

    got = await attached(db, cid)
    assert chihaya not in got, "the unticked character should be gone"
    assert imai in got, "the seiyuu was ticked on this submit and must survive it"


async def test_omitting_character_tags_removes_the_character(client, db):
    """DELIBERATE, and asserted so nobody 'fixes' it into keep-on-omission.

    The picker emits a hidden input per SELECTED id and none at all for an
    empty row, so `character_tags` absent is exactly how the form says "no
    characters" -- the same rule franchise_tags/group_tags/artist_tags have
    always followed. Reading omission as "leave them alone" would make
    removing the last character impossible through the UI.

    This one submits NEITHER row, which is what a form that predates the
    character field does. Under the ruling that is unambiguous: no character,
    no artist ticked in her own right, so the pair goes. (Before the ruling the
    seiyuu was pre-ticked and such a form silently rescued her.)
    """
    await make_editor(db)
    login(client)
    cid, chihaya, imai, _o, day_id = await seed_imas(db)

    r = resubmit(client, "imas-1", day_id)
    assert r.status_code == 303, r.text

    got = await attached(db, cid)
    assert chihaya not in got
    assert imai not in got


async def test_a_shared_seiyuu_survives_unticking_only_one_of_her_roles(client, db):
    """The refinement, through the route: two characters, one voice. Untick
    one and the other still needs her -- the cascade must not fire, and the
    surviving character must not be collateral either.

    Submitted as the page renders it, so the seiyuu is carried by the SURVIVING
    character's expansion and by nothing else. Ticking her by hand here would
    have made the test pass on the `keep_tag_ids` guard instead and said
    nothing about the shared-role refinement.
    """
    await make_editor(db)
    login(client)
    cid, chihaya, imai, other, day_id = await seed_imas(db, second_role=True)

    r = resubmit_as_rendered(client, "imas-1", day_id, drop_characters=[chihaya])
    assert r.status_code == 303, r.text

    got = await attached(db, cid)
    assert other in got
    assert imai in got
    assert chihaya not in got


# ── the ruling: a derived seiyuu is never offered as a choice ────────────


async def test_a_derived_seiyuu_is_not_pre_ticked(client, db):
    """She is not an editor choice. Offering her as one is what made the prune
    rule unreachable: pre-ticked means always submitted means always in the
    desired set means never detached, whatever became of her character.

    The character IS still pre-ticked -- the assertion pairs them so a page
    that rendered no selection at all could not pass this.
    """
    await make_editor(db)
    login(client)
    _cid, chihaya, imai, _o, _day = await seed_imas(db)

    sel = form_selection(client, "imas-1")
    assert sel["character"] == [str(chihaya)], "the character must still be a tick"
    assert str(imai) not in sel["artist"], (
        "her seiyuu is derived from the character and must not be offered as a tick"
    )
    # Nor smuggled in as a pruned group member, which would suppress her chip
    # for a different reason and hide a real regression here later.
    assert str(imai) not in sel["artist_excluded"]


async def test_a_seiyuu_attached_without_her_character_is_pre_ticked(client, db):
    """"An artist added by herself correlates with no character." Derived means
    "some ATTACHED character names her" -- not "she is somebody's voice", which
    is a property of the tag and true forever. Get that wrong and every seiyuu
    in the catalogue silently stops being editable as a performer.
    """
    await make_editor(db)
    login(client)
    async with db() as s:
        await ensure_user(s, EDITOR, "editor")
        c = Concert(title="Solo live", event_id="solo-1", created_by=EDITOR)
        imai = Tag(name="今井麻美", kind=TagKind.ARTIST, slug="asami-imai")
        s.add_all([c, imai])
        await s.flush()
        # She voices somebody; that character is simply not on this bill.
        s.add(Tag(name="如月千早", kind=TagKind.CHARACTER, slug="chihaya",
                  voiced_by_tag_id=imai.id))
        s.add(ConcertDay(concert_id=c.id, label="Day 1",
                         starts_at_utc=datetime(2099, 8, 1, 9, 0, tzinfo=UTC)))
        await s.flush()
        await attach_tag(s, c.id, imai)
        await s.commit()
        imai_id = imai.id

    assert form_selection(client, "solo-1")["artist"] == [str(imai_id)]


async def test_unticking_a_character_removes_her_seiyuu(client, db):
    """The owner rule, now actually reachable from the editor.

    Submitted as the page renders it: with the seiyuu no longer pre-ticked, the
    only thing carrying her is the character, so unticking the character drops
    both. Ticking her by hand instead is a different gesture with a different
    (also correct) answer -- see the explicit-tick test above.
    """
    await make_editor(db)
    login(client)
    cid, chihaya, imai, _o, day_id = await seed_imas(db)

    r = resubmit_as_rendered(client, "imas-1", day_id, drop_characters=[chihaya])
    assert r.status_code == 303, r.text

    got = await attached(db, cid)
    assert chihaya not in got, "the unticked character should be gone"
    assert imai not in got, "nothing credits her any more; the prune rule must fire"


async def test_keeping_a_character_keeps_her_seiyuu(client, db):
    """The case the naive fix breaks: she is not in the submitted artist list,
    so a plain before/after diff reads "removed" and detaches her on EVERY
    save -- silently stripping the performer the feature exists to reach.

    The desired set has to expand each character to her seiyuu before the
    detach diff, mirroring what `attach_tag` already does on the attach side.
    """
    await make_editor(db)
    login(client)
    cid, chihaya, imai, _o, day_id = await seed_imas(db)

    sel = form_selection(client, "imas-1")
    assert str(imai) not in sel["artist"], "precondition: she is not a tick"

    r = resubmit_as_rendered(client, "imas-1", day_id)
    assert r.status_code == 303, r.text
    assert await attached(db, cid) == {chihaya, imai}


async def test_a_standalone_artist_is_not_treated_as_derived(client, db):
    """Two artists on one concert: one derived from an attached character, one
    the editor added in her own right. Only the derived one disappears from the
    tick list, and the other survives a save that never mentions the character.
    """
    await make_editor(db)
    login(client)
    cid, chihaya, imai, _o, day_id = await seed_imas(db)
    async with db() as s:
        guest = Tag(name="中村繪里子", kind=TagKind.ARTIST, slug="eriko-nakamura")
        s.add(guest)
        await s.flush()
        await attach_tag(s, cid, guest)
        await s.commit()
        guest_id = guest.id

    sel = form_selection(client, "imas-1")
    assert sel["artist"] == [str(guest_id)], (
        "the standalone artist must stay a tick and the derived seiyuu must not"
    )

    r = resubmit_as_rendered(client, "imas-1", day_id)
    assert r.status_code == 303, r.text
    assert await attached(db, cid) == {chihaya, imai, guest_id}


async def test_an_artist_also_named_by_a_kept_character_collapses_to_derived(client, db):
    """THE OVERLAP CASE, which is contradictory data rather than a supported
    state: an event either credits the character or credits the performer.

    No machinery is built for it. The behaviour that falls out is stated here
    so it is not rediscovered as a bug: a hand-written submit CAN carry both,
    it is accepted (she is desired either way, so nothing detaches her), and it
    is NOT REMEMBERED -- there is no provenance column, so the very next render
    reads her as derived and the next save treats her as derived.
    """
    await make_editor(db)
    login(client)
    cid, chihaya, imai, _o, day_id = await seed_imas(db)

    r = resubmit(client, "imas-1", day_id, extra={
        "character_tags": [str(chihaya)],
        "artist_tags": [str(imai)],
    })
    assert r.status_code == 303, r.text
    assert await attached(db, cid) == {chihaya, imai}, "the contradiction is accepted"

    # ...and immediately forgotten: she is derived again on the next render.
    assert str(imai) not in form_selection(client, "imas-1")["artist"]
    r = resubmit_as_rendered(client, "imas-1", day_id, drop_characters=[chihaya])
    assert r.status_code == 303, r.text
    assert await attached(db, cid) == set(), (
        "the standalone tick is not provenance and must not outlive the render"
    )


async def _follow(db, *tag_ids):
    """A subscriber for every tag in play, so the outbox has something to fill
    with. Without one it is empty however wrong the code is -- which is exactly
    how the first draft of the test below passed against the shape it was
    written to reject."""
    async with db() as s:
        await ensure_user(s, FOLLOWER, "follower")
        s.add_all([
            TagSubscription(user_id=FOLLOWER, tag_id=tid, notify=True)
            for tid in tag_ids
        ])
        await s.commit()


async def _queued(db):
    async with db() as s:
        return [(n.kind, n.user_id) for n in
                (await s.execute(select(Notification))).scalars()]


async def test_unticking_a_character_queues_no_notification_for_her_seiyuu(client, db):
    """Invariant 4, and the SHARP end of it -- this is the trap the brief
    warned about, in the one scenario that springs it.

    The obvious ordering fix is to iterate `keep_ids` and let `attach_tag`'s
    `_is_attached` deduplicate. On this submit the character's detach cascades
    the seiyuu off, so `_is_attached` is FALSE when the loop reaches her, she
    is re-attached, and she lands in `newly` -- which `handle_newly_tagged`
    turns into a 🆕 "New event" DM to every follower she has, for a concert
    that already existed and that no DM can be un-sent from.

    A save that only REMOVES things must announce nothing, to anybody.
    """
    await make_editor(db)
    login(client)
    cid, chihaya, imai, _o, day_id = await seed_imas(db)
    await _follow(db, imai, chihaya)

    r = resubmit(client, "imas-1", day_id, extra={"artist_tags": [str(imai)]})
    assert r.status_code == 303, r.text

    assert imai in await attached(db, cid), "precondition: she must have survived"
    assert await _queued(db) == [], (
        f"a removal-only save announced something: {await _queued(db)}"
    )


async def test_an_ordinary_edit_save_changes_something_and_announces_nothing(client, db):
    """The companion case, and the one an editor performs most often: open the
    editor, change the title, press save. It attaches nothing, so it announces
    nothing -- merely editing a concert cannot spam a tag's followers.

    Submitted as the page renders it, and asserting BOTH tags survived, so it
    cannot pass vacuously: a shape that detached the pair would also queue
    nothing. The title change is what keeps it from being a no-op -- an
    unchanged save is a weaker claim, since `record_concert_edit` would find no
    diff either.
    """
    await make_editor(db)
    login(client)
    cid, chihaya, imai, _o, day_id = await seed_imas(db)
    await _follow(db, imai, chihaya)

    r = resubmit_as_rendered(client, "imas-1", day_id, title="THE IDOLM@STER 10th")
    assert r.status_code == 303, r.text

    async with db() as s:
        assert (await s.get(Concert, cid)).title == "THE IDOLM@STER 10th", (
            "precondition: this save has to have changed something"
        )
    assert await attached(db, cid) == {chihaya, imai}, "the save lost a tag"
    assert await _queued(db) == []


async def test_a_save_neither_attaches_nor_announces_a_missing_seiyuu(client, db):
    """Why the expansion feeds the DETACH diff only, and not the attach loop.

    A concert can carry a character without her seiyuu: rows predate the
    chaining, and an older save could untick her while keeping the character.
    Expanding the desired set for BOTH diffs would quietly attach her on the
    next unrelated save -- and `handle_newly_tagged` would DM a 🆕 "New event"
    to every follower she has, for a concert that already existed, with no
    un-send (invariant 4). Repairing that pairing is the editor's call, made by
    re-adding the character; a save must not make it for them.
    """
    await make_editor(db)
    login(client)
    cid, chihaya, imai, _o, day_id = await seed_imas(db)
    await _follow(db, imai, chihaya)
    async with db() as s:
        row = (await s.execute(
            select(ConcertTag).where(
                ConcertTag.concert_id == cid, ConcertTag.tag_id == imai
            )
        )).scalar_one()
        await s.delete(row)
        await s.commit()

    r = resubmit_as_rendered(client, "imas-1", day_id, title="THE IDOLM@STER 10th")
    assert r.status_code == 303, r.text

    assert await attached(db, cid) == {chihaya}, "the save attached her by itself"
    assert await _queued(db) == []


# ── creation, and the round trip back into the form ──────────────────────


async def test_creating_a_concert_with_a_character_attaches_her_seiyuu(client, db):
    """The other half of hole 1: without CHARACTER in create_concert_row's
    resolve/attach path there is no way to put one on a concert at all."""
    await make_editor(db)
    login(client)
    async with db() as s:
        imai = Tag(name="今井麻美", kind=TagKind.ARTIST, slug="asami-imai")
        s.add(imai)
        await s.flush()
        chihaya = Tag(name="如月千早", kind=TagKind.CHARACTER, slug="chihaya",
                      voiced_by_tag_id=imai.id)
        s.add(chihaya)
        await s.commit()
        chihaya_id, imai_id = chihaya.id, imai.id

    r = client.post("/concerts", data={
        "title": "ミリオンライブ",
        "title_en": "Million Live",
        "title_zh": "百万现场",
        "event_id": "ml-1",
        "character_tags": [str(chihaya_id)],
        "day_key": ["d0"],
        "day_label": ["Day 1"],
        "day_label_en": ["Day 1"],
        "day_label_zh": ["Day 1"],
        "day_starts_at": ["2099-08-01T18:00"],
        "day_doors_at": [""],
        "day_cancelled": ["false"],
    })
    assert r.status_code == 303, r.text

    async with db() as s:
        concert = (await s.execute(
            select(Concert).where(Concert.event_id == "ml-1")
        )).scalar_one()
    assert await attached(db, concert.id) == {chihaya_id, imai_id}


async def test_the_edit_page_pre_selects_an_attached_character(client, db):
    """`initial_selected` round-trip: without a `character` bucket the picker
    renders the row empty, the editor saves without noticing, and the tag is
    gone -- the resolution fix alone would not have been enough.

    Her seiyuu is a separate question and belongs to the ruling; see
    `test_a_derived_seiyuu_is_not_pre_ticked`.
    """
    await make_editor(db)
    login(client)
    _cid, chihaya, _imai, _o, _day = await seed_imas(db)

    page = client.get("/concerts/imas-1/edit")
    assert page.status_code == 200
    assert 'id="sel-character"' in page.text, "the picker has no character chip row"
    assert form_selection(client, "imas-1")["character"] == [str(chihaya)]


async def test_the_creation_form_offers_the_character_picker(client, db):
    """Every page needs a logged-in GET render test; this one also pins that
    the shared partial's new row reaches the blank creation form, not just
    the edit page."""
    await make_editor(db)
    login(client)
    async with db() as s:
        s.add(Tag(name="如月千早", kind=TagKind.CHARACTER, slug="chihaya"))
        await s.commit()

    page = client.get("/concerts/new")
    assert page.status_code == 200
    assert 'id="sel-character"' in page.text
    assert 'id="picker-character"' in page.text
    assert 'put("character_tags"' in page.text


# ── a GROUP whose members are CHARACTER tags ─────────────────────────────
#
# The shape the im@s reformat produces, and the case NO test anywhere combined
# before: group_tags and character_tags together. The picker built its
# group->members map with no kind filter, so ticking such a group posted
# CHARACTER ids as artist_tags -- a 422 the concert could not be created past,
# and, once the editor unticked the offending chips to get past it, a SILENT
# loss: the group attached alone (create expands with expand=False), so neither
# the characters nor their seiyuu landed and the performer's followers were
# never matched.


async def seed_765(db, *, seiyuu_is_also_a_member=False):
    """765PRO ALLSTARS as the reformat leaves it: a GROUP whose members are
    CHARACTER tags, each voiced by an ARTIST.

    Membership is written as TagMember rows directly, which is how it really
    arrives -- `apply_tag_import` writes them, and the Tags page's "+ Add
    member" select offers artists only.

    Returns (group, chihaya, imai, haruka, nakamura).
    """
    async with db() as s:
        await ensure_user(s, EDITOR, "editor")
        group = Tag(name="765PRO ALLSTARS", kind=TagKind.GROUP, slug="765pro")
        imai = Tag(name="今井麻美", kind=TagKind.ARTIST, slug="asami-imai")
        nakamura = Tag(name="中村繪里子", kind=TagKind.ARTIST, slug="eriko-nakamura")
        s.add_all([group, imai, nakamura])
        await s.flush()
        chihaya = Tag(name="如月千早", kind=TagKind.CHARACTER, slug="chihaya",
                      voiced_by_tag_id=imai.id)
        haruka = Tag(name="天海春香", kind=TagKind.CHARACTER, slug="haruka",
                     voiced_by_tag_id=nakamura.id)
        s.add_all([chihaya, haruka])
        await s.flush()
        s.add_all([
            TagMember(group_tag_id=group.id, member_tag_id=chihaya.id),
            TagMember(group_tag_id=group.id, member_tag_id=haruka.id),
        ])
        if seiyuu_is_also_a_member:
            # The pre-reformat overlap: the group still lists the PERFORMER as
            # a direct artist member while also listing the character she
            # voices.
            s.add(TagMember(group_tag_id=group.id, member_tag_id=imai.id))
        await s.commit()
        return group.id, chihaya.id, imai.id, haruka.id, nakamura.id


def create_from_picker(client, payload, *, event_id="imas-g"):
    data = {
        "title": "THE IDOLM@STER 10th",
        "title_en": "THE IDOLM@STER 10th",
        "title_zh": "偶像大师 10th",
        "event_id": event_id,
        "day_key": ["d0"],
        "day_label": ["Day 1"],
        "day_label_en": ["Day 1"],
        "day_label_zh": ["Day 1"],
        "day_starts_at": ["2099-08-01T18:00"],
        "day_doors_at": [""],
        "day_cancelled": ["false"],
    }
    data.update(payload)
    return client.post("/concerts", data=data)


async def concert_id(db, event_id):
    async with db() as s:
        return (await s.execute(
            select(Concert.id).where(Concert.event_id == event_id)
        )).scalar_one()


async def test_ticking_a_group_of_characters_posts_them_as_characters(client, db):
    """The 422 half, at its source. Every CHARACTER member must reach
    `character_tags`; `artist_tags` must carry none of them -- resolve_tags
    refuses a CHARACTER id there and the create dies with
    422 {"detail":"invalid artist tag"}."""
    await make_editor(db)
    login(client)
    group, chihaya, imai, haruka, nakamura = await seed_765(db)

    payload = picker_payload(client.get("/concerts/new").text, tick_groups=[group])
    assert payload["character_tags"] == sorted([str(chihaya), str(haruka)])
    assert payload["artist_tags"] == [], (
        "a character id posted as an artist is the 422; a seiyuu posted here is "
        "the prune rule going unreachable"
    )
    assert payload["group_tags"] == [str(group)]


async def test_creating_a_concert_from_a_group_of_characters(client, db):
    """Both halves through the real route: the create SUCCEEDS, and what lands
    in concert_tags is the group, both characters AND both seiyuu -- the last
    being the only reason a follower of 今井麻美 is matched at all."""
    await make_editor(db)
    login(client)
    group, chihaya, imai, haruka, nakamura = await seed_765(db)

    payload = picker_payload(client.get("/concerts/new").text, tick_groups=[group])
    r = create_from_picker(client, payload)
    assert r.status_code == 303, r.text

    cid = await concert_id(db, "imas-g")
    assert await attached(db, cid) == {group, chihaya, haruka, imai, nakamura}


async def test_a_group_of_artists_is_untouched_by_the_split(client, db):
    """The Love Live shape -- no characters anywhere -- must behave exactly as
    it did: artist members still expand into the artist row and nothing lands
    in the character row."""
    await make_editor(db)
    login(client)
    async with db() as s:
        await ensure_user(s, EDITOR, "editor")
        group = Tag(name="Hasunosora", kind=TagKind.GROUP, slug="hasunosora")
        kozue = Tag(name="乙宗梢", kind=TagKind.ARTIST, slug="kozue")
        s.add_all([group, kozue])
        await s.flush()
        s.add(TagMember(group_tag_id=group.id, member_tag_id=kozue.id))
        await s.commit()
        group_id, kozue_id = group.id, kozue.id

    payload = picker_payload(client.get("/concerts/new").text, tick_groups=[group_id])
    assert payload["artist_tags"] == [str(kozue_id)]
    assert payload["character_tags"] == []

    r = create_from_picker(client, payload, event_id="ll-1")
    assert r.status_code == 303, r.text
    assert await attached(db, await concert_id(db, "ll-1")) == {group_id, kozue_id}


async def test_pruning_a_group_character_sticks_across_the_round_trip(client, db):
    """Invariant 3's prune rule, now reachable for characters: × one of the
    group's characters, save, and neither she nor her seiyuu is attached --
    and the next render does NOT re-tick her just because her group is."""
    await make_editor(db)
    login(client)
    group, chihaya, imai, haruka, nakamura = await seed_765(db)
    r = create_from_picker(
        client, picker_payload(client.get("/concerts/new").text, tick_groups=[group])
    )
    assert r.status_code == 303, r.text
    cid = await concert_id(db, "imas-g")
    async with db() as s:
        day_id = (await s.execute(
            select(ConcertDay.id).where(ConcertDay.concert_id == cid)
        )).scalar_one()

    r = resubmit_as_rendered(
        client, "imas-g", day_id, drop_characters=[chihaya], title="THE IDOLM@STER 10th"
    )
    assert r.status_code == 303, r.text
    assert await attached(db, cid) == {group, haruka, nakamura}, (
        "the pruned character must go, and take her seiyuu with her"
    )

    assert str(chihaya) in form_selection(client, "imas-g")["character_excluded"], (
        "a pruned character must be remembered as pruned, not as never-a-member"
    )
    assert picker_payload(_edit_page(client, "imas-g"))["character_tags"] == [str(haruka)]


async def test_a_seiyuu_who_is_also_a_group_member_is_not_offered_as_a_tick(client, db):
    """The group-member hole, closed by the same split.

    今井麻美 is a direct ARTIST member of the group AND the voice of an attached
    character. Offering her in the artist row means POSTING her, which means
    edit_concert's `after_ids` holds her on every save -- so dropping 如月千早
    could never drop her, and the prune rule was unreachable from this editor
    for exactly the tag it matters most for.
    """
    await make_editor(db)
    login(client)
    group, chihaya, imai, haruka, nakamura = await seed_765(
        db, seiyuu_is_also_a_member=True
    )

    payload = picker_payload(client.get("/concerts/new").text, tick_groups=[group])
    assert str(imai) not in payload["artist_tags"], (
        "a derived seiyuu is auto-correlated, never a tick -- even as a member"
    )
    r = create_from_picker(client, payload)
    assert r.status_code == 303, r.text
    cid = await concert_id(db, "imas-g")
    assert imai in await attached(db, cid), "the chained step still attaches her"

    async with db() as s:
        day_id = (await s.execute(
            select(ConcertDay.id).where(ConcertDay.concert_id == cid)
        )).scalar_one()
    r = resubmit_as_rendered(
        client, "imas-g", day_id, drop_characters=[chihaya], title="THE IDOLM@STER 10th"
    )
    assert r.status_code == 303, r.text
    assert imai not in await attached(db, cid), (
        "dropping her character must drop her; being a group member must not pin her"
    )


async def test_a_group_of_characters_announces_each_follower_once(client, db):
    """Invariant 4 across the widened path: a create that materialises two
    characters and two seiyuu queues exactly ONE notice per follower."""
    await make_editor(db)
    login(client)
    group, chihaya, imai, haruka, nakamura = await seed_765(db)
    await _follow(db, group, chihaya, imai, haruka, nakamura)

    r = create_from_picker(
        client, picker_payload(client.get("/concerts/new").text, tick_groups=[group])
    )
    assert r.status_code == 303, r.text

    queued = await _queued(db)
    assert len(queued) == 1, queued


async def test_a_wrong_kind_id_in_character_tags_is_a_422(client, db):
    """CHARACTER joins the same resolve_tags gate every other kind passes --
    an id naming an ARTIST must not be attached as a character."""
    await make_editor(db)
    login(client)
    cid, chihaya, imai, _o, day_id = await seed_imas(db)

    r = resubmit(client, "imas-1", day_id, extra={"character_tags": [str(imai)]})
    assert r.status_code == 422
    # Nothing was written: the concert still carries exactly what it had.
    assert await attached(db, cid) == {chihaya, imai}
