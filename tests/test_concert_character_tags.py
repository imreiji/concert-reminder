"""The concert editor's CHARACTER path -- resolution AND detach/attach order.

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
the attach loop because she is in `after_ids & before_ids` and therefore in
neither diff. First save loses her; a second identical save puts her back,
which makes it a save-twice recovery rather than a visible error.

Every test here goes through the real HTTP route and re-reads from a FRESH
session, because the bug is entirely about what is left in `concert_tags`
after the request commits -- an assertion against the request's own identity
map would have believed the wrong thing.
"""

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

    r = resubmit(client, "imas-1", day_id, extra={
        "character_tags": [str(chihaya)],
        "artist_tags": [str(imai)],
    })
    assert r.status_code == 303, r.text

    got = await attached(db, cid)
    assert chihaya in got, "the character was detached by a save that submitted her"
    assert imai in got, "the prune rule cascaded her seiyuu away with her"


async def test_unticking_a_character_keeps_a_still_ticked_seiyuu(client, db):
    """The ORDERING half, which survives fixing resolution alone.

    Untick 如月千早, leave 今井麻美 ticked. Detach-before-attach cascades the
    seiyuu off with the character, and the attach loop then skips her because
    she is in `after_ids & before_ids` -- in neither diff. She must stay: the
    editor said so on this very submit.
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

    A form that predates the field still degrades safely, which is the point
    of pairing this with the ordering fix: the seiyuu is an ARTIST and is
    pre-selected from her own attachment, so she is submitted and survives.
    """
    await make_editor(db)
    login(client)
    cid, chihaya, imai, _o, day_id = await seed_imas(db)

    r = resubmit(client, "imas-1", day_id, extra={"artist_tags": [str(imai)]})
    assert r.status_code == 303, r.text

    got = await attached(db, cid)
    assert chihaya not in got
    assert imai in got


async def test_a_shared_seiyuu_survives_unticking_only_one_of_her_roles(client, db):
    """The refinement, through the route: two characters, one voice. Untick
    one and the other still needs her -- the cascade must not fire, and the
    surviving character must not be collateral either."""
    await make_editor(db)
    login(client)
    cid, chihaya, imai, other, day_id = await seed_imas(db, second_role=True)

    r = resubmit(client, "imas-1", day_id, extra={
        "character_tags": [str(other)],
        "artist_tags": [str(imai)],
    })
    assert r.status_code == 303, r.text

    got = await attached(db, cid)
    assert other in got
    assert imai in got
    assert chihaya not in got


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

    The obvious ordering fix is to iterate `after_ids` and let `attach_tag`'s
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


async def test_a_no_op_edit_save_queues_no_notification(client, db):
    """The companion case: re-saving an unchanged concert attaches nothing, so
    it announces nothing. Weaker than the test above -- any shape that reaches
    `attach_tag` here is deduplicated by `_is_attached` before `newly` grows --
    but it is the save an editor actually performs most often, and it pins that
    merely OPENING the editor and pressing save cannot spam a tag's followers.
    """
    await make_editor(db)
    login(client)
    _cid, chihaya, imai, _o, day_id = await seed_imas(db)
    await _follow(db, imai, chihaya)

    r = resubmit(client, "imas-1", day_id, extra={
        "character_tags": [str(chihaya)],
        "artist_tags": [str(imai)],
    })
    assert r.status_code == 303, r.text
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
    gone -- the resolution fix alone would not have been enough."""
    await make_editor(db)
    login(client)
    _cid, chihaya, imai, _o, _day = await seed_imas(db)

    page = client.get("/concerts/imas-1/edit")
    assert page.status_code == 200
    body = page.text
    assert 'id="sel-character"' in body, "the picker has no character chip row"
    # INITIAL_SELECTED is `| tojson`-serialized, so the ids are JSON strings.
    marker = body.split("const INITIAL_SELECTED = ")[1].split(";")[0]
    assert f'"{chihaya}"' in marker, "the attached character is not pre-selected"
    assert f'"{imai}"' in marker, "her seiyuu is not pre-selected either"


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
