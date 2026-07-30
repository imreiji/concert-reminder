# Catalogue Round-Trip Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An admin-only catalogue export (`GET /admin/export.zip`) and a tags import (`POST /admin/import/tags`) that round-trips it, with concert drafts gaining the identity keys that make a restore exact.

**Architecture:** A new pure `domain/tags_yaml.py` holds BOTH halves of the tags vocabulary — serializer and parser in one module, so the format cannot drift. `db/service.py` gains the row-gathering, the two-pass importer, and a shared tag writer extracted from three routes. The concert draft gains three optional keys (`event_id`, `series_handles`, per-leg `venue_handle`); handles win over names where present. Both routes live in `web/routes/admin.py`.

**Tech Stack:** Python 3.14, SQLAlchemy 2.0 async, FastAPI + Jinja2, PyYAML (`safe_load` only), `zipfile`, pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-07-30-catalogue-round-trip-design.md`

**Branch:** `catalogue-round-trip` (already exists, off `main`, carrying the spec commit).

## Global Constraints

- `uv run --isolated pytest -q` MUST pass before any commit. Use `--isolated`: an external `serve.py` can lock `.venv`.
- `uv run --isolated ruff check .` MUST be clean before any commit. Line length 100.
- **Admin pages are English-only and NOT wrapped in `_()`**, exactly like `admin_deliveries.html` and `rehearsal.html` (CLAUDE.md's rehearsal note states this). So **no `.po` work anywhere in this plan** — if you find yourself running `pybabel`, you have added a translatable string to an admin surface and should not have.
- `yaml.safe_load` ONLY. Never `yaml.load`.
- Route error details are NOT translated in this app; do not wrap `HTTPException(detail=...)` in `_()`.
- `domain/` takes no ORM, DB, FastAPI or httpx imports. `db/service.py` may import `domain/`.
- Business logic lives in `db/service.py`; routes stay thin (CLAUDE.md).
- The DB stores aware UTC only. The YAML vocabulary renders JST wall-clock strings via the existing `_jst_str`; do not add a second conversion.
- **Nothing in this plan may read a personal table.** `users`, `web_sessions`, `round_outcomes`, `concert_subscriptions`, `leg_opt_outs`, `reminder_rules`, `reminder_queue`, `notifications`, `delivery_log` are all off limits, and `created_by` is never emitted.

---

### Task 1: `domain/tags_yaml.py` — both halves of the tags vocabulary

**Files:**
- Create: `src/app/domain/tags_yaml.py`
- Test: `tests/test_tags_yaml.py` (create)

**Interfaces:**
- Consumes: `slug_core` from `app.domain.slugs`, `TagKind` from `app.domain.types`.
- Produces, all used by Tasks 2 and 6:
  - `@dataclass(frozen=True) class TagExport` — fields in this exact order: `handle: str`, `name: str`, `kind: str`, `name_en: str | None = None`, `name_zh: str | None = None`, `parent: str | None = None`, `members: tuple[str, ...] = ()`, `region: str | None = None`, `city: str | None = None`, `city_en: str | None = None`, `city_zh: str | None = None`, `address: str | None = None`, `location_url: str | None = None`, `eventernote_url: str | None = None`
  - `def tags_to_yaml(tags: Sequence[TagExport]) -> str`
  - `@dataclass class ParsedTag` — same fields as `TagExport` but `kind: TagKind` and `members: list[str]`
  - `@dataclass class ParsedTagFile` — `tags: list[ParsedTag]`, `warnings: list[str]`
  - `class TagsFileError(Exception)`
  - `def parse_tags(text: str) -> ParsedTagFile`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_tags_yaml.py
"""The tags vocabulary: serializer and parser, in one module on purpose.

Splitting export from import across two files is how the original round-trip
hole opened (see the tag-handles spec) -- so their tests live together too, and
the round-trip test below is the one that matters most.
"""

import pytest

from app.domain.tags_yaml import (
    ParsedTag,
    TagExport,
    TagsFileError,
    parse_tags,
    tags_to_yaml,
)
from app.domain.types import TagKind


def test_round_trips_every_field():
    """The centrepiece: what goes out must come back identical."""
    tags = [
        TagExport(
            handle="k-arena-yokohama", name="Kアリーナ横浜", kind="venue",
            name_en="K Arena Yokohama", name_zh="K竞技场横滨",
            region="Kanto", city="横浜", city_en="Yokohama", city_zh="横滨",
            address="神奈川県横浜市西区みなとみらい",
            location_url="https://maps.example/k-arena",
        ),
        TagExport(
            handle="hasunosora", name="蓮ノ空", kind="group", name_en="Hasunosora",
            parent="love-live", members=("kozue-otomune", "kaho-hinoshita"),
            eventernote_url="https://www.eventernote.com/actors/1",
        ),
    ]
    parsed = parse_tags(tags_to_yaml(tags))

    assert parsed.warnings == []
    assert [t.handle for t in parsed.tags] == ["k-arena-yokohama", "hasunosora"]
    venue, group = parsed.tags
    assert venue.kind is TagKind.VENUE
    assert (venue.name, venue.name_en, venue.name_zh) == ("Kアリーナ横浜", "K Arena Yokohama", "K竞技场横滨")
    assert (venue.region, venue.city, venue.city_en, venue.city_zh) == ("Kanto", "横浜", "Yokohama", "横滨")
    assert venue.address == "神奈川県横浜市西区みなとみらい"
    assert venue.location_url == "https://maps.example/k-arena"
    assert group.parent == "love-live"
    assert group.members == ["kozue-otomune", "kaho-hinoshita"]
    assert group.eventernote_url == "https://www.eventernote.com/actors/1"


def test_empty_fields_are_omitted_not_emitted_as_null():
    """A restore file is read by people; `city: null` on every artist is noise."""
    text = tags_to_yaml([TagExport(handle="kozue", name="乙宗梢", kind="artist")])
    assert "city" not in text
    assert "null" not in text
    assert "members" not in text
    assert "parent" not in text


def test_serialization_is_deterministic():
    """Two exports of an unchanged catalogue must be byte-identical, or the file
    is not diffable and most of its value as a backup is gone."""
    tags = [
        TagExport(handle="b-venue", name="B", kind="venue"),
        TagExport(handle="a-artist", name="A", kind="artist"),
    ]
    assert tags_to_yaml(tags) == tags_to_yaml(tags)
    # Sorted by (kind, handle), NOT input order, so caller ordering cannot leak.
    assert tags_to_yaml(tags) == tags_to_yaml(list(reversed(tags)))
    assert tags_to_yaml(tags).index("a-artist") < tags_to_yaml(tags).index("b-venue")


def test_unparseable_input_raises():
    for bad, why in [
        ("{[", "not YAML"),
        ("just a string", "not a mapping"),
        ("other: 1", "no tags key"),
    ]:
        with pytest.raises(TagsFileError):
            parse_tags(bad)


def test_a_tag_without_a_handle_is_skipped_with_a_warning():
    parsed = parse_tags("tags:\n  - name: 乙宗梢\n    kind: artist\n")
    assert parsed.tags == []
    assert any("handle" in w for w in parsed.warnings)


def test_a_tag_without_a_name_is_skipped_with_a_warning():
    parsed = parse_tags("tags:\n  - handle: kozue\n    kind: artist\n")
    assert parsed.tags == []
    assert any("name" in w for w in parsed.warnings)


def test_an_unknown_kind_is_skipped_with_a_warning():
    parsed = parse_tags("tags:\n  - handle: k\n    name: K\n    kind: mascot\n")
    assert parsed.tags == []
    assert any("mascot" in w for w in parsed.warnings)


def test_a_duplicate_handle_keeps_the_first_and_warns():
    parsed = parse_tags(
        "tags:\n"
        "  - {handle: kozue, name: 乙宗梢, kind: artist}\n"
        "  - {handle: kozue, name: Someone Else, kind: artist}\n"
    )
    assert [t.name for t in parsed.tags] == ["乙宗梢"]
    assert any("kozue" in w for w in parsed.warnings)


def test_a_handle_is_normalised_and_warns_when_it_changes():
    """The file is machine-written, but a person may edit one. Normalise through
    the same slug_core that mints a handle rather than storing something the app
    could never generate."""
    parsed = parse_tags("tags:\n  - {handle: 'Kozue Otomune!', name: 乙宗梢, kind: artist}\n")
    assert parsed.tags[0].handle == "kozue-otomune"
    assert any("kozue-otomune" in w for w in parsed.warnings)


def test_a_handle_that_normalises_to_nothing_is_skipped():
    parsed = parse_tags("tags:\n  - {handle: 'ホール', name: Hall, kind: venue}\n")
    assert parsed.tags == []
    assert any("handle" in w for w in parsed.warnings)


def test_a_scalar_where_a_list_belongs_warns_rather_than_crashing():
    parsed = parse_tags(
        "tags:\n  - {handle: g, name: G, kind: group, members: kozue}\n"
    )
    assert parsed.tags[0].members == []
    assert any("members" in w for w in parsed.warnings)


def test_an_unknown_key_warns_but_keeps_the_tag():
    parsed = parse_tags(
        "tags:\n  - {handle: kozue, name: 乙宗梢, kind: artist, favourite_food: ramen}\n"
    )
    assert len(parsed.tags) == 1
    assert any("favourite_food" in w for w in parsed.warnings)


def test_a_nested_structure_where_text_belongs_is_refused_not_stringified():
    """Same alias-bomb defence as yaml_import._text: never str() a dict."""
    parsed = parse_tags(
        "tags:\n  - {handle: kozue, name: {a: b}, kind: artist}\n"
    )
    assert parsed.tags == []
    assert any("name" in w for w in parsed.warnings)


def test_parsed_tag_defaults_are_empty_not_none_for_members():
    t = ParsedTag(handle="x", name="X", kind=TagKind.ARTIST)
    assert t.members == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --isolated pytest tests/test_tags_yaml.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.domain.tags_yaml'`

- [ ] **Step 3: Write the module**

```python
# src/app/domain/tags_yaml.py
"""The tags vocabulary: serializer AND parser, deliberately in one module.

Splitting a format's two halves across two files is how the catalogue
round-trip hole opened -- an export looked complete until something had to read
it back. Keeping them here means a field cannot be added to one side without
the other staring at you.

Pure: no I/O, no ORM. The caller adapts ORM rows into TagExport and consumes
ParsedTag, exactly as yaml_export/yaml_import do for concerts.

`parse_tags` follows parse_draft's philosophy: WARNINGS OVER FAILURES. One bad
row is skipped and named; only a file that cannot yield any tags at all raises.
A restore is run under stress, and refusing the whole file over one typo is the
wrong trade.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

import yaml

from app.domain.slugs import slug_core
from app.domain.types import TagKind

_TAG_KEYS = {
    "handle", "name", "kind", "name_en", "name_zh", "parent", "members",
    "region", "city", "city_en", "city_zh", "address", "location_url",
    "eventernote_url",
}


class TagsFileError(Exception):
    """The text cannot yield any tags at all."""


@dataclass(frozen=True)
class TagExport:
    handle: str
    name: str
    kind: str
    name_en: str | None = None
    name_zh: str | None = None
    parent: str | None = None
    members: tuple[str, ...] = ()
    region: str | None = None
    city: str | None = None
    city_en: str | None = None
    city_zh: str | None = None
    address: str | None = None
    location_url: str | None = None
    eventernote_url: str | None = None


@dataclass
class ParsedTag:
    handle: str
    name: str
    kind: TagKind
    name_en: str | None = None
    name_zh: str | None = None
    parent: str | None = None
    members: list[str] = field(default_factory=list)
    region: str | None = None
    city: str | None = None
    city_en: str | None = None
    city_zh: str | None = None
    address: str | None = None
    location_url: str | None = None
    eventernote_url: str | None = None


@dataclass
class ParsedTagFile:
    tags: list[ParsedTag] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def tags_to_yaml(tags: Sequence[TagExport]) -> str:
    """Serialize for a restore file: readable, and byte-stable.

    Sorted by (kind, handle) rather than caller order, so two exports of an
    unchanged catalogue are identical and the file diffs cleanly -- which is
    most of its value as a backup. Empty fields are omitted rather than emitted
    as `null`, because a person reads this.
    """
    rows = []
    for tag in sorted(tags, key=lambda t: (t.kind, t.handle)):
        row: dict[str, object] = {"handle": tag.handle, "name": tag.name, "kind": tag.kind}
        for key in ("name_en", "name_zh", "parent", "region", "city", "city_en",
                    "city_zh", "address", "location_url", "eventernote_url"):
            value = getattr(tag, key)
            if value:
                row[key] = value
        if tag.members:
            row["members"] = list(tag.members)
        rows.append(row)
    return yaml.dump(
        {"tags": rows}, sort_keys=False, allow_unicode=True, default_flow_style=False
    )


def _text(value, where: str, warnings: list[str]) -> str | None:
    """A string, or None with a warning. NEVER str() a list or dict.

    Same guard as yaml_import._text: stringifying a nested structure turns a
    YAML alias bomb into a plausible-looking value instead of a rejected one.
    """
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        warnings.append(f"{where}: expected text, got {type(value).__name__} -- ignored")
        return None
    text = str(value).strip()
    return text or None


def _handles(value, where: str, warnings: list[str]) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        warnings.append(f"{where}: expected a list, got {type(value).__name__} -- ignored")
        return []
    out = []
    for i, item in enumerate(value, start=1):
        raw = _text(item, f"{where} #{i}", warnings)
        if raw is None:
            continue
        normalised = slug_core(raw)
        if not normalised:
            warnings.append(f"{where} #{i}: {raw!r} is not a usable handle -- dropped")
            continue
        out.append(normalised)
    return out


def parse_tags(text: str) -> ParsedTagFile:
    try:
        data = yaml.safe_load(text)
    except RecursionError as exc:
        raise TagsFileError(
            "that file nests too deeply to read -- flatten it and try again"
        ) from exc
    except yaml.YAMLError as exc:
        raise TagsFileError(f"that does not parse as YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise TagsFileError("a tags file is a mapping with a `tags:` list at the top")
    rows = data.get("tags")
    if not isinstance(rows, list):
        raise TagsFileError("no `tags:` list found -- is this a tags export?")

    out = ParsedTagFile()
    seen: set[str] = set()
    for i, row in enumerate(rows, start=1):
        where = f"tag #{i}"
        if not isinstance(row, dict):
            out.warnings.append(f"{where}: expected a mapping -- skipped")
            continue
        unknown = sorted(set(row) - _TAG_KEYS)
        if unknown:
            out.warnings.append(f"{where}: unknown key(s) {', '.join(unknown)} -- ignored")

        raw_handle = _text(row.get("handle"), f"{where} handle", out.warnings)
        handle = slug_core(raw_handle) if raw_handle else ""
        if not handle:
            out.warnings.append(f"{where}: no usable handle -- skipped, it cannot be identified")
            continue
        if raw_handle != handle:
            out.warnings.append(f"{where}: handle {raw_handle!r} normalised to {handle!r}")
        if handle in seen:
            out.warnings.append(f"{where}: duplicate handle {handle!r} -- the first one wins")
            continue

        name = _text(row.get("name"), f"{where} name", out.warnings)
        if not name:
            out.warnings.append(f"{where} ({handle}): no name -- skipped, a tag cannot render")
            continue
        raw_kind = _text(row.get("kind"), f"{where} kind", out.warnings)
        try:
            kind = TagKind(raw_kind)
        except ValueError:
            out.warnings.append(f"{where} ({handle}): unknown kind {raw_kind!r} -- skipped")
            continue

        seen.add(handle)
        parent = _text(row.get("parent"), f"{where} parent", out.warnings)
        out.tags.append(ParsedTag(
            handle=handle,
            name=name,
            kind=kind,
            name_en=_text(row.get("name_en"), f"{where} name_en", out.warnings),
            name_zh=_text(row.get("name_zh"), f"{where} name_zh", out.warnings),
            parent=slug_core(parent) if parent else None,
            members=_handles(row.get("members"), f"{where} members", out.warnings),
            region=_text(row.get("region"), f"{where} region", out.warnings),
            city=_text(row.get("city"), f"{where} city", out.warnings),
            city_en=_text(row.get("city_en"), f"{where} city_en", out.warnings),
            city_zh=_text(row.get("city_zh"), f"{where} city_zh", out.warnings),
            address=_text(row.get("address"), f"{where} address", out.warnings),
            location_url=_text(row.get("location_url"), f"{where} location_url", out.warnings),
            eventernote_url=_text(
                row.get("eventernote_url"), f"{where} eventernote_url", out.warnings
            ),
        ))
    return out
```

- [ ] **Step 4: Run the tests**

Run: `uv run --isolated pytest tests/test_tags_yaml.py -q`
Expected: PASS, all fourteen.

- [ ] **Step 5: Lint and commit**

```bash
uv run --isolated ruff check . && uv run --isolated pytest -q
git add src/app/domain/tags_yaml.py tests/test_tags_yaml.py
git commit -m "feat: the tags YAML vocabulary, both halves in one module"
```

---

### Task 2: the shared tag writer and the two-pass importer

**Files:**
- Modify: `src/app/db/service.py` (Tags section, near `assign_tag_slug`)
- Modify: `src/app/web/routes/tags.py` — the three inline `Tag(...)` sites (`create_tag` ~line 144, `quick_create_venue` ~line 209, `quick_create_tag` ~line 300) now call the writer
- Test: `tests/test_tag_import.py` (create)

**Interfaces:**
- Consumes: `ParsedTagFile`, `ParsedTag` (Task 1); `assign_tag_slug`, `find_tags_by_name_and_kind` (existing).
- Produces, used by Task 3:
  - `async def create_tag_row(session, *, name: str, kind: TagKind, slug: str | None = None, name_en: str | None = None, name_zh: str | None = None, parent_id: int | None = None, region: str | None = None, city: str | None = None, city_en: str | None = None, city_zh: str | None = None, address: str | None = None, location_url: str | None = None, eventernote_url: str | None = None, created_by: int | None = None) -> Tag`
  - `@dataclass class TagImportReport` — `created: list[str]`, `skipped: list[str]`, `warnings: list[str]`
  - `async def import_tags(session, parsed: ParsedTagFile, created_by: int | None = None) -> TagImportReport`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_tag_import.py
"""The two-pass tags importer: create what is missing, wire what it created.

Spec: docs/superpowers/specs/2026-07-30-catalogue-round-trip-design.md
"""

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Concert, ConcertTag, Notification, Tag, TagMember
from app.db.service import assign_tag_slug, ensure_user, import_tags
from app.domain.tags_yaml import parse_tags
from app.domain.types import TagKind

ADMIN = 42


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")  # match production

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


FILE = """
tags:
  - {handle: love-live, name: "ラブライブ！", name_en: Love Live!, kind: franchise}
  - handle: hasunosora
    name: "蓮ノ空"
    name_en: Hasunosora
    kind: group
    parent: love-live
    members: [kozue-otomune]
  - {handle: kozue-otomune, name: "乙宗梢", kind: artist}
  - handle: k-arena
    name: "Kアリーナ横浜"
    kind: venue
    region: Kanto
    city: "横浜"
    city_en: Yokohama
    address: "神奈川県横浜市"
"""


async def _import(db, text=FILE):
    async with db() as s:
        await ensure_user(s, ADMIN, "reiji")
        report = await import_tags(s, parse_tags(text), created_by=ADMIN)
        await s.commit()
        return report


async def test_creates_every_missing_tag_with_its_handle(db):
    report = await _import(db)
    assert sorted(report.created) == ["hasunosora", "k-arena", "kozue-otomune", "love-live"]
    assert report.skipped == []

    async with db() as s:
        tags = {t.slug: t for t in (await s.execute(select(Tag))).scalars()}
    assert set(tags) == {"love-live", "hasunosora", "kozue-otomune", "k-arena"}
    assert tags["love-live"].name_en == "Love Live!"
    assert tags["k-arena"].region == "Kanto"
    assert tags["k-arena"].city_en == "Yokohama"
    assert tags["k-arena"].address == "神奈川県横浜市"
    assert tags["kozue-otomune"].kind is TagKind.ARTIST


async def test_wires_parent_and_members_in_the_second_pass(db):
    """`parent` and `members` are HANDLES, so they can only resolve once every
    tag exists -- which is why the importer has two passes at all."""
    await _import(db)
    async with db() as s:
        tags = {t.slug: t for t in (await s.execute(select(Tag))).scalars()}
        assert tags["hasunosora"].parent_id == tags["love-live"].id
        links = (await s.execute(select(TagMember))).scalars().all()
        assert [(m.group_tag_id, m.member_tag_id) for m in links] == [
            (tags["hasunosora"].id, tags["kozue-otomune"].id)
        ]


async def test_importing_twice_changes_nothing(db):
    """Idempotence is the property that makes this safe to run on a populated
    database: an existing handle is skipped ENTIRELY, never updated."""
    await _import(db)
    second = await _import(db)
    assert second.created == []
    assert sorted(second.skipped) == ["hasunosora", "k-arena", "kozue-otomune", "love-live"]

    async with db() as s:
        assert len((await s.execute(select(Tag))).scalars().all()) == 4
        assert len((await s.execute(select(TagMember))).scalars().all()) == 1


async def test_an_existing_tag_is_not_updated(db):
    """The owner's rule: an import can never revert an edit made since the
    export. A stale file must not overwrite the live row."""
    async with db() as s:
        await ensure_user(s, ADMIN, "reiji")
        tag = Tag(name="Renamed since the export", kind=TagKind.FRANCHISE, slug="love-live")
        s.add(tag)
        await s.commit()

    await _import(db)
    async with db() as s:
        kept = (await s.execute(select(Tag).where(Tag.slug == "love-live"))).scalar_one()
        assert kept.name == "Renamed since the export"
        assert kept.name_en is None, "the file's name_en must NOT have been applied"


async def test_a_parent_that_is_not_a_franchise_warns_and_is_dropped(db):
    report = await _import(db, """
tags:
  - {handle: a, name: A, kind: artist}
  - {handle: g, name: G, kind: group, parent: a}
""")
    assert sorted(report.created) == ["a", "g"]
    assert any("parent" in w for w in report.warnings)
    async with db() as s:
        g = (await s.execute(select(Tag).where(Tag.slug == "g"))).scalar_one()
        assert g.parent_id is None


async def test_a_missing_reference_warns_and_the_rest_still_lands(db):
    report = await _import(db, """
tags:
  - {handle: g, name: G, kind: group, parent: nowhere, members: [ghost]}
""")
    assert report.created == ["g"]
    assert any("nowhere" in w for w in report.warnings)
    assert any("ghost" in w for w in report.warnings)
    async with db() as s:
        assert (await s.execute(select(TagMember))).scalars().all() == []


async def test_a_group_cannot_be_a_member(db):
    """Groups do not nest -- the same rule POST /tags/{id}/members enforces."""
    report = await _import(db, """
tags:
  - {handle: g1, name: G1, kind: group}
  - {handle: g2, name: G2, kind: group, members: [g1]}
""")
    assert any("g1" in w for w in report.warnings)
    async with db() as s:
        assert (await s.execute(select(TagMember))).scalars().all() == []


async def test_membership_of_an_existing_tag_is_left_alone(db):
    """Skip means skip. Re-wiring an existing tag's members would be an update
    by another name, and the rule is that imports do not update."""
    async with db() as s:
        await ensure_user(s, ADMIN, "reiji")
        group = Tag(name="G", kind=TagKind.GROUP, slug="hasunosora")
        s.add(group)
        await s.commit()

    await _import(db)
    async with db() as s:
        assert (await s.execute(select(TagMember))).scalars().all() == [], (
            "the group already existed, so its membership must not be written"
        )


async def test_importing_queues_no_notification(db):
    """Invariant 4: creation is not attachment. Same reason quick_create_tag is
    silent -- nobody is owed a DM because taxonomy appeared."""
    await _import(db)
    async with db() as s:
        assert (await s.execute(select(Notification))).scalars().all() == []


async def test_importing_touches_no_concert(db):
    """Invariant 3: group expansion is an attach-time act. A restored membership
    list must never rewrite an existing concert's performers."""
    async with db() as s:
        await ensure_user(s, ADMIN, "reiji")
        # NB: Concert has no `slug` column -- handles are a TAG concept; a
        # concert's URL handle is `event_id`.
        concert = Concert(event_id="c1", title="C", created_by=ADMIN)
        s.add(concert)
        group = Tag(name="G", kind=TagKind.GROUP)
        s.add(group)
        await assign_tag_slug(s, group)
        await s.flush()
        s.add(ConcertTag(concert_id=concert.id, tag_id=group.id))
        await s.commit()
        before = len((await s.execute(select(ConcertTag))).scalars().all())

    await _import(db)
    async with db() as s:
        after = len((await s.execute(select(ConcertTag))).scalars().all())
    assert after == before


async def test_parser_warnings_reach_the_report(db):
    """A warning the parser raised is useless if the route never shows it."""
    report = await _import(db, "tags:\n  - {name: no handle here, kind: artist}\n")
    assert report.created == []
    assert any("handle" in w for w in report.warnings)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --isolated pytest tests/test_tag_import.py -q`
Expected: FAIL — `ImportError: cannot import name 'import_tags'`

- [ ] **Step 3: Add the shared writer to `db/service.py`**

Place immediately after `assign_tag_slug`:

```python
async def create_tag_row(
    session: AsyncSession,
    *,
    name: str,
    kind: TagKind,
    slug: str | None = None,
    name_en: str | None = None,
    name_zh: str | None = None,
    parent_id: int | None = None,
    region: str | None = None,
    city: str | None = None,
    city_en: str | None = None,
    city_zh: str | None = None,
    address: str | None = None,
    location_url: str | None = None,
    eventernote_url: str | None = None,
    created_by: int | None = None,
) -> Tag:
    """Build and add a Tag. The ONE place a tag row is constructed.

    `slug=None` means MINT one (assign_tag_slug de-duplicates); a value means
    the caller already owns the handle and it is used verbatim. That distinction
    is the whole reason this exists: the three editor routes generate a handle,
    while the catalogue import carries handles in the file and must not have
    them silently renamed. Callers passing a slug are responsible for having
    normalised it -- `domain.tags_yaml.parse_tags` does.

    Does NOT commit, and does NOT notify: creating a tag is not attaching one
    (invariant 4).
    """
    tag = Tag(
        name=name.strip(),
        kind=kind,
        slug=slug,
        name_en=name_en,
        name_zh=name_zh,
        parent_id=parent_id,
        region=region,
        city=city,
        city_en=city_en,
        city_zh=city_zh,
        address=address,
        location_url=location_url,
        eventernote_url=eventernote_url,
        created_by=created_by,
    )
    session.add(tag)
    if slug is None:
        await assign_tag_slug(session, tag)
    return tag
```

- [ ] **Step 4: Add the importer to `db/service.py`**

Place directly after `create_tag_row`:

```python
@dataclass
class TagImportReport:
    """What an import did, for the result page. Handles, not ids: the operator
    reads this next to the file they pasted."""

    created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


async def import_tags(
    session: AsyncSession, parsed: ParsedTagFile, created_by: int | None = None
) -> TagImportReport:
    """Create missing tags, then wire the ones just created. Never updates.

    TWO PASSES because `parent` and `members` are HANDLES: nothing can resolve
    until every tag in the file exists. Pass 2 wires only tags CREATED in this
    run -- re-wiring an existing tag's membership would be an update, and the
    rule (owner, 2026-07-30) is that an import is additive so a stale file can
    never revert an edit made since the export.

    Warnings from the parser are carried through: one the operator never sees is
    one that did not happen. Does not commit -- the caller owns the transaction,
    so a rejected file leaves nothing behind.
    """
    report = TagImportReport(warnings=list(parsed.warnings))
    existing = {
        slug: tag_id
        for tag_id, slug in await session.execute(select(Tag.id, Tag.slug))
    }

    # Pass 1: create what is missing.
    created_ids: dict[str, int] = {}
    for tag in parsed.tags:
        if tag.handle in existing:
            report.skipped.append(tag.handle)
            continue
        row = await create_tag_row(
            session,
            name=tag.name,
            kind=tag.kind,
            slug=tag.handle,
            name_en=tag.name_en,
            name_zh=tag.name_zh,
            region=tag.region,
            city=tag.city,
            city_en=tag.city_en,
            city_zh=tag.city_zh,
            address=tag.address,
            location_url=tag.location_url,
            eventernote_url=tag.eventernote_url,
            created_by=created_by,
        )
        await session.flush()  # need the id for pass 2
        created_ids[tag.handle] = row.id
        report.created.append(tag.handle)

    # Pass 2: resolve handles to ids. `known` spans both, because a parent may
    # legitimately already exist while its child is new.
    known = {**existing, **created_ids}
    kinds = {
        slug: kind for slug, kind in await session.execute(select(Tag.slug, Tag.kind))
    }
    for tag in parsed.tags:
        if tag.handle not in created_ids:
            continue  # skipped, so untouched -- including its membership
        tag_id = created_ids[tag.handle]
        if tag.parent:
            parent_id = known.get(tag.parent)
            if parent_id is None:
                report.warnings.append(
                    f"{tag.handle}: parent {tag.parent!r} is in neither the file nor the "
                    f"catalogue -- created without a parent"
                )
            elif kinds.get(tag.parent) is not TagKind.FRANCHISE:
                report.warnings.append(
                    f"{tag.handle}: parent {tag.parent!r} is not a franchise -- "
                    f"created without a parent"
                )
            else:
                (await session.get(Tag, tag_id)).parent_id = parent_id
        for member in tag.members:
            member_id = known.get(member)
            if member_id is None:
                report.warnings.append(
                    f"{tag.handle}: member {member!r} is in neither the file nor the "
                    f"catalogue -- that membership dropped"
                )
                continue
            if kinds.get(member) is TagKind.GROUP:
                report.warnings.append(
                    f"{tag.handle}: member {member!r} is a group, and groups do not "
                    f"nest -- dropped"
                )
                continue
            session.add(TagMember(group_tag_id=tag_id, member_tag_id=member_id))
    return report
```

Add to the imports at the top of `service.py`: `TagMember` to the
`app.db.models` import list, and
`from app.domain.tags_yaml import ParsedTagFile`. `dataclass`/`field` are
already imported.

- [ ] **Step 5: Point the three routes at the writer**

In `src/app/web/routes/tags.py`, replace each inline `Tag(...)` + `session.add`
+ `assign_tag_slug` trio with a `create_tag_row(...)` call passing `slug=None`
(the default). Import `create_tag_row` and drop `assign_tag_slug` from the
import list if nothing else uses it. Behaviour must not change — these routes
still mint a handle.

- [ ] **Step 6: Run the tests**

Run: `uv run --isolated pytest tests/test_tag_import.py tests/test_tags.py tests/test_tag_handles.py -q`
Expected: PASS. The `test_tags.py` run is the regression half — the three routes must behave exactly as before.

- [ ] **Step 7: Lint, full suite, commit**

```bash
uv run --isolated ruff check . && uv run --isolated pytest -q
git add src tests
git commit -m "feat: one tag writer, and the two-pass tags importer"
```

---

### Task 3: `GET`/`POST /admin/import/tags`

**Files:**
- Modify: `src/app/web/routes/admin.py`
- Create: `src/app/web/templates/admin_import_tags.html`
- Modify: `src/app/web/templates/preferences.html` (the admin-tools list gains a link)
- Test: `tests/test_admin_import_tags.py` (create)

**Interfaces:**
- Consumes: `parse_tags`, `TagsFileError` (Task 1); `import_tags`, `TagImportReport` (Task 2); `MAX_DRAFT_CHARS` from `app.web.routes.imports`.
- Produces: nothing later tasks depend on.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_admin_import_tags.py
"""The admin tags-import surface: gate, report, and the failure copy."""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base, Tag
from app.db.session import get_session
from app.web import auth
from app.web.app import create_app

ADMIN_ID, EDITOR_ID = 42, 77

FILE = """
tags:
  - {handle: love-live, name: "ラブライブ！", name_en: Love Live!, kind: franchise}
  - {handle: kozue, name: "乙宗梢", kind: artist}
"""


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
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
    monkeypatch.setattr(settings, "admin_whitelist", str(ADMIN_ID))
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


def login_as(client, discord_id, name):
    async def fake_identity(token):
        return {"id": str(discord_id), "username": name, "global_name": name, "avatar": None}

    client.monkeypatch.setattr(auth, "fetch_identity", fake_identity)
    r = client.get("/auth/login")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    client.get(f"/auth/callback?code=x&state={state}")


def test_an_editor_cannot_reach_either_half(client):
    login_as(client, EDITOR_ID, "editor")
    assert client.get("/admin/import/tags").status_code == 403
    assert client.post("/admin/import/tags", data={"text": FILE}).status_code == 403


def test_the_admin_gets_a_paste_form(client):
    login_as(client, ADMIN_ID, "reiji")
    body = client.get("/admin/import/tags").text
    assert 'name="text"' in body
    assert "tags.yaml" in body


async def test_a_good_file_creates_the_tags_and_reports(client):
    login_as(client, ADMIN_ID, "reiji")
    body = client.post("/admin/import/tags", data={"text": FILE}).text
    assert "love-live" in body and "kozue" in body
    assert "Created 2" in body

    async with client.db() as s:
        assert sorted(t.slug for t in (await s.execute(select(Tag))).scalars()) == [
            "kozue", "love-live",
        ]


async def test_a_second_import_reports_skips_and_writes_nothing(client):
    login_as(client, ADMIN_ID, "reiji")
    client.post("/admin/import/tags", data={"text": FILE})
    body = client.post("/admin/import/tags", data={"text": FILE}).text
    assert "Created 0" in body
    assert "Skipped 2" in body
    async with client.db() as s:
        assert len((await s.execute(select(Tag))).scalars().all()) == 2


async def test_an_unparseable_file_reports_and_writes_nothing(client):
    login_as(client, ADMIN_ID, "reiji")
    body = client.post("/admin/import/tags", data={"text": "{["}).text
    assert "does not parse as YAML" in body
    async with client.db() as s:
        assert (await s.execute(select(Tag))).scalars().all() == []


def test_warnings_are_shown_not_swallowed(client):
    login_as(client, ADMIN_ID, "reiji")
    body = client.post("/admin/import/tags", data={
        "text": "tags:\n  - {name: no handle, kind: artist}\n",
    }).text
    assert "handle" in body


def test_an_oversized_paste_is_refused(client):
    login_as(client, ADMIN_ID, "reiji")
    body = client.post("/admin/import/tags", data={"text": "x" * 200_001}).text
    assert "200k" in body


def test_preferences_links_the_importer_for_an_admin(client):
    login_as(client, ADMIN_ID, "reiji")
    assert "/admin/import/tags" in client.get("/preferences").text
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --isolated pytest tests/test_admin_import_tags.py -q`
Expected: FAIL — 404 on both routes.

- [ ] **Step 3: Add the routes**

Append to `src/app/web/routes/admin.py`:

```python
@router.get("/admin/import/tags", response_class=HTMLResponse)
async def import_tags_form(
    request: Request,
    user: SessionUser = Depends(require_admin),
):
    """Paste a tags.yaml from an export. English-only and NOT wrapped in _(),
    like every other admin surface."""
    return templates.TemplateResponse(
        request, "admin_import_tags.html", {"user": user, "report": None, "error": None}
    )


@router.post("/admin/import/tags", response_class=HTMLResponse)
async def import_tags_commit(
    request: Request,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    text: str = Form(""),
):
    """Create missing tags from a pasted export, and report what happened.

    NO preview, deliberately. The concert import needs one because its commit
    writes rich, ambiguous data a human should eyeball; the only outcome here is
    "tags that did not exist now do", so a result page afterwards carries the
    same information for a fraction of the build.

    One transaction: a file that raises leaves nothing behind.
    """
    def page(report=None, error=None):
        return templates.TemplateResponse(
            request,
            "admin_import_tags.html",
            {"user": user, "report": report, "error": error, "text": text},
        )

    if len(text) > MAX_DRAFT_CHARS:
        return page(error="that file is too large -- pastes are capped at 200k characters")
    try:
        parsed = parse_tags(text)
    except TagsFileError as exc:
        return page(error=str(exc))
    await ensure_user(session, user.id, user.username)
    report = await import_tags(session, parsed, created_by=user.id)
    await session.commit()
    return page(report=report)
```

Add to `admin.py`'s imports: `Form` from `fastapi`; `ensure_user`, `import_tags`
from `app.db.service`; `TagsFileError`, `parse_tags` from
`app.domain.tags_yaml`; `MAX_DRAFT_CHARS` from `app.web.routes.imports`.

- [ ] **Step 4: Add the template**

Create `src/app/web/templates/admin_import_tags.html`, modelled on
`admin_deliveries.html`. English-only, no `_()`:

```html
{% extends "base.html" %}
{% block title %}Import tags — dekimasen.app{% endblock %}
{% block content %}
<div class="lede">
  <h1>Import tags</h1>
  <p class="dim">Paste the <code>tags.yaml</code> from an
    <a href="/admin/export.zip">export</a>. Tags that already exist are skipped,
    never updated — so this is safe to run twice, and a stale file cannot revert
    an edit you made since.</p>
</div>

{% if error %}
<p class="banner dgr"><b>That file could not be read.</b> {{ error }}</p>
{% endif %}

{% if report %}
<div class="edgecard ok">
  <p><b>Created {{ report.created | length }}</b> · Skipped {{ report.skipped | length }}</p>
  {% if report.created %}
  <p class="dim">Created: {% for h in report.created %}<code>{{ h }}</code>{% if not loop.last %}, {% endif %}{% endfor %}</p>
  {% endif %}
  {% if report.skipped %}
  <p class="dim">Skipped: {% for h in report.skipped %}<code>{{ h }}</code>{% if not loop.last %}, {% endif %}{% endfor %}</p>
  {% endif %}
</div>
{% if report.warnings %}
<div class="banner warn">
  <div>
    <b>{{ report.warnings | length }} warning(s).</b> Everything else still imported.
    <ul>{% for w in report.warnings %}<li>{{ w }}</li>{% endfor %}</ul>
  </div>
</div>
{% endif %}
{% endif %}

<form method="post" action="/admin/import/tags" class="stack wide">
  <label class="fld"><span>tags.yaml</span>
    <textarea name="text" rows="18" spellcheck="false"
              placeholder="tags:&#10;  - handle: love-live&#10;    name: ラブライブ！&#10;    kind: franchise">{{ text or "" }}</textarea></label>
  <button type="submit">Import tags</button>
</form>
{% endblock %}
```

- [ ] **Step 5: Link it from Preferences**

In `src/app/web/templates/preferences.html`, inside the existing admin-tools
section (the block added by the error-pages build, beside the Deliveries and
Broadcast links), add:

```html
      <span class="nm3"><a href="/admin/import/tags">Import tags</a></span>
```

Match the surrounding markup exactly — copy the shape of the Deliveries row
rather than inventing one.

- [ ] **Step 6: Run tests, lint, commit**

```bash
uv run --isolated pytest tests/test_admin_import_tags.py -q
uv run --isolated ruff check . && uv run --isolated pytest -q
git add src tests
git commit -m "feat: POST /admin/import/tags, with a result report and no preview"
```

---

### Task 4: the draft carries `event_id`

**Files:**
- Modify: `src/app/domain/yaml_export.py` (`concert_to_yaml`: add `event_id` param, emit it, stop emitting `slug`)
- Modify: `src/app/domain/yaml_import.py` (`_TOP_KEYS`, `ParsedConcert.event_id`)
- Modify: `src/app/domain/draft.py` (`ParsedConcert` gains `event_id: str | None = None`)
- Modify: `src/app/web/routes/concerts.py` (the export route passes `event_id`)
- Modify: `src/app/web/routes/imports.py` (`import_draft` context; `import_commit` uses a submitted `event_id`)
- Modify: `src/app/web/templates/import_preview.html` (an `event_id` field in the Details fold)
- Test: `tests/test_draft_import.py`, `tests/test_yaml_export.py`

**Interfaces:**
- Consumes: `validate_event_id`, `generate_event_id` (both in `web/routes/concerts.py`).
- Produces: `ParsedConcert.event_id`, used by nothing later; the preview form field `event_id`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_draft_import.py`:

```python
async def test_a_draft_can_carry_its_event_id(client):
    """A restore must land on the ORIGINAL urls, or every link anybody holds
    breaks. Absent, generate_event_id runs exactly as before."""
    login_as(client, EDITOR_ID, "reiji")
    body = client.post("/concerts/import/draft", data={
        "draft": "event_id: hasunosora-6th-live\ntitle: 6th\ntitle_en: 6th\ntitle_zh: 6th\n",
    }).text
    assert 'name="event_id"' in body
    assert 'value="hasunosora-6th-live"' in body


async def test_committing_with_an_event_id_uses_it(client):
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
```

Add to `tests/test_yaml_export.py`:

```python
def test_export_emits_event_id_and_no_slug():
    """`slug` was slugify(title) and unrelated to event_id -- two near-identical
    keys with different meanings has no place in a restore file. The PARSER
    still tolerates it so older drafts keep working."""
    text = concert_to_yaml(
        title="6th", event_id="hasunosora-6th-live", kind=None,
        franchises=[], groups=[], artists=[], venues=[], days=[], rounds=[],
        notes=None,
    )
    assert "event_id: hasunosora-6th-live" in text
    assert "slug:" not in text
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --isolated pytest tests/test_draft_import.py tests/test_yaml_export.py -q -k "event_id or slug"`
Expected: FAIL — `concert_to_yaml` has no `event_id` parameter; the commit route ignores the field.

- [ ] **Step 3: Domain changes**

`domain/yaml_export.py` — add `event_id: str | None = None` to `concert_to_yaml`'s
keyword parameters, and replace the `"slug": slugify(title),` entry in the `data`
dict with:

```python
        # The URL handle, so a restore lands on the ORIGINAL address. Replaces
        # the old `slug` key, which was slugify(title) and unrelated -- two
        # near-identical keys with different meanings is a trap in a file whose
        # whole job is to be read back later. yaml_import still tolerates `slug`
        # so older drafts parse.
        "event_id": event_id,
```

`domain/draft.py` — add to `ParsedConcert`:

```python
    event_id: str | None = None   # the URL handle, when a draft carries one
```

`domain/yaml_import.py` — add `"event_id"` to `_TOP_KEYS`, and add to the
`ParsedConcert(...)` construction:

```python
        event_id=_text(data.get("event_id"), "event_id", warnings),
```

- [ ] **Step 4: Route changes**

`web/routes/concerts.py`, in `export_concert_yaml`, pass
`event_id=concert.event_id` to `concert_to_yaml`.

`web/routes/imports.py`, in `import_commit`, replace the unconditional generate:

```python
    # A draft may carry its own event_id, so a restore lands on the original
    # URL. validate_event_id is the SAME check the edit page runs -- format,
    # reserved words (invariant 6), uniqueness -- rather than a second copy of
    # the rule, so re-importing a file whose concert still exists answers 409.
    event_id = (
        await validate_event_id(session, submitted_event_id)
        if (submitted_event_id := event_id.strip())
        else await generate_event_id(session, title, title_en)
    )
```

with `event_id: str = Form(default="")` added to `import_commit`'s parameters
and `validate_event_id` added to the `from app.web.routes.concerts import (...)`
list. In `import_draft`, pass `parsed.event_id` into the template context as
`event_id`.

- [ ] **Step 5: Template field**

In `import_preview.html`'s Details fold, beside the Source URL field, add:

```html
        <label>Event ID <input name="event_id" maxlength="100" value="{{ event_id or '' }}"
          placeholder="auto-generated from the English title"></label>
```

Wrap the visible label in `_()` here — `import_preview.html` IS a translated
editor surface, unlike the admin pages — and update both catalogues:

```bash
uv run --isolated pybabel extract -F babel.cfg -k N_ -o messages.pot .
uv run --isolated pybabel update -i messages.pot -d src/app/translations -l ja
uv run --isolated pybabel update -i messages.pot -d src/app/translations -l zh
```

Fill by hand — ja `イベントID`, zh `活动 ID` for "Event ID", and translate the
placeholder too — then `rm messages.pot`. Clear any `#, fuzzy`.

- [ ] **Step 6: Run tests, lint, commit**

```bash
uv run --isolated pytest tests/test_draft_import.py tests/test_yaml_export.py tests/test_imports.py tests/test_i18n_catalogues.py -q
uv run --isolated ruff check . && uv run --isolated pytest -q
git add src tests
git commit -m "feat: a draft carries its event_id, so a restore keeps its URLs"
```

---

### Task 5: the draft carries handles, and handles win

**Files:**
- Modify: `src/app/domain/yaml_export.py` (emit `series_handles`, per-leg `venue_handle`)
- Modify: `src/app/domain/yaml_import.py` (parse both)
- Modify: `src/app/domain/draft.py` (`ParsedConcert` handle lists; `ParsedDay.venue_handle`)
- Modify: `src/app/db/service.py` (two slug matchers)
- Modify: `src/app/web/routes/imports.py` (`import_draft` prefers handles)
- Modify: `src/app/web/routes/concerts.py` (the export route passes handles)
- Test: `tests/test_draft_import.py`, `tests/test_tags_yaml.py`

**Interfaces:**
- Consumes: `ParsedConcert`, `ParsedDay` (Task 4 extended them).
- Produces:
  - `ParsedConcert.franchise_handles: list[str]`, `.group_handles: list[str]`, `.artist_handles: list[str]`
  - `ParsedDay.venue_handle: str | None`
  - `def match_tag_ids_by_slug(slugs: Sequence[str], tags: Sequence[Tag]) -> tuple[list[int], list[str]]`
  - `def match_venue_tag_id_by_slug(slug: str | None, venue_tags: Sequence[Tag]) -> int | None`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_draft_import.py`:

```python
async def test_handles_bind_the_right_one_of_two_same_named_performers(client):
    """THE case the whole round-trip arc exists for. Two performers are both
    written 佐藤有紀. Bound by NAME, match_tag_ids_by_name is documented
    first-tag-wins, so an exported concert would silently re-import against the
    wrong person. series_handles is what makes it exact."""
    login_as(client, EDITOR_ID, "reiji")
    for en in ("Yuki Sato", "Yuki Sato (Liella)"):
        client.post("/tags", data={
            "name": "佐藤有紀", "name_en": en, "name_zh": en, "kind": "artist",
        })
    async with client.db() as s:
        wanted = (await s.execute(
            select(Tag).where(Tag.slug == "yuki-sato-liella")
        )).scalar_one()

    body = client.post("/concerts/import/draft", data={
        "draft": (
            "title: 6th\ntitle_en: 6th\ntitle_zh: 6th\n"
            "series:\n  artists: [佐藤有紀]\n"
            "series_handles:\n  artists: [yuki-sato-liella]\n"
        ),
    }).text
    assert f'value="{wanted.id}"' in body


async def test_a_handle_block_makes_the_names_irrelevant(client):
    """THE RULE: if series_handles names a kind, it is authoritative for that
    kind and the name list is ignored outright. An export taken before a rename
    carries both and they disagree; resolving the name too would re-select a tag
    the handle deliberately did not choose."""
    login_as(client, EDITOR_ID, "reiji")
    for jp, en in (("乙宗梢", "Kozue"), ("日野下花帆", "Kaho")):
        client.post("/tags", data={
            "name": jp, "name_en": en, "name_zh": en, "kind": "artist",
        })
    async with client.db() as s:
        kozue = (await s.execute(select(Tag).where(Tag.slug == "kozue"))).scalar_one()
        kaho = (await s.execute(select(Tag).where(Tag.slug == "kaho"))).scalar_one()

    body = client.post("/concerts/import/draft", data={
        "draft": (
            "title: 6th\ntitle_en: 6th\ntitle_zh: 6th\n"
            "series:\n  artists: [日野下花帆]\n"       # would resolve to kaho
            "series_handles:\n  artists: [kozue]\n"   # but the handle says kozue
        ),
    }).text
    assert f'value="{kozue.id}"' in body
    assert f'value="{kaho.id}" selected' not in body, "the name must not also select"


async def test_a_handle_that_is_not_here_yet_does_not_fall_back_to_the_name(client):
    """No fallback, deliberately. A handle that is missing means "import tags
    first"; quietly binding its name instead would reintroduce exactly the
    first-tag-wins guess this whole arc removed. It surfaces as unmatched, so
    the editor can create or pick it."""
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
    async with client.db() as s:
        kozue = (await s.execute(select(Tag).where(Tag.slug == "kozue"))).scalar_one()
    assert f'value="{kozue.id}" selected' not in body
    assert "never-imported" in body, "the missing handle must be named, not swallowed"


async def test_without_a_handle_block_names_resolve_exactly_as_before(client):
    """The backward-compatibility half, and it matters more than the fix half:
    every agent-authored draft omits series_handles."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={
        "name": "乙宗梢", "name_en": "Kozue", "name_zh": "Kozue", "kind": "artist",
    })
    body = client.post("/concerts/import/draft", data={
        "draft": "title: 6th\ntitle_en: 6th\ntitle_zh: 6th\nseries:\n  artists: [乙宗梢]\n",
    }).text
    async with client.db() as s:
        kozue = (await s.execute(select(Tag).where(Tag.slug == "kozue"))).scalar_one()
    assert f'value="{kozue.id}"' in body


async def test_a_leg_venue_handle_preselects_the_venue(client):
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
    async with client.db() as s:
        venue = (await s.execute(select(Tag).where(Tag.slug == "k-arena"))).scalar_one()
    assert f'value="{venue.id}" selected' in body or f'value="{venue.id}"' in body


async def test_a_handle_absent_from_the_catalogue_falls_back_to_the_name(client):
    """A handle that is not here yet is not a dead end: the name still resolves,
    exactly as an agent-authored draft does."""
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
    async with client.db() as s:
        kozue = (await s.execute(select(Tag).where(Tag.slug == "kozue"))).scalar_one()
    assert f'value="{kozue.id}"' in body
    assert "never-imported" in body
```

Ensure `select` and `Tag` are imported in that test module.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --isolated pytest tests/test_draft_import.py -q -k "handle"`
Expected: FAIL — `series_handles` is an unknown key and nothing resolves it.

- [ ] **Step 3: Domain changes**

`domain/draft.py` — add to `ParsedDay`:

```python
    venue_handle: str | None = None   # a VENUE tag's handle; beats venue_name
```

and to `ParsedConcert`:

```python
    # Tag HANDLES, which beat the name lists above when present. An export
    # writes both -- names so a person can read the file, handles so it binds
    # exactly -- and an agent-authored draft writes only names.
    franchise_handles: list[str] = field(default_factory=list)
    group_handles: list[str] = field(default_factory=list)
    artist_handles: list[str] = field(default_factory=list)
```

`domain/yaml_import.py` — add `"series_handles"` to `_TOP_KEYS` and
`"venue_handle"` to `_DAY_KEYS`; parse the block beside the existing `series`
handling:

```python
    handles = data.get("series_handles") or {}
    if not isinstance(handles, dict):
        warnings.append("series_handles: expected a mapping -- ignored")
        handles = {}
    _warn_unknown(handles, _SERIES_KEYS, "series_handles", warnings)
```

then pass `franchise_handles=_names(handles.get("franchises"), "series_handles.franchises", warnings)`
and the group/artist equivalents into `ParsedConcert(...)`, plus
`venue_handle=_text(row.get("venue_handle"), f"{where} venue_handle", warnings)`
in the day loop.

`domain/yaml_export.py` — add `event_handles: dict[str, list[str]] | None = None`
and per-`YamlDay` `venue_handle: str | None = None`; emit
`"series_handles": event_handles` when truthy and `"venue_handle": d.venue_handle`
inside each performance.

- [ ] **Step 4: Service matchers**

In `db/service.py`, beside `match_tag_ids_by_name`:

```python
def match_tag_ids_by_slug(
    slugs: Sequence[str], tags: Sequence[Tag]
) -> tuple[list[int], list[str]]:
    """Resolve HANDLES to ids: (matched ids, unmatched handles).

    Exact, unlike its by-name sibling, because that is the entire point -- a
    handle identifies one tag, so there is no first-tag-wins rule to explain and
    no locale variant to accidentally match. Ids come back deduplicated in
    first-mention order; unmatched handles keep their input order for the
    preview to list.
    """
    by_slug = {t.slug: t.id for t in tags}
    ids: list[int] = []
    missing: list[str] = []
    for slug in slugs:
        tag_id = by_slug.get(slug)
        if tag_id is None:
            missing.append(slug)
        elif tag_id not in ids:
            ids.append(tag_id)
    return ids, missing


def match_venue_tag_id_by_slug(slug: str | None, venue_tags: Sequence[Tag]) -> int | None:
    """A leg's venue by handle. Exact, for the same reason as above."""
    if not slug:
        return None
    return next((t.id for t in venue_tags if t.slug == slug), None)
```

`Sequence` is already imported in `service.py`.

- [ ] **Step 5: Preview resolution prefers handles**

In `imports.py`'s `import_draft`, for each of the three kinds, resolve handles
FIRST and fall back to names for whatever the handles did not cover:

```python
    for kind_name, names, handles in (
        ("franchise", parsed.franchise_names, parsed.franchise_handles),
        ("group", parsed.group_names, parsed.group_handles),
        ("artist", parsed.artist_names, parsed.artist_handles),
    ):
        pool = picker["by_kind"].get(kind_name, [])
        # THE RULE, in one sentence: if series_handles names this kind, it is
        # AUTHORITATIVE and the name list is ignored outright; otherwise names
        # resolve exactly as they always have.
        #
        # No per-entry fallback, deliberately. A handle identifies exactly one
        # tag, while a name is documented first-tag-wins and -- now that names
        # repeat -- a guess. Falling back to the name for a handle that is not
        # here yet would quietly reintroduce that guess, which is the failure
        # this whole arc removed. A missing handle means "import tags.yaml
        # first", so it surfaces as unmatched and the editor decides.
        if handles:
            ids, missing = match_tag_ids_by_slug(handles, pool)
            if missing:
                parsed.warnings.append(
                    f"series_handles.{kind_name}s: {', '.join(missing)} not in the "
                    f"catalogue -- import tags.yaml first, or pick them by hand. "
                    f"The name list was NOT used as a fallback."
                )
        else:
            ids, missing = match_tag_ids_by_name(names, pool)
        if ids:
            initial_selected[kind_name] = [str(i) for i in ids]
        unmatched_tags.extend({"name": n, "kind": kind_name} for n in missing)
```

And for each parsed day, prefer the handle:

```python
        # Same rule per leg: a handle is authoritative, so the name is only
        # consulted when there is no handle at all.
        day.matched_venue_tag_id = (
            match_venue_tag_id_by_slug(day.venue_handle, venue_tags)
            if day.venue_handle
            else match_venue_tag_id(day.venue_name, venue_tags)
        )
```

- [ ] **Step 6: Export route passes handles**

In `export_concert_yaml`, add:

```python
        event_handles={
            "franchises": [t.slug for t in concert.tags if t.kind is TagKind.FRANCHISE],
            "groups": [t.slug for t in concert.tags if t.kind is TagKind.GROUP],
            "artists": [t.slug for t in concert.tags if t.kind is TagKind.ARTIST],
        },
```

and `venue_handle=d.venue_tag.slug if d.venue_tag else None` inside the
`YamlDay(...)` construction.

- [ ] **Step 7: Run tests, lint, commit**

```bash
uv run --isolated pytest tests/test_draft_import.py tests/test_imports.py tests/test_yaml_export.py tests/test_yaml_import.py -q
uv run --isolated ruff check . && uv run --isolated pytest -q
git add src tests
git commit -m "feat: drafts carry tag handles, and a handle beats a name"
```

---

### Task 6: `GET /admin/export.zip`

**Files:**
- Modify: `src/app/db/service.py` (`concert_export_yaml`, `catalogue_export_files`)
- Modify: `src/app/web/routes/concerts.py` (`export_concert_yaml` reuses the service function)
- Modify: `src/app/web/routes/admin.py` (the zip route)
- Modify: `src/app/web/templates/preferences.html` (link the export)
- Test: `tests/test_catalogue_export.py` (create)

**Interfaces:**
- Consumes: `tags_to_yaml`, `TagExport` (Task 1); `concert_to_yaml` with `event_id`/`event_handles` (Tasks 4–5).
- Produces:
  - `async def concert_export_yaml(session, concert: Concert) -> str`
  - `async def catalogue_export_files(session) -> list[tuple[str, str]]` — `(path_in_zip, text)`, including `tags.yaml`, `concerts/<event_id>.yaml` and `RESTORE.txt`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_catalogue_export.py
"""The admin catalogue export: complete, personal-data-free, reproducible."""

import io
import time
import zipfile

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base, Tag
from app.db.session import get_session
from app.web import auth
from app.web.app import create_app

ADMIN_ID, EDITOR_ID = 42, 77

# (fixtures `db`, `client` and helper `login_as` are identical to
# tests/test_admin_import_tags.py -- copy them in; this file needs the same
# admin/editor split.)


def _entries(payload: bytes) -> dict[str, str]:
    """EXTRACT every entry. Searching the raw zip bytes would pass vacuously --
    entries are DEFLATE-compressed, so a string is not there to find even when
    it is in the data."""
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        return {n: zf.read(n).decode("utf-8") for n in zf.namelist()}


def _seed(client):
    login_as(client, ADMIN_ID, "reiji")
    client.post("/tags", data={
        "name": "ラブライブ！", "name_en": "Love Live!", "name_zh": "LL", "kind": "franchise",
    })
    client.post("/tags", data={
        "name": "乙宗梢", "name_en": "Kozue Otomune", "name_zh": "乙宗梢", "kind": "artist",
    })
    client.post("/concerts", data={
        "title": "蓮ノ空 6th", "title_en": "Hasunosora 6th", "title_zh": "6th",
        "event_id": "hasunosora-6th", "franchise_tags": [1],
    })


def test_an_editor_cannot_download_it(client):
    login_as(client, EDITOR_ID, "editor")
    assert client.get("/admin/export.zip").status_code == 403


def test_the_zip_contains_tags_concerts_and_restore_notes(client):
    _seed(client)
    entries = _entries(client.get("/admin/export.zip").content)
    assert "tags.yaml" in entries
    assert "concerts/hasunosora-6th.yaml" in entries
    assert "RESTORE.txt" in entries
    assert "tags.yaml" in entries["RESTORE.txt"], "the restore ORDER is the point"


def test_tags_yaml_carries_handles_and_every_field(client):
    _seed(client)
    tags_yaml = _entries(client.get("/admin/export.zip").content)["tags.yaml"]
    assert "handle: love-live" in tags_yaml
    assert "name_en: Love Live!" in tags_yaml
    assert "kind: franchise" in tags_yaml


def test_the_concert_draft_carries_its_event_id_and_handles(client):
    _seed(client)
    draft = _entries(client.get("/admin/export.zip").content)["concerts/hasunosora-6th.yaml"]
    assert "event_id: hasunosora-6th" in draft
    assert "series_handles" in draft
    assert "love-live" in draft
    assert "slug:" not in draft


def test_no_personal_data_anywhere(client):
    """By construction, not by filter: the queries never reach a user table."""
    _seed(client)
    for name, text in _entries(client.get("/admin/export.zip").content).items():
        assert "created_by" not in text, name
        assert "reiji" not in text, name


def test_two_exports_are_byte_identical(client):
    """A backup you cannot diff is worth much less. ZipFile.writestr stamps
    every entry with the current time and zip timestamps have TWO-SECOND
    resolution, so the sleep here has to cross a bucket -- a 1s sleep passes on
    luck and proves nothing."""
    _seed(client)
    first = client.get("/admin/export.zip").content
    time.sleep(2.5)
    assert client.get("/admin/export.zip").content == first


def test_preferences_links_the_export_for_an_admin(client):
    login_as(client, ADMIN_ID, "reiji")
    assert "/admin/export.zip" in client.get("/preferences").text


async def test_the_export_round_trips_through_the_importer(client):
    """The end-to-end promise: export, drop every tag, import tags.yaml, and the
    taxonomy is back -- field for field, parents and memberships included."""
    _seed(client)
    async with client.db() as s:
        client.post("/tags/1/members", data={"member_tag_id": 2})
    entries = _entries(client.get("/admin/export.zip").content)

    async with client.db() as s:
        before = {
            t.slug: (t.name, t.name_en, t.name_zh, t.kind, t.region, t.city, t.address)
            for t in (await s.execute(select(Tag))).scalars()
        }
        for tag in (await s.execute(select(Tag))).scalars():
            await s.delete(tag)
        await s.commit()

    client.post("/admin/import/tags", data={"text": entries["tags.yaml"]})

    async with client.db() as s:
        after = {
            t.slug: (t.name, t.name_en, t.name_zh, t.kind, t.region, t.city, t.address)
            for t in (await s.execute(select(Tag))).scalars()
        }
    assert after == before
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --isolated pytest tests/test_catalogue_export.py -q`
Expected: FAIL — 404 on `/admin/export.zip`.

- [ ] **Step 3: Extract `concert_export_yaml` into `db/service.py`**

Move the ORM→YAML assembly out of `export_concert_yaml` verbatim (the `days`
query with `selectinload(ConcertDay.venue_tag)`, the `YamlDay`/`YamlRound`
building, the `concert_to_yaml` call including the Task 4 and 5 additions) into:

```python
async def concert_export_yaml(session: AsyncSession, concert: Concert) -> str:
    """One concert as a draft-vocabulary YAML document.

    Shared by GET /concerts/{event_id}/export.yaml and the admin catalogue zip,
    which must not drift -- a restore file that differs from the one an editor
    downloads is a second format nobody agreed to.

    Loads the legs with their venue_tag eagerly: ConcertDay.venue_tag is
    lazy="raise", so a missed selectinload here is a MissingGreenlet 500 rather
    than a slow export. Emits the tags' CANONICAL columns, never loc() -- an
    export is data, and its contents must not change with whoever downloaded it.
    """
```

Then `export_concert_yaml` becomes a thin route: fetch the concert, refresh
`["days", "rounds", "tags"]`, `text = await concert_export_yaml(session, concert)`,
return the same `Response`.

- [ ] **Step 4: Add `catalogue_export_files`**

```python
_RESTORE_NOTES = """\
dekimasen.app catalogue export

WHAT IS IN HERE
  tags.yaml            every tag, with its handle
  concerts/*.yaml      one draft per concert, named by its event_id
  RESTORE.txt          this file

NO PERSONAL DATA. This is built from the catalogue tables only -- it never
touches users, sessions, outcomes, subscriptions, reminders or delivery logs --
and it does not record who created anything.

HOW TO PUT IT BACK, IN THIS ORDER
  1. Paste tags.yaml into /admin/import/tags. Tags that already exist are
     skipped, never updated, so this is safe to run twice.
  2. Paste each concerts/*.yaml into /concerts/import (the "paste a draft" box)
     and commit the preview.

ORDER MATTERS. Tags first: a concert draft refers to its tags by handle, and a
handle that does not exist yet cannot be bound.

A concert that still exists will answer 409 rather than be duplicated. That is
deliberate -- it is how re-importing a file you already restored announces
itself.
"""


async def catalogue_export_files(session: AsyncSession) -> list[tuple[str, str]]:
    """(path in zip, text) for the whole catalogue, deterministically ordered.

    Catalogue tables ONLY -- concerts, days, rounds, qualifiers, tags,
    tag_members. Never a JOIN to a user table, and `created_by` is never
    emitted: nothing to leak beats a filter to get wrong.
    """
    tags = list((await session.execute(
        select(Tag).options(selectinload(Tag.members)).order_by(Tag.kind, Tag.slug)
    )).scalars())
    by_id = {t.id: t for t in tags}
    exports = [
        TagExport(
            handle=t.slug, name=t.name, kind=t.kind.value,
            name_en=t.name_en, name_zh=t.name_zh,
            parent=by_id[t.parent_id].slug if t.parent_id in by_id else None,
            members=tuple(m.slug for m in sorted(t.members, key=lambda m: m.slug)),
            region=t.region, city=t.city, city_en=t.city_en, city_zh=t.city_zh,
            address=t.address, location_url=t.location_url,
            eventernote_url=t.eventernote_url,
        )
        for t in tags
    ]
    files = [("tags.yaml", tags_to_yaml(exports)), ("RESTORE.txt", _RESTORE_NOTES)]

    concerts = list((await session.execute(
        select(Concert)
        .options(selectinload(Concert.tags), selectinload(Concert.rounds))
        .order_by(Concert.event_id)
    )).scalars())
    for concert in concerts:
        files.append(
            (f"concerts/{concert.event_id}.yaml", await concert_export_yaml(session, concert))
        )
    return files
```

Add `TagExport`/`tags_to_yaml` to the `app.domain.tags_yaml` import in
`service.py`.

- [ ] **Step 5: Add the route**

In `admin.py`:

```python
@router.get("/admin/export.zip")
async def export_zip(
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """The whole catalogue, zipped at request time.

    Same shape as GET /concerts/import/skill.zip: a committed binary would go
    stale, and a few hundred KB of YAML is not worth a thread hop.

    Every entry goes through an explicit ZipInfo pinned to the 1980
    reproducible-build epoch. ZipFile.writestr otherwise stamps the current
    time, and zip timestamps have two-second resolution -- so two exports
    seconds apart would differ in bytes while every file inside was identical,
    which would quietly cost the file its diffability.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, text in await catalogue_export_files(session):
            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            zf.writestr(info, text)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="dekimasen-catalogue.zip"'},
    )
```

Add `io`, `zipfile`, `Response` and `catalogue_export_files` to `admin.py`'s
imports.

- [ ] **Step 6: Link it from Preferences**

Beside the Import-tags row added in Task 3:

```html
      <span class="nm3"><a href="/admin/export.zip">Export catalogue</a></span>
```

- [ ] **Step 7: Run tests, lint, commit**

```bash
uv run --isolated pytest tests/test_catalogue_export.py -q
uv run --isolated ruff check . && uv run --isolated pytest -q
git add src tests
git commit -m "feat: GET /admin/export.zip, the whole catalogue and nothing personal"
```

---

### Task 7: docs and close-out

**Files:**
- Modify: `CLAUDE.md`, `WISHLIST.md`, `README.md`
- Modify: `docs/superpowers/specs/2026-07-30-catalogue-round-trip-design.md` (status + deviations)

- [ ] **Step 1: CLAUDE.md**

In the `src/app/domain/` bullet, add `tags_yaml.py` beside `yaml_export.py`/
`yaml_import.py`, noting both halves live in one module and why. In the
`src/app/web/` bullet, add the two admin routes. Add to the tag-identity
paragraph (invariant 3's neighbourhood) that the catalogue export/import key on
`slug`, that an import SKIPS an existing handle and never updates, and that
`create_tag_row` is the single tag-construction site — `slug=None` mints,
a value is used verbatim.

- [ ] **Step 2: WISHLIST.md**

Move **#1** to Shipped with today's date, describing the export, the tags
import, the three new draft keys, and the two decisions (skip-not-update;
tags-only in bulk). Then do the full revision pass the CLAUDE.md rule requires:
renumber the remaining Proposed entries, and re-read each against what shipped —
note in particular whether Eventernote actor-page discovery got cheaper now that
a sweep can be told which tags exist.

- [ ] **Step 3: README.md**

Add a bullet to "Shipped since Phase 12": the admin catalogue export and tags
import, keyed on handles, with no personal data by construction.

- [ ] **Step 4: Spec close-out**

Set the spec's `Status:` to `implemented (2026-07-30)` and record every
deviation at its foot, with the reason. If nothing deviated, say so explicitly —
an empty deviations section is information.

- [ ] **Step 5: Final gates and commit**

```bash
uv run --isolated ruff check . && uv run --isolated pytest -q
git add docs CLAUDE.md WISHLIST.md README.md
git commit -m "docs: close out the catalogue round-trip"
```

---

## Self-review notes

**Spec coverage.** Export route and zip determinism → Task 6. `tags.yaml` format
→ Tasks 1 and 6. `RESTORE.txt` → Task 6. `event_id` round-trip → Task 4.
`series_handles`/`venue_handle` and handle-wins → Task 5. Dropping the `slug`
key while still tolerating it → Task 4. Import route, no-preview, report → Task
3. Two passes, skip-not-update, every warning row → Tasks 1 and 2. Invariants 3
and 4 → Task 2. The extracted writer → Task 2. Module layout → Tasks 1, 2, 6.
Every test named in the spec appears in a task. Out-of-scope items are absent.

**Deliberate ordering.** Tasks 1–3 stand alone and deliver a working importer
before anything touches the concert draft. Task 4 precedes Task 5 because both
extend `ParsedConcert` and `concert_to_yaml`, and event_id is the simpler of the
two to get right first. Task 6 comes last because it consumes every earlier
piece, and its round-trip test is the only one that can fail for a reason
belonging to another task.

**A rule this review had to settle.** The first draft of Task 5 resolved handles
AND names and merged the results, and I could not state its behaviour in one
sentence — which is the tell. Two tags, a handle pointing at one and a name at
the other, and it selected both. The rule is now: **a handles block is
authoritative for its kind, and the name list is ignored outright; a missing
handle does NOT fall back to its name.** No fallback is the more correct answer,
not merely the simpler one — falling back would re-introduce the first-tag-wins
guess this entire arc exists to remove. The four tests are the four cases:
handles present and resolving, handles present and missing, no handles block at
all, and a handle disagreeing with a name.

**Known risks.** Task 6's `concert_export_yaml` extraction must be verbatim: if
the per-concert route's output changes, `tests/test_yaml_export.py` and the
add-concert skill's pinned example are what will say so. Task 2's second pass
writes `TagMember` rows directly rather than through `attach_tag`, which is
correct — `attach_tag` is about CONCERT attachment and would drag invariant 3's
expansion into a place that must not touch concerts — but it is the one spot
where a reviewer should check that no concert was harmed.
