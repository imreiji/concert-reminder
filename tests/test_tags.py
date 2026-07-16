"""Tag tests: creation gating, THE group-expansion semantics, filtering, sorting.

The expansion rules were agreed explicitly and are the contract Phase 10's
notifications will rely on, so they get exhaustive coverage:
  1. attaching a group tag materializes its members onto the concert
  2. editors can then remove individual members (not performing)
  3. later edits / membership changes never re-add removed members
  4. detach + re-attach of the group DOES re-expand (it's "newly added" again)
"""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base, Concert, ConcertTag, Tag, TagMember, User
from app.db.service import attach_tag, detach_tag
from app.db.session import get_session
from app.domain.types import TagKind
from app.web import auth
from app.web.app import create_app

EDITOR_ID, VIEWER_ID = 42, 777


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


# ── Service-level: expansion semantics ───────────────────────────────────


async def seed_group(s) -> tuple[Tag, Tag, Tag, Concert]:
    s.add(User(discord_id=EDITOR_ID, username="reiji"))
    await s.flush()
    group = Tag(name="Hasunosora", kind=TagKind.GROUP, created_by=EDITOR_ID)
    a1 = Tag(name="Kozue Otomune", kind=TagKind.ARTIST, created_by=EDITOR_ID)
    a2 = Tag(name="Kaho Hinoshita", kind=TagKind.ARTIST, created_by=EDITOR_ID)
    concert = Concert(title="Hasunosora 6th", created_by=EDITOR_ID)
    s.add_all([group, a1, a2, concert])
    await s.flush()
    s.add_all([
        TagMember(group_tag_id=group.id, member_tag_id=a1.id),
        TagMember(group_tag_id=group.id, member_tag_id=a2.id),
    ])
    await s.flush()
    return group, a1, a2, concert


async def tag_ids_on(s, concert_id) -> set[int]:
    res = await s.execute(select(ConcertTag.tag_id).where(ConcertTag.concert_id == concert_id))
    return set(res.scalars())


async def test_attaching_group_materializes_members(db):
    async with db() as s:
        group, a1, a2, concert = await seed_group(s)
        added = await attach_tag(s, concert.id, group)
        assert {t.id for t in added} == {group.id, a1.id, a2.id}
        assert await tag_ids_on(s, concert.id) == {group.id, a1.id, a2.id}


async def test_removed_member_stays_removed(db):
    """Rule 3: pruning a non-performer is permanent across ordinary edits."""
    async with db() as s:
        group, a1, a2, concert = await seed_group(s)
        await attach_tag(s, concert.id, group)
        await detach_tag(s, concert.id, a1.id)          # a1 isn't performing
        await attach_tag(s, concert.id, group)          # group ALREADY attached: no-op
        assert await tag_ids_on(s, concert.id) == {group.id, a2.id}


async def test_reattaching_group_reexpands(db):
    """Rule 4: detach + re-attach counts as 'newly added' -> members return."""
    async with db() as s:
        group, a1, a2, concert = await seed_group(s)
        await attach_tag(s, concert.id, group)
        await detach_tag(s, concert.id, a1.id)
        await detach_tag(s, concert.id, group.id)
        await attach_tag(s, concert.id, group)
        assert await tag_ids_on(s, concert.id) == {group.id, a1.id, a2.id}


async def test_membership_changes_do_not_rewrite_existing_concerts(db):
    async with db() as s:
        group, a1, a2, concert = await seed_group(s)
        await attach_tag(s, concert.id, group)
        newbie = Tag(name="Guest Artist", kind=TagKind.ARTIST, created_by=EDITOR_ID)
        s.add(newbie)
        await s.flush()
        s.add(TagMember(group_tag_id=group.id, member_tag_id=newbie.id))
        await s.flush()
        assert newbie.id not in await tag_ids_on(s, concert.id)  # untouched


# ── HTTP-level: gating, filtering, sorting ───────────────────────────────


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


def test_tag_creation_is_editor_only(client):
    login_as(client, VIEWER_ID, "viewer")
    assert client.post("/tags", data={"name": "X", "kind": "artist"}).status_code == 403
    login_as(client, EDITOR_ID, "reiji")
    assert client.post("/tags", data={"name": "X", "kind": "artist"}).status_code == 303


def test_duplicate_tag_names_rejected_case_insensitively(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Hasunosora", "kind": "franchise"})
    assert client.post("/tags", data={"name": "hasunosora", "kind": "artist"}).status_code == 409


def test_groups_cannot_contain_groups(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "G1", "kind": "group"})
    client.post("/tags", data={"name": "G2", "kind": "group"})
    r = client.post("/tags/1/members", data={"member_tag_id": 2})
    assert r.status_code == 422


def test_index_filters_by_tag(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "Hasu Live"})
    client.post("/concerts", data={"title": "Gakumas Live"})
    client.post("/concerts/1/tags", data={"name": "Hasunosora", "kind": "franchise"})

    everything = client.get("/").text
    assert "Hasu Live" in everything and "Gakumas Live" in everything
    filtered = client.get("/?tag=1").text
    assert "Hasu Live" in filtered and "Gakumas Live" not in filtered


def test_index_sorts_by_earliest_event_day(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/concerts", data={"title": "AAA Later Show"})
    client.post("/concerts/1/days", data={"label": "Day 1", "starts_at": "2099-12-01T18:00"})
    client.post("/concerts", data={"title": "BBB Sooner Show"})
    client.post("/concerts/2/days", data={"label": "Day 1", "starts_at": "2099-06-01T18:00"})

    by_event = client.get("/?sort=event").text
    assert by_event.index("BBB Sooner Show") < by_event.index("AAA Later Show")
    by_added = client.get("/?sort=added").text
    assert by_added.index("BBB Sooner Show") < by_added.index("AAA Later Show")  # newest first


def test_group_expansion_through_the_web_form(client):
    """End-to-end: build group on /tags, attach via concert form, members appear."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={"name": "Hasunosora", "kind": "group"})
    client.post("/tags", data={"name": "Kozue", "kind": "artist"})
    client.post("/tags/1/members", data={"member_tag_id": 2})
    client.post("/concerts", data={"title": "6th Live"})
    r = client.post("/concerts/1/tags", data={"name": "Hasunosora", "kind": "group"})
    assert r.status_code == 200
    assert "Kozue" in r.text  # member materialized into the fragment
