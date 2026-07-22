"""Parallel-column UGC translations: loc fallback, columns, search variants.

The `loc_field` unit tests use a plain duck-typed object so they exercise the
fallback logic without a DB round-trip. The column and search tests use the
same in-memory async-SQLite fixture shape as tests/test_service.py.
"""

from datetime import UTC, datetime, timedelta

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
from app.domain.types import Anchor, RoundKind, TagKind
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


# ── Service-layer label copies (Task 5) ──────────────────────────────────
#
# These sites copy Round.label / ConcertDay.label / Tag.name OUT of the ORM
# object into a frozen dataclass, so a loc() in a template can never reach
# them -- the variant must be resolved where the dataclass is built. Three
# locale sources are in play and mixing them up is silent:
#   1. get_locale()      -- web-request paths (the request ContextVar)
#   2. user.language     -- per-recipient paths built once for many recipients
#   3. an explicit param -- user_calendar_events only (None = canonical .ics)
# Each test below pins WHICH source the site must read, not merely that some
# localization happened: the per-recipient tests set an ambient locale that
# DIFFERS from the recipient's language, so a get_locale() slip fails loudly.


@pytest.fixture()
def _en_locale():
    i18n.set_locale("en")
    yield
    i18n.set_locale("en")


async def _label_fixture(session, *, event_id="lab", user_language="en"):
    """One concert with a localized round, leg, group tag and venue tag,
    tracked by one user whose DM language is `user_language`."""
    from app.db.models import ConcertTag, TagSubscription
    from app.db.service import ensure_user

    user = await ensure_user(session, 7001, "recipient")
    user.language = user_language
    tag = Tag(name="蓮ノ空", name_en="Hasunosora", name_zh="莲之空", kind=TagKind.GROUP)
    venue_tag = Tag(
        name="東京ドーム", name_en="Tokyo Dome", name_zh="东京巨蛋", kind=TagKind.VENUE
    )
    session.add_all([tag, venue_tag])
    concert = Concert(title="6th", event_id=event_id)
    session.add(concert)
    await session.flush()
    session.add_all([
        ConcertTag(concert_id=concert.id, tag_id=tag.id),
        ConcertTag(concert_id=concert.id, tag_id=venue_tag.id),
        TagSubscription(user_id=user.discord_id, tag_id=tag.id),
    ])
    day = ConcertDay(
        concert_id=concert.id, label="Day 1", label_en="Day one", label_zh="第一天",
        starts_at_utc=datetime(2030, 9, 1, 9, tzinfo=UTC),
    )
    round_ = Round(
        concert_id=concert.id, kind=RoundKind.LOTTERY_ROUND,
        label="1次先行抽選", label_en="1st-round lottery", label_zh="第一轮抽选",
        opens_at_utc=datetime(2030, 7, 1, 9, tzinfo=UTC),
        closes_at_utc=datetime(2030, 8, 1, 9, tzinfo=UTC),
    )
    session.add_all([day, round_])
    await session.flush()
    await session.commit()
    return user, concert, round_, day


# ── pattern 1: get_locale() (web-request paths) ──────────────────────────


async def test_upcoming_deadlines_localize_round_and_leg_labels(session, _en_locale):
    """Sites B and C. UpcomingDeadline.label is copied out of both ConcertDay
    and Round; Home's "Coming up" renders it with no template hook of its own,
    so the resolution has to happen at the copy site."""
    from app.db.service import upcoming_deadlines

    await _label_fixture(session)
    now = datetime(2030, 6, 1, tzinfo=UTC)

    i18n.set_locale("zh")
    labels = {row.label for row in await upcoming_deadlines(session, now=now)}
    assert labels == {"第一轮抽选", "第一天"}

    i18n.set_locale("en")
    labels_en = {row.label for row in await upcoming_deadlines(session, now=now)}
    assert labels_en == {"1st-round lottery", "Day one"}


async def test_board_card_rungs_localize_the_round_label(session, _en_locale):
    """Site D: Rung.label."""
    from app.db.service import board_cards

    user, *_rest = await _label_fixture(session)
    now = datetime(2030, 7, 15, tzinfo=UTC)

    i18n.set_locale("zh")
    columns, _total = await board_cards(session, user.discord_id, now=now)
    rungs = [r for cards in columns.values() for c in cards for r in c.rungs]
    assert [r.label for r in rungs] == ["第一轮抽选"]


async def test_setup_tiles_localize_the_next_round_label(session, _en_locale):
    """Site E: SetupTile.next_round_label, via _next_round_anchor."""
    from app.db.service import setup_prune_tiles

    user, *_rest = await _label_fixture(session)
    now = datetime(2030, 6, 1, tzinfo=UTC)

    i18n.set_locale("zh")
    tiles = await setup_prune_tiles(session, user.discord_id, now=now)
    assert [t.next_round_label for t in tiles] == ["第一轮抽选"]


async def test_qualifier_labels_localize_on_the_concert_page(session, _en_locale):
    """Site G: RoundRow.qualifier_labels, built from a label_by_id map."""
    from app.db.models import RoundQualifier
    from app.db.service import concert_round_rows

    user, concert, round_, _day = await _label_fixture(session, event_id="qual")
    upgrade = Round(
        concert_id=concert.id, kind=RoundKind.UPGRADE, label="アップグレード",
        label_en="Upgrade", label_zh="升级",
        closes_at_utc=datetime(2030, 8, 20, 9, tzinfo=UTC),
    )
    session.add(upgrade)
    await session.flush()
    session.add(RoundQualifier(upgrade_round_id=upgrade.id, qualifying_round_id=round_.id))
    await session.commit()
    await session.refresh(concert, ["days", "rounds", "tags"])

    i18n.set_locale("zh")
    _by_leg, all_legs = await concert_round_rows(
        session, user.discord_id, concert, now=datetime(2030, 6, 1, tzinfo=UTC)
    )
    quals = {q for row in all_legs for q in row.qualifier_labels}
    assert quals == {"第一轮抽选"}


# ── pattern 2: user.language (per-recipient, built once for many) ─────────


async def test_due_reminders_use_the_recipients_language_not_the_ambient_locale(
    session, _en_locale
):
    """Sites A1 and A2. The scheduler builds DueReminders for every recipient
    in one pass, so the locale must come from the row's own user. The ambient
    locale here is ja (which resolves to the ORIGINAL column) while the
    recipient's language is zh -- a get_locale() slip therefore hands back the
    Japanese label and fails loudly."""
    from app.db.models import ReminderQueue, ReminderRule
    from app.db.service import due_reminders

    user, concert, round_, day = await _label_fixture(session, user_language="zh")
    rule = ReminderRule(
        user_id=user.discord_id, concert_id=concert.id,
        anchor=Anchor.CLOSES, offset_days=-1,
    )
    session.add(rule)
    await session.flush()
    now = datetime(2030, 6, 1, tzinfo=UTC)
    session.add_all([
        ReminderQueue(rule_id=rule.id, round_id=round_.id, anchor=Anchor.CLOSES,
                      fire_at_utc=now - timedelta(hours=1)),
        ReminderQueue(rule_id=rule.id, day_id=day.id, anchor=Anchor.EVENT_START,
                      fire_at_utc=now - timedelta(hours=1)),
    ])
    await session.commit()

    i18n.set_locale("ja")
    due = await due_reminders(session, now=now)

    assert {d.round_label for d in due if d.round_label} == {"第一轮抽选"}
    assert {d.day_label for d in due if d.day_label} == {"第一天"}


async def test_notice_context_localizes_tags_venue_and_deadline_label(session, _en_locale):
    """Site J plus the tags_line/venues fix -- tag names have had name_en/
    name_zh since the i18n build but the DM tag line stayed raw. Same
    ambient-vs-recipient split: ja ambient, zh recipient."""
    from app.db.service import notice_context

    user, concert, *_rest = await _label_fixture(session, user_language="zh")

    i18n.set_locale("ja")
    ctx = await notice_context(session, concert.id, user.discord_id)

    assert ctx.tags_line == "莲之空"
    assert ctx.venue == "东京巨蛋"
    assert ctx.first_deadline_label == "第一轮抽选"


# ── pattern 3: the explicit locale parameter (calendar feed) ─────────────


async def test_calendar_events_follow_the_locale_parameter_not_the_contextvar(
    session, _en_locale
):
    """Sites H and I. The .ics route passes no locale and must stay canonical
    even under an ambient locale; the /mydeadlines cog passes the recipient's
    language explicitly and must get the variant. Substituting get_locale()
    here would start localizing the calendar feed, which is not wanted."""
    from app.db.models import ReminderQueue, ReminderRule
    from app.db.service import user_calendar_events

    user, concert, round_, day = await _label_fixture(session, event_id="cal")
    rule = ReminderRule(
        user_id=user.discord_id, concert_id=concert.id,
        anchor=Anchor.CLOSES, offset_days=-1,
    )
    session.add(rule)
    await session.flush()
    session.add_all([
        ReminderQueue(rule_id=rule.id, round_id=round_.id, anchor=Anchor.CLOSES,
                      fire_at_utc=datetime(2030, 7, 31, 9, tzinfo=UTC)),
        ReminderQueue(rule_id=rule.id, day_id=day.id, anchor=Anchor.EVENT_START,
                      fire_at_utc=datetime(2030, 8, 31, 9, tzinfo=UTC)),
    ])
    await session.commit()
    now = datetime(2030, 6, 1, tzinfo=UTC)

    # ambient zh, but the .ics feed passes nothing -> canonical, unchanged
    i18n.set_locale("zh")
    canonical = await user_calendar_events(session, user.discord_id, now=now)
    assert {e.label for e in canonical} == {"1次先行抽選", "Day 1"}

    # the cog passes the recipient's language explicitly -> variants
    i18n.set_locale("en")
    localized = await user_calendar_events(session, user.discord_id, now=now, locale="zh")
    assert {e.label for e in localized} == {"第一轮抽选", "第一天"}
