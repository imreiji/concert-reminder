"""A draft's per-leg Eventernote event id survives to the database.

The field is per-PERFORMANCE, not per-concert: one Eventernote event page is
one leg, so a two-day tour carries two different ids. Recording it is what
turns discovery's "do I already have this?" into an exact id lookup instead
of the fuzzy date-and-venue hint -- so what matters here is not that the
preview renders or that the commit answers 303, but that the value lands on
the ConcertDay row the diff actually queries.

Fixture shape borrowed from tests/test_draft_import.py (the pasted-draft
path); no network anywhere.
"""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base, ConcertDay
from app.db.session import get_session
from app.domain.yaml_export import YamlDay, YamlRound, concert_to_yaml
from app.domain.yaml_import import parse_draft
from app.web import auth
from app.web.app import create_app

EDITOR_ID = 42

DRAFT = """
title: テスト
title_en: Test
performances:
  - label: Day 1
    starts_at_jst: "2026-11-15 17:00"
    eventernote_event_id: "464372"
"""


# ── The parser ───────────────────────────────────────────────────────────


def test_the_parser_reads_a_per_leg_event_id():
    draft = parse_draft(DRAFT)
    assert draft.days[0].eventernote_event_id == "464372"
    # No "unknown key" grumble: the key is part of the vocabulary now, and a
    # warning here is how the skill and the parser announce they have drifted.
    assert draft.warnings == []


def test_an_absent_event_id_is_none_not_an_error():
    draft = parse_draft(
        'title: t\nperformances:\n  - {label: Day 1, starts_at_jst: "2026-11-15 17:00"}\n'
    )
    assert draft.days[0].eventernote_event_id is None


def test_an_unquoted_numeric_id_arrives_as_text():
    """YAML resolves a bare 464372 to an int; the column stores a string, and
    the discovery diff compares strings. _text is what bridges that."""
    draft = parse_draft(
        "title: t\nperformances:\n  - label: Day 1\n"
        '    starts_at_jst: "2026-11-15 17:00"\n'
        "    eventernote_event_id: 464372\n"
    )
    assert draft.days[0].eventernote_event_id == "464372"


def test_a_container_where_an_id_belongs_warns_and_blanks():
    """The _text guard, per invariant: a list is never stringified."""
    draft = parse_draft(
        "title: t\nperformances:\n  - label: Day 1\n"
        '    starts_at_jst: "2026-11-15 17:00"\n'
        "    eventernote_event_id: [464372, 464373]\n"
    )
    assert draft.days[0].eventernote_event_id is None
    assert any("eventernote_event_id" in w for w in draft.warnings)


# ── The exporter, and the round trip ─────────────────────────────────────


def _yaml_day(**kw) -> YamlDay:
    return YamlDay(
        label="Day 1", label_en="Day 1", label_zh="第1天",
        starts_at_utc=datetime(2026, 11, 15, 8, 0, tzinfo=UTC),
        **kw,
    )


def test_the_export_omits_the_key_when_a_leg_has_no_id():
    text = concert_to_yaml(
        title="T", kind=None, franchises=[], groups=[], artists=[], venues=[],
        days=[_yaml_day()], rounds=[], notes=None,
    )
    (performance,) = yaml.safe_load(text)["performances"]
    assert "eventernote_event_id" not in performance


def test_the_export_writes_the_key_when_a_leg_has_one():
    text = concert_to_yaml(
        title="T", kind=None, franchises=[], groups=[], artists=[], venues=[],
        days=[_yaml_day(eventernote_event_id="464372")], rounds=[], notes=None,
    )
    (performance,) = yaml.safe_load(text)["performances"]
    assert performance["eventernote_event_id"] == "464372"


def test_export_then_parse_round_trips_the_id_per_leg():
    """The whole point: a concert exported to YAML and read back keeps every
    leg's id, on the SAME leg -- not shuffled, not collapsed to one."""
    text = concert_to_yaml(
        title="T", kind=None, franchises=[], groups=[], artists=[], venues=[],
        days=[
            _yaml_day(eventernote_event_id="464372"),
            YamlDay(
                label="Day 2", label_en="Day 2", label_zh="第2天",
                starts_at_utc=datetime(2026, 11, 16, 8, 0, tzinfo=UTC),
                eventernote_event_id="464373",
            ),
            YamlDay(
                label="Day 3", label_en="Day 3", label_zh="第3天",
                starts_at_utc=datetime(2026, 11, 17, 8, 0, tzinfo=UTC),
            ),
        ],
        rounds=[YamlRound(label="R", kind="lottery_round")], notes=None,
    )
    parsed = parse_draft(text)
    assert parsed.warnings == []
    assert [(d.label, d.eventernote_event_id) for d in parsed.days] == [
        ("Day 1", "464372"), ("Day 2", "464373"), ("Day 3", None),
    ]


# ── The commit: it lands on the row ──────────────────────────────────────


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


async def _legs(db) -> list[ConcertDay]:
    async with db() as s:
        return list((await s.execute(
            select(ConcertDay).order_by(ConcertDay.starts_at_utc)
        )).scalars())


async def test_the_commit_writes_the_id_onto_the_right_leg(client, db):
    """The one that matters: assert the value on the ConcertDay row, not that
    the request answered 303. A blank stays NULL rather than becoming ""."""
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/concerts/import/commit", data={
        "title": "テスト", "title_en": "Test", "title_zh": "测试",
        "day_key": ["d0", "d1"], "day_label": ["Day 1", "Day 2"],
        # All three label variants: an import is a create boundary, so a
        # half-translated leg label is a 422 before anything is written.
        "day_label_en": ["Day 1", "Day 2"], "day_label_zh": ["第1天", "第2天"],
        "day_starts_at": ["2026-11-15T17:00", "2026-11-16T17:00"],
        "day_eventernote_event_id": ["464372", ""],
    })
    assert r.status_code == 303
    legs = await _legs(db)
    assert [(d.label, d.eventernote_event_id) for d in legs] == [
        ("Day 1", "464372"), ("Day 2", None),
    ]


async def test_commit_then_export_then_reparse_keeps_the_id(client, db):
    """The full loop the feature actually needs: a committed leg's id comes
    back out of GET /concerts/{event_id}/export.yaml and parses to the same
    value, so a catalogue restore does not lose the exact-match coverage the
    discovery diff has accumulated."""
    login_as(client, EDITOR_ID, "reiji")
    assert client.post("/concerts/import/commit", data={
        "title": "テスト", "title_en": "Round Trip", "title_zh": "测试",
        "event_id": "round-trip",
        "day_key": ["d0", "d1"], "day_label": ["Day 1", "Day 2"],
        "day_label_en": ["Day 1", "Day 2"], "day_label_zh": ["第1天", "第2天"],
        "day_starts_at": ["2026-11-15T17:00", "2026-11-16T17:00"],
        "day_eventernote_event_id": ["464372", "464373"],
    }).status_code == 303

    exported = client.get("/concerts/round-trip/export.yaml")
    assert exported.status_code == 200
    reparsed = parse_draft(exported.text)
    assert [(d.label, d.eventernote_event_id) for d in reparsed.days] == [
        ("Day 1", "464372"), ("Day 2", "464373"),
    ]


async def test_a_commit_that_sends_no_ids_at_all_still_works(client, db):
    """The minimal import contract (and every client predating the field)
    posts no day_eventernote_event_id array -- the strict zip must not care."""
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/concerts/import/commit", data={
        "title": "テスト2", "title_en": "Test 2", "title_zh": "测试2",
        "day_label": ["Day 1"], "day_label_en": ["Day 1"], "day_label_zh": ["第1天"],
        "day_starts_at": ["2026-11-15T17:00"],
    })
    assert r.status_code == 303
    assert [d.eventernote_event_id for d in await _legs(db)] == [None]


async def test_the_preview_carries_each_id_back_out_as_a_hidden_field(client):
    """Paste -> preview -> commit is two requests, so the id has to survive
    the browser. Without the hidden input the preview would silently drop it
    and the commit above would have nothing to write."""
    login_as(client, EDITOR_ID, "reiji")
    body = client.post("/concerts/import/draft", data={"draft": """
title: テスト
title_en: Test
title_zh: 测试
performances:
  - label: Day 1
    starts_at_jst: "2026-11-15 17:00"
    eventernote_event_id: "464372"
  - label: Day 2
    starts_at_jst: "2026-11-16 17:00"
"""}).text
    assert 'name="day_eventernote_event_id" value="464372"' in body
    # One input per leg, blanks included: import_commit zips the day_* arrays
    # strictly, so a leg without the field would slide every later leg's id.
    assert body.count('name="day_eventernote_event_id"') == 3  # 2 legs + the clone template
