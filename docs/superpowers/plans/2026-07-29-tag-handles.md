# Tag Handles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every tag a stable, unique handle (`Tag.slug`) so tags have an identity that is not their name, and drop name uniqueness entirely so two performers can share a name.

**Architecture:** A new pure `domain/slugs.py` holds the slug primitive shared by concerts and tags. `Tag.slug` becomes the unique column; `Tag.name` stops being unique. One migration adds and backfills the handle and drops the legacy anonymous `UNIQUE (name)`. The three create routes stop rejecting duplicate names (a client-side warning replaces the 409, which moves to the handle), and the picker shows a handle only where a same-kind name collision actually exists.

**Tech Stack:** Python 3.14, SQLAlchemy 2.0 async + Alembic (SQLite batch mode), FastAPI + Jinja2, pytest-asyncio, babel gettext (ja/zh).

**Spec:** `docs/superpowers/specs/2026-07-29-tag-handles-design.md`

**Branch:** `tag-handles` (already exists, off `main`, carrying the spec commit).

## Global Constraints

- `uv run --isolated pytest -q` MUST pass before any commit. Use `--isolated`: an external `serve.py` can lock `.venv`.
- `uv run --isolated ruff check .` MUST be clean before any commit. Line length 100.
- Every new user-visible template string needs BOTH `src/app/translations/{ja,zh}/LC_MESSAGES/messages.po` filled in, or `tests/test_i18n_catalogues.py` fails (fuzzy counts as untranslated). Route `HTTPException` details are NOT translated in this app — do not wrap them in `_()`.
- After autogenerating any migration: replace `app.db.models.UTCDateTime()` with `sa.DateTime()` and delete the `import app.db.models` line. This migration is hand-written, so it does not apply, but do not reintroduce it.
- Migrations must stay frozen in time: do NOT import helpers from `app.domain` into a revision. `from app.db.models import NAMING_CONVENTION` is the one sanctioned exception and is the existing house pattern (see `789bbcc95bc3`).
- DB stores aware UTC only. Nothing in this plan touches datetimes.
- Sentence case in all UI copy ("Create anyway", not "Create Anyway").
- Alembic head at plan time: `aebefef6ca70`. New revision must set `down_revision = "aebefef6ca70"`.

---

### Task 1: Extract the slug primitive into `domain/slugs.py`

`slugify` ends in `return slug or "concert"`. That fallback is concert-specific and, worse, indistinguishable from a tag legitimately named "Concert" — so tags cannot reuse it. Split the primitive out without changing concert behaviour.

**Files:**
- Create: `src/app/domain/slugs.py`
- Modify: `src/app/domain/yaml_export.py:48-52` (slugify delegates)
- Test: `tests/test_slugs.py` (create)
- Test: `tests/test_yaml_export.py:18-21` (must keep passing UNCHANGED)

**Interfaces:**
- Consumes: nothing.
- Produces: `slug_core(text: str) -> str` (may return `""`), `tag_slug_base(name: str, name_en: str | None) -> str` (may return `""`). Both pure, no I/O. Task 2 and Task 3 both use them.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_slugs.py
"""The slug primitive shared by concert event_ids and tag handles."""

from app.domain.slugs import slug_core, tag_slug_base


def test_slug_core_has_no_fallback():
    """The whole reason this exists: slugify() returns "concert" for input with
    no ASCII, which a tag cannot use -- it is indistinguishable from a tag
    really named "Concert"."""
    assert slug_core("Hasunosora 5th Live!") == "hasunosora-5th-live"
    assert slug_core("  Multiple   Spaces  ") == "multiple-spaces"
    assert slug_core("日本語タイトル") == ""
    assert slug_core("") == ""
    assert slug_core("---") == ""


def test_tag_slug_base_prefers_english():
    """name_en is mandatory at every tag create boundary, so it is reliably
    there for new tags; `name` is the fallback for rows predating that rule."""
    assert tag_slug_base("蓮ノ空", "Hasunosora") == "hasunosora"
    assert tag_slug_base("Zepp Haneda", None) == "zepp-haneda"
    assert tag_slug_base("Zepp Haneda", "") == "zepp-haneda"
    assert tag_slug_base("Zepp Haneda", "   ") == "zepp-haneda"


def test_tag_slug_base_empty_when_nothing_is_ascii():
    """Caller supplies the {kind}-{id} fallback, which needs a flushed row --
    so this returns "" rather than inventing something."""
    assert tag_slug_base("蓮ノ空", None) == ""
    assert tag_slug_base("蓮ノ空", "スクールアイドル") == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --isolated pytest tests/test_slugs.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.domain.slugs'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/app/domain/slugs.py
"""URL/handle slug primitives. Pure: no I/O, no ORM.

Its own module because two unrelated consumers need it -- concert `event_id`
generation (via yaml_export.slugify) and tag handles -- and neither is a
natural home for the other's fallback rule.
"""

import re


def slug_core(text: str) -> str:
    """'Hasunosora 5th Live!' -> 'hasunosora-5th-live'. NO fallback.

    Returns "" when nothing survives, which is the whole point: everything
    outside [a-z0-9] is stripped, so a Japanese-only string empties out and the
    CALLER decides what that means. slugify() below answers "concert"; a tag
    answers "{kind}-{id}". A shared fallback could not serve both, and worse,
    "concert" is indistinguishable from a real title of that name.
    """
    lowered = text.strip().lower()
    return re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")


def tag_slug_base(name: str, name_en: str | None) -> str:
    """A tag handle's base, before de-duplication: English name if it yields
    anything, else the canonical name, else "".

    name_en first because the trilingual rule makes it mandatory at every tag
    create boundary, so it is reliably present on new rows; `name` covers the
    older rows that predate that rule. "" means the caller must fall back to
    {kind}-{id}, which needs a flushed row and so cannot happen here.
    """
    return slug_core(name_en or "") or slug_core(name)
```

- [ ] **Step 4: Point `slugify` at it, changing nothing for concerts**

```python
# src/app/domain/yaml_export.py -- replace the slugify definition
from app.domain.slugs import slug_core


def slugify(title: str) -> str:
    """'Hasunosora 5th Live!' -> 'hasunosora-5th-live'.

    Thin wrapper over slug_core with the concert-specific fallback. Kept as the
    public name because generate_event_id and two Content-Disposition headers
    import it.
    """
    return slug_core(title) or "concert"
```

Delete the now-unused `import re` from `yaml_export.py` ONLY if no other use remains — check with `grep -n "re\." src/app/domain/yaml_export.py` first.

- [ ] **Step 5: Run both test files**

Run: `uv run --isolated pytest tests/test_slugs.py tests/test_yaml_export.py tests/test_imports.py -q`
Expected: PASS. `test_yaml_export.py::test_slugify` (including `slugify("日本語タイトル") == "concert"`) must pass **unmodified** — that is the proof the split is behaviour-preserving.

- [ ] **Step 6: Commit**

```bash
git add src/app/domain/slugs.py src/app/domain/yaml_export.py tests/test_slugs.py
git commit -m "refactor: extract slug_core, so a tag handle need not fall back to 'concert'"
```

---

### Task 2: Add `Tag.slug` to the model and a service-side assigner

Model-and-service only. No route uses it yet; no migration yet. Tests build from `Base.metadata`, so this task's tests already run against the final schema.

**Files:**
- Modify: `src/app/db/models.py:230` (drop `unique=True` from `name`), and add `slug` after it
- Modify: `src/app/db/service.py` (Tags section, near `find_tag_by_name_and_kind` at ~4093)
- Test: `tests/test_tag_handles.py` (create)

**Interfaces:**
- Consumes: `tag_slug_base` from Task 1.
- Produces: `async def assign_tag_slug(session: AsyncSession, tag: Tag) -> str` — sets and returns `tag.slug`; call AFTER `session.add(tag)` and before `commit`. Tasks 4 and 5 call it.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tag_handles.py
"""Tag handles: a stable identity that is not the name.

Spec: docs/superpowers/specs/2026-07-29-tag-handles-design.md
"""

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Tag
from app.db.service import assign_tag_slug
from app.domain.types import TagKind


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


async def _add(s, **kw) -> Tag:
    tag = Tag(**kw)
    s.add(tag)
    await assign_tag_slug(s, tag)
    return tag


async def test_handle_comes_from_the_english_name(db):
    async with db() as s:
        tag = await _add(s, name="蓮ノ空", name_en="Hasunosora", kind=TagKind.GROUP)
        await s.commit()
        assert tag.slug == "hasunosora"


async def test_handle_falls_back_to_the_canonical_name(db):
    async with db() as s:
        tag = await _add(s, name="Zepp Haneda", kind=TagKind.VENUE)
        await s.commit()
        assert tag.slug == "zepp-haneda"


async def test_japanese_only_name_gets_an_honest_placeholder(db):
    """NOT "concert" -- that is slugify's fallback and would be a lie here."""
    async with db() as s:
        tag = await _add(s, name="蓮ノ空", kind=TagKind.ARTIST)
        await s.commit()
        assert tag.slug == f"artist-{tag.id}"


async def test_colliding_handles_get_a_numeric_suffix(db):
    async with db() as s:
        a = await _add(s, name="Yuki Sato", kind=TagKind.ARTIST)
        b = await _add(s, name="Yuki Sato", kind=TagKind.ARTIST)
        c = await _add(s, name="yuki sato", kind=TagKind.ARTIST)
        await s.commit()
        assert [a.slug, b.slug, c.slug] == ["yuki-sato", "yuki-sato-2", "yuki-sato-3"]


async def test_two_performers_may_share_a_name(db):
    """The requirement that killed kind-scoped name uniqueness."""
    async with db() as s:
        await _add(s, name="Yuki Sato", kind=TagKind.ARTIST)
        await _add(s, name="Yuki Sato", kind=TagKind.ARTIST)
        await s.commit()
    async with db() as s:
        rows = list((await s.execute(select(Tag).where(Tag.name == "Yuki Sato"))).scalars())
        assert len(rows) == 2
        assert len({r.slug for r in rows}) == 2


async def test_a_venue_may_share_a_name_with_a_group(db):
    """The owner ruling that was documented but never implemented -- this is
    the shape that used to 500."""
    async with db() as s:
        await _add(s, name="Aqours", kind=TagKind.GROUP)
        await _add(s, name="Aqours", kind=TagKind.VENUE)
        await s.commit()
    async with db() as s:
        assert len(list((await s.execute(select(Tag))).scalars())) == 2


async def test_the_handle_itself_is_still_unique(db):
    from sqlalchemy.exc import IntegrityError

    async with db() as s:
        s.add(Tag(name="A", kind=TagKind.ARTIST, slug="dup"))
        s.add(Tag(name="B", kind=TagKind.ARTIST, slug="dup"))
        with pytest.raises(IntegrityError):
            await s.commit()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --isolated pytest tests/test_tag_handles.py -q`
Expected: FAIL — `ImportError: cannot import name 'assign_tag_slug'`

- [ ] **Step 3: Change the model**

In `src/app/db/models.py`, replace line 230 and the comment block above `name_en`:

```python
    id: Mapped[int] = mapped_column(primary_key=True)
    # The stable identity, and the ONLY unique column here. Names are NOT
    # unique -- two performers may genuinely share one, and a venue may share a
    # name with a group (owner ruling, 2026-07-29) -- so a name cannot identify
    # a tag and nothing may treat it as if it could. Auto-generated, editable,
    # ASCII by construction, and deliberately absent from every URL: tag pages
    # stay on the numeric id. This is what export/import key on.
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    # Viewer-locale variants of the tag name. Like `name`, not unique.
    name_en: Mapped[str | None] = mapped_column(String(100))
    name_zh: Mapped[str | None] = mapped_column(String(100))
```

- [ ] **Step 4: Add the assigner to `db/service.py`**

Place it in the `# ── Tags ──` section, immediately above `find_tag_by_name_and_kind`:

```python
async def assign_tag_slug(session: AsyncSession, tag: Tag) -> str:
    """Give `tag` a unique handle. Call after `session.add(tag)`, before commit.

    The handle is a tag's identity (names are not unique), so this is the single
    place one is minted -- every create path goes through it.

    Falls back to `{kind}-{id}`, which needs the row's id, so this FLUSHES when
    the name yields no ASCII. That is why it takes a session at all rather than
    being pure alongside `tag_slug_base`.

    De-duplication queries the DB and also inspects the pending session, because
    a caller may add several tags before committing (the import in sub-project C
    will) and two pending rows must not agree on a handle.
    """
    base = tag_slug_base(tag.name, tag.name_en)
    if not base:
        await session.flush()  # need the id for the placeholder
        base = f"{tag.kind.value}-{tag.id}"
    taken = {
        s for (s,) in await session.execute(select(Tag.slug).where(Tag.slug.is_not(None)))
    }
    taken |= {t.slug for t in session.new if isinstance(t, Tag) and t.slug}
    candidate, suffix = base, 2
    while candidate in taken:
        candidate = f"{base}-{suffix}"
        suffix += 1
    tag.slug = candidate
    return candidate
```

Add `tag_slug_base` to the `app.domain.slugs` imports at the top of `service.py`.

- [ ] **Step 5: Run the tests**

Run: `uv run --isolated pytest tests/test_tag_handles.py -q`
Expected: PASS, all eight.

- [ ] **Step 6: Run the whole suite to see what the model change breaks**

Run: `uv run --isolated pytest -q 2>&1 | tail -30`
Expected: FAILURES — every test that constructs a `Tag(...)` directly now violates `slug NOT NULL`. That list is the real work of this step. Fix each by routing through `assign_tag_slug` where the test is exercising real behaviour, or by passing an explicit `slug="..."` where the tag is inert scaffolding. Do NOT make `slug` nullable to dodge this.

- [ ] **Step 7: Run suite and lint**

Run: `uv run --isolated pytest -q` then `uv run --isolated ruff check .`
Expected: both clean.

- [ ] **Step 8: Commit**

```bash
git add src/app/db/models.py src/app/db/service.py tests/
git commit -m "feat: Tag.slug, the identity a tag name never was"
```

---

### Task 3: The migration

**Files:**
- Create: `alembic/versions/<rev>_tag_handles.py`
- Modify: `tests/test_migration_legacy_anonymous_constraints.py` (add current-shape `tags` DDL + a test for this revision)

**Interfaces:**
- Consumes: nothing at runtime (the slug rule is inlined, per Global Constraints).
- Produces: schema matching Task 2's model. No Python surface.

- [ ] **Step 1: Write the failing migration test**

Append to `tests/test_migration_legacy_anonymous_constraints.py`. It already has the fixture plumbing (`_alembic_config`, `_table_sql`); this adds a SECOND, current-shape DDL because the existing `LEGACY_DDL`'s `tags` table predates `name_en`/`city`/`address`.

```python
# Current-shape legacy tags: every column the app has today, but still the
# ANONYMOUS `UNIQUE (name)` a metadata-built DB never reproduces. This is the
# shape the tag-handles revision actually meets on the server.
LEGACY_TAGS_CURRENT = """
CREATE TABLE "users" (
  discord_id BIGINT NOT NULL, username VARCHAR(100) NOT NULL,
  PRIMARY KEY (discord_id));
CREATE TABLE "tags" (
  id INTEGER NOT NULL, name VARCHAR(100) NOT NULL, kind VARCHAR(9) NOT NULL,
  created_by BIGINT, created_at DATETIME NOT NULL,
  parent_id INTEGER, location_url VARCHAR(500), region VARCHAR(100),
  name_en VARCHAR(100), name_zh VARCHAR(100), city VARCHAR(100),
  city_en VARCHAR(100), city_zh VARCHAR(100), address VARCHAR(300),
  eventernote_url VARCHAR(500),
  PRIMARY KEY (id),
  CONSTRAINT fk_tags_parent_id FOREIGN KEY(parent_id) REFERENCES tags (id) ON DELETE SET NULL,
  UNIQUE (name), FOREIGN KEY(created_by) REFERENCES users (discord_id));
CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL,
  CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num));
"""

TAG_HANDLES_REV = "REPLACE_WITH_REVISION_ID"


def test_tag_handles_migration_on_legacy_anonymous_unique(tmp_path, monkeypatch):
    """The anonymous UNIQUE (name) must be droppable, and every existing row
    must come out with a distinct handle. A metadata-built fixture cannot see
    this: there the constraint is named, so drop_constraint trivially works."""
    db_path = tmp_path / "legacy.db"
    con = sqlite3.connect(db_path)
    con.executescript(LEGACY_TAGS_CURRENT)
    con.execute("INSERT INTO users (discord_id, username) VALUES (1, 'reiji')")
    con.executemany(
        "INSERT INTO tags (id, name, name_en, kind, created_by, created_at)"
        " VALUES (?, ?, ?, ?, 1, '2026-01-01 00:00:00')",
        [
            (1, "蓮ノ空", "Hasunosora", "group"),
            (2, "Zepp Haneda", None, "venue"),
            (3, "スクールアイドル", None, "artist"),   # no ASCII anywhere -> placeholder
            (4, "Yuki Sato", "Yuki Sato", "artist"),
            (5, "yuki sato", "Yuki Sato", "artist"),   # collides on the handle
        ],
    )
    con.execute("INSERT INTO alembic_version (version_num) VALUES ('aebefef6ca70')")
    con.commit()
    con.close()

    cfg = _alembic_config(monkeypatch, db_path)
    command.upgrade(cfg, TAG_HANDLES_REV)

    con = sqlite3.connect(db_path)
    sql = _table_sql(con, "tags")
    assert "UNIQUE (name)" not in sql          # the anonymous one is gone
    assert "uq_tags_slug" in sql or "slug" in sql

    rows = dict(con.execute("SELECT id, slug FROM tags"))
    assert rows[1] == "hasunosora"
    assert rows[2] == "zepp-haneda"
    assert rows[3] == "artist-3"               # NOT "concert"
    assert len(set(rows.values())) == 5        # every handle distinct
    assert None not in rows.values()

    # names may now collide; handles may not
    con.execute("INSERT INTO tags (id, name, kind, created_by, created_at, slug)"
                " VALUES (6, 'Yuki Sato', 'artist', 1, '2026-01-01 00:00:00', 'yuki-sato-9')")
    con.commit()
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO tags (id, name, kind, created_by, created_at, slug)"
                    " VALUES (7, 'Whatever', 'artist', 1, '2026-01-01 00:00:00', 'yuki-sato-9')")
```

Ensure `import pytest` and `import sqlite3` are present at the top of that file.

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --isolated pytest tests/test_migration_legacy_anonymous_constraints.py -q`
Expected: FAIL — the revision does not exist (`Can't locate revision`).

- [ ] **Step 3: Hand-write the revision**

Create `alembic/versions/<rev>_tag_handles.py`. Generate the id with `uv run --isolated alembic revision -m "tag handles"` to get a real file and revision id, then replace its body wholesale with this (do NOT autogenerate — autogenerate cannot express the backfill, and would try to drop the anonymous constraint by a name that does not exist in the DB).

```python
"""tag handles: add slug, drop the global unique on name

Revision ID: <keep the generated id>
Revises: aebefef6ca70
Create Date: <keep>

`tags` is one of the two legacy tables whose constraints are ANONYMOUS -- the
live DB has a bare `UNIQUE (name)`, while every test DB built from
Base.metadata has a named one. Passing naming_convention into
batch_alter_table is what lets reflection name it so drop_constraint can find
it; without it this dies on the server with "No such constraint", which this
project has shipped once.

The slug rule is INLINED rather than imported from app.domain.slugs: a
revision must keep working after the application changes underneath it.
"""

import re

import sqlalchemy as sa
from alembic import op

from app.db.models import NAMING_CONVENTION

revision = "<keep the generated id>"
down_revision = "aebefef6ca70"
branch_labels = None
depends_on = None


def _slug_core(text: str) -> str:
    """Frozen copy of app.domain.slugs.slug_core. Deliberately duplicated."""
    return re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")


def upgrade() -> None:
    # 1. Nullable first: the values do not exist yet.
    with op.batch_alter_table("tags", schema=None, naming_convention=NAMING_CONVENTION) as b:
        b.add_column(sa.Column("slug", sa.String(length=100), nullable=True))

    # 2. Backfill in PYTHON, not SQL. The name_en/name preference, the
    #    {kind}-{id} placeholder and the numeric de-duplication are all Python
    #    logic, and SQLite's lower()/trim() are ASCII-and-U+0020-only -- not to
    #    be trusted with the Japanese data this table is full of.
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, name, name_en, kind FROM tags ORDER BY id")
    ).fetchall()
    used: set[str] = set()
    for row in rows:
        base = _slug_core(row.name_en) or _slug_core(row.name) or f"{row.kind}-{row.id}"
        candidate, suffix = base, 2
        while candidate in used:
            candidate = f"{base}-{suffix}"
            suffix += 1
        used.add(candidate)
        conn.execute(
            sa.text("UPDATE tags SET slug = :slug WHERE id = :id"),
            {"slug": candidate, "id": row.id},
        )

    # 3+4. One rebuild for all three structural changes: drop the anonymous
    #      unique on name, make slug NOT NULL, make slug unique.
    with op.batch_alter_table("tags", schema=None, naming_convention=NAMING_CONVENTION) as b:
        b.drop_constraint("uq_tags_name", type_="unique")
        b.alter_column("slug", existing_type=sa.String(length=100), nullable=False)
        b.create_unique_constraint("uq_tags_slug", ["slug"])


def downgrade() -> None:
    """Will FAIL if the new freedom has been used, and that is correct: two
    tags sharing a name cannot go back to a unique name column. Restore from
    backup instead of trying to force it."""
    with op.batch_alter_table("tags", schema=None, naming_convention=NAMING_CONVENTION) as b:
        b.drop_constraint("uq_tags_slug", type_="unique")
        b.drop_column("slug")
        b.create_unique_constraint("uq_tags_name", ["name"])
```

- [ ] **Step 4: Put the real revision id in the test**

Replace `TAG_HANDLES_REV = "REPLACE_WITH_REVISION_ID"` with the generated id.

- [ ] **Step 5: Run the migration test**

Run: `uv run --isolated pytest tests/test_migration_legacy_anonymous_constraints.py -q`
Expected: PASS. If `drop_constraint` raises `No such constraint`, the `naming_convention` argument is missing or misspelled — that is the single most likely failure and the whole reason this test exists.

- [ ] **Step 6: Apply it locally and check the real DB**

```bash
uv run --isolated alembic upgrade head
uv run --isolated python -c "import sqlite3; c=sqlite3.connect('app.db'); print(c.execute(\"SELECT sql FROM sqlite_master WHERE name='tags'\").fetchone()[0]); print(list(c.execute('SELECT id, name, slug FROM tags LIMIT 10')))"
```
Expected: no `UNIQUE (name)`, a `slug` column, every row with a distinct non-null handle.

- [ ] **Step 7: Run suite and lint, then commit**

```bash
uv run --isolated pytest -q && uv run --isolated ruff check .
git add alembic/versions tests/test_migration_legacy_anonymous_constraints.py
git commit -m "feat: migrate tags to handles, dropping the anonymous unique on name"
```

---

### Task 4: Create paths stop rejecting duplicate names

**Files:**
- Modify: `src/app/db/service.py` — rename `find_tag_by_name_and_kind` -> `find_tags_by_name_and_kind` (plural, returns a list); DELETE `find_tag_by_name`; adapt the rehearsal seed at ~5837
- Modify: `src/app/web/routes/tags.py:109-150` (`create_tag`), `:153-222` (`quick_create_venue`), `:224-294` (`quick_create_tag`)
- Modify: `tests/test_rehearsal.py:30,731`
- Modify: `CLAUDE.md` (the tag-identity rule)
- Test: `tests/test_tags.py`, `tests/test_tag_handles.py`

**Interfaces:**
- Consumes: `assign_tag_slug` (Task 2).
- Produces: `async def find_tags_by_name_and_kind(session, name: str, kind: TagKind) -> list[Tag]`. Task 5 and sub-project C use it. `find_tag_by_name` no longer exists.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_tags.py`:

```python
def test_creating_a_venue_named_like_a_group_no_longer_500s(client):
    """The measured bug: the routes' duplicate check was kind-scoped while the
    column's UNIQUE was global, so this passed the check and then died on an
    unhandled IntegrityError with the editor's input lost."""
    login_as(client, EDITOR_ID, "reiji")
    assert client.post("/tags", data={
        "name": "Aqours", "name_en": "Aqours", "name_zh": "Aqours", "kind": "group",
    }).status_code == 303
    r = client.post("/tags", data={
        "name": "Aqours", "name_en": "Aqours", "name_zh": "Aqours", "kind": "venue",
    })
    assert r.status_code == 303


def test_creating_a_case_variant_across_kinds_no_longer_poisons_lookups(client):
    """The nastier half: this INSERT used to succeed, and from then on any
    name lookup raised MultipleResultsFound -- a working page starts 500ing."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={
        "name": "Aqours", "name_en": "Aqours", "name_zh": "Aqours", "kind": "group",
    })
    assert client.post("/tags", data={
        "name": "aqours", "name_en": "Aqours", "name_zh": "Aqours", "kind": "venue",
    }).status_code == 303
    assert client.get("/tags").status_code == 200


def test_two_artists_with_the_same_name_are_both_creatable(client):
    login_as(client, EDITOR_ID, "reiji")
    for _ in range(2):
        assert client.post("/tags", data={
            "name": "Yuki Sato", "name_en": "Yuki Sato", "name_zh": "佐藤有紀",
            "kind": "artist",
        }).status_code == 303
```

Add to `tests/test_tag_handles.py`:

```python
async def test_find_tags_by_name_and_kind_returns_every_match(db):
    from app.db.service import find_tags_by_name_and_kind

    async with db() as s:
        await _add(s, name="Yuki Sato", kind=TagKind.ARTIST)
        await _add(s, name="yuki sato", kind=TagKind.ARTIST)
        await _add(s, name="Yuki Sato", kind=TagKind.VENUE)
        await s.commit()
    async with db() as s:
        found = await find_tags_by_name_and_kind(s, "YUKI SATO", TagKind.ARTIST)
        assert len(found) == 2            # case-insensitive, kind-scoped
        assert all(t.kind is TagKind.ARTIST for t in found)


async def test_find_tag_by_name_is_gone(db):
    """A name-only single-result lookup over a non-unique column raises
    MultipleResultsFound by construction, so it must not exist to be called."""
    import app.db.service as service

    assert not hasattr(service, "find_tag_by_name")
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --isolated pytest tests/test_tags.py -q -k "500s or poisons or same_name" tests/test_tag_handles.py -q`
Expected: FAIL — the create routes still 409/500, `find_tags_by_name_and_kind` does not exist.

- [ ] **Step 3: Change the service layer**

In `src/app/db/service.py`: delete `find_tag_by_name` entirely, and replace `find_tag_by_name_and_kind` with:

```python
async def find_tags_by_name_and_kind(
    session: AsyncSession, name: str, kind: TagKind
) -> list[Tag]:
    """EVERY tag of this kind with this name, case-insensitively.

    Plural because names are not unique: two performers may share one (owner
    ruling, 2026-07-29). The single-result ancestors of this function --
    find_tag_by_name and find_tag_by_name_and_kind -- both raised
    MultipleResultsFound the moment that became possible, so neither survives.
    A caller wanting "one" must say which one it means.
    """
    from sqlalchemy import func as sa_func

    res = await session.execute(
        select(Tag).where(
            sa_func.lower(Tag.name) == name.strip().lower(), Tag.kind == kind
        ).order_by(Tag.id)
    )
    return list(res.scalars())
```

Adapt the rehearsal seed (~5837) — it wants *a* tag with that name, not *the* one:

```python
    existing = await find_tags_by_name_and_kind(session, REHEARSAL_TAG_NAME, TagKind.ARTIST)
    tag = existing[0] if existing else None
    if tag is None:
        tag = Tag(
            name=REHEARSAL_TAG_NAME,
            name_en="Rehearsal Artist",
            name_zh="彩排歌手",
            kind=TagKind.ARTIST,
            created_by=user_id,
        )
        session.add(tag)
        await assign_tag_slug(session, tag)
        await session.flush()
```

- [ ] **Step 4: Change the three create routes**

`create_tag` (`routes/tags.py`): delete the 409 block at 131-134 and its `find_tag_by_name_and_kind` import; add the slug after `session.add`. The whole point is that no name check remains:

```python
    # NO name-duplicate check. Names are not unique (owner ruling 2026-07-29):
    # two performers may share one, and a venue may share one with a group. The
    # dialog warns before submit; the handle is what must be unique, and
    # assign_tag_slug guarantees that.
    await ensure_user(session, user.id, user.username)
    tag = Tag(
        name=name, name_en=name_en.strip() or None, name_zh=name_zh.strip() or None,
        kind=kind, created_by=user.id, parent_id=parent.id if parent else None,
        location_url=form_url(location_url), region=region.strip() or None,
        eventernote_url=form_url(eventernote_url),
    )
    session.add(tag)
    await assign_tag_slug(session, tag)
    await session.commit()
    return RedirectResponse("/tags", status_code=303)
```

`quick_create_venue`: delete the 409 at 196-199, add `await assign_tag_slug(session, tag)` before commit, and update the docstring paragraph that promises a 409 on duplicate names — it now 409s on nothing.

`quick_create_tag`: delete the 409 block at 266-275 and add `await assign_tag_slug(session, tag)` before commit. Add `"slug": tag.slug` to the returned JSON. Rewrite the docstring's last paragraph, which currently documents the 409-carries-the-existing-id contract:

```python
    Duplicate NAMES are legal here and answer 200, not 409 -- the dialog warns
    before submit instead, which is both faster (no round trip) and the only
    thing that can work now that names are not unique. The 409 that used to
    live here has moved to the handle, which is the value that is actually
    unique.
```

- [ ] **Step 5: Update `tests/test_rehearsal.py`**

Line 30's import and line 731's call become `find_tags_by_name_and_kind`, taking `[0]`.

- [ ] **Step 6: Run the suite**

Run: `uv run --isolated pytest -q 2>&1 | tail -25`
Expected: the new tests pass; existing tests asserting a 409 on a duplicate tag name now FAIL. Those assertions are wrong by design — rewrite each to assert 303/200 and note why. Grep them out with `grep -rn "409" tests/test_tags.py tests/test_draft_import.py`.

- [ ] **Step 7: Update `CLAUDE.md`**

Under `## Non-negotiable invariants`, in the tag semantics area (invariant 3's neighbourhood), add:

```markdown
   **A tag is identified by its `slug`, never its name.** Names are NOT unique
   and never will be: two performers may genuinely share one, and a venue may
   share one with a group (owner ruling, 2026-07-29). `Tag.slug` is the unique
   column -- auto-generated from `name_en`/`name` via `assign_tag_slug`
   (`db/service.py`, the single minting path), editable, ASCII, and absent from
   every URL. Any code answering "do I already have this tag?" must ask by slug;
   a name match is a hint for a human, not an identity. There is deliberately NO
   single-result lookup by name -- `find_tags_by_name_and_kind` is plural, and
   its single-result ancestors were deleted because they raised
   `MultipleResultsFound` by construction once names could repeat.
```

- [ ] **Step 8: Run suite and lint, then commit**

```bash
uv run --isolated pytest -q && uv run --isolated ruff check .
git add src tests CLAUDE.md
git commit -m "feat: duplicate tag names are legal; the handle is what must be unique"
```

---

### Task 5: Editing a handle, and rename stops colliding on names

**Files:**
- Modify: `src/app/web/routes/tags.py:297-333` (`edit_tag`)
- Modify: `src/app/web/templates/tags.html:88-100` (edit dialog gains a Handle field)
- Modify: `src/app/translations/{ja,zh}/LC_MESSAGES/messages.po` (one new msgid)
- Modify: `tests/test_tags.py:761-772` (`test_rename_to_existing_name_still_409s` — deliberate reversal)

**Interfaces:**
- Consumes: `slug_core` (Task 1), `find_tags_by_name_and_kind` (Task 4).
- Produces: `POST /tags/{tag_id}/edit` accepts an optional `slug` form field.

- [ ] **Step 1: Write the failing tests**

Replace `test_rename_to_existing_name_still_409s` with:

```python
def test_renaming_onto_an_existing_name_is_now_allowed(client):
    """DELIBERATE REVERSAL of test_rename_to_existing_name_still_409s. Names
    are not unique any more (owner ruling 2026-07-29), so a rename collision is
    not an error -- the handle keeps the two tags apart."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={
        "name": "Aqours", "name_en": "Aqours", "name_zh": "Aqours", "kind": "group",
    })
    client.post("/tags", data={
        "name": "Hall", "name_en": "Hall", "name_zh": "Hall", "kind": "venue",
    })
    assert client.post("/tags/2/edit", data={"name": "aqours"}).status_code == 303


def test_editing_a_handle_onto_a_taken_one_409s(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={
        "name": "Aqours", "name_en": "Aqours", "name_zh": "Aqours", "kind": "group",
    })
    client.post("/tags", data={
        "name": "Hall", "name_en": "Hall", "name_zh": "Hall", "kind": "venue",
    })
    r = client.post("/tags/2/edit", data={"name": "Hall", "slug": "aqours"})
    assert r.status_code == 409


def test_a_submitted_handle_is_normalised_not_rejected(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={
        "name": "Hall", "name_en": "Hall", "name_zh": "Hall", "kind": "venue",
    })
    assert client.post("/tags/1/edit", data={
        "name": "Hall", "slug": "  Zepp Haneda  ",
    }).status_code == 303
    r = client.get("/tags")
    assert "zepp-haneda" in r.text


def test_a_handle_that_normalises_to_nothing_422s(client):
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={
        "name": "Hall", "name_en": "Hall", "name_zh": "Hall", "kind": "venue",
    })
    r = client.post("/tags/1/edit", data={"name": "Hall", "slug": "ホール"})
    assert r.status_code == 422
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --isolated pytest tests/test_tags.py -q -k "renaming or handle"`
Expected: FAIL — `edit_tag` has no `slug` parameter and still 409s on names.

- [ ] **Step 3: Rewrite `edit_tag`'s name/slug handling**

Replace lines 319-324 (the rename collision block) with:

```python
    name = name.strip()
    if name:
        # NO collision check: names are not unique (owner ruling 2026-07-29).
        # The handle below is the unique value, and the only 409 here.
        tag.name = name
    submitted_slug = slug.strip()
    if submitted_slug:
        normalised = slug_core(submitted_slug)
        if not normalised:
            # Nothing survives normalisation (all non-ASCII), so there is
            # nothing to store and nothing to fix on the editor's behalf.
            raise HTTPException(
                status_code=422,
                detail="a handle needs at least one letter or digit a-z 0-9",
            )
        if normalised != tag.slug:
            clash = await session.execute(
                select(Tag.id).where(Tag.slug == normalised, Tag.id != tag.id)
            )
            if clash.scalar_one_or_none() is not None:
                raise HTTPException(
                    status_code=409, detail=f"handle {normalised!r} is already taken"
                )
            tag.slug = normalised
```

Add `slug: str = Form("")` to the signature, import `slug_core` from `app.domain.slugs` and `select` if not already imported in that module.

- [ ] **Step 4: Add the field to the edit dialog**

In `src/app/web/templates/tags.html`, after the `name_zh` label (line ~99):

```html
      {#- The handle: a tag's identity for import/export, and what tells two
          same-named tags apart. Auto-generated, so it is shown only here (on
          edit) and never on the create form -- offering it up front invites
          bikeshedding over a value that does not matter until it collides. -#}
      <label class="fld"><span>{{ _("Handle") }}</span><input name="slug" maxlength="100"
        value="{{ t.slug }}"></label>
```

- [ ] **Step 5: Update both catalogues**

```bash
uv run --isolated pybabel extract -F babel.cfg -k N_ -o messages.pot .
uv run --isolated pybabel update -i messages.pot -d src/app/translations -l ja
uv run --isolated pybabel update -i messages.pot -d src/app/translations -l zh
```
Fill in by hand in both `.po` files — ja: `ハンドル`, zh: `标识符` — then `rm messages.pot`. Remove any `#, fuzzy` marker on the entry: fuzzy counts as untranslated.

- [ ] **Step 6: Run tests, lint, commit**

```bash
uv run --isolated pytest -q && uv run --isolated ruff check .
git add src tests
git commit -m "feat: edit a tag handle; renaming onto an existing name is fine now"
```

---

### Task 6: Show the handle where, and only where, names collide

**Files:**
- Modify: `src/app/db/service.py` (`tag_picker_context`, ~4490)
- Modify: `src/app/web/templates/_tag_picker_script.html:26-29, 36, 62, 125`
- Test: `tests/test_tag_handles.py`, plus the existing picker escaping tests must stay green

**Interfaces:**
- Consumes: nothing new.
- Produces: `tag_picker_context` return dict gains `"tag_disambiguators": dict[int, str]` — `{tag_id: slug}` present ONLY for tags sharing a `(lower(name), kind)` with another tag.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tag_handles.py
async def test_only_colliding_tags_get_a_disambiguator(db):
    from app.db.service import tag_picker_context

    async with db() as s:
        a = await _add(s, name="Yuki Sato", kind=TagKind.ARTIST)
        b = await _add(s, name="yuki sato", kind=TagKind.ARTIST)   # collides with a
        c = await _add(s, name="Kozue", kind=TagKind.ARTIST)       # unique
        d = await _add(s, name="Yuki Sato", kind=TagKind.VENUE)    # different kind
        await s.commit()
        ctx = await tag_picker_context(s)

    dis = ctx["tag_disambiguators"]
    assert set(dis) == {a.id, b.id}, "only the same-kind collision pair"
    assert dis[a.id] == a.slug
    assert c.id not in dis, "a unique name gets no visual noise"
    assert d.id not in dis, "a different kind is not a collision"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --isolated pytest tests/test_tag_handles.py -q -k disambiguator`
Expected: FAIL — `KeyError: 'tag_disambiguators'`

- [ ] **Step 3: Extend `tag_picker_context`**

Add before the `return`, and include the key in the returned dict:

```python
    # Which tags need their handle shown beside their name: only those sharing a
    # (name, kind) with another tag. Two identical chips would be unusable, but
    # showing every handle would add noise to the overwhelming majority that do
    # not collide. Decided HERE, not in the template's JS -- "are these the same
    # tag to a human" is a question about the data.
    seen: dict[tuple[str, str], list[int]] = {}
    for t in tags:
        seen.setdefault((t.name.strip().lower(), t.kind.value), []).append(t.id)
    tag_disambiguators = {
        tid: slug_by_id[tid]
        for ids in seen.values() if len(ids) > 1
        for tid in ids
    }
```

with `slug_by_id = {t.id: t.slug for t in tags}` alongside the existing `tag_names` line.

- [ ] **Step 4: Render it in the picker**

`_tag_picker_script.html` — the dialog chip list (lines 26-29). Keep `data-name` exactly as it is: `filterChips()` in `base.html` reads it, and the shared selector collides with `data-tag-name` (invariant 7).

```html
    {% for t in by_kind.get(kindname, []) %}
    <button type="button" class="chip kind-{{ kindname }}" data-name="{{ t.name | lower }}"
            data-id="{{ t.id }}" onclick="togglePick('{{ kindname }}', this)">{{ t.name
            }}{% if t.id in tag_disambiguators %} <small class="dis">{{ tag_disambiguators[t.id] }}</small>{% endif %}</button>
    {% endfor %}
```

Add the constant beside the others (line ~36) and use it in the selected-chip renderer (line 62):

```javascript
  const TAG_DIS = {{ tag_disambiguators | tojson }};
```

```javascript
      chip.textContent = TAG_NAMES[id] || id;
      if (TAG_DIS[id]) {
        const s = document.createElement("small");
        s.className = "dis";
        s.textContent = TAG_DIS[id];
        chip.appendChild(s);
      }
```

`| tojson` on the raw dict, never `| safe`, and never `json.dumps` first — invariant 7. Note `tojson` renders integer keys as JSON strings, so `TAG_DIS[id]` works with the string ids the JS already uses.

At line ~125 (`pickerAddAndSelect`), a freshly created tag is new and cannot collide with itself; leave `TAG_DIS` untouched there and add a one-line comment saying so.

- [ ] **Step 5: Style `.dis`**

In `src/app/web/static/style.css`, beside the other chip rules (NOT inside any `@media` block):

```css
/* A tag handle shown inside a chip, only where two same-kind tags share a
   name. Secondary weight so the name still reads as the label. */
.chip .dis { margin-left: .35em; font-weight: 400; opacity: .65; font-size: .85em; }
```

- [ ] **Step 6: Pass the new key from every caller**

`grep -rn "tag_picker_context" src/app/web/` and confirm each caller spreads the whole dict into the template context. If any names keys explicitly, add `tag_disambiguators`. Then check every page that includes `_tag_picker_script.html` still renders: `/concerts/new`, `/concerts/{id}/edit`, `/concerts/import` preview.

- [ ] **Step 7: Run tests, lint, commit**

```bash
uv run --isolated pytest -q && uv run --isolated ruff check .
git add src tests
git commit -m "feat: show a tag's handle in the picker only where names collide"
```

---

### Task 7: Warn before creating a duplicate name

The last piece: the 409 that used to block a duplicate name becomes a pre-submit warning, using data the page already holds. No round trip.

**Files:**
- Modify: `src/app/web/templates/tags.html:281-320` (the new-tag dialog)
- Modify: `src/app/web/templates/_tag_create_dialog.html` (the import-preview quick-create)
- Modify: `src/app/translations/{ja,zh}/LC_MESSAGES/messages.po` (three new msgids)
- Test: `tests/test_tags.py`

**Interfaces:**
- Consumes: nothing from earlier tasks at runtime; the routes already accept duplicates after Task 4.
- Produces: no Python surface.

- [ ] **Step 1: Write the failing test**

Assert the wiring is present and the copy is translatable, which is all a server-rendered test can see of client-side JS:

```python
def test_the_new_tag_dialog_carries_a_duplicate_name_warning(client):
    """The 409 became a pre-submit warning (Task 4 made duplicates legal), so
    the dialog must ship the hook and the copy."""
    login_as(client, EDITOR_ID, "reiji")
    client.post("/tags", data={
        "name": "Yuki Sato", "name_en": "Yuki Sato", "name_zh": "佐藤有紀",
        "kind": "artist",
    })
    r = client.get("/tags")
    assert 'id="dup-name-warn"' in r.text
    assert "A tag with this name already exists." in r.text
    assert "Create anyway" in r.text
    # the existing-names payload the check reads, escaped by tojson
    assert "EXISTING_TAG_NAMES" in r.text


def test_the_warning_copy_is_translatable(client):
    """Both catalogues must carry these or test_i18n_catalogues fails; this
    pins that the strings went through _() rather than being hardcoded."""
    from app.i18n import gettext_in

    assert gettext_in("ja", "Create anyway") != "Create anyway"
    assert gettext_in("zh", "Create anyway") != "Create anyway"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --isolated pytest tests/test_tags.py -q -k "duplicate_name_warning or warning_copy"`
Expected: FAIL — no such markup, and the msgids are absent from both catalogues.

- [ ] **Step 3: Add the warning to the Tags page dialog**

In `tags.html`'s `new-tag-dialog`, after the `name` input (~line 300), add the warning element and the payload. Read the name via `dataset`/`value` — never interpolate a tag name into an `on*` handler (invariant 7):

```html
      <p id="dup-name-warn" class="banner warn" hidden>
        {{ _("A tag with this name already exists.") }}
        <button type="button" class="chip" id="dup-name-pick">{{ _("Select the existing one") }}</button>
        <button type="button" class="chip" id="dup-name-go">{{ _("Create anyway") }}</button>
      </p>
```

and beside the dialog's existing script:

```html
<script>
  // {kind: {lowercased name: tag id}} for the pre-submit duplicate check. A
  // duplicate name is LEGAL (the handle keeps tags apart) -- this warns rather
  // than blocks, and it runs client-side because the page already holds every
  // name, so a round trip would buy nothing.
  const EXISTING_TAG_NAMES = {{ existing_tag_names | tojson }};
</script>
```

`tag_directory_context` (`db/service.py`) supplies `existing_tag_names` as a raw dict `{kind_value: {lower(name): id}}`. Add it there, next to the other context it already builds, and pass it through the `/tags` route.

Wire the check on the name input's `input` event: look up `EXISTING_TAG_NAMES[selectedKind]?.[value.trim().toLowerCase()]`, toggle `hidden` on `#dup-name-warn`, and have `#dup-name-pick` close the dialog and focus that tag's row. `#dup-name-go` just hides the warning so the normal submit proceeds.

- [ ] **Step 4: Do the same in `_tag_create_dialog.html`**

That dialog already receives `by_kind` through the picker context, so build its lookup from that rather than adding a second payload — one source of names, not two. Same three msgids; reuse them verbatim so the catalogues carry one entry each.

- [ ] **Step 5: Update both catalogues**

```bash
uv run --isolated pybabel extract -F babel.cfg -k N_ -o messages.pot .
uv run --isolated pybabel update -i messages.pot -d src/app/translations -l ja
uv run --isolated pybabel update -i messages.pot -d src/app/translations -l zh
```

Fill in both, then `rm messages.pot`. Suggested:

| msgid | ja | zh |
| --- | --- | --- |
| `A tag with this name already exists.` | この名前のタグはすでにあります。 | 已有同名标签。 |
| `Create anyway` | それでも作成 | 仍然创建 |
| `Select the existing one` | 既存のものを選ぶ | 选择现有的 |

Clear any `#, fuzzy` markers.

- [ ] **Step 6: Verify by hand in a browser**

Per the measure-don't-reason rule, click it rather than reasoning about it:

```bash
uv run --isolated python -m app.main   # DISCORD_TOKEN empty -> web-only
```
Open `/tags`, start typing a name that already exists as the selected kind, and confirm: the warning appears, "Create anyway" dismisses it and lets the submit through, and the new tag lands with a `-2` handle.

- [ ] **Step 7: Run suite and lint**

Run: `uv run --isolated pytest -q` then `uv run --isolated ruff check .`
Expected: both clean, including `tests/test_i18n_catalogues.py`.

- [ ] **Step 8: Update the spec's status line and WISHLIST**

Set the spec's `Status:` to `implemented (2026-07-29)` and record any deviations at its foot. In `WISHLIST.md`, add a Shipped entry dated today (tag handles; the two crashes it closed; that it unblocks #1) and do the revision pass the CLAUDE.md wishlist rule requires.

- [ ] **Step 9: Commit**

```bash
git add src tests docs WISHLIST.md
git commit -m "feat: warn on a duplicate tag name instead of blocking it"
```

---

## Self-review notes

**Spec coverage.** Section 1 (`Tag.slug`, format, normalisation) → Tasks 2 and 5. Section 2 (generation, `slug_core` split) → Tasks 1 and 2. Section 3 (name uniqueness removed, `find_tag_by_name` deleted, plural lookup, rehearsal seed) → Task 4. Section 4 (warn not block, both changed contracts) → Tasks 4 and 7. Section 5 (handle visible on collision, `tag_disambiguators`, edit dialog field) → Tasks 5 and 6. Section 6 (migration, four phases, `naming_convention`) → Task 3. Section 7 (legacy fixture at current column set) → Task 3. Out-of-scope items are not implemented anywhere, as intended.

**Deliberate ordering.** Task 2 lands the `NOT NULL` column before Task 3's migration, so its Step 6 flushes out every `Tag(...)` construction in the suite while the change is small and attributable. Task 4 must follow Task 2 because the routes need `assign_tag_slug`.

**Known risk.** Task 2 Step 6 is the widest-blast-radius step here: `slug` becoming `NOT NULL` breaks every test that builds a bare `Tag(...)`. It is called out as expected rather than hidden, and the instruction is explicitly not to make the column nullable to dodge it.
