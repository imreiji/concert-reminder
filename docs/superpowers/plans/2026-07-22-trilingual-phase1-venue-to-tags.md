# Trilingual Concert Pages — Phase 1: Venue to Tags — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move a leg's venue from three free-text columns onto a real VENUE tag FK, so venue names render in all three languages for free and are entered once ever instead of once per concert.

**Architecture:** `ConcertDay` gains `venue_tag_id` (FK → `tags.id`), replacing the current case-insensitive name match in `find_venue_tag`. `Tag` gains `city`/`city_en`/`city_zh` and `address`, since those are properties of a venue rather than of a leg. On every concert save the VENUE rows in `concert_tags` are rewritten as the union of the legs' venue tags, which also fixes a live bug where `Concert.venue` is written once at creation and never updated. The old `ConcertDay.city`/`venue`/`venue_address` columns are **left in place** this phase and dropped in phase 5, after live verification that the backfill matched every leg.

**Tech Stack:** Python 3.14, SQLAlchemy async, Alembic (SQLite batch mode), FastAPI, Jinja2, htmx, pytest / pytest-asyncio.

## Global Constraints

- `uv run pytest -q` and `uv run ruff check .` MUST both pass before any commit.
- The DB stores aware UTC only; `UTCDateTime` rejects naive datetimes (invariant 1).
- Business logic lives in `src/app/db/service.py`. Routes and bot are thin shells.
- `alembic.ini` and config files stay ASCII-only (the owner's Windows machine uses a GBK locale).
- After autogenerate, replace `app.db.models.UTCDateTime()` with `sa.DateTime()` and delete the `import app.db.models` line.
- Every `batch_alter_table` touching `concerts`, `tags`, or `concert_days` MUST pass `naming_convention=NAMING_CONVENTION` — these tables predate the convention and carry anonymous constraints on the live server that no metadata-built test DB can reproduce. This has shipped a server-only failure once.
- DB test fixtures MUST register the `PRAGMA foreign_keys=ON` connect listener.
- Every page needs at least one logged-in GET render test.
- Sentence case in all UI copy ("Add venue", not "Add Venue").
- New user-visible strings are wrapped in `_()` and MUST be filled into **both** `src/app/translations/ja/LC_MESSAGES/messages.po` and `.../zh/...` before commit; `tests/test_i18n_catalogues.py` fails on any untranslated or fuzzy entry.
- Current alembic head is `4d5a2d834b3a`. The migration in Task 3 uses it as `down_revision`.

---

### Task 1: Tag gains venue-detail columns

A venue is always in exactly one city, so city belongs on the tag, not on each leg that visits it. City needs locale variants (横浜 / Yokohama / 横滨); address does not — its job is to be pasted into a map, and `location_url` already covers the maps link.

**Files:**
- Modify: `src/app/db/models.py:239-243` (the VENUE-specific block on `Tag`)
- Test: `tests/test_i18n_ugc.py`

**Interfaces:**
- Produces: `Tag.city`, `Tag.city_en`, `Tag.city_zh` (`str | None`, `String(100)`), `Tag.address` (`str | None`, `String(300)`). Task 3 migrates them, Task 7 renders them, Task 8's create form writes them.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_i18n_ugc.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_i18n_ugc.py::test_tag_venue_detail_columns_are_nullable -q`
Expected: FAIL — `AssertionError: Tag.city missing`

- [ ] **Step 3: Add the columns**

In `src/app/db/models.py`, in the `Tag` VENUE-specific block after `region`:

```python
    location_url: Mapped[str | None] = mapped_column(String(500))
    region: Mapped[str | None] = mapped_column(String(100))
    # VENUE-specific: the city this venue sits in, finer-grained than `region`
    # ("Yokohama" inside "Kanto"). Carries locale variants because a city name
    # is user-facing text; `address` does not, because its job is to be pasted
    # into a map and location_url already covers the maps link.
    city: Mapped[str | None] = mapped_column(String(100))
    city_en: Mapped[str | None] = mapped_column(String(100))
    city_zh: Mapped[str | None] = mapped_column(String(100))
    address: Mapped[str | None] = mapped_column(String(300))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_i18n_ugc.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/app/db/models.py tests/test_i18n_ugc.py
git commit -m "feat: add city and address columns to VENUE tags"
```

---

### Task 2: ConcertDay gains venue_tag_id

**Files:**
- Modify: `src/app/db/models.py:290-311` (`ConcertDay`)
- Test: `tests/test_editor_legs.py`

**Interfaces:**
- Consumes: `Tag` from Task 1.
- Produces: `ConcertDay.venue_tag_id` (`int | None`) and the `ConcertDay.venue_tag` relationship. Task 4's rollup reads `venue_tag_id`; Task 7 renders `venue_tag`.

**Critical:** the relationship must be eager-loaded wherever a template touches it. Lazy loading during async template rendering raises `MissingGreenlet`, which is a 500 — `concerts.py:695-697` records that this project has already shipped that bug once. Task 7 handles the eager load.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_editor_legs.py`:

```python
async def test_day_venue_tag_fk_sets_null_on_tag_delete(session):
    """A venue tag is shared taxonomy; deleting one must not delete the legs
    that referenced it (mirrors Tag.created_by's SET NULL reasoning)."""
    tag = Tag(name="Zepp Haneda", kind=TagKind.VENUE)
    session.add(tag)
    await session.flush()

    concert = Concert(title="t", event_id="ev1", created_by=None)
    session.add(concert)
    await session.flush()

    day = ConcertDay(
        concert_id=concert.id,
        label="Day 1",
        starts_at_utc=datetime(2026, 8, 1, 9, 0, tzinfo=UTC),
        venue_tag_id=tag.id,
    )
    session.add(day)
    await session.flush()

    await session.delete(tag)
    await session.flush()
    await session.refresh(day)

    assert day.id is not None, "deleting the tag must not delete the leg"
    assert day.venue_tag_id is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_editor_legs.py::test_day_venue_tag_fk_sets_null_on_tag_delete -q`
Expected: FAIL — `TypeError: 'venue_tag_id' is an invalid keyword argument for ConcertDay`

- [ ] **Step 3: Add the column and relationship**

In `src/app/db/models.py`, in `ConcertDay` after `venue_address`:

```python
    # The structured venue. Replaces the free-text `venue` above, which
    # find_venue_tag resolved by case-insensitive name match -- this is that
    # same link made real. SET NULL rather than CASCADE: a venue tag is shared
    # taxonomy, and deleting one must never take performances down with it.
    venue_tag_id: Mapped[int | None] = mapped_column(
        ForeignKey("tags.id", ondelete="SET NULL"), index=True
    )
```

and alongside the existing `concert` relationship:

```python
    concert: Mapped[Concert] = relationship(back_populates="days")
    # Always eager-load this before handing a day to a template -- a lazy load
    # during async rendering raises MissingGreenlet (a 500). See
    # concerts.py's concert_rounds_context.
    venue_tag: Mapped["Tag | None"] = relationship(lazy="raise")
```

`lazy="raise"` turns an accidental lazy load into a loud error at development time instead of a 500 in production.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_editor_legs.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/app/db/models.py tests/test_editor_legs.py
git commit -m "feat: add venue_tag_id FK to ConcertDay"
```

---

### Task 3: Migration — add columns and backfill venue_tag_id

The backfill reuses the exact matching rule `find_venue_tag` uses today (`concerts.py:370-380`): trimmed, lowercased, exact. Unmatched venues are **reported, never silently nulled** — the old columns survive this phase precisely so an unmatched venue is recoverable.

**Files:**
- Create: `alembic/versions/<generated>_venue_tag_on_legs.py`
- Test: `tests/test_migration_venue_tag_backfill.py`

**Interfaces:**
- Consumes: the columns from Tasks 1 and 2.
- Produces: a populated `concert_days.venue_tag_id` on existing data.

- [ ] **Step 1: Generate the revision**

```bash
uv run alembic revision -m "venue tag on legs"
```

Note the generated filename. Set `down_revision = '4d5a2d834b3a'`.

- [ ] **Step 2: Write the migration**

Replace the generated body. Do **not** use autogenerate output verbatim — it will not include the backfill.

```python
"""venue tag on legs

Revision ID: <generated>
Revises: 4d5a2d834b3a
"""
from alembic import op
import sqlalchemy as sa

from app.db.models import NAMING_CONVENTION


revision = '<generated>'
down_revision = '4d5a2d834b3a'
branch_labels = None
depends_on = None


# `tags` and `concert_days` predate the naming convention, so their constraints
# are anonymous on the live server. Batch mode copies them and refuses to name
# them itself unless handed the convention (see CLAUDE.md's migration notes).
def upgrade() -> None:
    with op.batch_alter_table(
        'tags', schema=None, naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.add_column(sa.Column('city', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('city_en', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('city_zh', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('address', sa.String(length=300), nullable=True))

    with op.batch_alter_table(
        'concert_days', schema=None, naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.add_column(sa.Column('venue_tag_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_concert_days_venue_tag_id_tags', 'tags',
            ['venue_tag_id'], ['id'], ondelete='SET NULL',
        )
        batch_op.create_index(
            'ix_concert_days_venue_tag_id', ['venue_tag_id'], unique=False
        )

    # Backfill using the SAME rule find_venue_tag applies today: trimmed,
    # lowercased, exact. Anything that does not match is left NULL and reported
    # below -- the free-text columns stay in place this deploy so a miss is
    # recoverable.
    conn = op.get_bind()
    conn.execute(sa.text("""
        UPDATE concert_days
           SET venue_tag_id = (
               SELECT t.id FROM tags t
                WHERE t.kind = 'venue'
                  AND lower(trim(t.name)) = lower(trim(concert_days.venue))
                LIMIT 1
           )
         WHERE venue IS NOT NULL AND trim(venue) <> ''
    """))

    unmatched = conn.execute(sa.text("""
        SELECT id, venue FROM concert_days
         WHERE venue IS NOT NULL AND trim(venue) <> '' AND venue_tag_id IS NULL
         ORDER BY id
    """)).fetchall()
    if unmatched:
        print(f"\n  {len(unmatched)} leg(s) had a venue with no matching VENUE tag:")
        for day_id, venue in unmatched:
            print(f"    concert_days.id={day_id}  venue={venue!r}")
        print("  Create those tags and set the legs' venue before phase 5 drops "
              "the free-text columns.\n")


def downgrade() -> None:
    with op.batch_alter_table(
        'concert_days', schema=None, naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.drop_index('ix_concert_days_venue_tag_id')
        batch_op.drop_constraint('fk_concert_days_venue_tag_id_tags', type_='foreignkey')
        batch_op.drop_column('venue_tag_id')

    with op.batch_alter_table(
        'tags', schema=None, naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.drop_column('address')
        batch_op.drop_column('city_zh')
        batch_op.drop_column('city_en')
        batch_op.drop_column('city')
```

- [ ] **Step 3: Write the backfill test against a legacy-shaped DDL fixture**

A metadata-built DB has named constraints and cannot reproduce the server's shape. Hand-write the DDL, following `tests/test_migration_legacy_anonymous_constraints.py`.

Create `tests/test_migration_venue_tag_backfill.py`:

```python
"""The backfill in the venue-tag migration, against a LEGACY-shaped DB.

Test DBs are built from Base.metadata, so every constraint is named and the
divergence from the live server (anonymous constraints on tables created by
older migrations) is invisible to the rest of the suite. This fixture writes
the real server DDL by hand so batch mode is exercised the way production
will exercise it.
"""
import sqlite3

import pytest


LEGACY_DDL = """
CREATE TABLE tags (
    id INTEGER NOT NULL,
    name VARCHAR(100) NOT NULL,
    name_en VARCHAR(100),
    name_zh VARCHAR(100),
    kind VARCHAR(9) NOT NULL,
    parent_id INTEGER,
    location_url VARCHAR(500),
    region VARCHAR(100),
    eventernote_url VARCHAR(500),
    created_by BIGINT,
    created_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (name),
    FOREIGN KEY(parent_id) REFERENCES tags (id) ON DELETE SET NULL
);
CREATE TABLE concerts (
    id INTEGER NOT NULL,
    event_id VARCHAR(50) NOT NULL,
    title VARCHAR(200) NOT NULL,
    created_by BIGINT,
    PRIMARY KEY (id),
    UNIQUE (event_id)
);
CREATE TABLE concert_days (
    id INTEGER NOT NULL,
    concert_id INTEGER NOT NULL,
    label VARCHAR(100) NOT NULL,
    city VARCHAR(100),
    venue VARCHAR(200),
    venue_address VARCHAR(300),
    doors_at_utc DATETIME,
    starts_at_utc DATETIME NOT NULL,
    cancelled BOOLEAN DEFAULT '0' NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(concert_id) REFERENCES concerts (id) ON DELETE CASCADE
);
"""


@pytest.fixture()
def legacy_db(tmp_path):
    path = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_DDL)
    conn.executescript("""
        INSERT INTO tags (id, name, kind, created_at) VALUES
            (1, 'Zepp Haneda', 'venue', '2026-01-01 00:00:00'),
            (2, 'K Arena Yokohama', 'venue', '2026-01-01 00:00:00'),
            (3, 'Hasunosora', 'group', '2026-01-01 00:00:00');
        INSERT INTO concerts (id, event_id, title) VALUES (1, 'ev1', 'T');
        INSERT INTO concert_days (id, concert_id, label, venue, starts_at_utc) VALUES
            (1, 1, 'Day 1', 'Zepp Haneda',       '2026-08-01 09:00:00'),
            (2, 1, 'Day 2', '  zepp haneda  ',   '2026-08-02 09:00:00'),
            (3, 1, 'Day 3', 'Nowhere Hall',      '2026-08-03 09:00:00'),
            (4, 1, 'Day 4', NULL,                '2026-08-04 09:00:00'),
            (5, 1, 'Day 5', 'Hasunosora',        '2026-08-05 09:00:00');
    """)
    conn.commit()
    conn.close()
    return path


def _run_upgrade(db_path):
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "head")


def test_backfill_matches_case_and_whitespace_insensitively(legacy_db):
    _run_upgrade(legacy_db)
    conn = sqlite3.connect(legacy_db)
    rows = dict(conn.execute(
        "SELECT id, venue_tag_id FROM concert_days ORDER BY id"
    ).fetchall())
    conn.close()

    assert rows[1] == 1, "exact match"
    assert rows[2] == 1, "case and whitespace differences must still match"
    assert rows[3] is None, "no such venue tag -- left NULL for reporting"
    assert rows[4] is None, "no venue at all"
    assert rows[5] is None, "a same-named GROUP tag must NOT match a venue"


def test_backfill_preserves_free_text_columns(legacy_db):
    """Phase 1 must not destroy the source data -- phase 5 drops it, after
    the unmatched report has been acted on."""
    _run_upgrade(legacy_db)
    conn = sqlite3.connect(legacy_db)
    venue = conn.execute("SELECT venue FROM concert_days WHERE id = 3").fetchone()[0]
    conn.close()
    assert venue == "Nowhere Hall"
```

- [ ] **Step 4: Run the migration tests**

Run: `uv run pytest tests/test_migration_venue_tag_backfill.py -q`
Expected: PASS — 2 passed

- [ ] **Step 5: Apply the migration locally and run the full suite**

```bash
uv run alembic upgrade head
uv run pytest -q
uv run ruff check .
```

Expected: migration applies clean; full suite green.

- [ ] **Step 6: Commit**

```bash
git add alembic/versions/ tests/test_migration_venue_tag_backfill.py
git commit -m "feat: migrate leg venues onto VENUE tag FK"
```

---

### Task 4: Venue rollup in the service layer

The concert's VENUE tags become the union of its legs' venue tags, so a venue is entered in exactly one place. This also fixes the staleness bug: `Concert.venue` is written once at creation (`concerts.py:471`) and the edit route never re-derives it, so changing venue tags after creation leaves the string permanently wrong.

**Files:**
- Modify: `src/app/db/service.py` (new section `# Venue rollup (legs -> concert)`)
- Test: `tests/test_venue_rollup.py` (create)

**Interfaces:**
- Consumes: `ConcertDay.venue_tag_id` (Task 2).
- Produces: `async def sync_concert_venue_tags(session, concert_id) -> None`. Task 5 calls it from all three save paths.

- [ ] **Step 1: Write the failing test**

Create `tests/test_venue_rollup.py`:

```python
"""The concert's VENUE tags are derived from its legs, never typed."""
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.db.models import Concert, ConcertDay, ConcertTag, Tag
from app.db.service import sync_concert_venue_tags
from app.domain.types import TagKind


async def _venue_tag_ids(session, concert_id):
    rows = (await session.execute(
        select(ConcertTag.tag_id)
        .join(Tag, Tag.id == ConcertTag.tag_id)
        .where(ConcertTag.concert_id == concert_id, Tag.kind == TagKind.VENUE)
    )).scalars()
    return set(rows)


async def _setup(session, venue_names):
    tags = [Tag(name=n, kind=TagKind.VENUE) for n in venue_names]
    session.add_all(tags)
    concert = Concert(title="T", event_id="ev1")
    session.add(concert)
    await session.flush()
    return concert, tags


async def test_rollup_unions_leg_venues(session):
    concert, tags = await _setup(session, ["Zepp Haneda", "Zepp Namba"])
    for i, tag in enumerate(tags):
        session.add(ConcertDay(
            concert_id=concert.id, label=f"Day {i + 1}",
            starts_at_utc=datetime(2026, 8, i + 1, 9, tzinfo=UTC),
            venue_tag_id=tag.id,
        ))
    await session.flush()

    await sync_concert_venue_tags(session, concert.id)

    assert await _venue_tag_ids(session, concert.id) == {t.id for t in tags}


async def test_rollup_removes_a_venue_no_leg_uses(session):
    """The bug this fixes: Concert.venue was written once at creation and the
    edit route never re-derived it, so a changed venue stayed stale forever."""
    concert, tags = await _setup(session, ["Old Hall", "New Hall"])
    old, new = tags
    session.add(ConcertTag(concert_id=concert.id, tag_id=old.id))
    session.add(ConcertDay(
        concert_id=concert.id, label="Day 1",
        starts_at_utc=datetime(2026, 8, 1, 9, tzinfo=UTC),
        venue_tag_id=new.id,
    ))
    await session.flush()

    await sync_concert_venue_tags(session, concert.id)

    assert await _venue_tag_ids(session, concert.id) == {new.id}


async def test_rollup_leaves_non_venue_tags_alone(session):
    """Group-tag expansion (invariant 3) materializes members deliberately;
    the venue rollup must never touch them."""
    concert, _ = await _setup(session, ["Zepp Haneda"])
    group = Tag(name="Hasunosora", kind=TagKind.GROUP)
    session.add(group)
    await session.flush()
    session.add(ConcertTag(concert_id=concert.id, tag_id=group.id))
    await session.flush()

    await sync_concert_venue_tags(session, concert.id)

    all_ids = set((await session.execute(
        select(ConcertTag.tag_id).where(ConcertTag.concert_id == concert.id)
    )).scalars())
    assert group.id in all_ids


async def test_rollup_with_no_leg_venues_clears_them(session):
    concert, tags = await _setup(session, ["Old Hall"])
    session.add(ConcertTag(concert_id=concert.id, tag_id=tags[0].id))
    session.add(ConcertDay(
        concert_id=concert.id, label="Day 1",
        starts_at_utc=datetime(2026, 8, 1, 9, tzinfo=UTC), venue_tag_id=None,
    ))
    await session.flush()

    await sync_concert_venue_tags(session, concert.id)

    assert await _venue_tag_ids(session, concert.id) == set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_venue_rollup.py -q`
Expected: FAIL — `ImportError: cannot import name 'sync_concert_venue_tags'`

- [ ] **Step 3: Implement the rollup**

Add to `src/app/db/service.py`:

```python
# ── Venue rollup (legs -> concert) ───────────────────────────────────────


async def sync_concert_venue_tags(session: AsyncSession, concert_id: int) -> None:
    """Rewrite a concert's VENUE tag rows as the union of its legs' venues.

    The leg is the single place a venue is entered, so the concert level is
    derived and can never contradict it. Only VENUE rows are touched --
    franchise/group/artist attachment is deliberate and materialized (invariant
    3), and must survive untouched.

    Discover's region filter reads concert_tags client-side off each tile's
    data-tags, so keeping this rollup current is exactly what lets that filter
    stay unchanged while venues live on legs.
    """
    desired = set((await session.execute(
        select(ConcertDay.venue_tag_id).where(
            ConcertDay.concert_id == concert_id,
            ConcertDay.venue_tag_id.is_not(None),
        )
    )).scalars())

    current = set((await session.execute(
        select(ConcertTag.tag_id)
        .join(Tag, Tag.id == ConcertTag.tag_id)
        .where(ConcertTag.concert_id == concert_id, Tag.kind == TagKind.VENUE)
    )).scalars())

    for tag_id in current - desired:
        await session.execute(
            delete(ConcertTag).where(
                ConcertTag.concert_id == concert_id, ConcertTag.tag_id == tag_id
            )
        )
    for tag_id in desired - current:
        session.add(ConcertTag(concert_id=concert_id, tag_id=tag_id))
    await session.flush()
```

Confirm `delete` is imported from `sqlalchemy` at the top of `service.py`; add it to the existing import if not.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_venue_rollup.py -q`
Expected: PASS — 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/app/db/service.py tests/test_venue_rollup.py
git commit -m "feat: derive a concert's venue tags from its legs"
```

---

### Task 5: Wire the rollup into every save path

**Files:**
- Modify: `src/app/web/routes/concerts.py:154-194` (`apply_day_fields`, `build_day`), `:466-487` (`create_concert_row`), the create and edit routes, `src/app/web/routes/imports.py`
- Test: `tests/test_venue_rollup.py`

**Interfaces:**
- Consumes: `sync_concert_venue_tags` (Task 4).
- Produces: `apply_day_fields` and `build_day` accept `venue_tag_id: str = ""`. Task 6's form posts a `day_venue_tag_id` field per leg.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_venue_rollup.py`:

```python
async def test_edit_form_rolls_up_changed_leg_venue(editor_client, session):
    """The end-to-end version of the staleness fix: change a leg's venue on the
    edit form and the concert's venue tags follow."""
    old = Tag(name="Old Hall", kind=TagKind.VENUE)
    new = Tag(name="New Hall", kind=TagKind.VENUE)
    session.add_all([old, new])
    concert = Concert(title="T", event_id="rollup1")
    session.add(concert)
    await session.flush()
    day = ConcertDay(
        concert_id=concert.id, label="Day 1",
        starts_at_utc=datetime(2026, 8, 1, 9, tzinfo=UTC), venue_tag_id=old.id,
    )
    session.add(day)
    session.add(ConcertTag(concert_id=concert.id, tag_id=old.id))
    await session.commit()

    resp = await editor_client.post("/concerts/rollup1/edit", data={
        "title": "T", "event_id": "rollup1",
        "day_id": [str(day.id)], "day_key": [""],
        "day_label": ["Day 1"], "day_starts_at": ["2026-08-01T18:00"],
        "day_venue_tag_id": [str(new.id)],
        "day_doors_at": [""], "day_cancelled": ["false"],
    })
    assert resp.status_code in (200, 303)

    assert await _venue_tag_ids(session, concert.id) == {new.id}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_venue_rollup.py::test_edit_form_rolls_up_changed_leg_venue -q`
Expected: FAIL — the concert still carries `old.id`

- [ ] **Step 3: Thread venue_tag_id through the day builders**

In `src/app/web/routes/concerts.py`, extend both signatures. Keep the free-text params this phase — the columns still exist and the import route still fills them.

```python
def apply_day_fields(
    day: ConcertDay,
    label: str,
    starts_at: str,
    city: str = "",
    venue: str = "",
    venue_address: str = "",
    doors_at: str = "",
    cancelled: str = "false",
    venue_tag_id: str = "",
) -> ConcertDay:
    """The JST->UTC parse + assignment shared by build_day (new rows) and
    the edit page's in-place update of existing rows."""
    starts = parse_jst(starts_at)
    if starts is None:
        raise HTTPException(status_code=422, detail="a day needs a start time")
    day.label = label.strip()
    day.starts_at_utc = starts
    day.city = city.strip() or None
    day.venue = venue.strip() or None
    day.venue_address = venue_address.strip() or None
    day.doors_at_utc = parse_jst(doors_at)
    day.cancelled = cancelled == "true"
    day.venue_tag_id = int(venue_tag_id) if venue_tag_id.strip().isdigit() else None
    return day


def build_day(
    concert_id: int,
    label: str,
    starts_at: str,
    city: str = "",
    venue: str = "",
    venue_address: str = "",
    doors_at: str = "",
    cancelled: str = "false",
    venue_tag_id: str = "",
) -> ConcertDay:
    """New-row constructor: the rich creation form, the edit page's new
    rows, and the URL-import commit route."""
    return apply_day_fields(
        ConcertDay(concert_id=concert_id), label, starts_at, city, venue,
        venue_address, doors_at, cancelled, venue_tag_id,
    )
```

- [ ] **Step 4: Accept the form field and call the rollup**

In the create route, add the form param alongside the other `day_*` params:

```python
    day_venue_tag_id: list[str] = Form([]),
```

Pad it to the leg count exactly as the neighbouring `day_*` lists are padded, pass it into `build_day`, and after every leg is added call:

```python
    await sync_concert_venue_tags(session, concert.id)
```

Do the same in the edit route (after the day loop, before the response) and in `imports.py`'s commit route. Import the helper:

```python
from app.db.service import sync_concert_venue_tags
```

In `create_concert_row`, the `venue=` derivation now runs before any leg exists, so it can no longer produce a useful value. Replace it with `None` and let the rollup own it:

```python
        franchise=", ".join(t.name for t in f_tags) or None,  # denormalized display
        # Venue is derived from the legs by sync_concert_venue_tags, which the
        # caller runs after the legs are added. It cannot be computed here --
        # no leg exists yet. (Phase 5 drops this column entirely.)
        venue=None,
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_venue_rollup.py tests/test_crud.py tests/test_editor_legs.py -q`
Expected: PASS

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q && uv run ruff check .`
Expected: green. Existing tests posting `day_venue` still pass — the free-text columns are untouched this phase.

- [ ] **Step 7: Commit**

```bash
git add src/app/web/routes/concerts.py src/app/web/routes/imports.py tests/test_venue_rollup.py
git commit -m "feat: roll leg venue tags up to the concert on every save"
```

---

### Task 6: Leg editor selects a venue tag

**Files:**
- Modify: `src/app/web/templates/concert_new.html:55-60`, `concert_edit.html` (the matching `eleg-fields` block), `src/app/web/templates/_leg_chips_script.html`
- Modify: `src/app/web/routes/concerts.py` (pass `venue_tags` into both form contexts)
- Test: `tests/test_editor_legs.py`

**Interfaces:**
- Consumes: `day_venue_tag_id` (Task 5).
- Produces: a `<select name="day_venue_tag_id">` per leg row, and the `venue_tags` template context key Task 7's dialog extends.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_editor_legs.py`:

```python
async def test_leg_editor_offers_venue_tags(editor_client, session):
    session.add(Tag(name="Zepp Haneda", kind=TagKind.VENUE))
    await session.commit()

    resp = await editor_client.get("/concerts/new")

    assert resp.status_code == 200
    assert 'name="day_venue_tag_id"' in resp.text
    assert "Zepp Haneda" in resp.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_editor_legs.py::test_leg_editor_offers_venue_tags -q`
Expected: FAIL — `assert 'name="day_venue_tag_id"' in resp.text`

- [ ] **Step 3: Load venue tags into both form contexts**

In `new_concert_form` and the edit form handler in `concerts.py`, add to the template context:

```python
        "venue_tags": list((await session.execute(
            select(Tag).where(Tag.kind == TagKind.VENUE).order_by(Tag.name)
        )).scalars()),
```

- [ ] **Step 4: Replace the free-text venue input**

In `concert_new.html`, in the `eleg-fields` block, replace the `day_venue` and `day_venue_address` inputs:

```jinja
      <div class="eleg-fields">
        <select name="day_venue_tag_id" data-venue-select>
          <option value="">{{ _("Venue") }}</option>
          {% for v in venue_tags %}
          <option value="{{ v.id }}">{{ loc(v, "name") }}</option>
          {% endfor %}
        </select>
        <label>{{ _("Doors (JST)") }} <input type="datetime-local" name="day_doors_at"></label>
        <label>{{ _("Starts (JST)") }} <input type="datetime-local" name="day_starts_at" required></label>
      </div>
```

Also remove the `day_city` input from the `eleg-top` block above it — city now lives on the venue tag.

Apply the same change to `concert_edit.html`, with the existing leg's value preselected:

```jinja
          <option value="{{ v.id }}"{% if d.venue_tag_id == v.id %} selected{% endif %}>{{ loc(v, "name") }}</option>
```

- [ ] **Step 5: Update the client-side leg template**

`_leg_chips_script.html` clones leg rows in JS. Update its row template to emit the same `<select>`, and update the chip-label fallback at line 45 — `fieldValue(row, "day_city")` no longer exists:

```javascript
  // label -> venue tag name -> date. `city` is gone (it lives on the venue tag).
  var sel = row.querySelector('[data-venue-select]');
  var venueName = sel && sel.selectedIndex > 0 ? sel.options[sel.selectedIndex].text : "";
  var label = fieldValue(row, "day_label") || venueName || dateLabel(row);
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_editor_legs.py tests/test_crud.py -q`
Expected: PASS

- [ ] **Step 7: Update both catalogues**

```bash
uv run pybabel extract -F babel.cfg -k N_ -o messages.pot .
uv run pybabel update -i messages.pot -d src/app/translations -l ja
uv run pybabel update -i messages.pot -d src/app/translations -l zh
```

Fill every new and fuzzy msgstr by hand in both `.po` files, clear the fuzzy flags, then `rm messages.pot`.

Run: `uv run pytest tests/test_i18n_catalogues.py -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/app/web/ src/app/translations/ tests/test_editor_legs.py
git commit -m "feat: pick a leg's venue from VENUE tags instead of free text"
```

---

### Task 7: Inline venue creation from the leg editor

The tag picker currently dead-ends with "Create it on the tags page first" (`_tag_picker_fields.html`). Creating a venue mid-entry should not cost a detour.

**Files:**
- Create: `src/app/web/templates/_venue_create_dialog.html`
- Modify: `src/app/web/routes/tags.py` (new endpoint), `concert_new.html`, `concert_edit.html`
- Test: `tests/test_tags.py`

**Interfaces:**
- Consumes: `Tag` venue columns (Task 1), the `venue_tags` context (Task 6).
- Produces: `POST /tags/venue/quick` returning JSON `{"id": int, "name": str}`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_tags.py`:

```python
async def test_quick_venue_create_returns_the_new_tag(editor_client, session):
    resp = await editor_client.post("/tags/venue/quick", data={
        "name": "Zepp Haneda", "name_en": "Zepp Haneda", "name_zh": "Zepp 羽田",
        "city": "東京", "city_en": "Tokyo", "city_zh": "东京",
        "region": "Kanto", "address": "", "location_url": "",
    })

    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Zepp Haneda"

    tag = await session.get(Tag, body["id"])
    assert tag.kind is TagKind.VENUE
    assert tag.city_en == "Tokyo"


async def test_quick_venue_create_rejects_a_duplicate_name(editor_client, session):
    session.add(Tag(name="Zepp Haneda", kind=TagKind.VENUE))
    await session.commit()

    resp = await editor_client.post("/tags/venue/quick", data={
        "name": "Zepp Haneda", "name_en": "Zepp Haneda", "name_zh": "Zepp 羽田",
    })

    assert resp.status_code == 422


async def test_quick_venue_create_requires_editor(client):
    """Signed out is not an error -- require_editor's LoginRequired sends the
    visitor to `/` (invariant 5), so this must not be a 403."""
    resp = await client.post("/tags/venue/quick", data={"name": "X"})
    assert resp.status_code in (200, 303, 204)
    assert "/tags/venue/quick" not in resp.headers.get("location", "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tags.py::test_quick_venue_create_returns_the_new_tag -q`
Expected: FAIL — 404

- [ ] **Step 3: Add the endpoint**

In `src/app/web/routes/tags.py`:

```python
@router.post("/tags/venue/quick")
async def quick_create_venue(
    name: str = Form(...),
    name_en: str = Form(""),
    name_zh: str = Form(""),
    city: str = Form(""),
    city_en: str = Form(""),
    city_zh: str = Form(""),
    region: str = Form(""),
    address: str = Form(""),
    location_url: str = Form(""),
    user: SessionUser = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Create a VENUE tag without leaving the concert editor. Returns JSON so
    the caller can select the new tag into the leg it was creating it for.

    Deliberately NOT a second write path: it builds the same Tag row the tags
    page does, with the same uniqueness check on `name`.
    """
    name = name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="a venue needs a name")
    existing = (await session.execute(
        select(Tag).where(Tag.name == name, Tag.kind == TagKind.VENUE)
    )).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=422, detail="that venue already exists")

    tag = Tag(
        name=name,
        name_en=name_en.strip() or None,
        name_zh=name_zh.strip() or None,
        kind=TagKind.VENUE,
        city=city.strip() or None,
        city_en=city_en.strip() or None,
        city_zh=city_zh.strip() or None,
        region=region.strip() or None,
        address=address.strip() or None,
        location_url=form_url(location_url),
        created_by=user.id,
    )
    session.add(tag)
    await session.flush()
    return {"id": tag.id, "name": tag.name}
```

`form_url` comes from `app.web.forms` (invariant 7 — every editor-supplied URL crosses the boundary through it).

- [ ] **Step 4: Add the dialog**

Create `src/app/web/templates/_venue_create_dialog.html` on the existing picker pattern — header with title and ×, no footer, backdrop-click and Esc close. Under 700px it becomes a bottom sheet automatically via the mobile retrofit's `<dialog>` rules; add no new `@media` block.

```jinja
{# Create a VENUE tag without leaving the editor. Same shape as .picker; the
   only new behaviour is that it POSTs and selects the result. #}
<dialog class="picker" id="venue-create">
  <form method="dialog" class="pickhead">
    <h3>{{ _("New venue") }}</h3>
    <button class="x" value="cancel">×</button>
  </form>
  <div class="pickbody">
    <label>{{ _("Name") }} <input name="v_name" maxlength="100" required></label>
    <label>{{ _("Name · English") }} <input name="v_name_en" maxlength="100"></label>
    <label>{{ _("Name · 中文") }} <input name="v_name_zh" maxlength="100"></label>
    <label>{{ _("City") }} <input name="v_city" maxlength="100"></label>
    <label>{{ _("City · English") }} <input name="v_city_en" maxlength="100"></label>
    <label>{{ _("City · 中文") }} <input name="v_city_zh" maxlength="100"></label>
    <label>{{ _("Region") }} <input name="v_region" maxlength="100"></label>
    <label>{{ _("Address") }} <input name="v_address" maxlength="300"></label>
    <label>{{ _("Map link") }} <input name="v_location_url" maxlength="500"></label>
    <p class="err tiny" data-venue-err hidden></p>
    <button type="button" class="chip" data-venue-save>{{ _("Add venue") }}</button>
  </div>
</dialog>
```

Include it from both `concert_new.html` and `concert_edit.html`, and add a "+ New venue" chip beside each leg's venue `<select>` that opens it:

```jinja
        <button type="button" class="chip" data-new-venue>{{ _("+ New venue") }}</button>
```

- [ ] **Step 5: Wire the dialog up**

Add this script alongside the dialog. Note it reads the opening `<select>` from a JS variable set by the delegated listener, **never** from an inline `on*` handler — the browser HTML-decodes attributes before parsing them as JS, so Jinja's `&#39;` escaping does not protect an interpolated value there (invariant 7).

```javascript
(function () {
  var dlg = document.getElementById("venue-create");
  if (!dlg) return;
  var opener = null;

  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-new-venue]");
    if (!btn) return;
    // Remember which leg asked, so the new venue lands in the right row.
    opener = btn.closest(".eleg").querySelector("[data-venue-select]");
    dlg.showModal();
  });

  dlg.addEventListener("click", function (e) {
    if (e.target === dlg) dlg.close();   // backdrop-click closes, like .picker
  });

  dlg.querySelector("[data-venue-save]").addEventListener("click", async function () {
    var err = dlg.querySelector("[data-venue-err]");
    var body = new FormData();
    [["name", "v_name"], ["name_en", "v_name_en"], ["name_zh", "v_name_zh"],
     ["city", "v_city"], ["city_en", "v_city_en"], ["city_zh", "v_city_zh"],
     ["region", "v_region"], ["address", "v_address"],
     ["location_url", "v_location_url"]].forEach(function (pair) {
      body.append(pair[0], dlg.querySelector('[name="' + pair[1] + '"]').value);
    });

    var resp = await fetch("/tags/venue/quick", { method: "POST", body: body });
    if (!resp.ok) {
      err.textContent = resp.status === 422
        ? dlg.dataset.errDuplicate : dlg.dataset.errGeneric;
      err.hidden = false;
      return;
    }
    var tag = await resp.json();

    // The new venue exists for every leg, not just the one that created it.
    // textContent, never innerHTML -- the name is user-controlled.
    document.querySelectorAll("[data-venue-select]").forEach(function (sel) {
      var opt = document.createElement("option");
      opt.value = tag.id;
      opt.textContent = tag.name;
      sel.appendChild(opt);
    });
    if (opener) opener.value = tag.id;

    err.hidden = true;
    dlg.querySelectorAll("input").forEach(function (i) { i.value = ""; });
    dlg.close();
  });
})();
```

Carry the two error strings as `data-` attributes on the dialog so they stay translatable without interpolating into JS:

```jinja
<dialog class="picker" id="venue-create"
        data-err-duplicate="{{ _('That venue already exists.') }}"
        data-err-generic="{{ _('Could not add that venue.') }}">
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_tags.py -q`
Expected: PASS

- [ ] **Step 7: Update both catalogues**

Repeat Task 6 Step 7 for the dialog's new strings.

- [ ] **Step 8: Commit**

```bash
git add src/app/web/ src/app/translations/ tests/test_tags.py
git commit -m "feat: create a venue tag without leaving the concert editor"
```

---

### Task 8: Render the leg's venue from its tag

**Files:**
- Modify: `src/app/web/templates/_round_rows.html:117,130`, `_round_leg_chips.html:32`, `src/app/web/routes/concerts.py:700-724` (`concert_rounds_context`)
- Delete: `find_venue_tag` (`concerts.py:370-380`) and its tests
- Test: `tests/test_concert_page.py`

**Interfaces:**
- Consumes: `ConcertDay.venue_tag` (Task 2), the rollup (Task 4).
- Produces: `day_venue_tags` keeps its existing shape (`{day_id: Tag | None}`), so `_round_rows.html` needs no context rename.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_concert_page.py`:

```python
async def test_leg_venue_renders_from_its_tag_in_each_locale(client, session):
    tag = Tag(
        name="Kアリーナ横浜", name_en="K Arena Yokohama", name_zh="K竞技场横滨",
        kind=TagKind.VENUE, city="横浜", city_en="Yokohama", city_zh="横滨",
    )
    session.add(tag)
    concert = Concert(title="T", event_id="vt1")
    session.add(concert)
    await session.flush()
    session.add(ConcertDay(
        concert_id=concert.id, label="Day 1",
        starts_at_utc=datetime(2026, 8, 1, 9, tzinfo=UTC), venue_tag_id=tag.id,
    ))
    await session.commit()

    en = await client.get("/concerts/vt1", cookies={"lang": "en"})
    assert "K Arena Yokohama" in en.text
    assert "Kアリーナ横浜" not in en.text

    zh = await client.get("/concerts/vt1", cookies={"lang": "zh"})
    assert "K竞技场横滨" in zh.text

    ja = await client.get("/concerts/vt1", cookies={"lang": "ja"})
    assert "Kアリーナ横浜" in ja.text


async def test_leg_venue_does_not_lazy_load(client, session):
    """A lazy load during async rendering is a MissingGreenlet 500. The
    relationship is lazy="raise", so a missing eager load fails loudly here
    rather than in production."""
    tag = Tag(name="Zepp Haneda", kind=TagKind.VENUE)
    session.add(tag)
    concert = Concert(title="T", event_id="vt2")
    session.add(concert)
    await session.flush()
    session.add(ConcertDay(
        concert_id=concert.id, label="Day 1",
        starts_at_utc=datetime(2026, 8, 1, 9, tzinfo=UTC), venue_tag_id=tag.id,
    ))
    await session.commit()

    resp = await client.get("/concerts/vt2")
    assert resp.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_concert_page.py::test_leg_venue_renders_from_its_tag_in_each_locale -q`
Expected: FAIL — the venue does not render

- [ ] **Step 3: Build the context from the FK**

In `concert_rounds_context` (`concerts.py:700-724`), replace the load-everything-and-match block:

```python
    # The leg's venue is a real FK now, so this is a targeted load of exactly
    # the tags in play -- not every VENUE tag in the DB matched by name.
    # Eager-loaded here because ConcertDay.venue_tag is lazy="raise": touching
    # it lazily during async rendering would be a MissingGreenlet 500.
    venue_tag_ids = {g.day.venue_tag_id for g in leg_groups if g.day.venue_tag_id}
    venue_tags_by_id = {}
    if venue_tag_ids:
        venue_tags_by_id = {
            t.id: t for t in (await session.execute(
                select(Tag).where(Tag.id.in_(venue_tag_ids))
            )).scalars()
        }
```

and the context key becomes:

```python
        "day_venue_tags": {
            g.day.id: venue_tags_by_id.get(g.day.venue_tag_id) for g in leg_groups
        },
```

- [ ] **Step 4: Update the leg header**

In `_round_rows.html:130`, the venue span drops the free-text fallbacks and reads city and address from the tag:

```jinja
        <span class="lvenue">{% if vtag %}📍 {% if vtag.location_url %}<a href="{{ vtag.location_url }}" target="_blank" rel="noopener">{{ loc(vtag, "name") }}</a>{% else %}{{ loc(vtag, "name") }}{% endif %}{% if vtag.address %} — {{ vtag.address }}{% endif %}{% if loc(vtag, "city") %} · {{ loc(vtag, "city") }}{% endif %}{% if vtag.region %} · {{ vtag.region }}{% endif %}{% endif %}</span>
```

- [ ] **Step 5: Update the leg chip fallback**

`_round_leg_chips.html:32` falls back to `d.city`, which is no longer populated. The chain becomes label → venue tag name → date:

```jinja
    >{{ d.label or (loc(d.venue_tag, "name") if d.venue_tag else "") or (jst(d.starts_at_utc).strftime('%b %d') if d.starts_at_utc else _('Leg')) }}</button>
```

The caller must eager-load `venue_tag` on the legs it passes here, for the same `lazy="raise"` reason as Step 3.

- [ ] **Step 6: Delete find_venue_tag**

Remove `find_venue_tag` from `concerts.py:370-380` and any test asserting on it (`tests/test_concert_page.py:365-366,382-383` set free-text venue strings for exactly that purpose — update them to set `venue_tag_id`).

- [ ] **Step 7: Repoint the YAML export**

`concerts.py:1188` builds each `YamlDay` from the free-text columns. Task 6 removed the inputs that populated them, so without this the export silently starts emitting nulls — a phase-1 regression, not a phase-5 one.

Write the failing test first, in `tests/test_yaml_export.py`:

```python
async def test_yaml_export_reads_venue_from_the_tag(session):
    tag = Tag(
        name="K Arena Yokohama", kind=TagKind.VENUE,
        city="Yokohama", address="Yokohama, Japan",
    )
    session.add(tag)
    concert = Concert(title="T", event_id="y1")
    session.add(concert)
    await session.flush()
    session.add(ConcertDay(
        concert_id=concert.id, label="Day 1",
        starts_at_utc=datetime(2026, 8, 1, 9, tzinfo=UTC), venue_tag_id=tag.id,
    ))
    await session.commit()

    text = await concert_to_yaml_text(session, concert.event_id)

    assert "K Arena Yokohama" in text
    assert "Yokohama, Japan" in text
```

Then change the `YamlDay` construction. `YamlDay`'s own fields (`yaml_export.py:26-27`) do not change — only where their values come from. Use the canonical columns, not `loc_field`: an export is data, not a viewer-facing render.

```python
            city=d.venue_tag.city if d.venue_tag else None,
            venue=d.venue_tag.name if d.venue_tag else None,
            venue_address=d.venue_tag.address if d.venue_tag else None,
```

The query feeding this must eager-load `venue_tag` (`selectinload(ConcertDay.venue_tag)`) — `lazy="raise"` will otherwise raise here too.

- [ ] **Step 8: Run the full suite**

Run: `uv run pytest -q && uv run ruff check .`
Expected: green.

- [ ] **Step 8: Commit**

```bash
git add src/app/web/ tests/
git commit -m "feat: render a leg's venue from its tag in the viewer's language"
```

---

## Done when

- `uv run pytest -q` and `uv run ruff check .` are green.
- A concert page shows its leg venues in English, Chinese and Japanese from one entry.
- A venue can be created from the leg editor without leaving the page.
- Editing a leg's venue updates the concert's venue tags, and Discover's region filter still matches that concert.
- The migration reports any leg whose free-text venue did not match a tag.

**Not in this phase:** dropping `Concert.venue`/`venue_en`/`venue_zh` and `ConcertDay.city`/`venue`/`venue_address` (phase 5, after live verification), trilingual leg and round labels (phase 2), the phrase library (phase 3), enforcement (phase 4).

## Deploy note

After `git pull && uv sync && uv run alembic upgrade head`, **read the migration output**. Any leg listed as unmatched needs its venue tag created and the leg re-pointed before phase 5 drops the free-text columns.
