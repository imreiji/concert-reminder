"""POST /concerts/import/draft: paste a YAML draft, get a prefilled preview.

Same fixture shape as test_imports.py (which owns the URL-scrape path);
this file owns the pasted-draft path. No network anywhere -- the draft
route never fetches.
"""

import io
import zipfile
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base, Concert, Tag, TagKind
from app.db.session import get_session
from app.web import auth
from app.web.app import create_app

EDITOR_ID, FAN_ID = 42, 777

DRAFT = """\
title: 蓮ノ空 6thライブ
title_en: Hasunosora 6th Live
title_zh: 莲之空 6th 演唱会
kind: tour
organizer: バンナム
series:
  franchises: [Love Live!]
  artists: [日野下花帆]
eventernote_url: https://www.eventernote.com/events/465358
official_url: https://example.jp/6th/
source_url: https://example.jp/6th/ticket/
performances:
  - label: Day 1
    label_en: Day 1
    label_zh: 第1天
    venue: Kアリーナ横浜
    starts_at_jst: 2026-11-07 17:00
  - label: Day 2
    label_en: Day 2
    label_zh: 第2天
    venue: 幻の新会場
    city: 幻都
    venue_address: 幻都1-2-3
    starts_at_jst: 2026-11-08 17:00
rounds:
  - label: 最速先行
    label_en: Earliest lottery
    label_zh: 最速先行(中)
    kind: lottery_round
    applies_to: [Day 1, Day 2]
    apply_opens_jst: 2026-08-01 12:00
    apply_closes_jst: 2026-08-16 23:59
    results_jst: 2026-08-22 15:00
    payment_deadline_jst: 2026-08-25 23:00
notes: 全席指定
notes_en: All reserved
notes_zh: 全部指定席
"""


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


async def _seed_tags(db):
    async with db() as s:
        s.add(Tag(name="Love Live!", kind=TagKind.FRANCHISE))
        s.add(Tag(name="日野下花帆", kind=TagKind.ARTIST, name_en="Kaho Hinoshita"))
        s.add(Tag(name="Kアリーナ横浜", kind=TagKind.VENUE, city="横浜"))
        await s.commit()
        rows = list((await s.execute(select(Tag))).scalars())
        return {t.name: t.id for t in rows}


# ── Gating ───────────────────────────────────────────────────────────────


def test_anonymous_is_redirected(client):
    assert client.post("/concerts/import/draft", data={"draft": "title: X"}).status_code == 303


def test_non_editor_is_forbidden(client):
    login_as(client, FAN_ID, "fan")
    assert client.post("/concerts/import/draft", data={"draft": "title: X"}).status_code == 403


# ── The paste card on the import form ────────────────────────────────────


def test_import_form_offers_the_paste_card(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.get("/concerts/import")
    assert r.status_code == 200
    assert 'action="/concerts/import/draft"' in r.text
    assert 'name="draft"' in r.text


# ── Preview prefill ──────────────────────────────────────────────────────


async def test_draft_renders_fully_prefilled_preview(client, db):
    ids = await _seed_tags(db)
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/concerts/import/draft", data={"draft": DRAFT})
    assert r.status_code == 200
    text = r.text
    # trilingual title + notes trio + organizer land in the Details fold
    assert 'value="Hasunosora 6th Live"' in text
    assert 'value="莲之空 6th 演唱会"' in text
    assert ">全席指定</textarea>" in text
    assert ">All reserved</textarea>" in text
    assert 'value="バンナム"' in text
    # concert kind pre-selected
    assert '<option value="tour" selected>' in text
    # trilingual day + round labels
    assert 'value="第1天"' in text
    assert 'value="Earliest lottery"' in text
    assert 'value="最速先行(中)"' in text
    # all four round anchors
    assert 'value="2026-08-01T12:00"' in text
    assert 'value="2026-08-16T23:59"' in text
    assert 'value="2026-08-22T15:00"' in text
    assert 'value="2026-08-25T23:00"' in text
    # round applies to both legs: hidden field + both chips pressed
    assert 'name="round_legs" value="d0 d1"' in text
    assert text.count('aria-pressed="true"') >= 2
    # matched venue pre-selected on Day 1's select
    assert f'<option value="{ids["Kアリーナ横浜"]}" selected>' in text
    # unmatched venue -> visible per-leg hint, not silence
    assert "幻の新会場" in text
    # unmatched venue's scraped facts ride the + New venue button as
    # dialog prefill hints (rendered rows only -- the clone template
    # stays hint-free)
    assert 'data-hint-venue="幻の新会場"' in text
    assert 'data-hint-city="幻都"' in text
    assert 'data-hint-address="幻都1-2-3"' in text
    assert '<template id="day-row-template">' in text
    assert text.split('<template id="day-row-template">')[1].count("data-hint-venue") == 0
    # matched tags pre-selected for the picker script
    assert f'"{ids["Love Live!"]}"' in text
    assert f'"{ids["日野下花帆"]}"' in text
    # parity links prefilled
    assert 'value="https://www.eventernote.com/events/465358"' in text
    assert 'value="https://example.jp/6th/ticket/"' in text


async def test_unmatched_tag_names_render_create_chips(client, db):
    login_as(client, EDITOR_ID, "reiji")  # no tags seeded at all
    r = client.post("/concerts/import/draft", data={"draft": DRAFT})
    assert r.status_code == 200
    text = r.text
    # Each unmatched name is its own "+ create this tag" chip carrying its kind
    # -- structured (name, kind) pairs, not a flat text list. The DRAFT names a
    # franchise (Love Live!) and an artist (日野下花帆), neither seeded.
    assert 'data-new-tag' in text
    assert 'data-tag-name="Love Live!"' in text
    assert 'data-tag-kind="franchise"' in text
    assert 'data-tag-name="日野下花帆"' in text
    assert 'data-tag-kind="artist"' in text
    # The lead-in line still gives the chips context.
    assert "No tag yet:" in text
    # The quick-create dialog is present, included outside the form.
    assert 'id="tag-create"' in text
    assert 'data-tag-save' in text
    # Invariant 7: the opener chips carry data-tag-name, NEVER data-name (which
    # collides with base.html's filterChips selector). Check the Tags fold has
    # no stray data-name on a create chip.
    for chunk in text.split('data-new-tag')[1:]:
        seg = chunk.split('>', 1)[0]
        assert 'data-name=' not in seg


def test_draft_error_rerenders_the_form(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/concerts/import/draft", data={"draft": "- not\n- a\n- mapping"})
    assert r.status_code == 200
    assert 'name="draft"' in r.text  # back on the form, message shown
    assert "mapping" in r.text


def test_oversized_draft_is_rejected(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/concerts/import/draft", data={"draft": "title: X\n" + "#x\n" * 120_000})
    assert r.status_code == 200
    assert "too large" in r.text


# ── Paste-to-commit flow (the commit route is unchanged; this proves the
#    prefilled field shape it receives is one it accepts) ─────────────────


async def test_full_paste_then_commit_flow(client, db):
    ids = await _seed_tags(db)
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/concerts/import/commit", data={
        "title": "蓮ノ空 6thライブ", "title_en": "Hasunosora 6th Live",
        "title_zh": "莲之空 6th 演唱会", "kind": "tour", "organizer": "バンナム",
        "notes": "全席指定", "notes_en": "All reserved", "notes_zh": "全部指定席",
        "source_url": "https://example.jp/6th/ticket/",
        "official_url": "https://example.jp/6th/",
        "eventernote_url": "https://www.eventernote.com/events/465358",
        "performers_text": "日野下花帆",
        "franchise_tags": [ids["Love Live!"]], "artist_tags": [ids["日野下花帆"]],
        "day_key": ["d0", "d1"], "day_label": ["Day 1", "Day 2"],
        "day_label_en": ["Day 1", "Day 2"], "day_label_zh": ["第1天", "第2天"],
        "day_starts_at": ["2026-11-07T17:00", "2026-11-08T17:00"],
        "day_venue_tag_id": [str(ids["Kアリーナ横浜"]), ""],
        "round_label": ["最速先行"], "round_label_en": ["Earliest lottery"],
        "round_label_zh": ["最速先行(中)"], "round_kind": ["lottery_round"],
        "round_opens_at": ["2026-08-01T12:00"], "round_closes_at": ["2026-08-16T23:59"],
        "round_results_at": ["2026-08-22T15:00"], "round_payment_at": ["2026-08-25T23:00"],
        "round_url": ["https://eplus.jp/x/"], "round_legs": ["d0 d1"],
    })
    assert r.status_code == 303
    async with db() as s:
        concert = (await s.execute(select(Concert))).scalar_one()
        assert concert.title_zh == "莲之空 6th 演唱会"
        assert concert.eventernote_url == "https://www.eventernote.com/events/465358"
        assert concert.official_url == "https://example.jp/6th/"
        assert concert.performers_text == "日野下花帆"


# ── Skill download ───────────────────────────────────────────────────────


def test_skill_zip_is_editor_gated(client):
    assert client.get("/concerts/import/skill.zip").status_code == 303
    login_as(client, FAN_ID, "fan")
    assert client.get("/concerts/import/skill.zip").status_code == 403


def test_skill_zip_downloads_the_skill(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.get("/concerts/import/skill.zip")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = set(zf.namelist())
        assert "add-concert/SKILL.md" in names
        assert "add-concert/references/example-draft.yaml" in names
        skill_md = zf.read("add-concert/SKILL.md").decode("utf-8")
        assert "dekimasen.app/concerts/import" in skill_md


def test_import_form_links_the_skill_zip(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.get("/concerts/import")
    assert 'href="/concerts/import/skill.zip"' in r.text


def test_dist_example_draft_is_byte_identical_to_repo_skill():
    """The distribution copy's schema example must never fork from the repo
    skill's -- that one file IS the schema contract both copies teach."""
    root = Path(__file__).parent.parent
    repo = root / ".claude" / "skills" / "add-concert" / "references" / "example-draft.yaml"
    dist = (
        root / "src" / "app" / "web" / "skill_dist" / "add-concert"
        / "references" / "example-draft.yaml"
    )
    assert dist.read_bytes() == repo.read_bytes()


async def test_import_preview_serves_kebab_with_remove_in_menu(client, db):
    """The import preview is the third surface built on the shared card
    partials -- so it too moves Remove into a kebab menu (spec D2). Every
    remove control is a danger `.kmenu` item carrying its handler hook, never
    an inline × beside the Cancelled toggle."""
    await _seed_tags(db)
    login_as(client, EDITOR_ID, "reiji")
    body = client.post("/concerts/import/draft", data={"draft": DRAFT}).text

    assert 'class="kebab"' in body
    assert body.count("data-remove-leg>") == body.count(
        'class="kitem danger" type="button" data-remove-leg>'
    )
    assert body.count("data-remove-round>") == body.count(
        'class="kitem danger" type="button" data-remove-round>'
    )
    # the leg's remove is nested in the menu, not inline next to Cancelled
    seg = body.split("data-cancel-toggle", 1)[1].split("data-remove-leg", 1)[0]
    assert 'class="kmenu"' in seg
    assert "data-remove-leg>×" not in body


# ── event_id: a restore keeps the URLs people hold ────────────────────────


def test_a_draft_can_carry_its_event_id(client):
    """A restore must land on the ORIGINAL urls, or every link anybody holds
    breaks. Absent, generate_event_id runs exactly as before."""
    login_as(client, EDITOR_ID, "reiji")
    body = client.post("/concerts/import/draft", data={
        "draft": "event_id: hasunosora-6th-live\ntitle: 6th\ntitle_en: 6th\ntitle_zh: 6th\n",
    }).text
    assert 'name="event_id"' in body
    assert 'value="hasunosora-6th-live"' in body


def test_committing_with_an_event_id_uses_it(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/concerts/import/commit", data={
        "title": "6th", "title_en": "6th", "title_zh": "6th",
        "event_id": "hasunosora-6th-live",
    })
    assert r.status_code == 303
    assert r.headers["location"] == "/concerts/hasunosora-6th-live"


def test_committing_the_same_event_id_twice_is_a_409(client):
    """How re-importing a file into a populated catalogue announces itself,
    instead of quietly creating a second concert."""
    login_as(client, EDITOR_ID, "reiji")
    data = {
        "title": "6th", "title_en": "6th", "title_zh": "6th",
        "event_id": "hasunosora-6th-live",
    }
    assert client.post("/concerts/import/commit", data=data).status_code == 303
    assert client.post("/concerts/import/commit", data=data).status_code == 409


def test_committing_without_an_event_id_still_generates_one(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/concerts/import/commit", data={
        "title": "蓮ノ空", "title_en": "Hasunosora 6th", "title_zh": "6th",
    })
    assert r.status_code == 303
    assert r.headers["location"] == "/concerts/hasunosora-6th"


def test_a_reserved_event_id_in_a_draft_is_refused(client):
    """validate_event_id, not a second copy of the rule (invariant 6)."""
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/concerts/import/commit", data={
        "title": "X", "title_en": "X", "title_zh": "X", "event_id": "import",
    })
    assert r.status_code == 422


def test_a_malformed_event_id_in_a_draft_is_refused(client):
    login_as(client, EDITOR_ID, "reiji")
    r = client.post("/concerts/import/commit", data={
        "title": "X", "title_en": "X", "title_zh": "X", "event_id": "not a slug!",
    })
    assert r.status_code == 422


# ── Handles: exact binding, and the rule that makes it exact ──────────────


def test_handles_bind_the_right_one_of_two_same_named_performers(client):
    """THE case the whole round-trip arc exists for. Two performers are both
    written 佐藤有紀. Bound by NAME, match_tag_ids_by_name is documented
    first-tag-wins, so an exported concert would silently re-import against the
    wrong person. series_handles is what makes it exact."""
    login_as(client, EDITOR_ID, "reiji")
    for en in ("Yuki Sato", "Yuki Sato (Liella)"):
        client.post("/tags", data={
            "name": "佐藤有紀", "name_en": en, "name_zh": en, "kind": "artist",
        })

    body = client.post("/concerts/import/draft", data={
        "draft": (
            "title: 6th\ntitle_en: 6th\ntitle_zh: 6th\n"
            "series:\n  artists: [佐藤有紀]\n"
            "series_handles:\n  artists: [yuki-sato-liella]\n"
        ),
    }).text
    assert '"artist": ["2"]' in body or "'artist': ['2']" in body, (
        "the SECOND Yuki Sato (id 2, handle yuki-sato-liella) must be selected"
    )


def test_a_handle_block_makes_the_names_irrelevant(client):
    """THE RULE: if series_handles names a kind, it is authoritative for that
    kind and the name list is ignored outright. An export taken before a rename
    carries both and they disagree; resolving the name too would re-select a tag
    the handle deliberately did not choose."""
    login_as(client, EDITOR_ID, "reiji")
    for jp, en in (("乙宗梢", "Kozue"), ("日野下花帆", "Kaho")):
        client.post("/tags", data={
            "name": jp, "name_en": en, "name_zh": en, "kind": "artist",
        })

    body = client.post("/concerts/import/draft", data={
        "draft": (
            "title: 6th\ntitle_en: 6th\ntitle_zh: 6th\n"
            "series:\n  artists: [日野下花帆]\n"       # would resolve to id 2
            "series_handles:\n  artists: [kozue]\n"   # but the handle says id 1
        ),
    }).text
    assert '"artist": ["1"]' in body or "'artist': ['1']" in body
    assert '"2"' not in body.split('"artist"')[1][:20], "the name must not also select"


def test_a_handle_that_is_not_here_yet_does_not_fall_back_to_the_name(client):
    """No fallback, deliberately. A handle that is missing means "import tags
    first"; quietly binding its name instead would reintroduce exactly the
    first-tag-wins guess this whole arc removed. It surfaces as unmatched."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={
        "name": "乙宗梢", "name_en": "Kozue", "name_zh": "Kozue", "kind": "artist",
    })
    body = client.post("/concerts/import/draft", data={
        "draft": (
            "title: 6th\ntitle_en: 6th\ntitle_zh: 6th\n"
            "series:\n  artists: [乙宗梢]\n"
            "series_handles:\n  artists: [never-imported]\n"
        ),
    }).text
    assert "never-imported" in body, "the missing handle must be named, not swallowed"
    assert "import tags.yaml first" in body


def test_without_a_handle_block_names_resolve_exactly_as_before(client):
    """The backward-compatibility half, and it matters more than the fix half:
    every agent-authored draft omits series_handles."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={
        "name": "乙宗梢", "name_en": "Kozue", "name_zh": "Kozue", "kind": "artist",
    })
    body = client.post("/concerts/import/draft", data={
        "draft": "title: 6th\ntitle_en: 6th\ntitle_zh: 6th\nseries:\n  artists: [乙宗梢]\n",
    }).text
    assert '"artist": ["1"]' in body or "'artist': ['1']" in body


def test_a_leg_venue_handle_preselects_the_venue(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={
        "name": "Kアリーナ横浜", "name_en": "K Arena", "name_zh": "K", "kind": "venue",
    })
    body = client.post("/concerts/import/draft", data={
        "draft": (
            "title: 6th\ntitle_en: 6th\ntitle_zh: 6th\n"
            "performances:\n"
            "  - label: Day 1\n    starts_at_jst: '2027-01-23 17:00'\n"
            "    venue: Anything At All\n    venue_handle: k-arena\n"
        ),
    }).text
    assert 'value="1" selected' in body, "the handle must win over the unmatchable name"
