"""Parallel-column UGC translations: loc fallback, columns, search variants.

The `loc_field` unit tests use a plain duck-typed object so they exercise the
fallback logic without a DB round-trip. The column and search tests use the
same in-memory async-SQLite fixture shape as tests/test_service.py.
"""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app import i18n
from app.db.models import Base, Concert, ConcertDay, Round, Tag, User
from app.db.service import ensure_user, snapshot_concert
from app.db.session import get_session
from app.domain.types import RoundKind, TagKind
from app.i18n import loc_field
from app.web import auth
from app.web.app import create_app
from app.web.routes.discover import concert_search_text

EDITOR = 9001


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


# ── loc_field fallback rule ──────────────────────────────────────────────


def test_loc_field_picks_locale_column():
    c = _Obj(title="ラブライブ！", title_en="Love Live!", title_zh=None)
    assert i18n.loc_field(c, "title", "en") == "Love Live!"
    assert i18n.loc_field(c, "title", "zh") == "ラブライブ！"  # no zh -> original
    assert i18n.loc_field(c, "title", "ja") == "ラブライブ！"  # ja IS the original


def test_loc_field_empty_counts_as_unfilled():
    c = _Obj(title="original", title_en="")
    assert i18n.loc_field(c, "title", "en") == "original"


def test_loc_field_no_cross_locale_chaining():
    # zh unfilled must fall to the ORIGINAL, never to the en column.
    c = _Obj(title="原題", title_en="English", title_zh=None)
    assert i18n.loc_field(c, "title", "zh") == "原題"


def test_loc_field_works_on_other_fields():
    c = _Obj(venue="渋谷", venue_en="Shibuya", venue_zh="涩谷")
    assert i18n.loc_field(c, "venue", "en") == "Shibuya"
    assert i18n.loc_field(c, "venue", "zh") == "涩谷"
    assert i18n.loc_field(c, "venue", "ja") == "渋谷"


# ── new columns are nullable, no backfill ────────────────────────────────


@pytest.mark.asyncio
async def test_concert_columns_nullable(session):
    c = Concert(event_id="x1", title="t", created_by=None)
    session.add(c)
    await session.commit()
    assert c.title_zh is None
    assert c.notes_en is None and c.notes_zh is None
    assert c.venue_en is None and c.venue_zh is None


@pytest.mark.asyncio
async def test_tag_variant_columns_nullable(session):
    t = Tag(name="Aqours", kind=TagKind.GROUP)
    session.add(t)
    await session.commit()
    assert t.name_en is None and t.name_zh is None


@pytest.mark.asyncio
async def test_tag_name_variants_not_unique(session):
    # name is unique; the variants must NOT be, so two tags can share an
    # English or Chinese rendering without colliding.
    t1 = Tag(name="ラブライブ", kind=TagKind.FRANCHISE, name_en="Love Live", name_zh="爱")
    t2 = Tag(name="ラブライブ！", kind=TagKind.FRANCHISE, name_en="Love Live", name_zh="爱")
    session.add_all([t1, t2])
    await session.commit()  # must not raise


def test_tag_venue_detail_columns_are_nullable():
    cols = Tag.__table__.columns
    for name in ("city", "city_en", "city_zh", "address"):
        assert name in cols, f"Tag.{name} missing"
        assert cols[name].nullable, f"Tag.{name} must be nullable"


def test_loc_field_resolves_tag_city():
    tag = Tag(name="Kアリーナ横浜", kind=TagKind.VENUE, city="横浜", city_en="Yokohama")
    assert loc_field(tag, "city", "en") == "Yokohama"
    assert loc_field(tag, "city", "ja") == "横浜"
    # zh is unfilled and there is no cross-locale chaining -- it must NOT
    # fall through to the English variant.
    assert loc_field(tag, "city", "zh") == "横浜"


def test_label_variant_columns_are_nullable():
    for model, name, length in (
        (ConcertDay, "label_en", 100), (ConcertDay, "label_zh", 100),
        (Round, "label_zh", 200),
    ):
        col = model.__table__.columns.get(name)
        assert col is not None, f"{model.__name__}.{name} missing"
        assert col.nullable, f"{model.__name__}.{name} must be nullable"
        assert col.type.length == length


def test_loc_field_resolves_round_label():
    r = Round(label="1次先行抽選", label_en="1st-round lottery", label_zh="第一轮先行")
    assert loc_field(r, "label", "en") == "1st-round lottery"
    assert loc_field(r, "label", "zh") == "第一轮先行"
    assert loc_field(r, "label", "ja") == "1次先行抽選"


def test_loc_field_resolves_day_label_without_chaining():
    d = ConcertDay(label="2日目 夜公演", label_en="Day 2 evening")
    assert loc_field(d, "label", "en") == "Day 2 evening"
    # zh is unfilled; it must fall through to the ORIGINAL, never to the
    # English variant.
    assert loc_field(d, "label", "zh") == "2日目 夜公演"


# ── search haystack picks up the variants ────────────────────────────────


@pytest.mark.asyncio
async def test_search_text_includes_variants(session):
    tag = Tag(name="Aqours", kind=TagKind.GROUP, name_en="Aqours EN", name_zh="水团")
    session.add(tag)
    await session.flush()
    concert = Concert(
        event_id="ll1", title="ラブライブ", title_en="Love Live", title_zh="爱与生活",
        venue="渋谷", venue_en="Shibuya Hall", venue_zh="涩谷厅", created_by=None,
    )
    concert.tags.append(tag)
    session.add(concert)
    await session.flush()
    await session.refresh(concert, ["tags"])

    text = concert_search_text(concert)
    assert "水团" in text          # tag name_zh
    assert "aqours en" in text     # tag name_en (lowercased)
    assert "爱与生活" in text        # concert title_zh
    # venue variants land because no VENUE tag is attached (free-text fallback)
    assert "shibuya hall" in text
    assert "涩谷厅" in text


# ── snapshot_concert tracks the new columns so edits diff ─────────────────


@pytest.mark.asyncio
async def test_snapshot_concert_includes_new_fields(session):
    c = Concert(event_id="s1", title="t", title_zh="标题", notes_en="en note",
                venue_zh="场地", created_by=None)
    snap = snapshot_concert(c)
    for f in ("title_zh", "notes_en", "notes_zh", "venue_en", "venue_zh"):
        assert f in snap
    assert snap["title_zh"] == "标题"
    assert snap["notes_en"] == "en note"


# ── web fixtures (mirror tests/test_editor_legs.py) ──────────────────────


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

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


async def _seed_editor_concert(db, *, event_id="ll", title="ラブライブ", **variants):
    async with db() as s:
        await ensure_user(s, EDITOR, "editor")
        u = await s.get(User, EDITOR)
        u.is_editor = True
        c = Concert(title=title, event_id=event_id, created_by=EDITOR, **variants)
        s.add(c)
        await s.flush()
        s.add(ConcertDay(
            concert_id=c.id, label="Day 1",
            starts_at_utc=datetime(2099, 8, 1, 9, tzinfo=UTC),
        ))
        s.add(Round(
            concert_id=c.id, kind=RoundKind.LOTTERY_ROUND, label="1次",
            closes_at_utc=datetime(2099, 6, 25, 14, tzinfo=UTC),
        ))
        await s.commit()
        return c.id


# ── editor round-trip: the edit form persists variants ───────────────────


@pytest.mark.asyncio
async def test_edit_form_persists_translation_variants(client, db):
    await _seed_editor_concert(db, event_id="ll", title="ラブライブ")
    login(client)
    r = client.post("/concerts/ll/edit", data={
        "title": "ラブライブ",
        "event_id": "ll",
        "title_en": "Love Live",
        "title_zh": "爱与生活",
        "notes_en": "EN note",
        "notes_zh": "中文备注",
        "venue_en": "Shibuya",
        "venue_zh": "涩谷",
    })
    assert r.status_code == 303
    async with db() as s:
        c = (await s.execute(select(Concert).where(Concert.event_id == "ll"))).scalar_one()
        assert c.title_zh == "爱与生活"
        assert c.notes_en == "EN note" and c.notes_zh == "中文备注"
        assert c.venue_en == "Shibuya" and c.venue_zh == "涩谷"
        # an empty variant round-trips to None, never ""
        await s.refresh(c, ["audits"])
    # the edit recorded an audit row (snapshot tracks the new columns)
    async with db() as s:
        from app.db.service import concert_audit_log
        audits = await concert_audit_log(s, c.id)
    changed = {ch["field"] for a in audits for ch in a.changes}
    assert {"title_zh", "notes_en", "venue_zh"} <= changed


@pytest.mark.asyncio
async def test_empty_variant_saves_as_none(client, db):
    await _seed_editor_concert(db, event_id="ll2", title="t", title_zh="旧")
    login(client)
    r = client.post("/concerts/ll2/edit", data={
        "title": "t", "event_id": "ll2", "title_zh": "",
    })
    assert r.status_code == 303
    async with db() as s:
        c = (await s.execute(select(Concert).where(Concert.event_id == "ll2"))).scalar_one()
        assert c.title_zh is None


# ── display: a zh viewer sees the variant, an en viewer the original ──────


@pytest.mark.asyncio
async def test_discover_localizes_title_for_zh_viewer(client, db):
    await _seed_editor_concert(db, event_id="ll3", title="ラブライブ", title_zh="爱与生活")
    # Discover is public -- no login needed. The lang cookie drives the locale.
    # Assert on the DISPLAY element, not the whole body: the zh variant also
    # lives in every card's data-search haystack (the all-locale search blob),
    # so its mere presence proves nothing about what the viewer READS.
    client.cookies.set("lang", "zh")
    body = client.get("/discover").text
    assert "<strong>爱与生活</strong>" in body      # zh viewer sees the variant

    client.cookies.set("lang", "en")
    body_en = client.get("/discover").text
    assert "<strong>ラブライブ</strong>" in body_en  # en viewer -> original
    assert "<strong>爱与生活</strong>" not in body_en
